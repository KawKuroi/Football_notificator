# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python script ([football_API.py](football_API.py)) that sends per-subscriber email notifications for Colombian *Primera A* football matches happening "today." Diseñado como **one-shot job**: arranca, corre el pipeline (~3s en condiciones normales) y sale. La calendarización es externa al contenedor (Cloud Scheduler, cron del host, etc.).

**Target de despliegue:** Google Cloud Run Job + Cloud Scheduler. Cloud Scheduler hace POST al endpoint del Job una vez al día (7am Bogotá), Cloud Run levanta el contenedor, corre `football_API.py`, factura los segundos de CPU usados, y descarga la instancia. Sin idle, sin `time.sleep` interno — la optimización clave es que el pipeline termine rápido.

No build step. Runtime deps en [requirements.txt](requirements.txt) (`requests`, `python-dotenv`); test deps en [requirements-dev.txt](requirements-dev.txt) (añade `pytest`).

## Running

### Local

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in GMAIL_USER / GMAIL_PASSWORD
python football_API.py
```

`load_dotenv()` runs at import ([football_API.py:14](football_API.py#L14)). Real env vars take precedence over `.env`. The script **fails fast** with a non-zero exit if `GMAIL_USER` or `GMAIL_PASSWORD` is missing — there are no baked-in fallbacks anymore.

### Docker (one-shot)

```bash
docker build -t football-api .
docker run --rm --env-file .env football-api
```

El `Dockerfile` es single-stage sobre `python:3.12-slim`. Hardcodea `TZ=America/Bogota` y `PYTHONUNBUFFERED=1` (para que Cloud Run capture stdout en vivo). El `CMD` es `python football_API.py`: arranca, corre, sale. `.env` está en `.dockerignore`, así que los secretos solo entran vía `--env-file` o `-e`.

**Causas comunes de "no funciona en Docker":**

1. Falta `--env-file .env`: el script aborta con `❌ Faltan GMAIL_USER y/o GMAIL_PASSWORD` y exit-code distinto de cero (Cloud Run lo marca como fallo y reintenta).
2. Imagen vieja: cambios al código requieren `docker build` antes del próximo `docker run`. El `.py` se copia en build-time.

**Zona horaria:** `TZ=America/Bogota` en el `Dockerfile` hace que `datetime.now()` devuelva hora Bogotá dentro del contenedor, alineando `HOY` con la conversión UTC→Bogotá que `obtener_partidos()` aplica a los kickoffs. Override de `TZ` rompe la alineación.

### Cloud Run Job + Cloud Scheduler

Modelo de deploy objetivo. Cloud Scheduler dispara via HTTP el Job a las 7am Bogotá; Cloud Run levanta el contenedor, corre el pipeline, factura por segundo, descarga. **No hay scheduler interno en el código** — meterlo desperdicia recursos (Cloud Run Jobs cap a 1h y se factura cada segundo de CPU).

Optimizaciones que importan en este modelo:
- `enriquecer_con_estadio()` paraleliza las peticiones de detalle con `ThreadPoolExecutor` (8 workers max). Para una jornada típica de 5 partidos, baja la latencia de ~7s a ~2s — son segundos de Cloud Run que se cobran.
- Evitar imports/efectos colaterales pesados al cargar el módulo (cold start). Hoy `load_dotenv()` es lo único que corre en import; mantenerlo así.
- Mantener la imagen pequeña (`python:3.12-slim`, ~150MB). No cambiar a alpine sin probar (algunos wheels rompen con musl).

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                              # toda la suite
python -m pytest tests/test_football_API.py::TestNormalizar  # una clase
python -m pytest -k parsear_fecha             # por nombre
```

Tests viven en [tests/test_football_API.py](tests/test_football_API.py), agrupados por función. Toda la I/O está mockeada vía `monkeypatch` (no se hacen llamadas reales a onefootball.com, Google Sheets ni a Gmail). El env-var check vive en `main()` precisamente para que importar el módulo en tests no falle si `.env` no está cargado — no muevas esa validación al nivel del módulo.

### Secrets

`GMAIL_USER` / `GMAIL_PASSWORD` are the only secrets. Gmail requires an **app password** (not the account password) and 2FA must be enabled on the account to generate one. `.env` is gitignored; commit changes to `.env.example` instead.

## Architecture (the parts that matter)

The script is one linear pipeline in `main()`. Three things are non-obvious and span multiple functions:

### 1. One Google Sheet + scraping de onefootball.com

**Partidos:** `obtener_partidos()` raspa `https://onefootball.com/es/competicion/primera-a-109/partidos` extrayendo el bloque `<script id="__NEXT_DATA__">` que Next.js inyecta en el HTML. El helper `_buscar_lista_partidos()` navega el JSON recursivamente buscando la primera lista de dicts que tenga claves `homeTeam`/`kickoff`, para ser tolerante a cambios de estructura. Los kickoffs vienen en UTC y se convierten a `America/Bogota` antes de formatear la fecha y hora. El campo `Estadio` se rellena en un segundo paso por `enriquecer_con_estadio()`, que solo hace peticiones a páginas de detalle para los partidos de hoy (0–5 peticiones típicamente).

**Estructura real observada (mayo 2026, sujeta a cambio):**

- En la lista la URL del detalle viene en la clave `link` (no `url` ni `detailUrl`). `obtener_partidos()` también acepta los otros nombres como fallback.
- En el detalle, el estadio **no** está como `venue`/`stadium`. Vive dentro de `props.pageProps.containers[*].…matchInfo.entries`, como un objeto `{title: "Estadio", subtitle: "<nombre del estadio>"}`. `_buscar_venue()` reconoce ambos formatos (claves directas + patrón title/subtitle).
- **No hay número de jornada** ni en la lista ni en el detalle hoy en día. El campo `Jornada` queda en `"N/D"` en el dict del partido pero **no se renderiza en el HTML** (el rediseño minimalista lo eliminó). Si onefootball lo expone más adelante, ajustar los fallbacks `matchday`/`round` en `obtener_partidos()` y volver a meter el campo en `generar_html()`.

Si onefootball cambia su JSON, el punto de parcheo es `_buscar_lista_partidos()`, `_buscar_venue()` y los nombres de campo en `obtener_partidos()`.

**Suscriptores:** la hoja del Form sigue leyéndose vía `leer_sheet()` con el endpoint `gviz/tq?tqx=out:csv` — sin cliente de Google API, sin auth. Debe estar compartida como "cualquiera con el enlace." ID y tab hardcodeados en `SHEET_FORM_ID` / `SHEET_FORM_NAME`. Las dos columnas que lee el script están en `COL_CORREO` y `COL_EQUIPOS` y **deben coincidir exactamente con el texto de la pregunta del Form**, incluyendo tildes y el "¿…?". Renombrar una pregunta del Form rompe silenciosamente la ingesta de suscriptores.

### 2. Team-name normalization is the join key

El Form deja que los usuarios escriban nombres libremente mientras onefootball usa nombres canónicos. El dict `ALIAS` + `normalizar()` es el puente: todo nombre de equipo de cualquier fuente pasa por `normalizar()` (lowercase + strip + alias lookup) antes de usarse como clave.

`construir_indice()` construye un dict clave = nombre normalizado → fila del partido. Cada partido se inserta **dos veces** (local y visitante) para que el loop de suscriptores haga lookups `O(1)` via `indice_equipos.get(equipo)`.

**Cuando un suscriptor no recibe notificación, la causa casi siempre es una entrada faltante en `ALIAS`** — el Form envió un nombre que no coincide (ni aliasa a) lo que muestra onefootball.

### 3. "Today" is captured once at import, with timezone conversion

`HOY = datetime.now().date()` se evalúa al cargar el módulo. En el modelo one-shot (Cloud Run Job, `docker run`, `python football_API.py` local) el script importa, corre, y termina, así que `HOY` siempre refleja el momento del disparo. No mover esta línea a un nivel más profundo: queda en module-scope a propósito porque importar el módulo en tests no debe tardar.

Los kickoffs de onefootball llegan en UTC. `obtener_partidos()` los convierte a `America/Bogota` (UTC−5) antes de formatear. Esto importa en partidos nocturnos: un partido a las `01:00 UTC` del día siguiente es `20:00` del día anterior en Bogotá, y debe quedar con la `Fecha` correcta para que `partidos_de_hoy()` lo incluya.

`HOY` lee la hora del SO. El `Dockerfile` ya fija `TZ=America/Bogota` para que `HOY` y los kickoffs estén en la misma zona. Override de `TZ` a otra cosa rompe esta alineación.

`parsear_fecha()` tolera varios formatos (`d/m/Y`, `d-m-Y`, `Y-m-d`, `d/m/y`) más un fallback sin ceros a la izquierda.

## Email path

`enviar_correo()` uses `smtplib.SMTP_SSL` against `smtp.gmail.com:465` with the configured user/password. Gmail requires an **app password** (not the account password), and the account must have 2FA enabled to generate one. `SMTPAuthenticationError` is caught and logged but does not abort the run — other recipients will still be attempted.

The HTML body is built inline as an f-string en `generar_html()`. Diseño "tabla compacta moderna": paleta blanco/negro/gris (`#ffffff`/`#0f172a`/`#64748b`), tipografía system-ui (`-apple-system`, `BlinkMacSystemFont`, `'Segoe UI'`, etc., con fallback a `Roboto`/`sans-serif`), un bloque por partido con la línea `Local vs Visitante` en peso 600 y `HH:MM · Estadio` en gris debajo. Sin emojis, sin colores de marca, sin footer largo. La fecha del header se humaniza vía `MESES_ES` (dict literal en español, locale-independent). Todo el CSS es inline porque Gmail strips `<style>` blocks; el layout usa `<table role="presentation">` con `cellpadding`/`cellspacing`/`border` como atributos HTML para que Outlook desktop no rompa el render.

Si se quiere volver a agregar campos al correo (p.ej. cuando onefootball exponga `Jornada`), el cambio vive en el bloque `bloques += f"""..."""` dentro del loop de `generar_html()` — el dict de `partidos_usuario` ya trae todos los campos disponibles, simplemente no todos se renderizan.

## Language note

User-facing strings, log output, column names, and most identifiers are in **Spanish** (`partidos`, `suscriptores`, `leer_sheet`, `HOY`, etc.). Keep new code consistent with that convention rather than mixing English.
