# Autoria Parser

Python-based Playwright scraper for [auto.ria.com](https://auto.ria.com) catalogue listings. The project follows the technical brief in `ТЗ_ Парсер Autoria.pdf` and is designed to be extended iteratively with the selectors and behavioural tweaks you provide.

## Project Layout

```
├── config.json          # Runtime configuration (selectors, timeouts, cache/proxy flags)
├── input.txt            # Entry URLs (one per line)
├── src/
│   └── autoria_parser/
│       ├── __init__.py
│       ├── __main__.py  # CLI entry point
│       ├── app.py       # High-level orchestration logic
│       ├── cli.py       # Argument parsing helpers
│       ├── config.py    # Config schemas and loaders
│       └── logging.py   # Shared logging setup
└── tests/
    ├── integration/
    └── unit/
```

## Quick Start

1. Create a virtual environment (e.g. `python3 -m venv .venv && source .venv/bin/activate`).
2. Upgrade pip inside your venv (required for editable installs with `pyproject.toml`): `python -m pip install --upgrade pip`.
3. Install dependencies: `pip install -e '.[dev]'`.
4. Put one or more search-result URLs into `input.txt` (one per line).
5. Update `config.json` with the selectors, proxy list, caching preferences, and timing knobs you want to use.
6. Run the CLI: `python -m autoria_parser --config config.json --input input.txt`.

The initial implementation only wires up configuration and CLI plumbing so that we can plug in Playwright-driven scraping logic step by step.

## Configuration Notes

- `proxy.enabled`, `proxy.rotation`, and `proxy.list` control how many Playwright browser instances start (each proxy gets its own isolated browser profile). Leaving proxies disabled falls back to a single direct browser.
- `playwright.headless` toggles headless vs headed mode (`true` by default). Set it to `false` in `config.json` if you want to observe the browser UI while debugging.
