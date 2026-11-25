import os
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user

from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Debes iniciar sesión para acceder a esta sección."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import Usuario
    from app.security import get_csrf_token, validate_csrf
    from app.utils import formatear_fecha_chile, registrar_auditoria

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Usuario, int(user_id))

    @app.template_filter("fecha_chile")
    def fecha_chile_filter(dt, with_seconds=False):
        return formatear_fecha_chile(dt, with_seconds=with_seconds)

    @app.context_processor
    def inject_security_helpers():
        return {
            "csrf_token": get_csrf_token,
            "is_demo_env": app.config.get("IS_DEMO_ENV", True),
        }

    @app.before_request
    def check_csrf_and_password_rotation():
        validate_csrf()
        if current_user.is_authenticated and getattr(current_user, "debe_cambiar_clave", False):
            if request.endpoint not in ("auth.change_password", "auth.logout", "static"):
                flash("Por seguridad, debes cambiar tu contraseña temporal antes de continuar.", "warning")
                return redirect(url_for("auth.change_password"))

    @app.after_request
    def add_security_headers(response):
        """Cabeceras de seguridad HTTP (OWASP A05: Security Misconfiguration)."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(self)")
        if request.is_secure or app.config.get("FLASK_ENV") == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
            "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://*.tile.openstreetmap.fr "
            "https://server.arcgisonline.com https://unpkg.com; "
            "connect-src 'self'; "
            "frame-ancestors 'self';",
        )
        return response

    from app.routes.public import public_bp
    from app.routes.auth import auth_bp
    from app.routes.operator import operator_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(operator_bp, url_prefix="/operador")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    with app.app_context():
        db.create_all()
        if not app.config.get("TESTING", False) and app.config.get("SEED_DEMO_DATA", False):
            from app.seed import seed_database
            seed_database()

    @app.errorhandler(400)
    def bad_request(error):
        if "CSRF" in str(getattr(error, "description", "")):
            registrar_auditoria(
                accion="CSRF_INVALIDO",
                entidad="Seguridad",
                detalle=f"Solicitud rechazada por token CSRF inválido en {request.path}.",
            )
            db.session.commit()
        return render_template("errors/400.html", detalle=getattr(error, "description", "")), 400

    @app.errorhandler(403)
    def forbidden(error):
        if current_user.is_authenticated:
            registrar_auditoria(
                accion="ACCESO_DENEGADO_403",
                entidad="Seguridad",
                detalle=f"Usuario '{current_user.email}' ({current_user.rol}) intentó acceder a {request.path}.",
            )
            db.session.commit()
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def file_too_large(error):
        return render_template(
            "errors/400.html",
            detalle="La solicitud supera el límite máximo permitido por el servidor (MAX_CONTENT_LENGTH = 12 MB).",
        ), 413

    return app
