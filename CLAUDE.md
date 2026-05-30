# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`mtg-db` is a Magic: The Gathering database application backed by Neo4j (graph database). It uses `requests` to fetch card data and `neo4j` to store and query it.

## Package Manager

This project uses **uv**. Always use `uv` instead of `pip` or `python` directly.

```sh
uv run main.py          # run the app
uv add <package>        # add a dependency
uv sync                 # install dependencies from uv.lock
```

## Runtime

Python 3.14 (pinned in `.python-version`).

## Dependencies

- `neo4j` — graph database driver for storing card/deck data
- `requests` — HTTP client for fetching card data (likely from Scryfall or similar MTG APIs)
