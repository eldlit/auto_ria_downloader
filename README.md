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
   - Add `--clear-cache` to wipe the configured cache directory before scraping when you want to start fresh.

The initial implementation only wires up configuration and CLI plumbing so that we can plug in Playwright-driven scraping logic step by step.

## Windows Setup

1. Install the latest Python 3.x (64-bit) from [python.org](https://www.python.org/downloads/windows/) and enable the “Add python.exe to PATH” checkbox during setup.
2. Open **PowerShell** and allow script execution for the current session (required for activating virtual environments): `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.
3. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
4. Upgrade pip and install dependencies (editable install keeps local changes live):
   ```powershell
   python -m pip install --upgrade pip
   pip install -e ".[dev]"
   ```
5. Install the Playwright browser binaries (once per machine):
   ```powershell
   playwright install
   ```
6. Populate `input.txt` and `config.json`, then run the scraper:
   ```powershell
   python -m autoria_parser --config config.json --input input.txt --clear-cache
   ```
   Omit `--clear-cache` if you want to reuse the existing cache directory between runs.

## Configuration Notes

- `proxy.enabled`, `proxy.rotation`, and `proxy.list` control how many Playwright browser instances start (each proxy gets its own isolated browser profile). Leaving proxies disabled falls back to a single direct browser.
- `playwright.headless` toggles headless vs headed mode (`true` by default). Set it to `false` in `config.json` if you want to observe the browser UI while debugging.
