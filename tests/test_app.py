import inspect
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
import pytest
from PIL import Image

from app import create_app, db
from app.models import Auditoria, Reporte, SectorGeografico, Usuario
from app.security import reset_rate_limiter
from app.seed import seed_database
from app.utils import (
    distancia_haversine_km,
    formatear_fecha_chile,
    generar_codigo_reporte,
)
from config import Config, TestingConfig


@pytest.fixture
def app(tmp_path):
    reset_rate_limiter()

    class LocalTestConfig(TestingConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(LocalTestConfig)
    with application.app_context():
        seed_database()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, email, password):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def _create_test_image(width=2400, height=1800, fmt="JPEG", noisy=False, with_exif=False):
    buf = io.BytesIO()
    if noisy:
        raw = os.urandom(width * height * 3)
        img = Image.frombytes("RGB", (width, height), raw)
        img.save(buf, format="PNG")
    else:
        img = Image.new("RGB", (width, height), color=(30, 120, 200))
        if with_exif:
            exif = Image.Exif()
            exif[0x010F] = "CameraTestMaker"
            exif[0x0110] = "PhoneModelX"
            img.save(buf, format=fmt, exif=exif.tobytes())
        else:
            img.save(buf, format=fmt)
    buf.seek(0)
    return buf


def test_cp01_rf01_anonymous_report_valid_and_sector_detection(client, app):
    """CP-01 (Positivo RF01): Reporte anónimo con GPS y foto crea código CSPRNG y estado Pendiente."""
    img_buf = _create_test_image()
    resp_post = client.post(
        "/reportar",
        data={
            "direccion_referencia": "Av. Gabriela Mistral 450, Alerce",
            "descripcion": "Anegamiento importante en esquina por lluvia intensa.",
            "nivel_agua": "Alto",
            "latitud": "-41.3950",
            "longitud": "-72.9050",
            "reportante_nombre": "Vecino Test",
            "foto": (img_buf, "inundacion.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp_post.status_code == 302
    loc = resp_post.headers["Location"]
    assert "/seguimiento?codigo=PM-2026-" in loc

    resp_follow = client.get(loc)
    assert resp_follow.status_code == 200

    with app.app_context():
        rep = Reporte.query.filter_by(direccion_referencia="Av. Gabriela Mistral 450, Alerce").first()
        assert rep is not None
        assert re.match(r"^PM-2026-[0-9A-F]{8}$", rep.codigo)
        assert rep.estado == "Pendiente"
        assert rep.severidad == "Alta"
        assert rep.sector is not None and rep.sector.nombre == "Alerce"
        assert rep.foto_filename is not None
        saved_path = os.path.join(app.config["UPLOAD_FOLDER"], rep.foto_filename)
        assert os.path.exists(saved_path)
        assert os.path.getsize(saved_path) <= 2 * 1024 * 1024


def test_cp02_rf01_negative_invalid_inputs_puerto_varas_and_sea(client, app):
    """CP-02 (Negativo RF01): Rechaza HTTP 400 Santiago, Puerto Varas, mar del Seno de Reloncaví y campos inválidos."""
    with app.app_context():
        inicial = Reporte.query.count()

    # 1) Santiago (-33.45, -70.66)
    assert client.post(
        "/reportar",
        data={
            "direccion_referencia": "Alameda 100",
            "descripcion": "Intento de reporte fuera de la comuna de Puerto Montt.",
            "nivel_agua": "Alto",
            "latitud": "-33.4500",
            "longitud": "-70.6600",
        },
    ).status_code == 400

    # 2) Puerto Varas (-41.3195, -72.9854) -> excluido por PM_LAT_MAX=-41.370
    assert client.post(
        "/reportar",
        data={
            "direccion_referencia": "Costanera Puerto Varas",
            "descripcion": "Intento en comuna vecina de Puerto Varas fuera del cuadrante urbano.",
            "nivel_agua": "Moderado",
            "latitud": "-41.3195",
            "longitud": "-72.9854",
        },
    ).status_code == 400

    # 3) Aguas abiertas del Seno de Reloncaví (-41.5050, -72.9250) -> excluido por filtro marino
    assert client.post(
        "/reportar",
        data={
            "direccion_referencia": "Bahía Reloncaví mar adentro",
            "descripcion": "Punto sobre el espejo de agua del Seno de Reloncaví.",
            "nivel_agua": "Bajo",
            "latitud": "-41.5050",
            "longitud": "-72.9250",
        },
    ).status_code == 400

    # 4) Exceso de longitud y nivel inválido
    assert client.post(
        "/reportar",
        data={
            "direccion_referencia": "A" * 205,
            "descripcion": "corto",
            "nivel_agua": "ExtremoInvalido",
            "latitud": "999.0",
            "longitud": "-72.9396",
        },
    ).status_code == 400

    with app.app_context():
        assert Reporte.query.count() == inicial


def test_cp03_rf01_privacy_consent_password_policy_and_my_reports(client):
    """CP-03 (Auth/Ley 21.719): Rechaza clave débil o falta de consentimiento (400), registra con aviso y lista reportes."""
    # Falta acepta_privacidad
    resp_no_consent = client.post(
        "/registro",
        data={
            "nombre": "Camila Soto",
            "email": "camila@ejemplo.cl",
            "password": "Password2026!",
            "confirm_password": "Password2026!",
        },
    )
    assert resp_no_consent.status_code == 400

    # Clave débil
    resp_weak = client.post(
        "/registro",
        data={
            "nombre": "Camila Soto",
            "email": "camila@ejemplo.cl",
            "password": "123",
            "confirm_password": "123",
            "acepta_privacidad": "1",
        },
    )
    assert resp_weak.status_code == 400

    # Registro válido con consentimiento explícito
    resp = client.post(
        "/registro",
        data={
            "nombre": "Camila Soto",
            "email": "camila@ejemplo.cl",
            "password": "Password2026!",
            "confirm_password": "Password2026!",
            "acepta_privacidad": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Mis Reportes" in resp.data

    resp_rep = client.post(
        "/reportar",
        data={
            "direccion_referencia": "Calle Antonio Varas 800",
            "descripcion": "Agua acumulada en esquina dificultando paso peatonal.",
            "nivel_agua": "Moderado",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
        },
        follow_redirects=True,
    )
    assert resp_rep.status_code == 200
    my_rep_page = client.get("/mis-reportes")
    assert my_rep_page.status_code == 200
    assert b"Calle Antonio Varas 800" in my_rep_page.data


def test_cp04_rf02_public_api_contract_exact_keys_and_pii_redaction(client, app):
    """CP-04 (RF02 + Ley 21.719): Verifica las 15 llaves exactas de /api/reportes y sanitización PII en textos libres."""
    expected_keys = {
        "id",
        "codigo",
        "direccion_referencia",
        "descripcion",
        "latitud",
        "longitud",
        "nivel_agua",
        "severidad",
        "estado",
        "sector_id",
        "sector_nombre",
        "foto_url",
        "nota_operador",
        "fecha_creacion",
        "fecha_iso",
    }
    resp = client.get("/api/reportes")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) >= 1
    for item in data:
        assert set(item.keys()) == expected_keys
        assert item["estado"] == "Publicado/Validado"
        assert "usuario_id" not in item
        assert "email" not in item
        assert "reportante_nombre" not in item
        if item["foto_url"]:
            assert item["foto_url"].startswith("/evidencias/")

    # Verificar sanitización automática de RUT, teléfono y correo en texto libre
    client.post(
        "/reportar",
        data={
            "direccion_referencia": "Pasaje Los Alerces 12, contacto vecino@privado.cl",
            "descripcion": "Inundación frente a casa RUT 12.345.678-9 fono +56 9 8765 4321 urgente.",
            "nivel_agua": "Alto",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
        },
    )
    with app.app_context():
        rep_pii = Reporte.query.order_by(Reporte.id.desc()).first()
        assert "12.345.678-9" not in rep_pii.descripcion
        assert "8765" not in rep_pii.descripcion
        assert "vecino@privado.cl" not in rep_pii.direccion_referencia
        assert "[RUT OMITIDO]" in rep_pii.descripcion
        assert "[TELÉFONO OMITIDO]" in rep_pii.descripcion
        assert "[CORREO OMITIDO]" in rep_pii.direccion_referencia

    resp_res = client.get("/api/reportes?incluir_resueltos=1")
    estados = {r["estado"] for r in resp_res.get_json()}
    assert "Resuelto/Despejado" in estados
    assert "Pendiente" not in estados
    assert "Rechazado/Duplicado" not in estados

    kpis = client.get("/api/resumen").get_json()
    assert kpis["activos"] == len(data)
    assert len(client.get("/api/sectores").get_json()) == 8


def test_cp05_rf03_operator_lifecycle_reject_and_invalid_transition(client, app):
    """CP-05 (RF03 Positivo/Negativo): Ciclo completo, rechazo y bloqueo HTTP 400 de transición inválida."""
    with app.app_context():
        pendiente = Reporte.query.filter_by(estado="Pendiente").first()
        rechazado = Reporte.query.filter_by(estado="Rechazado/Duplicado").first()
        assert pendiente is not None and rechazado is not None
        rep_id, rep_codigo = pendiente.id, pendiente.codigo
        rej_id = rechazado.id

    _login(client, "operador@puertomontt.cl", "Operador123!")
    assert client.get(f"/operador/reporte/{rep_id}").status_code == 200

    # Estado inexistente y transición inválida: Rechazado/Duplicado -> Pendiente directo debe retornar 400
    assert client.post(f"/operador/reporte/{rej_id}/estado", data={"estado": "NoExiste"}).status_code == 400
    resp_inv = client.post(
        f"/operador/reporte/{rej_id}/estado",
        data={"estado": "Pendiente", "nota_operador": "Intento inválido"},
    )
    assert resp_inv.status_code == 400

    # Camino válido completo
    assert client.post(f"/operador/reporte/{rep_id}/estado", data={"estado": "En Verificación"}).status_code == 302
    assert all(r["codigo"] != rep_codigo for r in client.get("/api/reportes").get_json())

    resp_pub = client.post(
        f"/operador/reporte/{rep_id}/estado",
        data={"estado": "Publicado/Validado", "nota_operador": "Validado fono 912345678", "sector_id": "1", "next": "dashboard"},
        follow_redirects=True,
    )
    assert resp_pub.status_code == 200
    pub_items = [r for r in client.get("/api/reportes").get_json() if r["codigo"] == rep_codigo]
    assert len(pub_items) == 1
    assert "[TELÉFONO OMITIDO]" in pub_items[0]["nota_operador"]

    # Borrado intencional de nota previa enviando nota_operador=""
    assert client.post(
        f"/operador/reporte/{rep_id}/estado",
        data={"estado": "Resuelto/Despejado", "nota_operador": ""},
    ).status_code == 302
    assert all(r["codigo"] != rep_codigo for r in client.get("/api/reportes").get_json())
    assert client.get("/operador/?fecha_desde=2026-01-01&fecha_hasta=2026-12-31").status_code == 200

    with app.app_context():
        assert db.session.get(Reporte, rep_id).nota_operador is None
        assert Auditoria.query.filter_by(accion="CAMBIO_ESTADO_REPORTE", entidad_id=rep_id).count() == 3


def test_cp06_rf04_filters_tracking_history_dst_timezone_and_haversine(client, app):
    """CP-06 (RF04 + DST + Haversine): Filtros fecha/sector/severidad, /historial, /seguimiento, DST y Haversine."""
    with app.app_context():
        alerce = SectorGeografico.query.filter_by(nombre="Alerce").first()
        pub = Reporte.query.filter_by(estado="Publicado/Validado").first()
        alerce_id, pub_codigo = alerce.id, pub.codigo

    data = client.get(f"/api/reportes?sector_id={alerce_id}&fecha_desde=2026-01-01&fecha_hasta=2026-12-31").get_json()
    assert len(data) >= 1 and all(item["sector_id"] == alerce_id for item in data)
    # Fechas malformadas no rompen endpoint
    assert client.get("/api/reportes?fecha_desde=invalida&fecha_hasta=invalida").status_code == 200

    data_crit = client.get("/api/reportes?severidad=Critica").get_json()
    assert len(data_crit) >= 1 and all(item["severidad"] == "Critica" for item in data_crit)

    # /historial con filtro de fechas y /seguimiento
    assert client.get(
        f"/historial?sector_id={alerce_id}&severidad=Alta&estado=Publicado/Validado&fecha_desde=2026-01-01&fecha_hasta=2026-12-31"
    ).status_code == 200
    assert pub_codigo.encode() in client.get(f"/seguimiento?codigo={pub_codigo}").data
    assert b"No se encontr" in client.get("/seguimiento?codigo=PM-2026-00000000").data

    # Zona horaria oficial America/Santiago (invierno UTC-4 vs verano UTC-3)
    dt_invierno = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    dt_verano = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
    assert formatear_fecha_chile(dt_invierno) == "15/07/2026 12:00"
    assert formatear_fecha_chile(dt_verano) == "24/11/2026 13:00"

    dist_km = distancia_haversine_km(-41.4718, -72.9396, -41.3945, -72.9055)
    assert 8.0 < dist_km < 10.5


def test_cp07_rnf02_large_image_compressed_and_exif_stripped(client, app):
    """CP-07 (Positivo RNF02): Comprime fotos >2MB bajo el límite y elimina metadatos EXIF."""
    big_png = _create_test_image(width=1200, height=900, noisy=True)
    assert len(big_png.getvalue()) > 2 * 1024 * 1024

    resp_big = client.post(
        "/reportar",
        data={
            "direccion_referencia": "Av. Presidente Ibáñez 320",
            "descripcion": "Prueba de compresión automática de fotografía pesada de celular.",
            "nivel_agua": "Alto",
            "latitud": "-41.4600",
            "longitud": "-72.9400",
            "foto": (big_png, "foto_pesada.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp_big.status_code == 200
    with app.app_context():
        rep = Reporte.query.filter_by(direccion_referencia="Av. Presidente Ibáñez 320").first()
        saved_path = os.path.join(app.config["UPLOAD_FOLDER"], rep.foto_filename)
        assert os.path.getsize(saved_path) <= 2 * 1024 * 1024

    exif_jpg = _create_test_image(width=800, height=600, fmt="JPEG", with_exif=True)
    client.post(
        "/reportar",
        data={
            "direccion_referencia": "Av. Austral 510",
            "descripcion": "Verificación de eliminación automática de metadatos EXIF en foto subida.",
            "nivel_agua": "Bajo",
            "latitud": "-41.4545",
            "longitud": "-72.9125",
            "foto": (exif_jpg, "con_exif.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        rep2 = Reporte.query.filter_by(direccion_referencia="Av. Austral 510").first()
        saved_exif_path = os.path.join(app.config["UPLOAD_FOLDER"], rep2.foto_filename)
        with Image.open(saved_exif_path) as saved_img:
            assert len(saved_img.getexif()) == 0


def test_cp08_rnf02_negative_invalid_extension_spoofed_jpg_and_413_limit(client, tmp_path):
    """CP-08 (Negativo RNF02): Rechaza 400 extensiones ejecutables, imágenes falsas y 413 al superar MAX_CONTENT_LENGTH."""
    assert client.post(
        "/reportar",
        data={
            "direccion_referencia": "Calle Varas 100",
            "descripcion": "Prueba con extensión ejecutable no permitida para validar RNF02.",
            "nivel_agua": "Bajo",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
            "foto": (io.BytesIO(b"MZ_fake_binary"), "payload.exe"),
        },
        content_type="multipart/form-data",
    ).status_code == 400

    assert client.post(
        "/reportar",
        data={
            "direccion_referencia": "Calle Varas 101",
            "descripcion": "Prueba con contenido de texto renombrado con extensión jpg.",
            "nivel_agua": "Bajo",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
            "foto": (io.BytesIO(b"<script>alert(1)</script>"), "falsa.jpg"),
        },
        content_type="multipart/form-data",
    ).status_code == 400

    # Verificar límite duro MAX_CONTENT_LENGTH -> HTTP 413
    class SmallLimitConfig(TestingConfig):
        MAX_CONTENT_LENGTH = 2048
        UPLOAD_FOLDER = str(tmp_path / "uploads_413")

    limit_app = create_app(SmallLimitConfig)
    with limit_app.app_context():
        seed_database()
        r_413 = limit_app.test_client().post(
            "/reportar",
            data={
                "direccion_referencia": "Calle 413",
                "descripcion": "Solicitud que excede MAX_CONTENT_LENGTH configurado.",
                "nivel_agua": "Bajo",
                "latitud": "-41.4718",
                "longitud": "-72.9396",
                "foto": (io.BytesIO(b"X" * 8192), "enorme.jpg"),
            },
            content_type="multipart/form-data",
        )
        assert r_413.status_code == 413
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_cp09_owasp_a01_rbac_protected_evidences_and_open_redirect(client, app):
    """CP-09 (Negativo OWASP A01): RBAC 403, bloqueo /evidencias de pendientes para anónimos y Open Redirect."""
    client.post(
        "/reportar",
        data={
            "direccion_referencia": "Calle Privada Pendiente 10",
            "descripcion": "Reporte pendiente con foto protegida contra acceso anónimo directo.",
            "nivel_agua": "Alto",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
            "foto": (_create_test_image(400, 300), "privada.jpg"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        rep_pend = Reporte.query.filter_by(direccion_referencia="Calle Privada Pendiente 10").first()
        foto_pend, cod_pend = rep_pend.foto_filename, rep_pend.codigo

    assert client.get(f"/evidencias/{foto_pend}").status_code == 403
    with client.get(f"/evidencias/{foto_pend}?codigo={cod_pend}") as r_ev_code:
        assert r_ev_code.status_code == 200
    assert client.get("/operador/").status_code == 302
    assert client.get("/admin/").status_code == 302

    _login(client, "ciudadano@ejemplo.cl", "Ciudadano123!")
    assert client.get("/operador/").status_code == 403
    assert client.get("/admin/").status_code == 403
    client.post("/logout")

    _login(client, "operador@puertomontt.cl", "Operador123!")
    assert client.get("/operador/").status_code == 200
    with client.get(f"/evidencias/{foto_pend}") as r_ev_op:
        assert r_ev_op.status_code == 200
    assert client.get("/admin/").status_code == 403
    client.post("/logout")

    resp_redir = client.post(
        "/login?next=//evil.com",
        data={"email": "ciudadano@ejemplo.cl", "password": "Ciudadano123!"},
        follow_redirects=False,
    )
    assert resp_redir.status_code == 302
    assert "evil.com" not in resp_redir.headers.get("Location", "")

    with app.app_context():
        assert Auditoria.query.filter_by(accion="ACCESO_DENEGADO_403").count() >= 3


def test_cp10_owasp_csrf_protection_across_all_modules(tmp_path):
    """CP-10 (OWASP CSRF): Verifica rechazo 400 sin token en auth, público, operador y los 3 endpoints POST de admin."""
    reset_rate_limiter()

    class CsrfTestConfig(Config):
        TESTING = True
        SEED_DEMO_DATA = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        CSRF_ENABLED = True
        SESSION_COOKIE_SECURE = False
        UPLOAD_FOLDER = str(tmp_path / "uploads_csrf")

    csrf_app = create_app(CsrfTestConfig)
    with csrf_app.app_context():
        seed_database()
        c = csrf_app.test_client()

        assert c.post("/login", data={"email": "admin@puertomontt.cl", "password": "Admin123!"}).status_code == 400
        assert c.post("/registro", data={"nombre": "Test"}).status_code == 400
        assert c.post("/reportar", data={"direccion_referencia": "X"}).status_code == 400

        html_login = c.get("/login").data.decode("utf-8")
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', html_login).group(1)

        resp_ok = c.post(
            "/login",
            data={"csrf_token": token, "email": "admin@puertomontt.cl", "password": "Admin123!"},
            follow_redirects=True,
        )
        assert resp_ok.status_code == 200 and b"Panel de Administraci" in resp_ok.data

        # Sin CSRF deben fallar operador y todos los formularios administrativos
        assert c.post("/operador/reporte/1/estado", data={"estado": "Resuelto/Despejado"}).status_code == 400
        assert c.post("/admin/usuarios", data={"nombre": "X", "email": "x@x.cl"}).status_code == 400
        assert c.post("/admin/usuarios/2/actualizar", data={"rol": "operador", "activo": "1"}).status_code == 400
        assert c.post("/admin/sectores", data={"nombre": "SecCSRF", "lat_centro": "-41.47", "lng_centro": "-72.94"}).status_code == 400

        # Con token CSRF válido en admin/usuarios funciona (302 redirect)
        token_admin = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', c.get("/admin/usuarios").data.decode("utf-8")
        ).group(1)
        assert c.post(
            "/admin/usuarios/2/actualizar",
            data={"csrf_token": token_admin, "rol": "operador", "activo": "1"},
        ).status_code == 302

        assert Auditoria.query.filter_by(accion="CSRF_INVALIDO").count() >= 5
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_cp11_owasp_a03_xss_and_sqli_resistance(client):
    """CP-11 (Negativo OWASP A03): Escapa payloads XSS y neutraliza intentos de SQL Injection en búsquedas."""
    xss_payload = "<script>alert('xss_test')</script>"
    resp_rep = client.post(
        "/reportar",
        data={
            "direccion_referencia": f"Calle Prueba {xss_payload}",
            "descripcion": f"Descripción de incidente con carga {xss_payload} para validar escape.",
            "nivel_agua": "Alto",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
        },
        follow_redirects=True,
    )
    assert resp_rep.status_code == 200
    assert b"<script>alert('xss_test')</script>" not in resp_rep.data
    assert b"&lt;script&gt;alert(&#39;xss_test&#39;)&lt;/script&gt;" in resp_rep.data

    _login(client, "operador@puertomontt.cl", "Operador123!")
    resp_sqli = client.get("/operador/?q=' OR 1=1 --")
    assert resp_sqli.status_code == 200


def test_cp12_owasp_a07_rate_limiting_login_and_report_spam(client, app):
    """CP-12 (Negativo OWASP A07/Spam): Bloqueo 429 tras 5 fallos en /login y 10 envíos en /reportar."""
    for _ in range(5):
        assert client.post("/login", data={"email": "operador@puertomontt.cl", "password": "Wrong"}).status_code == 401
    assert client.post("/login", data={"email": "operador@puertomontt.cl", "password": "Operador123!"}).status_code == 429

    for i in range(10):
        r = client.post(
            "/reportar",
            data={
                "direccion_referencia": f"Calle Spam {i}",
                "descripcion": "Reporte válido repetido para probar límite anti-spam por IP.",
                "nivel_agua": "Bajo",
                "latitud": "-41.4718",
                "longitud": "-72.9396",
            },
        )
        assert r.status_code == 302

    r_spam = client.post(
        "/reportar",
        data={
            "direccion_referencia": "Calle Spam 11",
            "descripcion": "Onceavo reporte consecutivo que debe ser limitado con HTTP 429.",
            "nivel_agua": "Bajo",
            "latitud": "-41.4718",
            "longitud": "-72.9396",
        },
    )
    assert r_spam.status_code == 429
    with app.app_context():
        assert Auditoria.query.filter_by(accion="LOGIN_BLOQUEADO").first() is not None
        assert Auditoria.query.filter_by(accion="REPORTE_RATE_LIMIT").first() is not None

    # Verificar cota máxima de llaves en RAM (anti-DoS por IPs/correos rotativos)
    from app.security import _FAILED_LOGINS, MAX_RATE_LIMIT_KEYS, record_failed_login
    for idx in range(MAX_RATE_LIMIT_KEYS + 25):
        _FAILED_LOGINS[(f"10.0.{idx // 250}.{idx % 250}", f"u{idx}@test.cl")] = [0.0]
    record_failed_login("127.0.0.1", "trigger@test.cl")
    assert len(_FAILED_LOGINS) <= MAX_RATE_LIMIT_KEYS


def test_cp13_owasp_a05_security_headers_and_hsts(client, tmp_path):
    """CP-13 (Positivo OWASP A05): Cabeceras CSP, X-Frame-Options, nosniff, Referrer-Policy y HSTS."""
    home = client.get("/")
    assert home.status_code == 200
    assert home.headers.get("X-Content-Type-Options") == "nosniff"
    assert home.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert home.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in (home.headers.get("Content-Security-Policy") or "")

    class ProdHeaderConfig(TestingConfig):
        FLASK_ENV = "production"
        UPLOAD_FOLDER = str(tmp_path / "uploads_prod")

    prod_app = create_app(ProdHeaderConfig)
    with prod_app.app_context():
        seed_database()
        r_prod = prod_app.test_client().get("/")
        assert "max-age=31536000" in (r_prod.headers.get("Strict-Transport-Security") or "")
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_cp14_cu04_admin_users_temp_password_rotation_and_audit(client, app):
    """CP-14 (CU-04 Usuarios + A07): Dashboard, auditoría, validaciones, no auto-degradación y rotación obligatoria de clave temporal."""
    _login(client, "admin@puertomontt.cl", "Admin123!")
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/usuarios").status_code == 200
    assert client.get("/admin/auditoria?accion=LOGIN_EXITOSO").status_code == 200

    # Validaciones negativas en creación de usuario (correo duplicado y rol inválido)
    resp_err = client.post(
        "/admin/usuarios",
        data={"nombre": "AB", "email": "admin@puertomontt.cl", "password": "123", "rol": "invalido"},
    )
    assert resp_err.status_code == 200

    # Crear nuevo operador con contraseña temporal -> debe_cambiar_clave=True
    resp_u = client.post(
        "/admin/usuarios",
        data={
            "nombre": "Nuevo Operador",
            "email": "op2@puertomontt.cl",
            "password": "TempPassword1!",
            "rol": "operador",
        },
        follow_redirects=True,
    )
    assert resp_u.status_code == 200

    with app.app_context():
        admin_u = Usuario.query.filter_by(email="admin@puertomontt.cl").first()
        op2 = Usuario.query.filter_by(email="op2@puertomontt.cl").first()
        assert op2 is not None and op2.rol == "operador" and op2.debe_cambiar_clave is True
        admin_id, op2_id = admin_u.id, op2.id

    # Admin intenta auto-degradarse y cambiar a rol inválido -> se rechaza
    client.post(f"/admin/usuarios/{admin_id}/actualizar", data={"rol": "ciudadano", "activo": "0"})
    client.post(f"/admin/usuarios/{op2_id}/actualizar", data={"rol": "rol_inexistente", "activo": "1"})

    # Cerrar sesión de admin e ingresar como op2 -> debe forzar /cambiar-clave
    client.post("/logout")
    r_login_temp = client.post(
        "/login",
        data={"email": "op2@puertomontt.cl", "password": "TempPassword1!"},
        follow_redirects=False,
    )
    assert r_login_temp.status_code == 302
    assert "/cambiar-clave" in r_login_temp.headers["Location"]
    # Intentar saltar a /operador/ sin rotar clave redirige de vuelta a /cambiar-clave
    assert "/cambiar-clave" in client.get("/operador/", follow_redirects=False).headers["Location"]
    # Reutilizar la misma clave temporal retorna 400
    assert client.post(
        "/cambiar-clave",
        data={"password": "TempPassword1!", "confirm_password": "TempPassword1!"},
    ).status_code == 400
    # Rotar a clave personal nueva habilita acceso
    assert client.post(
        "/cambiar-clave",
        data={"password": "NuevaClave2026!", "confirm_password": "NuevaClave2026!"},
        follow_redirects=True,
    ).status_code == 200
    client.post("/logout")

    # Admin desactiva a op2 y verifica bloqueo 403 en login
    _login(client, "admin@puertomontt.cl", "Admin123!")
    client.post(f"/admin/usuarios/{op2_id}/actualizar", data={"rol": "operador", "activo": "0"})
    client.post("/logout")
    assert client.post("/login", data={"email": "op2@puertomontt.cl", "password": "NuevaClave2026!"}).status_code == 403

    with app.app_context():
        admin_after = db.session.get(Usuario, admin_id)
        op2_after = db.session.get(Usuario, op2_id)
        assert admin_after.rol == "admin" and admin_after.activo is True
        assert op2_after.activo is False and op2_after.debe_cambiar_clave is False
        assert Auditoria.query.filter_by(accion="CAMBIO_CLAVE_OBLIGATORIO", entidad_id=op2_id).first() is not None


def test_cp15_cu04_admin_sectors_create_inactive_edit_and_validations(client, app):
    """CP-15 (CU-04 Sectores): Admin lista, crea sector inactivo, edita sector existente y valida errores."""
    _login(client, "admin@puertomontt.cl", "Admin123!")
    assert client.get("/admin/sectores").status_code == 200

    poly = [[-41.43, -72.91], [-41.43, -72.90], [-41.44, -72.90], [-41.44, -72.91]]
    resp_s = client.post(
        "/admin/sectores",
        data={
            "nombre": "Sector Chamiza Inactivo",
            "descripcion": "Prueba de checkbox activo=False.",
            "nivel_riesgo": "Medio",
            "latitud_invalida": "x",
            "lat_centro": "-41.435",
            "lng_centro": "-72.905",
            "poligono_json": json.dumps(poly),
        },
        follow_redirects=True,
    )
    assert resp_s.status_code == 200
    with app.app_context():
        sec = SectorGeografico.query.filter_by(nombre="Sector Chamiza Inactivo").first()
        assert sec is not None and sec.activo is False
        sec_id = sec.id

    # Editar el sector existente activándolo y cambiando nivel de riesgo a Alto
    assert client.post(
        "/admin/sectores",
        data={
            "sector_id": str(sec_id),
            "nombre": "Sector Chamiza Activo",
            "descripcion": "Sector actualizado en CP-15.",
            "nivel_riesgo": "Alto",
            "lat_centro": "-41.436",
            "lng_centro": "-72.906",
            "poligono_json": json.dumps(poly),
            "activo": "1",
        },
        follow_redirects=True,
    ).status_code == 200

    # Validaciones negativas: lat/lng no numéricas, polígono malformado, riesgo inválido y nombre duplicado
    assert client.post("/admin/sectores", data={"nombre": "BadCoords", "lat_centro": "abc", "lng_centro": "xyz"}).status_code == 302
    assert client.post(
        "/admin/sectores",
        data={"nombre": "Sector Malo", "nivel_riesgo": "Alto", "lat_centro": "-41.43", "lng_centro": "-72.90", "poligono_json": "{no_es_lista"},
    ).status_code == 302
    assert client.post(
        "/admin/sectores",
        data={"nombre": "", "nivel_riesgo": "Invalido", "lat_centro": "-41.43", "lng_centro": "-72.90"},
    ).status_code == 302
    assert client.post(
        "/admin/sectores",
        data={"nombre": "Alerce", "nivel_riesgo": "Alto", "lat_centro": "-41.39", "lng_centro": "-72.90"},
    ).status_code == 302

    with app.app_context():
        sec_updated = db.session.get(SectorGeografico, sec_id)
        assert sec_updated.nombre == "Sector Chamiza Activo" and sec_updated.activo is True
        assert SectorGeografico.query.filter_by(nombre="Sector Malo").first() is None
        assert Auditoria.query.filter_by(accion="EDITAR_SECTOR", entidad_id=sec_id).first() is not None


def test_cp16_sqlite_disk_persistence_across_app_restart(tmp_path):
    """CP-16 (Persistencia sin ResourceWarning): Reportes y auditoría sobreviven al cierre y reinicio completo."""
    db_file = tmp_path / "persist_test.db"

    class DiskPersistConfig(Config):
        TESTING = True
        SEED_DEMO_DATA = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_file}"
        CSRF_ENABLED = False
        SESSION_COOKIE_SECURE = False
        UPLOAD_FOLDER = str(tmp_path / "uploads_disk")

    app1 = create_app(DiskPersistConfig)
    with app1.test_client() as c1:
        r = c1.post(
            "/reportar",
            data={
                "direccion_referencia": "Av. Persistencia Disco 777",
                "descripcion": "Reporte de prueba para validar persistencia real en disco SQLite.",
                "nivel_agua": "Critico",
                "latitud": "-41.4718",
                "longitud": "-72.9396",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        codigo_creado = r.headers["Location"].split("codigo=")[-1]
    with app1.app_context():
        db.session.remove()
        db.engine.dispose()

    app2 = create_app(DiskPersistConfig)
    with app2.app_context():
        rep = Reporte.query.filter_by(codigo=codigo_creado).first()
        assert rep is not None
        assert rep.direccion_referencia == "Av. Persistencia Disco 777"
        assert Auditoria.query.filter_by(accion="CREAR_REPORTE", entidad_id=rep.id).first() is not None
        db.session.remove()
        db.engine.dispose()


def test_cp17_owasp_a04_tracking_rate_limit_and_audit(client, app):
    """CP-17 (Negativo OWASP A04/A07): Límite anti-enumeración en /seguimiento (20 consultas/min -> 21va HTTP 429)."""
    for i in range(20):
        resp = client.get(f"/seguimiento?codigo=PM-2026-000000{i:02d}")
        assert resp.status_code == 200

    resp_blocked = client.get("/seguimiento?codigo=PM-2026-A1F4C201")
    assert resp_blocked.status_code == 429

    with app.app_context():
        assert Auditoria.query.filter_by(accion="TRACK_RATE_LIMIT").first() is not None


def test_cp18_csprng_secrets_and_defect_regression_proofs(app):
    """CP-18 (Seguridad CSPRNG + Regresión): Confirma uso de secrets.token_hex y demuestra fallos previos (DST/Euclidiana)."""
    src = inspect.getsource(generar_codigo_reporte)
    assert "secrets.token_hex" in src
    assert "random." not in src

    with app.app_context():
        codigos = {generar_codigo_reporte() for _ in range(50)}
        assert len(codigos) == 50

    # Demostración cuantitativa de I-02 (offset fijo UTC-3 erraba por 1 h en invierno austral frente a America/Santiago)
    evento_invierno_utc = datetime(2026, 6, 20, 15, 30, tzinfo=timezone.utc)
    offset_fijo_erroneo = evento_invierno_utc.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
    hora_chile_correcta = formatear_fecha_chile(evento_invierno_utc).split(" ")[1]
    assert offset_fijo_erroneo == "12:30" and hora_chile_correcta == "11:30"

    # Demostración cuantitativa de I-05 (euclidiana sobre grados distorsiona >22% en latitud -41.47° vs Haversine)
    lat1, lng1, lat2, lng2 = -41.4718, -72.9900, -41.4718, -72.8900
    euclid_aprox_km = math.hypot(lat2 - lat1, lng2 - lng1) * 111.32
    hav_real_km = distancia_haversine_km(lat1, lng1, lat2, lng2)
    assert euclid_aprox_km > 11.0 and 8.2 < hav_real_km < 8.5
