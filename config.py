from pathlib import Path

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
EXPORTS_DIR = BASE_DIR / "exports"

SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
SCRYFALL_BULK = CACHE_DIR / "scryfall_bulk.json"
SCRYFALL_BULK_MAX_AGE_DAYS = 7

# Neo4j AuraDB — populate after provisioning
NEO4J_URI = ""
NEO4J_USER = ""
NEO4J_PASSWORD = ""
