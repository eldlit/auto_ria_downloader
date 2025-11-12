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

## Как работает приложение

1. При старте CLI читает `config.json` и список URL из `input.txt`. Конфиг описывает селекторы, таймауты, кэш, прокси и место сохранения CSV.
2. Если передан флаг `--dry-run`, приложение просто валидирует файлы. В обычном режиме запускается Playwright с количеством браузеров из `playwright.maxBrowsers` (каждый может использовать свой прокси).
3. Каталожный модуль проходит пагинацию, собирает ссылки на объявления и выдерживает паузы между запросами (`parsing.delayBetweenRequests`). При ошибках навигации используется `errorRetryTimes`.
4. Модуль деталей открывает каждое объявление, нажимает кнопку телефона (XPath берутся из `phoneButtonXpaths`), вытягивает поля из `dataFields`, при необходимости сохраняет/читает кэш из `cache.directory` и фильтрует дубликаты по телефону.
5. Итоговые данные уходят в CSV согласно `output.file`, `output.delimiter` и `output.encoding`. Если задан файл с расширением `.csv`, к имени добавляется метка времени; если указан каталог, файл создаётся внутри него.

### Настройка конфигурации

- **Каталог и пагинация:** заполните `catalogXpaths` и `paginationXpaths`, чтобы парсер находил карточки и кнопки перехода. Если оставить пустыми, используются дефолтные XPath.
- **Таймауты и параллелизм:** `parsing.pageLoadTimeout`, `parsing.waitForPaginationTimeout`, `playwright.detailConcurrency`, `playwright.maxBrowsers` позволяют адаптировать нагрузку под железо.
- **Кэш:** `cache.enabled`, `cache.cacheListings`, `cache.directory` управляют включением/пути к кэшу. Флаг CLI `--clear-cache` очищает каталог перед запуском.
- **Прокси:** при `proxy.enabled=true` каждый браузер стартует с соответствующим прокси из `proxy.list`. Включите `proxy.rotation`, чтобы при ошибке браузер перезапускался со следующими данными.
- **Выходные данные:** добавляйте нужные поля в `dataFields` (имя + список XPath). Их порядок определяет порядок колонок в CSV.

## Инструкция для пользователя (RU)

1. **Подготовка входных данных**
   - Откройте `input.txt` и впишите ссылки на поисковые страницы auto.ria.com (по одной на строку). Пустые строки и строки c `#` игнорируются.
   - В `config.json` проверьте блок `dataFields`: здесь перечислены поля, которые появятся в итоговом CSV.

2. **Запуск**
   - В активированной виртуальной среде выполните:  
     ```bash
     python -m autoria_parser --config config.json --input input.txt --clear-cache
     ```
     Флаг `--clear-cache` опционален; уберите его, если хотите использовать ранее сохранённый кэш.

3. **Где искать результаты**
   - Путь к CSV задаётся полем `output.file` в `config.json`.  
     *Если указано имя с `.csv`, например `reports/autoria.csv`, программа создаст файл вида `reports/autoria_20251113-14.csv`.*  
     *Если указана папка (`"output": {"file": "reports"}`), файл появится внутри неё как `output_ГГГГММДД-ЧЧ.csv`.*
   - Логи каждого запуска сохраняются в папке `logs/` (файл `run-YYYYmmdd-HHMMSS.log`). Одновременно те же сообщения выводятся в консоль.

4. **Параллельность и нагрузка**
   - `playwright.maxBrowsers` — число одновременно запущенных браузеров Chromium. Чем выше значение, тем больше нагрузка на CPU/RAM, но тем быстрее обработка.
   - `playwright.detailConcurrency` — количество страниц объявлений, открывающихся параллельно **внутри одного браузера**. Общая параллельность = `maxBrowsers × detailConcurrency`.
   - На слабых ПК ставьте оба параметра в `1`, на более мощных можно увеличить до 2–3.

5. **Прокси**
   - Чтобы отключить прокси, установите `proxy.enabled: false`. Тогда будет один прямой браузер без ротации.
   - Чтобы включить прокси, поставьте `proxy.enabled: true` и перечислите строки формата `http://user:pass@host:port` в `proxy.list`.
   - Опция `proxy.rotation: true` заставляет приложение перезапускать браузер со следующим прокси из списка, если текущий вернул ошибку/бан. Значение `false` оставляет текущий прокси даже при ошибках.
