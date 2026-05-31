import json
import logging
import time
from pathlib import Path

import requests

from config import (
    SPELLBOOK_API_URL, SPELLBOOK_BULK, SPELLBOOK_BULK_MAX_AGE_DAYS, CACHE_DIR,
)
from scryfall import normalize_name
from neo4j_ops import (
    Session,
    upsert_combo_nodes_batch,
    link_cards_to_combo_batch,
)

log = logging.getLogger(__name__)

_BATCH_SIZE = 200
_http = requests.Session()
_http.headers["User-Agent"] = "mtg-db/0.1 (personal collection tool)"


# ---------------------------------------------------------------------------
# Download and cache
# ---------------------------------------------------------------------------

def _bulk_is_fresh() -> bool:
    if not SPELLBOOK_BULK.exists():
        return False
    age_days = (time.time() - SPELLBOOK_BULK.stat().st_mtime) / 86400
    return age_days < SPELLBOOK_BULK_MAX_AGE_DAYS


def download_combos() -> list[dict]:
    """Return all Commander Spellbook variants, using a 7-day local cache."""
    if _bulk_is_fresh():
        log.info("Spellbook cache is fresh, loading from %s", SPELLBOOK_BULK)
        with SPELLBOOK_BULK.open(encoding="utf-8") as f:
            return json.load(f)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    partial_path = SPELLBOOK_BULK.with_suffix(".partial.json")

    # Resume from partial download if one exists
    if partial_path.exists():
        with partial_path.open(encoding="utf-8") as f:
            state = json.load(f)
        all_variants = state["variants"]
        next_url = state["next_url"]
        log.info("Resuming Spellbook download from offset %d", len(all_variants))
    else:
        all_variants = []
        next_url = f"{SPELLBOOK_API_URL}/variants/"

    log.info("Downloading Commander Spellbook variants from %s", SPELLBOOK_API_URL)
    page = len(all_variants) // 100 + 1

    while next_url:
        for attempt in range(6):
            try:
                resp = _http.get(next_url, timeout=60)
            except Exception as exc:
                log.warning("Request error on attempt %d/6: %s", attempt + 1, exc)
                time.sleep(30)
                continue
            if resp.status_code == 429:
                wait = 60 * (2 ** attempt)
                log.warning("429 rate limit — waiting %ds (attempt %d/6)", wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            # Save progress before giving up so next run can resume
            partial_path.write_text(
                json.dumps({"variants": all_variants, "next_url": next_url}),
                encoding="utf-8",
            )
            raise RuntimeError(
                f"Spellbook rate limit exceeded — progress saved to {partial_path}. "
                "Re-run sync_combos() to resume."
            )

        data = resp.json()
        all_variants.extend(data.get("results", []))
        next_url = data.get("next")
        log.info("Spellbook page %d — %d combos so far", page, len(all_variants))
        page += 1

        # Checkpoint every 20 pages
        if page % 20 == 0:
            partial_path.write_text(
                json.dumps({"variants": all_variants, "next_url": next_url}),
                encoding="utf-8",
            )

        time.sleep(2.0)  # 0.5 req/sec — conservative

    # Complete — write final cache and remove partial
    with SPELLBOOK_BULK.open("w", encoding="utf-8") as f:
        json.dump(all_variants, f)
    if partial_path.exists():
        partial_path.unlink()

    log.info("Cached %d combos to %s", len(all_variants), SPELLBOOK_BULK)
    return all_variants


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _parse_combo(raw: dict) -> dict:
    """Normalise a raw Spellbook variant into a combo dict.

    Returns:
        combo_id, card_names, card_oracle_ids, results, description,
        prerequisites, color_identity
    """
    card_names = []
    card_oracle_ids = []
    for entry in raw.get("uses", []):
        card = entry.get("card", {})
        name = card.get("name")
        oid  = card.get("oracleId")
        if name:
            card_names.append(normalize_name(name))
        if oid:
            card_oracle_ids.append(oid)

    results = [
        entry["feature"]["name"]
        for entry in raw.get("produces", [])
        if entry.get("feature", {}).get("name")
    ]

    # Color identity comes as full words ("White", "Blue", ...) or single letters
    raw_identity = raw.get("identity", [])
    color_map = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
    color_identity = [color_map.get(c, c) for c in raw_identity]

    prerequisites = " | ".join(filter(None, [
        raw.get("easyPrerequisites", ""),
        raw.get("notablePrerequisites", ""),
    ])) or None

    return {
        "combo_id":        str(raw["id"]),
        "card_names":      card_names,
        "card_oracle_ids": card_oracle_ids,
        "results":         results,
        "description":     raw.get("description") or None,
        "prerequisites":   prerequisites,
        "color_identity":  color_identity,
    }


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def filter_by_deck_cards(combos: list[dict], deck_card_names: set[str]) -> list[dict]:
    """Return combos where at least one card appears in any of your decklists."""
    return [c for c in combos if deck_card_names.intersection(c["card_names"])]


def get_deck_card_names(session: Session) -> set[str]:
    """Query Neo4j for all card names that appear in at least one deck."""
    result = session.run(
        "MATCH (c:Card)-[:IN_DECK]->(:Deck) RETURN DISTINCT c.name AS name"
    )
    return {record["name"] for record in result}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_combos(session: Session, card_lookup: dict) -> dict:
    """Download Spellbook combos, filter to your decklists, upsert to Neo4j.

    Returns summary dict with total, relevant, synced, skipped counts.
    """
    all_combos_raw = download_combos()
    all_combos = [_parse_combo(r) for r in all_combos_raw]
    log.info("Parsed %d total Spellbook combos", len(all_combos))

    deck_card_names = get_deck_card_names(session)
    log.info("Cards in your decklists: %d", len(deck_card_names))

    relevant = filter_by_deck_cards(all_combos, deck_card_names)
    log.info("Combos involving at least one deck card: %d", len(relevant))

    synced = skipped = 0
    combo_batch: list[dict] = []

    for combo in relevant:
        oracle_ids = combo["card_oracle_ids"]
        if not oracle_ids:
            skipped += 1
            continue

        combo_batch.append({
            "combo_id":       combo["combo_id"],
            "results":        combo["results"],
            "description":    combo["description"],
            "prerequisites":  combo["prerequisites"],
            "color_identity": combo["color_identity"],
        })

        # Flush combo node batch
        if len(combo_batch) >= _BATCH_SIZE:
            upsert_combo_nodes_batch(session, combo_batch)
            combo_batch = []

        link_cards_to_combo_batch(session, combo["combo_id"], oracle_ids)
        synced += 1

    if combo_batch:
        upsert_combo_nodes_batch(session, combo_batch)

    log.info(
        "Spellbook sync complete — total=%d relevant=%d synced=%d skipped=%d",
        len(all_combos), len(relevant), synced, skipped,
    )
    return {
        "total":    len(all_combos),
        "relevant": len(relevant),
        "synced":   synced,
        "skipped":  skipped,
    }
