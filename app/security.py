import hmac
import re
import secrets
import time
from collections import defaultdict
from flask import abort, current_app, request, session

# Almacenes en memoria por proceso para limitación de tasa (OWASP A07 / Anti-Spam)
_FAILED_LOGINS = defaultdict(list)
_REPORT_SUBMISSIONS = defaultdict(list)
_TRACK_LOOKUPS = defaultdict(list)

MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutos

MAX_REPORTS_PER_WINDOW = 10
REPORT_WINDOW_SECONDS = 600  # 10 minutos

MAX_TRACK_PER_WINDOW = 20
TRACK_WINDOW_SECONDS = 60  # 1 minuto
MAX_RATE_LIMIT_KEYS = 5000  # Cota máxima de llaves activas en RAM (anti-DoS en memoria)


def get_client_ip() -> str:
    """Obtiene la IP del cliente respetando X-Forwarded-For solo detrás de proxy confiable."""
    if current_app.config.get("TRUST_PROXY_HEADERS", False):
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def get_csrf_token() -> str:
    """Genera o retorna el token CSRF de la sesión actual."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    """Valida el token CSRF en solicitudes que modifican estado (POST/PUT/DELETE/PATCH)."""
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    expected = session.get("_csrf_token")
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not provided or not hmac.compare_digest(str(expected), str(provided)):
        abort(400, description="Token CSRF ausente o inválido.")


def _evict_if_oversized(store: dict, window_sec: int, now: float) -> None:
    """Poda global acotada cuando el diccionario supera MAX_RATE_LIMIT_KEYS (evita agotamiento de RAM)."""
    if len(store) <= MAX_RATE_LIMIT_KEYS:
        return
    expired = [k for k, vals in list(store.items()) if not any(now - t < window_sec for t in vals)]
    for k in expired:
        store.pop(k, None)
    if len(store) > MAX_RATE_LIMIT_KEYS:
        oldest_keys = sorted(store.keys(), key=lambda k: store[k][-1] if store[k] else 0.0)
        for k in oldest_keys[: len(store) - MAX_RATE_LIMIT_KEYS]:
            store.pop(k, None)


def _prune_and_check(store: dict, key, limit: int, window_sec: int) -> bool:
    now = time.time()
    _evict_if_oversized(store, window_sec, now)
    recent = [t for t in store.get(key, ()) if now - t < window_sec]
    if recent:
        store[key] = recent
    else:
        store.pop(key, None)
    return len(recent) >= limit


def _record_hit(store: dict, key, window_sec: int) -> int:
    now = time.time()
    _evict_if_oversized(store, window_sec, now)
    recent = [t for t in store.get(key, ()) if now - t < window_sec]
    recent.append(now)
    store[key] = recent
    return len(recent)


def is_login_blocked(ip: str, email: str) -> bool:
    return _prune_and_check(_FAILED_LOGINS, (ip or "unknown", (email or "").lower()), MAX_LOGIN_ATTEMPTS, LOGIN_WINDOW_SECONDS)


def record_failed_login(ip: str, email: str) -> int:
    return _record_hit(_FAILED_LOGINS, (ip or "unknown", (email or "").lower()), LOGIN_WINDOW_SECONDS)


def clear_failed_logins(ip: str, email: str) -> None:
    _FAILED_LOGINS.pop((ip or "unknown", (email or "").lower()), None)


def is_report_rate_limited(ip: str) -> bool:
    return _prune_and_check(_REPORT_SUBMISSIONS, ip or "unknown", MAX_REPORTS_PER_WINDOW, REPORT_WINDOW_SECONDS)


def record_report_submission(ip: str) -> int:
    return _record_hit(_REPORT_SUBMISSIONS, ip or "unknown", REPORT_WINDOW_SECONDS)


def is_track_rate_limited(ip: str) -> bool:
    blocked = _prune_and_check(_TRACK_LOOKUPS, ip or "unknown", MAX_TRACK_PER_WINDOW, TRACK_WINDOW_SECONDS)
    if not blocked:
        _record_hit(_TRACK_LOOKUPS, ip or "unknown", TRACK_WINDOW_SECONDS)
    return blocked


def reset_rate_limiter() -> None:
    """Limpia todos los contadores en memoria (útil para pruebas automatizadas)."""
    _FAILED_LOGINS.clear()
    _REPORT_SUBMISSIONS.clear()
    _TRACK_LOOKUPS.clear()


def is_safe_redirect_url(target: str) -> bool:
    """Previene Open Redirect (OWASP A01): solo permite rutas relativas internas."""
    if not target:
        return False
    return target.startswith("/") and not target.startswith("//") and "\\" not in target


def validar_politica_contrasena(password: str) -> list[str]:
    """Política mínima OWASP A07: >= 8 caracteres, mayúscula, minúscula y dígito."""
    errores = []
    if len(password or "") < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")
    if not re.search(r"[A-Z]", password or ""):
        errores.append("La contraseña debe incluir al menos una letra mayúscula.")
    if not re.search(r"[a-z]", password or ""):
        errores.append("La contraseña debe incluir al menos una letra minúscula.")
    if not re.search(r"\d", password or ""):
        errores.append("La contraseña debe incluir al menos un dígito numérico.")
    return errores
