# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`mtg-db` is a hybrid SQLite + Neo4j database backing Claude's MTG collection management — reducing token usage and enabling relationship-based queries across cards, decks, and combos.

**Full schema spec**: `MTG_Database_Schema_Spec.md` in the Claude project files. Read it before doing any schema work.

## Architecture

| Layer | Tech | Purpose |
|-------|------|---------|
| 1 | SQLite (`mtg_collection.db`) | Inventory — physical card instances, quantities, condition, purchase data |
| 2 | Neo4j AuraDB (cloud, free tier) | Graph — Card, Deck, Combo nodes and their relationships |

## Current Database State (as of 2026-05-31)

**SQLite**
- 3,257 collection rows across 79 sets (340 foils)
- Source: ManaBox export (`MTG_5.csv`)

**Neo4j AuraDB** (`neo4j+s://bfa0085d.databases.neo4j.io`, db: `bfa0085d`)
- 36,999 Card nodes — every Scryfall oracle card, with `scryfall_id`, `oracle_id`, `mechanics[]`, `broad_role`, `sub_roles[]`
- 19 Deck nodes — all public Archidekt decks for user MillerTime88
- 47,476 Combo nodes — Commander Spellbook combos involving deck cards
- 2,084 `IN_DECK` relationships
- 172,635 `PART_OF_COMBO` relationships

## CLI Usage

All commands via `uv run python main.py`:

```sh
uv run python main.py verify                        # test Neo4j + SQLite connections
uv run python main.py stats                         # full DB state summary
uv run python main.py sync                          # full pipeline (all steps)
uv run python main.py sync --csv MTG_5.csv          # full pipeline + CSV ingest
uv run python main.py sync --exports-only           # use exports/ instead of Archidekt API
uv run python main.py sync-collection MTG_5.csv     # ManaBox CSV -> SQLite only
uv run python main.py sync-cards                    # Scryfall -> Neo4j card nodes only
uv run python main.py sync-decks                    # Archidekt -> Neo4j only
uv run python main.py sync-combos                   # Spellbook -> Neo4j only
uv run python main.py tag-mechanics                 # oracle text -> Card.mechanics[]
uv run python main.py classify-roles                # -> Card.broad_role + sub_roles[]
```

All commands support `-v` for debug logging.

## Module Map

| File | Responsibility |
|------|---------------|
| `config.py` | All credentials and path constants — **gitignored, never committed** |
| `scryfall.py` | Bulk download + card lookup; `normalize_name()` handles Scryfall's U+A789 modifier colon |
| `manabox.py` | CSV ingest (all 16 ManaBox columns); upserts to `collection` table |
| `sqlite_ops.py` | `init_db()`, `get_connection()`, `upsert_collection_row()`, `write_sync_log()` |
| `neo4j_ops.py` | All Neo4j operations — connection, constraints, batch MERGE for Card/Deck/Combo nodes and relationships |
| `archidekt.py` | Archidekt API auth (JWT Bearer), deck fetch, `sync_all_decks()`; `exports/` fallback |
| `spellbook.py` | Commander Spellbook bulk download (resume-capable, 429-backoff), filter to deck cards, sync combos |
| `mechanics.py` | 40+ regex patterns against oracle text → `Card.mechanics[]`; `run_mechanic_tagging()` |
| `roles.py` | Priority rule engine → `Card.broad_role` + `Card.sub_roles[]`; `MANUAL_OVERRIDES` dict for edge cases |
| `main.py` | `argparse` CLI; `sync` orchestrates all steps in order |

## Environment

- OS: Windows 11, no WSL2
- Shell: Git Bash preferred over PowerShell
- Package manager: **uv** — always use instead of `pip`/`python`
- Runtime: Python 3.14 (pinned in `.python-version`)
- Neo4j: AuraDB cloud only — no local install
- Archidekt user: `MillerTime88`

## Key Design Decisions

**ID strategy**: Card nodes use `scryfall_id` (Scryfall printing ID from oracle_cards bulk) as primary key and `oracle_id` (Scryfall stable oracle ID) as a secondary indexed property. Spellbook combo matching uses `oracle_id`; Archidekt and ManaBox card lookup resolves by name via `card_lookup`.

**Scryfall name normalization**: `scryfall.normalize_name()` maps U+A789 (modifier letter colon, used in Scryfall names like Ratonhnhaké:ton) to U+003A (ASCII colon, used by ManaBox). Apply to any card name before lookup.

**Combo scope**: All Spellbook combos where ≥1 card appears in any of the 19 decklists. 89,904 total combos downloaded and cached; 47,476 relevant.

**Role taxonomy**: `broad_role` is a single string (primary function); `sub_roles` is an additive list. Cards in ≥20 combos get `broad_role=combo_piece` automatically. `MANUAL_OVERRIDES` in `roles.py` seeds known misclassifications (Thassa's Oracle, sacrifice altars, Rhystic Study, etc.).

**Archidekt auth**: JWT Bearer via `/api/rest-auth/login/`. Deck list extracted from login response payload — no separate list API call needed. Private decks (flag in payload) are skipped; API returns 404 for them.

**Spellbook rate limiting**: API allows ~90 req/burst then 429s. Downloader sleeps 2s/page, retries with exponential backoff (60s → 120s → 240s...), and checkpoints to `.partial.json` every 20 pages so interrupted downloads resume cleanly.

## Caches (gitignored)

| File | Contents | Max age |
|------|----------|---------|
| `cache/scryfall_bulk.json` | All Scryfall oracle cards (~250 MB) | 7 days |
| `cache/spellbook_combos.json` | All Commander Spellbook variants (~89k combos) | 7 days |

## Working with This User

- IT Data/Platform/Integration Engineer — Mac at work, Windows at home (bash/zsh fluent)
- Skip beginner explanations; be precise about commands; flag tradeoffs
- Offer a handoff summary when context gets heavy
- One-deck-per-conversation is the standing MTG workflow; this build project is the exception
