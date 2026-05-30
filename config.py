from pathlib import Path

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
EXPORTS_DIR = BASE_DIR / "exports"

SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
SCRYFALL_BULK = CACHE_DIR / "scryfall_bulk.json"
SCRYFALL_BULK_MAX_AGE_DAYS = 7

DB_PATH = BASE_DIR / "mtg_collection.db"

# Neo4j AuraDB
NEO4J_URI = "neo4j+s://bfa0085d.databases.neo4j.io"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = ""  # fill in before connecting
