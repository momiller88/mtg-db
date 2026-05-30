# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`mtg-db` builds a hybrid SQLite + Neo4j database to back Claude's MTG collection management — reducing token usage and enabling relationship-based queries across cards, decks, and combos.

**Full schema spec**: `MTG_Database_Schema_Spec.md` in the Claude project files. Read it before doing any schema work.

## Architecture

| Layer | Tech | Purpose |
|-------|------|---------|
| 1 | SQLite (`mtg_collection.db`) | Inventory — cards, printings, collection quantities |
| 2 | Neo4j AuraDB (cloud, free tier) | Relationships — decks, combos, roles |

- Role taxonomy: granular (broad roles + sub-roles)
- Combo scope: all combos involving any card across all decklists (current and future)
- Deck sync: Archidekt API primary, manual export fallback (`exports/` dir)

## Environment

- OS: Windows 11, no WSL2
- Shell: **Git Bash** (preferred over PowerShell for all commands)
- Package manager: **uv** — always use instead of `pip`/`python`
- Neo4j: AuraDB cloud only — no local Neo4j install
- Credentials/connection strings: `config.py`

## Package Manager

```sh
uv run main.py          # run the app
uv add <package>        # add a dependency
uv sync                 # install from uv.lock
uv python list          # confirm available Python versions
```

Runtime: Python 3.14 (pinned in `.python-version`).

## Repository Structure

```
mtg_db/
  config.py         # credentials and constants
  scryfall.py       # Scryfall bulk download + card lookup
  manabox.py        # ManaBox CSV collection ingest
  archidekt.py      # Archidekt API sync + manual export fallback
  spellbook.py      # Commander Spellbook combo import
  sqlite_ops.py     # SQLite read/write
  neo4j_ops.py      # Neo4j AuraDB read/write
  roles.py          # role rule engine (broad → sub-roles, MANUAL_OVERRIDES)
  mechanics.py      # oracle text pattern matching
  main.py           # CLI entry point + full sync orchestration
  exports/          # manual Archidekt exports dropped here
  cache/            # API response cache
  mtg_collection.db # output — upload to Claude project when ready
```

## Dependencies

External (in `pyproject.toml`): `neo4j`, `requests`

Stdlib only (no additional installs): `sqlite3`, `csv`, `json`, `re`, `os`, `datetime`, `argparse`, `pathlib`, `logging`

## Build Plan

| Week | Days | Focus |
|------|------|-------|
| 1 | 1–3 | Foundation: AuraDB provisioning, SQLite schema, `scryfall.py`, `manabox.py`, `sqlite_ops.py`, `neo4j_ops.py` |
| 2 | 4–6 | Sync pipeline: `archidekt.py` (API + fallback), `spellbook.py` |
| 3 | 7–9 | Intelligence: `mechanics.py`, `roles.py` (broad then sub-roles) |
| 4 | 10–12 | Integration: `main.py` CLI, cross-reference rebuild, query validation, upload db to Claude project |

## Working with This User

- IT Data/Platform/Integration Engineer — Mac at work, Windows at home (bash/zsh fluent)
- Skip beginner explanations; be precise about commands; flag tradeoffs rather than hiding them
- This build project may span multiple sessions — offer a handoff summary when context gets heavy
- One-deck-per-conversation is the standing MTG workflow; this project is the exception
