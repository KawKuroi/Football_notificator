import requests
import csv
import io
import smtplib
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date

from dotenv import load_dotenv

# ─────────────────────────────────────────────
# CONFIGURACIÓN — variables de entorno (.env en local, --env-file/-e en Docker)
# ─────────────────────────────────────────────
load_dotenv()

GMAIL_USER     = os.environ.get("GMAIL_USER")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")

# Sheet de partidos (público)
SHEET_PARTIDOS_ID   = "138zg3LcS8wQTpBJd_WhTPLOkGeMwj7aZ37MGJToc0Tw"
SHEET_PARTIDOS_NAME = "Hoja1"

# Sheet de respuestas del Form (público)
SHEET_FORM_ID   = "18N-jBqoiurxsd66mVli83SiTWUY58IgYh8Ov70ff8l0"
SHEET_FORM_NAME = "Form_Responses"

# Columnas del Form (exactamente como aparecen en el Sheet)
COL_CORREO  = "Correo a enviar notificaciones"
COL_EQUIPOS = "¿Sobre que equipos quieres recibir notificaciones?"

HOY = datetime.now().date()

# ─────────────────────────────────────────────
# NORMALIZACIÓN DE NOMBRES DE EQUIPOS
# Mapea variaciones del Form → nombre exacto en el Sheet de partidos
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
    hoy_str = HOY.strftime("%d/%m/%Y")
    filas_html = ""
    for p in partidos_usuario:
        local     = p.get("Equipo Local",     "N/D")
        visitante = p.get("Equipo Visitante", "N/D")
        hora      = p.get("Hora",             "N/D")
        estadio   = p.get("Estadio",          "N/D")
        jornada   = p.get("Jornada",          "N/D")
        filas_html += f"""
        <tr>
          <td style="padding:12px 16px;font-weight:600;color:#1a1a1a">{local}</td>
          <td style="padding:12px 8px;text-align:center;color:#666;font-size:13px">vs</td>
          <td style="padding:12px 16px;font-weight:600;color:#1a1a1a">{visitante}</td>
          <td style="padding:12px 16px;color:#444;font-size:13px">{hora}</td>
          <td style="padding:12px 16px;color:#444;font-size:13px">{estadio}</td>
          <td style="padding:12px 16px;color:#888;font-size:12px">J{jornada}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:24px;margin:0">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <!-- Header -->
    <div style="background:#1B5E20;padding:28px 32px">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700">⚽ Primera A Colombia</h1>
      <p style="color:#A5D6A7;margin:6px 0 0;font-size:14px">Partidos de hoy · {hoy_str}</p>
    </div>
    <!-- Tabla de partidos -->
    <div style="padding:24px 32px">
      <p style="color:#333;font-size:15px;margin:0 0 20px">
        Hoy juegan los equipos que sigues:
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="background:#F1F8E9;border-bottom:2px solid #C8E6C9">
            <th style="padding:10px 16px;text-align:left;color:#2E7D32;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Local</th>
            <th style="padding:10px 8px"></th>
            <th style="padding:10px 16px;text-align:left;color:#2E7D32;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Visitante</th>
            <th style="padding:10px 16px;text-align:left;color:#2E7D32;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Hora</th>
            <th style="padding:10px 16px;text-align:left;color:#2E7D32;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Estadio</th>
            <th style="padding:10px 16px;text-align:left;color:#2E7D32;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Jornada</th>
          </tr>
        </thead>
        <tbody>
          {filas_html}
        </tbody>
      </table>
    </div>
    <!-- Footer -->
    <div style="padding:20px 32px;background:#FAFAFA;border-top:1px solid #eee">
      <p style="color:#999;font-size:12px;margin:0">
        Notificación automática · Primera A Colombia 2026<br>
        Para cancelar tu suscripción, responde este correo con "cancelar".
      </p>
    </div>
  </div>
</body>
</html>"""


# ─────────────────────────────────────────────
# ENVIAR CORREO
# ─────────────────────────────────────────────
def enviar_correo(destinatario: str, partidos_usuario: list) -> bool:
    hoy_str = HOY.strftime("%d/%m/%Y")
    nombres = " y ".join(
        set(p.get("Equipo Local", "") + " / " + p.get("Equipo Visitante", "")
            for p in partidos_usuario)
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚽ Tus equipos juegan hoy {hoy_str} — Primera A"
    msg["From"]    = f"Primera A Notificaciones <{GMAIL_USER}>"
    msg["To"]      = destinatario

    msg.attach(MIMEText(generar_html(partidos_usuario), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, destinatario, msg.as_string())
        print(f"  ✅ Correo enviado a {destinatario}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  ❌ Error de autenticación Gmail. Verifica GMAIL_USER y GMAIL_PASSWORD.")
        return False
    except Exception as e:
        print(f"  ❌ Error enviando a {destinatario}: {e}")
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
    print("1️⃣  Cargando partidos...")
    filas_partidos = leer_sheet(SHEET_PARTIDOS_ID, SHEET_PARTIDOS_NAME)
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