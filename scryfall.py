import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import CACHE_DIR, SCRYFALL_BULK, SCRYFALL_BULK_MAX_AGE_DAYS, SCRYFALL_BULK_URL

log = logging.getLogger(__name__)


def _bulk_is_fresh() -> bool:
    if not SCRYFALL_BULK.exists():
        return False
    age_days = (time.time() - SCRYFALL_BULK.stat().st_mtime) / 86400
    return age_days < SCRYFALL_BULK_MAX_AGE_DAYS


def download_bulk_data() -> Path:
    """Return path to cached oracle_cards bulk JSON, downloading if stale."""
    if _bulk_is_fresh():
        log.info("Scryfall bulk cache is fresh, skipping download")
        return SCRYFALL_BULK

    log.info("Fetching Scryfall bulk-data index from %s", SCRYFALL_BULK_URL)
    resp = requests.get(SCRYFALL_BULK_URL, timeout=30)
    resp.raise_for_status()

    datasets = resp.json().get("data", [])
    oracle = next((d for d in datasets if d["type"] == "oracle_cards"), None)
    if oracle is None:
        raise RuntimeError("oracle_cards dataset not found in Scryfall bulk-data index")

    download_url = oracle["download_uri"]
    log.info("Downloading oracle_cards from %s", download_url)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with requests.get(download_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with SCRYFALL_BULK.open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    log.info("Saved bulk data to %s", SCRYFALL_BULK)
    return SCRYFALL_BULK


def build_card_lookup() -> dict[str, dict]:
    """Parse bulk JSON into a dict keyed by canonical card name.

    Returns:
        {name: {scryfall_id, mana_cost, cmc, type_line, oracle_text,
                colors, color_identity, keywords, power, toughness, loyalty}}
    """
    path = download_bulk_data()
    log.info("Building card lookup from %s", path)

    with path.open(encoding="utf-8") as f:
        cards = json.load(f)

    lookup: dict[str, dict] = {}
    for card in cards:
        name = card.get("name")
        if not name:
            continue

        # For double-faced cards Scryfall stores oracle_text on card_faces;
        # fall back to joining face texts when the top-level field is absent.
        oracle_text = card.get("oracle_text")
        if oracle_text is None and "card_faces" in card:
            oracle_text = "\n//\n".join(
                face.get("oracle_text", "") for face in card["card_faces"]
            )

        lookup[name] = {
            "scryfall_id": card.get("id"),
            "mana_cost": card.get("mana_cost"),
            "cmc": card.get("cmc"),
            "type_line": card.get("type_line"),
            "oracle_text": oracle_text,
            "colors": card.get("colors", []),
            "color_identity": card.get("color_identity", []),
            "keywords": card.get("keywords", []),
            "power": card.get("power"),
            "toughness": card.get("toughness"),
            "loyalty": card.get("loyalty"),
        }

    log.info("Built lookup with %d cards", len(lookup))
    return lookup


def populate_card_nodes(neo4j_session, card_names: list[str]) -> None:
    """Stub — populate Neo4j card nodes for the given card names.

    Implement after neo4j_ops.py is built.
    """
    raise NotImplementedError("populate_card_nodes: implement after neo4j_ops.py")
