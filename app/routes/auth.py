from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.models import Reporte, Usuario
from app.security import (
    clear_failed_logins,
    get_client_ip,
    is_login_blocked,
    is_safe_redirect_url,
    record_failed_login,
    validar_politica_contrasena,
)
from app.utils import registrar_auditoria

auth_bp = Blueprint("auth", __name__)


def _redirect_by_role(user: Usuario):
    if user.is_admin:
        return redirect(url_for("admin.dashboard"))
    if user.rol == "operador":
        return redirect(url_for("operator.dashboard"))
    return redirect(url_for("auth.my_reports"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        client_ip = get_client_ip()

        if is_login_blocked(client_ip, email):
            registrar_auditoria(
                accion="LOGIN_BLOQUEADO",
                entidad="Seguridad",
                detalle=f"Bloqueo temporal tras múltiples intentos fallidos para '{email}' desde {client_ip}.",
            )
            db.session.commit()
            flash(
                "Demasiados intentos fallidos. Por seguridad, espera 5 minutos antes de volver a intentar.",
                "danger",
            )
            return render_template("auth/login.html"), 429

        user = Usuario.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if not user.activo:
                flash("Tu cuenta se encuentra desactivada. Contacta al Administrador.", "danger")
                return render_template("auth/login.html"), 403
            clear_failed_logins(client_ip, email)
            session.permanent = True
            login_user(user)
            registrar_auditoria(
                accion="LOGIN_EXITOSO",
                entidad="Usuario",
                entidad_id=user.id,
                detalle=f"Inicio de sesión correcto de '{user.email}' (rol {user.rol}).",
                usuario_id=user.id,
            )
            db.session.commit()
            if user.debe_cambiar_clave:
                flash("Debes actualizar tu contraseña temporal en tu primer inicio de sesión.", "warning")
                return redirect(url_for("auth.change_password"))
            flash(f"Bienvenido(a), {user.nombre}.", "success")
            next_page = request.args.get("next")
            if is_safe_redirect_url(next_page):
                return redirect(next_page)
            return _redirect_by_role(user)

        intentos = record_failed_login(client_ip, email)
        registrar_auditoria(
            accion="LOGIN_FALLIDO",
            entidad="Seguridad",
            detalle=f"Intento fallido #{intentos} para '{email}' desde {client_ip}.",
        )
        db.session.commit()
        flash("Correo o contraseña incorrectos.", "danger")
        return render_template("auth/login.html"), 401

    return render_template("auth/login.html")


@auth_bp.route("/registro", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        acepta_privacidad = request.form.get("acepta_privacidad") == "1"

        errores = []
        if not nombre or len(nombre) < 3 or len(nombre) > 120:
            errores.append("Ingresa tu nombre completo (entre 3 y 120 caracteres).")
        if not email or "@" not in email or len(email) > 150:
            errores.append("Ingresa un correo electrónico válido.")
        errores.extend(validar_politica_contrasena(password))
        if password != confirm:
            errores.append("Las contraseñas no coinciden.")
        if not acepta_privacidad:
            errores.append("Debes leer y aceptar el Aviso de Privacidad y tratamiento de datos personales.")
        if Usuario.query.filter_by(email=email).first():
            errores.append("Ya existe una cuenta registrada con ese correo.")

        if errores:
            for e in errores:
                flash(e, "danger")
            return render_template("auth/register.html", form_data=request.form), 400

        nuevo = Usuario(
            nombre=nombre,
            email=email,
            rol="ciudadano",
            activo=True,
            acepta_privacidad=True,
            debe_cambiar_clave=False,
        )
        nuevo.set_password(password)
        db.session.add(nuevo)
        db.session.flush()
        registrar_auditoria(
            accion="REGISTRO_CIUDADANO",
            entidad="Usuario",
            entidad_id=nuevo.id,
            detalle=f"Registro de ciudadano {nuevo.email}.",
            usuario_id=nuevo.id,
        )
        db.session.commit()

        session.permanent = True
        login_user(nuevo)
        flash("Cuenta creada correctamente. Ahora puedes hacer seguimiento a todos tus reportes.", "success")
        return redirect(url_for("auth.my_reports"))

    return render_template("auth/register.html", form_data={})


@auth_bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    logout_user()
    flash("Has cerrado sesión correctamente.", "info")
    return redirect(url_for("public.index"))


@auth_bp.route("/cambiar-clave", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        errores = validar_politica_contrasena(password)
        if password != confirm:
            errores.append("Las contraseñas no coinciden.")
        if current_user.check_password(password):
            errores.append("La nueva contraseña debe ser distinta a la contraseña temporal asignada.")
        if errores:
            for e in errores:
                flash(e, "danger")
            return render_template("auth/change_password.html"), 400

        current_user.set_password(password)
        current_user.debe_cambiar_clave = False
        registrar_auditoria(
            accion="CAMBIO_CLAVE_OBLIGATORIO",
            entidad="Usuario",
            entidad_id=current_user.id,
            detalle=f"Usuario '{current_user.email}' actualizó su contraseña temporal.",
            usuario_id=current_user.id,
        )
        db.session.commit()
        flash("Contraseña actualizada correctamente.", "success")
        return _redirect_by_role(current_user)

    return render_template("auth/change_password.html")


@auth_bp.route("/mis-reportes")
@login_required
def my_reports():
    reportes = (
        Reporte.query.filter_by(usuario_id=current_user.id)
        .order_by(Reporte.fecha_creacion.desc())
        .all()
    )
    return render_template("auth/my_reports.html", reportes=reportes)
