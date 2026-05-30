import logging
import re
import time
from pathlib import Path

import requests

from config import ARCHIDEKT_BASE_URL, ARCHIDEKT_USERNAME, ARCHIDEKT_PASSWORD, EXPORTS_DIR
from scryfall import normalize_name
from neo4j_ops import (
    Session,
    upsert_card_nodes_batch,
    upsert_deck_node,
    link_cards_to_deck_batch,
)

log = logging.getLogger(__name__)

_RATE_LIMIT_DELAY = 0.5  # seconds between Archidekt API calls

_FORMAT_MAP = {
    1: "Standard", 2: "Modern", 3: "Commander", 4: "Legacy",
    5: "Vintage", 6: "Pauper", 7: "Pioneer", 8: "Historic",
    14: "Explorer", 17: "Timeless",
}

_http = requests.Session()
_http.headers["User-Agent"] = "mtg-db/0.1 (personal collection tool)"

_authenticated = False
_user_decks: list[dict] = []   # populated from login response


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def authenticate(username: str | None = None, password: str | None = None) -> None:
    """POST credentials to /api/rest-auth/login/, store Bearer JWT, and cache deck list."""
    global _authenticated, _user_decks
    username = username or ARCHIDEKT_USERNAME
    password = password or ARCHIDEKT_PASSWORD

    resp = _http.post(
        f"{ARCHIDEKT_BASE_URL}/rest-auth/login/",
        json={"username": username, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("access_token") or data.get("token")
    if not token:
        raise RuntimeError(f"Login succeeded but no token in response: {list(data.keys())}")

    _http.headers["Authorization"] = f"Bearer {token}"
    _authenticated = True

    _user_decks = data.get("user", {}).get("decks", [])
    log.info("Archidekt auth OK — user '%s', %d decks visible", username, len(_user_decks))


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{ARCHIDEKT_BASE_URL}{path}"
    resp = _http.get(url, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(_RATE_LIMIT_DELAY)
    return resp.json()


def fetch_deck(deck_id: int | str) -> dict:
    """Fetch a single deck by ID from the Archidekt API."""
    return _get(f"/decks/{deck_id}/")


def fetch_user_deck_ids(username: str | None = None, include_private: bool = False) -> list[int]:
    """Return deck IDs for the authenticated user from the login payload.

    Private decks are excluded by default — the API returns 404 for them
    even when authenticated as the owner.
    """
    if not _authenticated:
        authenticate()
    decks = _user_decks if include_private else [d for d in _user_decks if not d.get("private")]
    ids = [d["id"] for d in decks]
    skipped = len(_user_decks) - len(ids)
    log.info("Deck IDs from login payload: %d public%s",
             len(ids), f" ({skipped} private skipped)" if skipped else "")
    return ids


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_deck_api(data: dict) -> dict:
    """Normalise a raw Archidekt API deck response into a standard deck dict.

    Standard deck dict:
        deck_id (str), name (str), format (str), url (str),
        cards (list of {scryfall_id, name, quantity, is_commander})
    """
    deck_id = str(data["id"])
    fmt_code = data.get("deckFormat", 0)
    cards_raw = data.get("cards", [])

    cards: list[dict] = []
    for entry in cards_raw:
        oracle = entry.get("card", {}).get("oracleCard", {})
        name = normalize_name(oracle.get("name", ""))
        quantity = entry.get("quantity", 1)
        categories = [c.lower() for c in entry.get("categories", [])]
        is_commander = "commander" in categories

        if not name:
            continue

        # Archidekt's oracleCard.uid is their internal ID, not Scryfall's.
        # scryfall_id is resolved at sync time via card_lookup keyed by name.
        cards.append({
            "scryfall_id": None,
            "name": name,
            "quantity": quantity,
            "is_commander": is_commander,
        })

    return {
        "deck_id": deck_id,
        "name": data.get("name", f"Deck {deck_id}"),
        "format": _FORMAT_MAP.get(fmt_code, str(fmt_code)),
        "url": f"https://archidekt.com/decks/{deck_id}",
        "cards": cards,
    }


# Line patterns for manual export files:
#   "1 Card Name"
#   "1 Card Name (SET) 123"         ← strip set/collector suffix
#   "// Commander"                  ← section header
_CARD_LINE = re.compile(r"^(\d+)\s+(.+?)(?:\s+\([A-Z0-9]+\)\s*\S*)?$")
_SECTION   = re.compile(r"^//\s*(.*)")


def _parse_export_file(path: Path) -> dict:
    """Parse a plain-text Archidekt export into a standard deck dict.

    Supports:
        1 Card Name
        1 Card Name (SET) 123
        // Commander  (section header — cards after this are flagged is_commander)
    """
    deck_id = f"export_{path.stem}"
    cards: list[dict] = []
    current_section = ""

    with path.open(encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            section_match = _SECTION.match(line)
            if section_match:
                current_section = section_match.group(1).strip().lower()
                continue

            card_match = _CARD_LINE.match(line)
            if card_match:
                quantity = int(card_match.group(1))
                name = normalize_name(card_match.group(2).strip())
                cards.append({
                    "scryfall_id": None,  # resolved via card_lookup at sync time
                    "name": name,
                    "quantity": quantity,
                    "is_commander": current_section == "commander",
                })

    return {
        "deck_id": deck_id,
        "name": path.stem.replace("_", " ").replace("-", " "),
        "format": "Unknown",
        "url": "",
        "cards": cards,
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_deck(deck: dict, session: Session, card_lookup: dict) -> dict:
    """Upsert a deck node and all its IN_DECK relationships into Neo4j.

    Args:
        deck:        Standard deck dict (from _parse_deck_api or _parse_export_file).
        session:     Active Neo4j session.
        card_lookup: Output of scryfall.build_card_lookup().

    Returns:
        Summary dict with deck_id, name, linked, missing counts.
    """
    upsert_deck_node(session, deck)

    # Resolve scryfall_ids via card_lookup for export-parsed cards (id=None)
    # and ensure all card nodes exist before creating relationships.
    resolved: list[dict] = []
    missing: list[str] = []
    new_nodes: list[dict] = []

    for card in deck["cards"]:
        sid = card["scryfall_id"]
        name = card["name"]

        if sid is None:
            # Manual export — look up by name
            data = card_lookup.get(name)
            if data is None:
                missing.append(name)
                continue
            sid = data["scryfall_id"]
            new_nodes.append({"name": name, **data})
        else:
            # API path — scryfall_id already known; ensure node exists
            if name in card_lookup:
                new_nodes.append({"name": name, **card_lookup[name]})

        resolved.append({
            "scryfall_id": sid,
            "quantity": card["quantity"],
            "is_commander": card["is_commander"],
        })

    if new_nodes:
        upsert_card_nodes_batch(session, new_nodes)

    if resolved:
        link_cards_to_deck_batch(session, deck["deck_id"], resolved)

    if missing:
        log.warning("sync_deck '%s': %d card(s) not in lookup: %s",
                    deck["name"], len(missing), missing[:10])

    log.info("sync_deck '%s' — linked=%d missing=%d",
             deck["name"], len(resolved), len(missing))

    return {
        "deck_id": deck["deck_id"],
        "name": deck["name"],
        "linked": len(resolved),
        "missing": len(missing),
    }


def sync_all_decks(session: Session, card_lookup: dict,
                   username: str | None = None) -> list[dict]:
    """Fetch and sync all Archidekt decks for username via the API.

    Falls back to ARCHIDEKT_USERNAME from config if username is not provided.
    """
    username = username or ARCHIDEKT_USERNAME
    if not username:
        raise ValueError("No Archidekt username — set ARCHIDEKT_USERNAME in config.py")

    deck_ids = fetch_user_deck_ids(username)
    log.info("Found %d decks for user '%s'", len(deck_ids), username)

    results = []
    for deck_id in deck_ids:
        try:
            raw = fetch_deck(deck_id)
            deck = _parse_deck_api(raw)
            summary = sync_deck(deck, session, card_lookup)
            results.append(summary)
        except requests.HTTPError as exc:
            log.warning("Skipping deck %s — HTTP %s", deck_id, exc.response.status_code)
        except Exception as exc:
            log.warning("Skipping deck %s — %s", deck_id, exc)

    return results


def ingest_exports(session: Session, card_lookup: dict) -> list[dict]:
    """Parse every .txt file in EXPORTS_DIR and sync each as a deck.

    Drop your Archidekt text exports into the exports/ folder and call this
    as a fallback when the API isn't available.
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    export_files = sorted(EXPORTS_DIR.glob("*.txt"))

    if not export_files:
        log.warning("No .txt files found in %s", EXPORTS_DIR)
        return []

    results = []
    for path in export_files:
        log.info("Ingesting export: %s", path.name)
        deck = _parse_export_file(path)
        summary = sync_deck(deck, session, card_lookup)
        results.append(summary)

    return results
