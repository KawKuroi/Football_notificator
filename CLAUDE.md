# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python script ([football_API.py](football_API.py)) that sends per-subscriber email notifications for Colombian *Primera A* football matches happening "today." Designed to run once per day (e.g. as a Cloud Run job or a cron'd container).

No build step. Runtime deps in [requirements.txt](requirements.txt) (`requests`, `python-dotenv`); test deps in [requirements-dev.txt](requirements-dev.txt) (adds `pytest`).

## Running

### Local

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in GMAIL_USER / GMAIL_PASSWORD
python football_API.py
```

`load_dotenv()` runs at import ([football_API.py:14](football_API.py#L14)). Real env vars take precedence over `.env`. The script **fails fast** with a non-zero exit if `GMAIL_USER` or `GMAIL_PASSWORD` is missing — there are no baked-in fallbacks anymore.

### Docker

```bash
docker build -t football-api .
docker run --rm --env-file .env football-api
```

The `Dockerfile` is a single-stage `python:3.12-slim` image; `CMD` runs the script and exits (one-shot job, not a server). `.env` is excluded by `.dockerignore` so secrets never enter the image — they must be passed at `docker run` time via `--env-file` or `-e`.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                              # toda la suite
python -m pytest tests/test_football_API.py::TestNormalizar  # una clase
python -m pytest -k parsear_fecha             # por nombre
```

Tests viven en [tests/test_football_API.py](tests/test_football_API.py), agrupados por función. Toda la I/O está mockeada vía `monkeypatch` (no se hacen llamadas reales a Google Sheets ni a Gmail). El env-var check vive en `main()` precisamente para que importar el módulo en tests no falle si `.env` no está cargado — no muevas esa validación al nivel del módulo.

### Secrets

`GMAIL_USER` / `GMAIL_PASSWORD` are the only secrets. Gmail requires an **app password** (not the account password) and 2FA must be enabled on the account to generate one. `.env` is gitignored; commit changes to `.env.example` instead.

## Architecture (the parts that matter)

The script is one linear pipeline in `main()` ([football_API.py:251](football_API.py#L251)). Three things are non-obvious and span multiple functions:

### 1. Two Google Sheets, read as public CSV

Both sheets are pulled via the `gviz/tq?tqx=out:csv` endpoint by `leer_sheet()` ([football_API.py:60](football_API.py#L60)) — no Google API client, no auth. They must be shared as "anyone with the link." IDs and tab names are hardcoded ([football_API.py:17-22](football_API.py#L17-L22)):

- **`SHEET_PARTIDOS_ID` / `Hoja1`** — match schedule. Expected columns: `Fecha`, `Equipo Local`, `Equipo Visitante`, `Hora`, `Estadio`, `Jornada`.
- **`SHEET_FORM_ID` / `Form_Responses`** — Google Form responses. The two columns the script reads are stored in `COL_CORREO` and `COL_EQUIPOS` constants and **must match the Form question text exactly**, including accents and the "¿…?" wrapping. Renaming a Form question silently breaks subscriber ingestion.

### 2. Team-name normalization is the join key

The Form lets users type team names freely while the match sheet uses canonical names. The `ALIAS` dict + `normalizar()` ([football_API.py:34-54](football_API.py#L34-L54)) is the bridge: every team name from either source goes through `normalizar()` (lowercase + strip + alias lookup) before it's used as a lookup key.

`construir_indice()` ([football_API.py:118](football_API.py#L118)) builds a dict keyed by normalized team name → match row. Each match is inserted **twice** (once under the local team, once under the visitor) so the per-subscriber loop in `main()` can do `O(1)` lookups via `indice_equipos.get(equipo)`.

**When fixing "user X didn't get a notification" bugs, the cause is almost always a missing `ALIAS` entry** — the Form gave a spelling that doesn't match (or alias to) what's in the match sheet.

### 3. "Today" is captured once at import

`HOY = datetime.now().date()` is evaluated at module load and used everywhere downstream. This is fine for the one-shot Cloud Run / Docker invocation model but will go stale in any long-lived process — don't import this module from a daemon and expect "today" to roll over.

Date parsing in `parsear_fecha()` ([football_API.py:87](football_API.py#L87)) tolerates several formats (`d/m/Y`, `d-m-Y`, `Y-m-d`, `d/m/y`) plus a no-leading-zero fallback because Google Sheets renders dates inconsistently.

## Email path

`enviar_correo()` uses `smtplib.SMTP_SSL` against `smtp.gmail.com:465` with the configured user/password. Gmail requires an **app password** (not the account password), and the account must have 2FA enabled to generate one. `SMTPAuthenticationError` is caught and logged but does not abort the run — other recipients will still be attempted.

The HTML body is built inline as an f-string in `generar_html()` ([football_API.py:155](football_API.py#L155)). All styling is inline because Gmail strips `<style>` blocks.

## Language note

User-facing strings, log output, column names, and most identifiers are in **Spanish** (`partidos`, `suscriptores`, `leer_sheet`, `HOY`, etc.). Keep new code consistent with that convention rather than mixing English.
