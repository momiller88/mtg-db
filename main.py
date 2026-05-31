import argparse
import logging
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy neo4j driver notifications at INFO level
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Individual command handlers
# ---------------------------------------------------------------------------

def cmd_verify(args) -> int:
    """Test Neo4j and SQLite connections."""
    from neo4j_ops import verify_connection
    from config import DB_PATH

    ok = True

    neo4j_ok = verify_connection()
    if not neo4j_ok:
        log.error("Neo4j connection FAILED")
        ok = False

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        log.info("SQLite OK — %s", DB_PATH)
    except Exception as exc:
        log.error("SQLite connection FAILED: %s", exc)
        ok = False

    return 0 if ok else 1


def cmd_sync_collection(args) -> int:
    """Ingest a ManaBox CSV export into SQLite."""
    from manabox import ingest_csv

    csv_path = Path(args.csv)
    if not csv_path.exists():
        log.error("CSV file not found: %s", csv_path)
        return 1

    result = ingest_csv(csv_path)
    log.info("Collection sync complete: %s", result)
    return 0


def cmd_sync_cards(args) -> int:
    """Populate Card nodes in Neo4j from the Scryfall oracle_cards bulk."""
    import sqlite3
    from config import DB_PATH
    from scryfall import build_card_lookup
    from neo4j_ops import get_session, ensure_constraints, populate_card_nodes

    card_lookup = build_card_lookup()

    # Collect card names from SQLite collection
    try:
        conn = sqlite3.connect(DB_PATH)
        names = [r[0] for r in conn.execute("SELECT DISTINCT name FROM collection").fetchall()]
        conn.close()
        log.info("Loaded %d distinct card names from collection", len(names))
    except Exception:
        names = list(card_lookup.keys())
        log.warning("Could not read SQLite collection — syncing all %d Scryfall cards", len(names))

    with get_session() as session:
        ensure_constraints(session)
        written = populate_card_nodes(session, card_lookup, names)

    log.info("Card sync complete — %d nodes written", written)
    return 0


def cmd_sync_decks(args) -> int:
    """Sync Archidekt decks and card nodes into Neo4j."""
    from scryfall import build_card_lookup
    from neo4j_ops import get_session
    from archidekt import sync_all_decks, ingest_exports

    card_lookup = build_card_lookup()

    with get_session() as session:
        if args.exports_only:
            results = ingest_exports(session, card_lookup)
        else:
            results = sync_all_decks(session, card_lookup)

    total_linked = sum(r["linked"] for r in results)
    total_missing = sum(r["missing"] for r in results)
    log.info("Deck sync complete — %d decks, %d cards linked, %d missing",
             len(results), total_linked, total_missing)
    return 0


def cmd_sync_combos(args) -> int:
    """Sync Commander Spellbook combos into Neo4j."""
    from scryfall import build_card_lookup
    from neo4j_ops import get_session
    from spellbook import sync_combos

    card_lookup = build_card_lookup()

    with get_session() as session:
        result = sync_combos(session, card_lookup)

    log.info("Combo sync complete: %s", result)
    return 0


def cmd_tag_mechanics(args) -> int:
    """Detect mechanics from oracle text and write to Card nodes."""
    from scryfall import build_card_lookup
    from neo4j_ops import get_session
    from mechanics import run_mechanic_tagging

    card_lookup = build_card_lookup()

    with get_session() as session:
        result = run_mechanic_tagging(session, card_lookup)

    log.info("Mechanic tagging complete: %s", result)
    return 0


def cmd_classify_roles(args) -> int:
    """Classify broad_role and sub_roles for all Card nodes."""
    from scryfall import build_card_lookup
    from neo4j_ops import get_session
    from roles import run_role_classification

    card_lookup = build_card_lookup()

    with get_session() as session:
        result = run_role_classification(session, card_lookup)

    log.info("Role classification complete: %s", result)
    return 0


def cmd_stats(args) -> int:
    """Print current state of both databases."""
    import sqlite3
    from config import DB_PATH
    from neo4j_ops import get_session

    # --- SQLite ---
    print("\n=== SQLite ===")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
        foil  = conn.execute("SELECT COUNT(*) FROM collection WHERE foil = 1").fetchone()[0]
        sets  = conn.execute("SELECT COUNT(DISTINCT set_code) FROM collection").fetchone()[0]
        print(f"  Collection rows : {total:,}")
        print(f"  Foil copies     : {foil:,}")
        print(f"  Distinct sets   : {sets}")

        syncs = conn.execute(
            "SELECT source, added, updated, unchanged, timestamp FROM sync_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if syncs:
            print("  Recent syncs:")
            for s in syncs:
                print(f"    [{s['timestamp'][:19]}] {s['source']}: "
                      f"+{s['added']} ~{s['updated']} ={s['unchanged']}")
        conn.close()
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # --- Neo4j ---
    print("\n=== Neo4j AuraDB ===")
    try:
        with get_session() as session:
            counts = session.run("""
                MATCH (c:Card)   WITH count(c) AS cards
                MATCH (d:Deck)   WITH cards, count(d) AS decks
                MATCH (x:Combo)  WITH cards, decks, count(x) AS combos
                RETURN cards, decks, combos
            """).single()
            in_deck = session.run(
                "MATCH ()-[r:IN_DECK]->() RETURN count(r) AS n"
            ).single()["n"]
            in_combo = session.run(
                "MATCH ()-[r:PART_OF_COMBO]->() RETURN count(r) AS n"
            ).single()["n"]

            print(f"  Card nodes      : {counts['cards']:,}")
            print(f"  Deck nodes      : {counts['decks']:,}")
            print(f"  Combo nodes     : {counts['combos']:,}")
            print(f"  IN_DECK rels    : {in_deck:,}")
            print(f"  PART_OF_COMBO   : {in_combo:,}")

            role_dist = session.run("""
                MATCH (c:Card) WHERE c.broad_role IS NOT NULL
                RETURN c.broad_role AS role, count(*) AS n
                ORDER BY n DESC
            """).data()
            if role_dist:
                print("  Role distribution:")
                for r in role_dist:
                    print(f"    {r['role']:20s} {r['n']:>6,}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print()
    return 0


def cmd_sync(args) -> int:
    """Run the full sync pipeline end-to-end."""
    import sqlite3
    from config import DB_PATH
    from scryfall import build_card_lookup
    from sqlite_ops import init_db
    from neo4j_ops import get_session, ensure_constraints, populate_card_nodes
    from archidekt import sync_all_decks, ingest_exports
    from spellbook import sync_combos
    from mechanics import run_mechanic_tagging
    from roles import run_role_classification

    steps_ok: list[str] = []
    steps_failed: list[str] = []

    def run_step(label: str, fn):
        log.info("--- %s ---", label)
        try:
            result = fn()
            steps_ok.append(label)
            return result
        except Exception as exc:
            log.error("%s FAILED: %s", label, exc)
            steps_failed.append(label)
            return None

    # 1. Verify connections
    rc = cmd_verify(args)
    if rc != 0:
        log.error("Connection check failed — aborting sync")
        return 1

    # 2. SQLite init
    run_step("SQLite init", init_db)

    # 3. Ingest CSV if provided
    if args.csv:
        csv_path = Path(args.csv)
        if csv_path.exists():
            from manabox import ingest_csv
            run_step("ManaBox CSV ingest", lambda: ingest_csv(csv_path))
        else:
            log.warning("CSV path not found, skipping: %s", csv_path)

    # 4. Build Scryfall lookup (cached)
    log.info("--- Scryfall card lookup ---")
    card_lookup = build_card_lookup()

    with get_session() as session:
        # 5. Neo4j schema
        run_step("Neo4j constraints", lambda: ensure_constraints(session))

        # 6. Populate card nodes from collection
        try:
            conn = sqlite3.connect(DB_PATH)
            names = [r[0] for r in conn.execute(
                "SELECT DISTINCT name FROM collection"
            ).fetchall()]
            conn.close()
        except Exception:
            names = list(card_lookup.keys())

        run_step("Card node population",
                 lambda: populate_card_nodes(session, card_lookup, names))

        # 7. Deck sync (Archidekt API or exports/)
        if args.exports_only:
            run_step("Deck sync (exports)", lambda: ingest_exports(session, card_lookup))
        else:
            run_step("Deck sync (Archidekt)", lambda: sync_all_decks(session, card_lookup))

        # 8. Combo sync (Commander Spellbook)
        run_step("Combo sync (Spellbook)", lambda: sync_combos(session, card_lookup))

        # 9. Mechanic tagging
        run_step("Mechanic tagging", lambda: run_mechanic_tagging(session, card_lookup))

        # 10. Role classification
        run_step("Role classification", lambda: run_role_classification(session, card_lookup))

    print(f"\nSync complete — {len(steps_ok)} steps OK, {len(steps_failed)} failed")
    if steps_failed:
        print(f"Failed: {', '.join(steps_failed)}")
        return 1

    cmd_stats(args)
    return 0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mtg-db",
        description="MTG collection database — sync, query, and manage your cards.",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable DEBUG logging")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # verify
    p = subparsers.add_parser("verify", help="Test Neo4j and SQLite connections")
    p.set_defaults(func=cmd_verify)

    # stats
    p = subparsers.add_parser("stats", help="Show database statistics")
    p.set_defaults(func=cmd_stats)

    # sync-collection
    p = subparsers.add_parser("sync-collection", help="Ingest a ManaBox CSV into SQLite")
    p.add_argument("csv", help="Path to ManaBox CSV export")
    p.set_defaults(func=cmd_sync_collection)

    # sync-cards
    p = subparsers.add_parser("sync-cards", help="Sync Scryfall card nodes to Neo4j")
    p.set_defaults(func=cmd_sync_cards)

    # sync-decks
    p = subparsers.add_parser("sync-decks", help="Sync Archidekt decks to Neo4j")
    p.add_argument("--exports-only", action="store_true",
                   help="Use exports/ folder instead of Archidekt API")
    p.set_defaults(func=cmd_sync_decks)

    # sync-combos
    p = subparsers.add_parser("sync-combos", help="Sync Commander Spellbook combos to Neo4j")
    p.set_defaults(func=cmd_sync_combos)

    # tag-mechanics
    p = subparsers.add_parser("tag-mechanics", help="Tag card mechanics from oracle text")
    p.set_defaults(func=cmd_tag_mechanics)

    # classify-roles
    p = subparsers.add_parser("classify-roles", help="Classify broad and sub roles for all cards")
    p.set_defaults(func=cmd_classify_roles)

    # sync (full pipeline)
    p = subparsers.add_parser("sync", help="Run the full sync pipeline")
    p.add_argument("--csv", metavar="PATH",
                   help="ManaBox CSV to ingest (optional)")
    p.add_argument("--exports-only", action="store_true",
                   help="Use exports/ folder for decks instead of Archidekt API")
    p.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    _setup_logging(args.verbose)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
