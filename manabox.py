import csv
import logging
from pathlib import Path

from sqlite_ops import get_connection, init_db, upsert_collection_row, write_sync_log

log = logging.getLogger(__name__)

# ManaBox CSV column → collection table column.
# Adjust if MTG_Database_Schema_Spec.md uses different names.
CSV_TO_DB = {
    "Name":             "name",
    "Scryfall ID":      "scryfall_id",
    "Set code":         "set_code",
    "Collector number": "collector_number",
    "Foil":             "foil",
    "Condition":        "condition",
    "Language":         "language",
    "Quantity":         "quantity",
    "Purchase price":   "purchase_price",
}

REQUIRED = {"Name", "Scryfall ID", "Quantity"}


def _parse_row(raw: dict) -> dict:
    """Normalise a raw CSV row into a collection-table dict."""
    return {
        "name":             raw["Name"].strip(),
        "scryfall_id":      raw["Scryfall ID"].strip(),
        "set_code":         raw.get("Set code", "").strip() or None,
        "collector_number": raw.get("Collector number", "").strip() or None,
        "foil":             1 if raw.get("Foil", "").strip().lower() in ("yes", "true", "1", "foil") else 0,
        "condition":        raw.get("Condition", "").strip() or None,
        "language":         raw.get("Language", "").strip() or None,
        "quantity":         int(raw.get("Quantity", 1) or 1),
        "purchase_price":   float(raw["Purchase price"]) if raw.get("Purchase price", "").strip() else None,
    }


def ingest_csv(csv_path: str | Path) -> dict:
    """Parse a ManaBox CSV export and upsert rows into the SQLite collection table.

    Returns a summary dict with added/updated/unchanged counts.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"ManaBox export not found: {csv_path}")

    init_db()

    added = updated = unchanged = skipped = 0

    with get_connection() as conn:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"ManaBox CSV is missing required columns: {missing}")

            for i, raw in enumerate(reader, start=2):  # row 1 is header
                try:
                    row = _parse_row(raw)
                except (ValueError, KeyError) as exc:
                    log.warning("Skipping CSV row %d — %s", i, exc)
                    skipped += 1
                    continue

                result = upsert_collection_row(conn, row)
                if result == "added":
                    added += 1
                elif result == "updated":
                    updated += 1
                else:
                    unchanged += 1

        write_sync_log(conn, source="manabox", added=added, updated=updated, unchanged=unchanged)

    log.info(
        "ManaBox ingest complete — added=%d updated=%d unchanged=%d skipped=%d",
        added, updated, unchanged, skipped,
    )
    return {"added": added, "updated": updated, "unchanged": unchanged, "skipped": skipped}
