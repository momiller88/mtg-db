import logging
from contextlib import contextmanager

from neo4j import GraphDatabase, Driver, Session

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE

log = logging.getLogger(__name__)

_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_driver() -> Driver:
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


@contextmanager
def get_session(driver: Driver | None = None):
    """Yield a Neo4j session, closing it and any created driver on exit."""
    _own_driver = driver is None
    drv = get_driver() if _own_driver else driver
    session = drv.session(database=NEO4J_DATABASE)
    try:
        yield session
    finally:
        session.close()
        if _own_driver:
            drv.close()


def verify_connection() -> bool:
    """Return True if AuraDB is reachable, False otherwise."""
    try:
        with get_session() as session:
            session.run("RETURN 1")
        log.info("Neo4j connection OK — %s", NEO4J_URI)
        return True
    except Exception as exc:
        log.error("Neo4j connection failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Schema constraints and indexes
# ---------------------------------------------------------------------------

def ensure_constraints(session: Session) -> None:
    """Create uniqueness constraints and indexes (idempotent)."""
    stmts = [
        "CREATE CONSTRAINT card_scryfall_id IF NOT EXISTS FOR (c:Card) REQUIRE c.scryfall_id IS UNIQUE",
        "CREATE CONSTRAINT deck_id IF NOT EXISTS FOR (d:Deck) REQUIRE d.deck_id IS UNIQUE",
        "CREATE CONSTRAINT combo_id IF NOT EXISTS FOR (x:Combo) REQUIRE x.combo_id IS UNIQUE",
        "CREATE INDEX card_name IF NOT EXISTS FOR (c:Card) ON (c.name)",
    ]
    for stmt in stmts:
        session.run(stmt)
    log.info("Neo4j constraints/indexes ensured")


# ---------------------------------------------------------------------------
# Card nodes
# ---------------------------------------------------------------------------

def upsert_card_node(session: Session, card: dict) -> None:
    """MERGE a single Card node by scryfall_id, setting all properties."""
    session.run(
        """
        MERGE (c:Card {scryfall_id: $scryfall_id})
        SET c.name                = $name,
            c.mana_cost           = $mana_cost,
            c.cmc                 = $cmc,
            c.type_line           = $type_line,
            c.oracle_text         = $oracle_text,
            c.colors              = $colors,
            c.color_identity      = $color_identity,
            c.keywords            = $keywords,
            c.power               = $power,
            c.toughness           = $toughness,
            c.loyalty             = $loyalty
        """,
        **card,
    )


def upsert_card_nodes_batch(session: Session, cards: list[dict]) -> None:
    """MERGE a batch of Card nodes using UNWIND for efficiency."""
    session.run(
        """
        UNWIND $cards AS card
        MERGE (c:Card {scryfall_id: card.scryfall_id})
        SET c.name           = card.name,
            c.mana_cost      = card.mana_cost,
            c.cmc            = card.cmc,
            c.type_line      = card.type_line,
            c.oracle_text    = card.oracle_text,
            c.colors         = card.colors,
            c.color_identity = card.color_identity,
            c.keywords       = card.keywords,
            c.power          = card.power,
            c.toughness      = card.toughness,
            c.loyalty        = card.loyalty
        """,
        cards=cards,
    )


def populate_card_nodes(session: Session, card_lookup: dict, card_names: list[str]) -> int:
    """MERGE Card nodes for the given names using data from build_card_lookup().

    Args:
        session:     Active Neo4j session.
        card_lookup: Output of scryfall.build_card_lookup().
        card_names:  Names of cards to upsert (e.g. all names in a deck).

    Returns:
        Number of cards written (names not found in lookup are skipped with a warning).
    """
    batch: list[dict] = []
    missing: list[str] = []

    for name in card_names:
        data = card_lookup.get(name)
        if data is None:
            missing.append(name)
            continue
        batch.append({"name": name, **data})

    if missing:
        log.warning("populate_card_nodes: %d name(s) not in Scryfall lookup: %s",
                    len(missing), missing[:10])

    total = 0
    for i in range(0, len(batch), _BATCH_SIZE):
        chunk = batch[i : i + _BATCH_SIZE]
        upsert_card_nodes_batch(session, chunk)
        total += len(chunk)
        log.debug("Card nodes: wrote batch %d–%d", i, i + len(chunk))

    log.info("populate_card_nodes: upserted %d card nodes (%d missing)", total, len(missing))
    return total


# ---------------------------------------------------------------------------
# Deck nodes and relationships  (stubs — implement in archidekt.py)
# ---------------------------------------------------------------------------

def upsert_deck_node(session: Session, deck: dict) -> None:
    """MERGE a Deck node. Expected keys: deck_id, name, format, url.

    Stub — call from archidekt.py after that module is built.
    """
    raise NotImplementedError("upsert_deck_node: implement when archidekt.py is ready")


def link_card_to_deck(session: Session, scryfall_id: str, deck_id: str,
                      quantity: int, is_commander: bool = False) -> None:
    """Create or update an IN_DECK relationship between a Card and a Deck.

    Stub — call from archidekt.py after that module is built.
    """
    raise NotImplementedError("link_card_to_deck: implement when archidekt.py is ready")


# ---------------------------------------------------------------------------
# Combo nodes and relationships  (stubs — implement in spellbook.py)
# ---------------------------------------------------------------------------

def upsert_combo_node(session: Session, combo: dict) -> None:
    """MERGE a Combo node. Expected keys: combo_id, description, results.

    Stub — call from spellbook.py after that module is built.
    """
    raise NotImplementedError("upsert_combo_node: implement when spellbook.py is ready")


def link_card_to_combo(session: Session, scryfall_id: str, combo_id: str) -> None:
    """Create a PART_OF_COMBO relationship between a Card and a Combo.

    Stub — call from spellbook.py after that module is built.
    """
    raise NotImplementedError("link_card_to_combo: implement when spellbook.py is ready")
