# Football-API · Notificaciones Primera A Colombia

Script en Python que envía un correo diario a cada suscriptor con los partidos del día de los equipos que sigue en la Primera A colombiana.

## Cómo funciona

1. Lee la **agenda de partidos** desde un Google Sheet público.
2. Lee los **suscriptores** desde un Sheet poblado por un Google Form (correo + equipos seguidos).
3. Filtra los partidos cuya fecha es hoy.
4. Para cada suscriptor, busca coincidencias entre sus equipos y los partidos del día.
5. Envía un correo HTML por SMTP de Gmail solo a quienes tienen al menos un partido hoy.

Los nombres de equipos se normalizan vía un diccionario `ALIAS` para tolerar variaciones de escritura del Form.

## Requisitos

- Python 3.12+
- Cuenta Gmail con 2FA y una [contraseña de aplicación](https://myaccount.google.com/apppasswords)
- Los dos Google Sheets compartidos como *"cualquiera con el enlace"*

## Configuración

```bash
cp .env.example .env
# editar .env con GMAIL_USER y GMAIL_PASSWORD
```

## Ejecución

**Local:**

```bash
pip install -r requirements.txt
python football_API.py
```

**Docker:**

```bash
docker build -t football-api .
docker run --rm --env-file .env football-api
```

El script es de una sola pasada: corre, envía los correos del día y termina. Pensado para ejecutarse una vez al día (cron, Cloud Run Job, etc.).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Toda la I/O (HTTP a Google Sheets, SMTP a Gmail) está mockeada — los tests no hacen llamadas reales.

## Estructura

```
.
├── football_API.py        # script único, todo el pipeline está aquí
├── tests/
│   └── test_football_API.py
├── requirements.txt        # deps de runtime
├── requirements-dev.txt    # + pytest
├── Dockerfile
├── .env.example            # plantilla de variables (copiar a .env)
└── CLAUDE.md               # notas de arquitectura para asistentes
```
