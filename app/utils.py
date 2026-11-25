import io
import math
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo
from flask import abort, current_app
from flask_login import current_user, login_required
from PIL import Image, ImageOps

from app import db
from app.models import Auditoria, Reporte, SectorGeografico

CHILE_TZ = ZoneInfo("America/Santiago")

# Protección contra decompression bombs al decodificar imágenes no confiables (OWASP)
Image.MAX_IMAGE_PIXELS = 25_000_000


def formatear_fecha_chile(dt, with_seconds: bool = False) -> str:
    """Convierte timestamps UTC a hora oficial de Chile continental (maneja DST UTC-3/UTC-4)."""
    if not dt:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(CHILE_TZ)
    fmt = "%d/%m/%Y %H:%M:%S" if with_seconds else "%d/%m/%Y %H:%M"
    return local_dt.strftime(fmt)


def fecha_local_a_utc_naive(fecha_str: str, fin_del_dia: bool = False):
    """Convierte YYYY-MM-DD (America/Santiago) a datetime UTC naive para consultas SQL."""
    dt_local = datetime.strptime(fecha_str, "%Y-%m-%d")
    if fin_del_dia:
        dt_local = dt_local.replace(hour=23, minute=59, second=59)
    dt_local = dt_local.replace(tzinfo=CHILE_TZ)
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def role_required(*roles):
    """Decorador RBAC para restringir rutas según rol del usuario."""
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.rol not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def registrar_auditoria(accion: str, entidad: str, entidad_id=None, detalle: str = "", usuario_id=None):
    uid = usuario_id
    if uid is None and current_user and current_user.is_authenticated:
        uid = current_user.id
    entry = Auditoria(
        usuario_id=uid,
        accion=accion,
        entidad=entidad,
        entidad_id=entidad_id,
        detalle=detalle,
    )
    db.session.add(entry)


def generar_codigo_reporte() -> str:
    """Genera un código único no enumerable para seguimiento ciudadano (ej. PM-2026-A3F9B12C)."""
    while True:
        candidato = f"PM-2026-{secrets.token_hex(4).upper()}"
        if not Reporte.query.filter_by(codigo=candidato).first():
            return candidato


_RE_RUT = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
_RE_PHONE = re.compile(r"(?:\+?56\s*9\s*\d{4}\s*\d{4}|\b9\d{8}\b)")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def sanitizar_pii_texto(texto: str) -> str:
    """Enmascara RUT, teléfonos móviles y correos en textos libres públicos (Ley 19.628 / Ley 21.719)."""
    if not texto:
        return ""
    limpio = _RE_EMAIL.sub("[CORREO OMITIDO]", texto)
    limpio = _RE_RUT.sub("[RUT OMITIDO]", limpio)
    limpio = _RE_PHONE.sub("[TELÉFONO OMITIDO]", limpio)
    return limpio


def coordenadas_en_puerto_montt(lat: float, lng: float) -> bool:
    """Valida cuadrante urbano/periurbano de Puerto Montt excluyendo Puerto Varas y aguas abiertas del Seno de Reloncaví."""
    lat_min = current_app.config.get("PM_LAT_MIN", -41.515)
    lat_max = current_app.config.get("PM_LAT_MAX", -41.370)
    lng_min = current_app.config.get("PM_LNG_MIN", -73.030)
    lng_max = current_app.config.get("PM_LNG_MAX", -72.840)
    if not ((lat_min <= lat <= lat_max) and (lng_min <= lng <= lng_max)):
        return False
    if lat < -41.490 and (-72.945 < lng < -72.905):
        return False
    return True


def distancia_haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calcula la distancia geodésica real en kilómetros entre dos coordenadas WGS84."""
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _allowed_extension(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def procesar_y_guardar_foto(file_storage):
    """
    Valida, elimina metadatos EXIF y comprime la fotografía subida (RNF02 <= 2 MB).
    Reduce dimensiones progresivamente si quality=35 aún supera max_bytes.
    Retorna (filename, error_message).
    """
    if not file_storage or not file_storage.filename:
        return None, None

    if not _allowed_extension(file_storage.filename):
        return None, "Formato de imagen no permitido. Usa JPG, PNG o WEBP."

    max_bytes = current_app.config.get("MAX_PHOTO_BYTES", 2 * 1024 * 1024)
    raw_bytes = file_storage.read()
    if not raw_bytes:
        return None, None

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
    except Exception:
        return None, "El archivo adjunto no es una imagen válida o supera los píxeles permitidos."

    max_dimension = 1600
    if max(img.size) > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    for _resize_pass in range(4):
        quality = 85
        while quality >= 35:
            output.seek(0)
            output.truncate(0)
            img.save(output, format="JPEG", quality=quality, optimize=True, exif=b"")
            if output.tell() <= max_bytes:
                break
            quality -= 10
        if output.tell() <= max_bytes or min(img.size) <= 400:
            break
        # Reducir dimensiones al 75% y reintentar compresión
        new_w = max(400, int(img.width * 0.75))
        new_h = max(400, int(img.height * 0.75))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    if output.tell() > max_bytes:
        return None, "La imagen supera el límite de 2 MB incluso tras la compresión."

    filename = f"rep_{uuid.uuid4().hex[:12]}.jpg"
    os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)
    dest_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    with open(dest_path, "wb") as f:
        f.write(output.getvalue())

    return filename, None


def punto_en_poligono(lat: float, lng: float, poligono: list) -> bool:
    """Algoritmo Ray Casting para verificar si (lat, lng) está dentro de un polígono."""
    if not poligono or len(poligono) < 3:
        return False
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i][0], poligono[i][1]
        yj, xj = poligono[j][0], poligono[j][1]
        intersecta = ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersecta:
            dentro = not dentro
        j = i
    return dentro


def detectar_sector_por_coordenadas(lat: float, lng: float):
    """
    Detecta el sector geográfico de Puerto Montt al que pertenecen las coordenadas.
    1) Busca polígonos contenedores vía Ray-Casting; si hay solapamiento desempata por
       menor distancia geodésica Haversine al centroide del sector.
    2) Si ningún polígono contiene el punto, asigna el sector activo más cercano <= 8 km.
    """
    sectores = SectorGeografico.query.filter_by(activo=True).all()
    contenedores = [
        sec for sec in sectores
        if (poly := sec.get_poligono()) and punto_en_poligono(lat, lng, poly)
    ]
    if len(contenedores) == 1:
        return contenedores[0]
    if len(contenedores) > 1:
        return min(contenedores, key=lambda s: distancia_haversine_km(lat, lng, s.lat_centro, s.lng_centro))

    mejor_sector = None
    mejor_dist = float("inf")
    for sector in sectores:
        dist_km = distancia_haversine_km(lat, lng, sector.lat_centro, sector.lng_centro)
        if dist_km < mejor_dist:
            mejor_dist = dist_km
            mejor_sector = sector

    if mejor_sector and mejor_dist <= 8.0:
        return mejor_sector
    return None
