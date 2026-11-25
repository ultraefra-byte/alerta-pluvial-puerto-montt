import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _normalize_database_url(url: str) -> str:
    """Render y otros proveedores a veces entregan postgres:// en vez de postgresql://."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    IS_DEMO_ENV = FLASK_ENV != "production"
    SEED_DEMO_DATA = os.environ.get("SEED_DEMO_DATA", "1" if IS_DEMO_ENV else "0") == "1"
    TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "0") == "1"

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-local-key-change-in-production")
    _db_url = os.environ.get("DATABASE_URL")
    if _db_url:
        SQLALCHEMY_DATABASE_URI = _normalize_database_url(_db_url)
    else:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'inundaciones.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Seguridad de sesiones y formularios (OWASP A02 / A05 / A07)
    CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = FLASK_ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # Límite de carga (RNF02): aceptamos hasta 12MB en request para comprimir fotos
    # de smartphones modernos en el servidor y garantizar almacenamiento <= 2MB.
    # Almacenamiento fuera de static/ para impedir acceso público a reportes pendientes.
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024
    MAX_PHOTO_BYTES = 2 * 1024 * 1024  # 2 MB exactos (RNF02)
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "instance", "uploads")
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

    # Centro por defecto y cuadrante urbano/periurbano de Puerto Montt (excluye Puerto Varas y mar abierto)
    DEFAULT_MAP_LAT = -41.4693
    DEFAULT_MAP_LNG = -72.9424
    DEFAULT_MAP_ZOOM = 13
    PM_LAT_MIN = -41.515
    PM_LAT_MAX = -41.370
    PM_LNG_MIN = -73.030
    PM_LNG_MAX = -72.840


class TestingConfig(Config):
    TESTING = True
    FLASK_ENV = "testing"
    IS_DEMO_ENV = True
    SEED_DEMO_DATA = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
