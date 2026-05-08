from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

import football_API as fa


# ─────────────────────────────────────────────
# normalizar
# ─────────────────────────────────────────────
class TestNormalizar:
    def test_alias_simple(self):
        assert fa.normalizar("Llaneros") == "llaneros fc"

    def test_alias_con_espacios(self):
        assert fa.normalizar("  Santa Fe  ") == "independiente santa fe"

    def test_alias_sin_acentos(self):
        assert fa.normalizar("atletico nacional") == "atlético nacional"

    def test_sin_alias_solo_minusculiza(self):
        assert fa.normalizar("Real Madrid") == "real madrid"


# ─────────────────────────────────────────────
# parsear_fecha
# ─────────────────────────────────────────────
class TestParsearFecha:
    @pytest.mark.parametrize(
        "raw,esperado",
        [
            ("15/03/2026", date(2026, 3, 15)),
            ("15-03-2026", date(2026, 3, 15)),
            ("2026-03-15", date(2026, 3, 15)),
            ("15/03/26", date(2026, 3, 15)),
        ],
    )
    def test_formatos_validos(self, raw, esperado):
        assert fa.parsear_fecha(raw) == esperado

    def test_fallback_sin_ceros_a_la_izquierda(self):
        assert fa.parsear_fecha("5/3/2026") == date(2026, 3, 5)

    def test_invalida_devuelve_none(self):
        assert fa.parsear_fecha("not a date") is None

    def test_vacia_devuelve_none(self):
        assert fa.parsear_fecha("") is None


# ─────────────────────────────────────────────
# partidos_de_hoy
# ─────────────────────────────────────────────
class TestPartidosDeHoy:
    def test_filtra_solo_los_de_hoy(self, monkeypatch):
        monkeypatch.setattr(fa, "HOY", date(2026, 5, 8))
        filas = [
            {"Fecha": "08/05/2026", "Equipo Local": "A"},
            {"Fecha": "07/05/2026", "Equipo Local": "B"},
            {"Fecha": "08-05-2026", "Equipo Local": "C"},
            {"Fecha": "basura", "Equipo Local": "D"},
        ]
        res = fa.partidos_de_hoy(filas)
        assert {f["Equipo Local"] for f in res} == {"A", "C"}

    def test_lista_vacia(self):
        assert fa.partidos_de_hoy([]) == []


# ─────────────────────────────────────────────
# construir_indice
# ─────────────────────────────────────────────
class TestConstruirIndice:
    def test_indexa_local_y_visitante_normalizados(self):
        partidos = [
            {"Equipo Local": "Llaneros", "Equipo Visitante": "Santa Fe", "Hora": "20:00"},
        ]
        idx = fa.construir_indice(partidos)
        assert "llaneros fc" in idx
        assert "independiente santa fe" in idx
        assert idx["llaneros fc"]["Hora"] == "20:00"
        assert idx["llaneros fc"] is idx["independiente santa fe"]

    def test_skipea_equipos_vacios(self):
        partidos = [{"Equipo Local": "", "Equipo Visitante": "Millonarios"}]
        idx = fa.construir_indice(partidos)
        assert "millonarios" in idx
        assert "" not in idx


# ─────────────────────────────────────────────
# leer_suscriptores
# ─────────────────────────────────────────────
class TestLeerSuscriptores:
    def test_normaliza_y_separa_por_coma(self):
        filas = [
            {fa.COL_CORREO: "a@b.com", fa.COL_EQUIPOS: "Llaneros, Santa Fe"},
        ]
        subs = fa.leer_suscriptores(filas)
        assert subs == [
            {"correo": "a@b.com", "equipos": ["llaneros fc", "independiente santa fe"]}
        ]

    def test_descarta_filas_sin_correo_o_sin_equipos(self):
        filas = [
            {fa.COL_CORREO: "  ", fa.COL_EQUIPOS: "Millonarios"},
            {fa.COL_CORREO: "c@d.com", fa.COL_EQUIPOS: ""},
            {fa.COL_CORREO: "ok@x.com", fa.COL_EQUIPOS: "Millonarios"},
        ]
        subs = fa.leer_suscriptores(filas)
        assert len(subs) == 1
        assert subs[0]["correo"] == "ok@x.com"


# ─────────────────────────────────────────────
# generar_html
# ─────────────────────────────────────────────
class TestGenerarHtml:
    def test_incluye_datos_del_partido(self, monkeypatch):
        monkeypatch.setattr(fa, "HOY", date(2026, 5, 8))
        partidos = [{
            "Equipo Local": "Millonarios",
            "Equipo Visitante": "Atlético Nacional",
            "Hora": "20:00",
            "Estadio": "El Campín",
            "Jornada": "12",
        }]
        html = fa.generar_html(partidos)
        assert "Millonarios" in html
        assert "Atlético Nacional" in html
        assert "20:00" in html
        assert "El Campín" in html
        assert "08/05/2026" in html
        assert "<table" in html


# ─────────────────────────────────────────────
# leer_sheet (mock de requests.get)
# ─────────────────────────────────────────────
class TestLeerSheet:
    def test_csv_a_lista_de_dicts(self, monkeypatch):
        csv_text = "Fecha,Equipo Local,Equipo Visitante\n08/05/2026,Llaneros,Santa Fe\n"
        fake_resp = MagicMock()
        fake_resp.content = csv_text.encode("utf-8")
        fake_resp.raise_for_status = MagicMock()
        monkeypatch.setattr(fa.requests, "get", lambda *a, **kw: fake_resp)

        filas = fa.leer_sheet("sheet_id", "tab")
        assert len(filas) == 1
        assert filas[0] == {
            "Fecha": "08/05/2026",
            "Equipo Local": "Llaneros",
            "Equipo Visitante": "Santa Fe",
        }

    def test_http_error_devuelve_lista_vacia(self, monkeypatch):
        err_resp = MagicMock()
        err_resp.status_code = 403
        fake_resp = MagicMock()
        fake_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=err_resp)
        monkeypatch.setattr(fa.requests, "get", lambda *a, **kw: fake_resp)

        assert fa.leer_sheet("x", "y") == []

    def test_error_de_conexion_devuelve_lista_vacia(self, monkeypatch):
        def boom(*a, **kw):
            raise requests.exceptions.ConnectionError("offline")
        monkeypatch.setattr(fa.requests, "get", boom)

        assert fa.leer_sheet("x", "y") == []


# ─────────────────────────────────────────────
# enviar_correo (mock de smtplib)
# ─────────────────────────────────────────────
class TestEnviarCorreo:
    @pytest.fixture
    def fake_smtp(self, monkeypatch):
        monkeypatch.setattr(fa, "GMAIL_USER", "test@gmail.com")
        monkeypatch.setattr(fa, "GMAIL_PASSWORD", "pwd")
        server = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=server)
        ctx.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(fa.smtplib, "SMTP_SSL", MagicMock(return_value=ctx))
        return server

    def test_login_y_sendmail_con_args_correctos(self, fake_smtp):
        partidos = [{
            "Equipo Local": "A", "Equipo Visitante": "B",
            "Hora": "20:00", "Estadio": "X", "Jornada": "1",
        }]
        ok = fa.enviar_correo("dest@x.com", partidos)
        assert ok is True
        fake_smtp.login.assert_called_once_with("test@gmail.com", "pwd")
        from_addr, to_addr, _body = fake_smtp.sendmail.call_args.args
        assert from_addr == "test@gmail.com"
        assert to_addr == "dest@x.com"

    def test_devuelve_false_si_smtp_falla(self, monkeypatch):
        monkeypatch.setattr(fa, "GMAIL_USER", "test@gmail.com")
        monkeypatch.setattr(fa, "GMAIL_PASSWORD", "pwd")
        monkeypatch.setattr(
            fa.smtplib, "SMTP_SSL", MagicMock(side_effect=Exception("boom"))
        )
        partidos = [{"Equipo Local": "A", "Equipo Visitante": "B",
                     "Hora": "20:00", "Estadio": "X", "Jornada": "1"}]
        assert fa.enviar_correo("dest@x.com", partidos) is False


# ─────────────────────────────────────────────
# main — pipeline completo
# ─────────────────────────────────────────────
class TestMain:
    def test_envia_solo_a_suscriptores_con_partido_hoy(self, monkeypatch):
        monkeypatch.setattr(fa, "HOY", date(2026, 5, 8))
        monkeypatch.setattr(fa, "GMAIL_USER", "test@gmail.com")
        monkeypatch.setattr(fa, "GMAIL_PASSWORD", "pwd")

        partidos_data = [
            {"Fecha": "08/05/2026", "Equipo Local": "Millonarios", "Equipo Visitante": "Santa Fe",
             "Hora": "20:00", "Estadio": "El Campín", "Jornada": "12"},
            {"Fecha": "09/05/2026", "Equipo Local": "Llaneros", "Equipo Visitante": "Junior",
             "Hora": "18:00", "Estadio": "Bello Horizonte", "Jornada": "12"},
        ]
        form_data = [
            {fa.COL_CORREO: "fan@x.com",     fa.COL_EQUIPOS: "Millonarios"},
            {fa.COL_CORREO: "neutral@x.com", fa.COL_EQUIPOS: "Llaneros"},  # juega mañana
        ]

        def fake_leer_sheet(sheet_id, sheet_name):
            return partidos_data if sheet_id == fa.SHEET_PARTIDOS_ID else form_data
        monkeypatch.setattr(fa, "leer_sheet", fake_leer_sheet)

        enviados = []
        monkeypatch.setattr(
            fa, "enviar_correo",
            lambda dest, partidos: enviados.append((dest, partidos)) or True,
        )

        fa.main()

        assert len(enviados) == 1
        dest, partidos = enviados[0]
        assert dest == "fan@x.com"
        assert partidos[0]["Equipo Local"] == "Millonarios"

    def test_aborta_si_faltan_credenciales(self, monkeypatch):
        monkeypatch.setattr(fa, "GMAIL_USER", None)
        monkeypatch.setattr(fa, "GMAIL_PASSWORD", None)
        with pytest.raises(SystemExit):
            fa.main()

    def test_no_envia_si_no_hay_partidos_hoy(self, monkeypatch):
        monkeypatch.setattr(fa, "HOY", date(2026, 5, 8))
        monkeypatch.setattr(fa, "GMAIL_USER", "test@gmail.com")
        monkeypatch.setattr(fa, "GMAIL_PASSWORD", "pwd")

        partidos_data = [
            {"Fecha": "09/05/2026", "Equipo Local": "Llaneros", "Equipo Visitante": "Junior",
             "Hora": "18:00", "Estadio": "X", "Jornada": "12"},
        ]
        form_data = [{fa.COL_CORREO: "fan@x.com", fa.COL_EQUIPOS: "Llaneros"}]

        monkeypatch.setattr(
            fa, "leer_sheet",
            lambda sid, name: partidos_data if sid == fa.SHEET_PARTIDOS_ID else form_data,
        )
        envio_spy = MagicMock()
        monkeypatch.setattr(fa, "enviar_correo", envio_spy)

        fa.main()
        envio_spy.assert_not_called()
