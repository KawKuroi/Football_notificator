import requests
import csv
import io
import json
import re
import smtplib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ─────────────────────────────────────────────
# CONFIGURACIÓN — variables de entorno (.env en local, --env-file/-e en Docker)
# ─────────────────────────────────────────────
load_dotenv()

GMAIL_USER     = os.environ.get("GMAIL_USER")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")

# Fuente de partidos (scraping directo de onefootball.com)
URL_ONEFOOTBALL = "https://onefootball.com/es/competicion/primera-a-109/partidos"
ZONA_HORARIA    = "America/Bogota"
USER_AGENT      = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Sheet de respuestas del Form (público)
SHEET_FORM_ID   = "18N-jBqoiurxsd66mVli83SiTWUY58IgYh8Ov70ff8l0"
SHEET_FORM_NAME = "Form_Responses"

# Columnas del Form (exactamente como aparecen en el Sheet)
COL_CORREO  = "Correo a enviar notificaciones"
COL_EQUIPOS = "¿Sobre que equipos quieres recibir notificaciones?"

HOY = datetime.now().date()

# Nombres de meses en español (locale-independent — Cloud Run Linux puede no tener es_CO).
MESES_ES = {
    1: "enero",   2: "febrero", 3: "marzo",      4: "abril",
    5: "mayo",    6: "junio",   7: "julio",      8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

# ─────────────────────────────────────────────
# NORMALIZACIÓN DE NOMBRES DE EQUIPOS
# Mapea variaciones del Form → nombre exacto en onefootball
# ─────────────────────────────────────────────
ALIAS = {
    "llaneros":                  "llaneros fc",
    "santa fe":                  "independiente santa fe",
    "jaguares de córdoba":       "jaguares fc",
    "jaguares de cordoba":       "jaguares fc",
    "millonarios":               "millonarios",
    "boyacá chicó":              "boyacá chicó fc",
    "boyaca chico":              "boyacá chicó fc",
    "boyacá chico":              "boyacá chicó fc",
    "atletico bucaramanga":      "atlético bucaramanga",
    "atletico nacional":         "atlético nacional",
    "america de cali":           "américa de cali",
    "aguilas doradas":           "águilas doradas",
    "cucuta deportivo":          "cúcuta deportivo",
    "deportivo cali":            "deportivo cali",
}

def normalizar(nombre: str) -> str:
    """Minúsculas + quita espacios extra. Aplica alias si existe."""
    n = nombre.strip().lower()
    return ALIAS.get(n, n)


# ─────────────────────────────────────────────
# LEER GOOGLE SHEET
# ─────────────────────────────────────────────
def leer_sheet(sheet_id: str, sheet_name: str) -> list:
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={sheet_name}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        codigo = e.response.status_code
        if codigo in (401, 403):
            print(f"❌ Sheet '{sheet_name}' no es público o el ID es incorrecto.")
        else:
            print(f"❌ Error HTTP {codigo} leyendo '{sheet_name}'")
        return []
    except requests.exceptions.RequestException as e:
        print(f"❌ Error de conexión: {e}")
        return []

    contenido = r.content.decode("utf-8")
    reader    = csv.DictReader(io.StringIO(contenido))
    return list(reader)


# ─────────────────────────────────────────────
# SCRAPING DE PARTIDOS — onefootball.com
# ─────────────────────────────────────────────
def _extraer_next_data(html: str) -> dict | None:
    """Extrae el payload JSON embebido en <script id="__NEXT_DATA__">."""
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        print("❌ No se encontró __NEXT_DATA__ en la página de onefootball.")
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"❌ JSON inválido en __NEXT_DATA__: {e}")
        return None


def _buscar_lista_partidos(node):
    """Busca recursivamente la primera lista de dicts que parezcan partidos."""
    if isinstance(node, list):
        if node and isinstance(node[0], dict) and (
            "homeTeam" in node[0] or "kickoff" in node[0] or "scheduledAt" in node[0]
        ):
            return node
        for item in node:
            found = _buscar_lista_partidos(item)
            if found:
                return found
    elif isinstance(node, dict):
        for v in node.values():
            found = _buscar_lista_partidos(v)
            if found:
                return found
    return None


def _buscar_venue(node) -> str | None:
    """Busca recursivamente un nombre de estadio dentro de un payload JSON.

    Soporta dos formatos vistos en onefootball:
    1) Claves directas `venue`/`stadium` (con sub-clave `name` o string).
    2) Pares title/subtitle dentro de `matchInfo.entries`, donde
       `title in {"Estadio", "Stadium"}` y el nombre real está en `subtitle`.
    """
    if isinstance(node, dict):
        for clave in ("venue", "stadium"):
            v = node.get(clave)
            if isinstance(v, dict) and v.get("name"):
                return v["name"].strip()
            if isinstance(v, str) and v.strip():
                return v.strip()
        titulo = node.get("title")
        if isinstance(titulo, str) and titulo.strip().lower() in ("estadio", "stadium"):
            sub = node.get("subtitle")
            if isinstance(sub, str) and sub.strip():
                return sub.strip()
        for valor in node.values():
            r = _buscar_venue(valor)
            if r:
                return r
    elif isinstance(node, list):
        for item in node:
            r = _buscar_venue(item)
            if r:
                return r
    return None


def obtener_partidos() -> list:
    """Scrapea el listado completo de Primera A desde onefootball.com.

    Devuelve list[dict] con las claves canónicas del pipeline:
    Fecha, Hora, Equipo Local, Equipo Visitante, Estadio, Jornada.
    También incluye _url_detalle (consumida y eliminada por enriquecer_con_estadio).
    """
    headers = {
        "User-Agent":      USER_AGENT,
        "Accept-Language": "es-CO,es;q=0.9",
    }
    try:
        r = requests.get(URL_ONEFOOTBALL, headers=headers, timeout=15)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error obteniendo partidos de onefootball: {e}")
        return []

    payload = _extraer_next_data(r.text)
    if payload is None:
        return []

    raw_matches = _buscar_lista_partidos(payload)
    if not raw_matches:
        print("❌ No se encontró la lista de partidos en el JSON de onefootball.")
        return []

    tz = ZoneInfo(ZONA_HORARIA)
    partidos = []
    for m in raw_matches:
        kickoff = m.get("kickoff") or m.get("kickoffTime") or m.get("scheduledAt")
        if not kickoff:
            continue
        try:
            dt_local = datetime.fromisoformat(
                kickoff.replace("Z", "+00:00")
            ).astimezone(tz)
        except (ValueError, AttributeError):
            continue
        partidos.append({
            "Fecha":            dt_local.strftime("%d/%m/%Y"),
            "Hora":             dt_local.strftime("%H:%M"),
            "Equipo Local":     ((m.get("homeTeam") or {}).get("name") or "").strip(),
            "Equipo Visitante": ((m.get("awayTeam") or {}).get("name") or "").strip(),
            "Estadio":          "N/D",
            "Jornada":          str(m.get("matchday") or m.get("round") or "N/D"),
            "_url_detalle":     m.get("url") or m.get("detailUrl") or m.get("link") or "",
        })
    return partidos


def _scrapear_estadio(partido: dict) -> None:
    """Worker: scrapea la página de detalle de un partido y actualiza Estadio in-place.

    Diseñado para correr en paralelo desde un ThreadPoolExecutor.
    Fallos individuales se ignoran (el partido conserva 'N/D').
    """
    url = partido.pop("_url_detalle", "")
    if not url:
        return
    if url.startswith("/"):
        url = "https://onefootball.com" + url
    headers = {
        "User-Agent":      USER_AGENT,
        "Accept-Language": "es-CO,es;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
    except requests.exceptions.RequestException:
        return
    payload = _extraer_next_data(r.text)
    if not payload:
        return
    venue = _buscar_venue(payload)
    if venue:
        partido["Estadio"] = venue


def enriquecer_con_estadio(partidos: list) -> None:
    """Enriquece in-place cada partido con el estadio desde su página de detalle.

    Las peticiones se hacen en paralelo (un thread por partido, hasta 8) porque
    son IO-bound: en Cloud Run Jobs esto baja una latencia de ~7s a ~2s para
    una jornada típica de 5 partidos.
    """
    if not partidos:
        return
    workers = min(8, len(partidos))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_scrapear_estadio, partidos))


# ─────────────────────────────────────────────
# PARSEAR FECHA
# ─────────────────────────────────────────────
def parsear_fecha(fecha_raw: str):
    fecha_raw = fecha_raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(fecha_raw, fmt).date()
        except ValueError:
            continue
    # Fallback: D/M/AAAA sin ceros (Google Sheets)
    try:
        partes = fecha_raw.replace("-", "/").split("/")
        if len(partes) == 3:
            d, m, a = partes
            return date(int(a), int(m), int(d))
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# FILTRAR PARTIDOS DE HOY
# ─────────────────────────────────────────────
def partidos_de_hoy(filas: list) -> list:
    return [
        f for f in filas
        if parsear_fecha(f.get("Fecha", "")) == HOY
    ]


# ─────────────────────────────────────────────
# CONSTRUIR MAPA: equipo normalizado → datos partido
# ─────────────────────────────────────────────
def construir_indice(partidos: list) -> dict:
    """
    Devuelve un dict: nombre_equipo_normalizado → partido completo.
    Cada partido aparece dos veces (local y visitante).
    """
    indice = {}
    for p in partidos:
        local     = normalizar(p.get("Equipo Local",     ""))
        visitante = normalizar(p.get("Equipo Visitante", ""))
        if local:
            indice[local]     = p
        if visitante:
            indice[visitante] = p
    return indice


# ─────────────────────────────────────────────
# LEER SUSCRIPTORES DEL FORM
# ─────────────────────────────────────────────
def leer_suscriptores(filas: list) -> list:
    """
    Retorna lista de dicts: {"correo": str, "equipos": [str normalizado]}
    """
    suscriptores = []
    for fila in filas:
        correo       = fila.get(COL_CORREO,  "").strip()
        equipos_raw  = fila.get(COL_EQUIPOS, "").strip()
        if not correo or not equipos_raw:
            continue
        equipos = [normalizar(e.strip()) for e in equipos_raw.split(",") if e.strip()]
        suscriptores.append({"correo": correo, "equipos": equipos})
    return suscriptores


# ─────────────────────────────────────────────
# GENERAR CUERPO DEL CORREO (HTML)
# ─────────────────────────────────────────────
def generar_html(partidos_usuario: list) -> str:
    fuente   = "-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,Roboto,sans-serif"
    titulo   = f"Primera A · {HOY.day} de {MESES_ES[HOY.month]}"

    bloques = ""
    for p in partidos_usuario:
        local     = p.get("Equipo Local",     "")
        visitante = p.get("Equipo Visitante", "")
        hora      = p.get("Hora",             "")
        estadio   = p.get("Estadio",          "")
        meta      = " · ".join(x for x in (hora, estadio) if x and x != "N/D")
        bloques += f"""
              <tr><td style="padding:20px 0;border-top:1px solid #e5e7eb">
                <div style="font-size:16px;font-weight:600;color:#0f172a;line-height:1.4">
                  {local} <span style="color:#94a3b8;font-weight:400">vs</span> {visitante}
                </div>
                <div style="font-size:13px;color:#64748b;margin-top:4px">{meta}</div>
              </td></tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:{fuente};color:#0f172a">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#ffffff">
    <tr><td align="center" style="padding:48px 24px">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:520px">
        <tr><td style="padding-bottom:32px">
          <div style="font-size:13px;color:#64748b;letter-spacing:.02em">{titulo}</div>
        </td></tr>
        {bloques}
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────
# ENVIAR CORREO
# ─────────────────────────────────────────────
def enviar_correo(destinatario: str, partidos_usuario: list) -> bool:
    hoy_str = HOY.strftime("%d/%m/%Y")
    n       = len(partidos_usuario)
    plural  = "partido" if n == 1 else "partidos"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Primera A · {hoy_str} · {n} {plural} hoy"
    msg["From"]    = f"Primera A Notificaciones <{GMAIL_USER}>"
    msg["To"]      = destinatario

    msg.attach(MIMEText(generar_html(partidos_usuario), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, destinatario, msg.as_string())
        print(f"  [OK] Correo enviado a {destinatario}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  [ERROR] Autenticación Gmail fallida. Verifica GMAIL_USER y GMAIL_PASSWORD.")
        return False
    except Exception as e:
        print(f"  [ERROR] Error enviando a {destinatario}: {e}")
        return False


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    if not GMAIL_USER or not GMAIL_PASSWORD:
        sys.exit("❌ Faltan GMAIL_USER y/o GMAIL_PASSWORD. Define las variables en .env o en el entorno.")

    hoy_str = HOY.strftime("%d/%m/%Y")
    print(f"\n{'═'*52}")
    print(f"  ⚽  NOTIFICACIONES PRIMERA A — {hoy_str}")
    print(f"{'═'*52}\n")

    # 1. Cargar datos
    print("1️⃣  Cargando partidos desde onefootball...")
    filas_partidos = obtener_partidos()
    if not filas_partidos:
        print("   Sin datos de partidos. Abortando.")
        return

    print("2️⃣  Cargando suscriptores...")
    filas_form = leer_sheet(SHEET_FORM_ID, SHEET_FORM_NAME)
    if not filas_form:
        print("   Sin suscriptores. Abortando.")
        return

    # 2. Filtrar partidos de hoy y construir índice
    partidos_hoy = partidos_de_hoy(filas_partidos)
    print(f"   → {len(partidos_hoy)} partido(s) hoy.\n")

    if not partidos_hoy:
        print("ℹ️  No hay partidos hoy. No se envían notificaciones.\n")
        return

    print("   Obteniendo estadios desde páginas de detalle...")
    enriquecer_con_estadio(partidos_hoy)
    indice_equipos = construir_indice(partidos_hoy)

    # 3. Procesar suscriptores
    suscriptores   = leer_suscriptores(filas_form)
    print(f"3️⃣  Procesando {len(suscriptores)} suscriptor(es)...\n")

    enviados = 0
    omitidos = 0
    for sub in suscriptores:
        correo  = sub["correo"]
        equipos = sub["equipos"]

        # Buscar qué equipos del suscriptor juegan hoy
        partidos_match = []
        for equipo in equipos:
            partido = indice_equipos.get(equipo)
            if partido and partido not in partidos_match:
                partidos_match.append(partido)

        if not partidos_match:
            print(f"  ⏭️  {correo} — ningún equipo juega hoy.")
            omitidos += 1
            continue

        print(f"  📧 {correo} — {len(partidos_match)} partido(s) encontrado(s):")
        for p in partidos_match:
            print(f"      {p['Equipo Local']} vs {p['Equipo Visitante']} — {p['Hora']}")

        if enviar_correo(correo, partidos_match):
            enviados += 1

    # 4. Resumen
    print(f"\n{'═'*52}")
    print(f"  Correos enviados : {enviados}")
    print(f"  Sin partidos hoy : {omitidos}")
    print(f"{'═'*52}\n")


if __name__ == "__main__":
    main()