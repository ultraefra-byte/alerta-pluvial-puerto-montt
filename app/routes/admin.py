import json
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.models import (
    Auditoria,
    NIVELES_RIESGO_SECTOR,
    ROLES_VALIDOS,
    Reporte,
    SectorGeografico,
    Usuario,
)
from app.security import validar_politica_contrasena
from app.utils import registrar_auditoria, role_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/")
@role_required("admin")
def dashboard():
    stats = {
        "total_usuarios": Usuario.query.count(),
        "operadores": Usuario.query.filter_by(rol="operador", activo=True).count(),
        "sectores_activos": SectorGeografico.query.filter_by(activo=True).count(),
        "reportes_totales": Reporte.query.count(),
        "reportes_pendientes": Reporte.query.filter_by(estado="Pendiente").count(),
        "reportes_publicados": Reporte.query.filter_by(estado="Publicado/Validado").count(),
    }
    ultimas_auditorias = Auditoria.query.order_by(Auditoria.fecha.desc()).limit(10).all()
    return render_template("admin/dashboard.html", stats=stats, auditorias=ultimas_auditorias)


@admin_bp.route("/usuarios", methods=["GET", "POST"])
@role_required("admin")
def users():
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        rol = (request.form.get("rol") or "ciudadano").strip()

        errores = []
        if not nombre or len(nombre) < 3 or len(nombre) > 120:
            errores.append("El nombre debe tener entre 3 y 120 caracteres.")
        if not email or "@" not in email or len(email) > 150:
            errores.append("Ingresa un correo electrónico válido.")
        errores.extend(validar_politica_contrasena(password))
        if rol not in ROLES_VALIDOS:
            errores.append("Rol no válido.")
        if Usuario.query.filter_by(email=email).first():
            errores.append("Ya existe un usuario con ese correo.")

        if errores:
            for e in errores:
                flash(e, "danger")
        else:
            nuevo = Usuario(
                nombre=nombre,
                email=email,
                rol=rol,
                activo=True,
                acepta_privacidad=True,
                debe_cambiar_clave=True,
            )
            nuevo.set_password(password)
            db.session.add(nuevo)
            db.session.flush()
            registrar_auditoria(
                accion="CREAR_USUARIO",
                entidad="Usuario",
                entidad_id=nuevo.id,
                detalle=f"Usuario {nuevo.email} creado con rol '{nuevo.rol}'.",
            )
            db.session.commit()
            flash(f"Usuario {nuevo.nombre} ({nuevo.rol}) creado correctamente.", "success")
            return redirect(url_for("admin.users"))

    usuarios = Usuario.query.order_by(Usuario.fecha_creacion.desc()).all()
    return render_template("admin/users.html", usuarios=usuarios, roles=ROLES_VALIDOS)


@admin_bp.route("/usuarios/<int:user_id>/actualizar", methods=["POST"])
@role_required("admin")
def update_user(user_id):
    usuario = db.get_or_404(Usuario, user_id)
    nuevo_rol = (request.form.get("rol") or usuario.rol).strip()
    activo = request.form.get("activo") == "1"

    if usuario.id == current_user.id and (nuevo_rol != "admin" or not activo):
        flash("No puedes quitarte tu propio rol de administrador ni desactivar tu cuenta.", "warning")
        return redirect(url_for("admin.users"))

    if nuevo_rol not in ROLES_VALIDOS:
        flash("Rol no válido.", "danger")
        return redirect(url_for("admin.users"))

    detalle = f"Usuario {usuario.email}: rol '{usuario.rol}' -> '{nuevo_rol}', activo={activo}."
    usuario.rol = nuevo_rol
    usuario.activo = activo
    registrar_auditoria("ACTUALIZAR_USUARIO", "Usuario", usuario.id, detalle)
    db.session.commit()
    flash(f"Usuario {usuario.nombre} actualizado.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/sectores", methods=["GET", "POST"])
@role_required("admin")
def sectors():
    if request.method == "POST":
        sector_id = request.form.get("sector_id", type=int)
        nombre = (request.form.get("nombre") or "").strip()
        descripcion = (request.form.get("descripcion") or "").strip()
        nivel_riesgo = (request.form.get("nivel_riesgo") or "Medio").strip()
        poligono_raw = (request.form.get("poligono_json") or "").strip()
        activo = request.form.get("activo") == "1"

        try:
            lat_centro = float(request.form.get("lat_centro"))
            lng_centro = float(request.form.get("lng_centro"))
        except (TypeError, ValueError):
            flash("Latitud y longitud del centro del sector deben ser números válidos.", "danger")
            return redirect(url_for("admin.sectors"))

        poligono_validado = None
        if poligono_raw:
            try:
                parsed = json.loads(poligono_raw)
                if not isinstance(parsed, list) or len(parsed) < 3:
                    raise ValueError("El polígono debe tener al menos 3 puntos.")
                poligono_validado = json.dumps(parsed)
            except Exception:
                flash("El polígono GeoJSON/coordenadas no tiene un formato válido.", "danger")
                return redirect(url_for("admin.sectors"))

        if not nombre or nivel_riesgo not in NIVELES_RIESGO_SECTOR:
            flash("Nombre de sector y nivel de riesgo válidos son obligatorios.", "danger")
            return redirect(url_for("admin.sectors"))

        if sector_id:
            sector = db.get_or_404(SectorGeografico, sector_id)
            sector.nombre = nombre
            sector.descripcion = descripcion
            sector.nivel_riesgo = nivel_riesgo
            sector.lat_centro = lat_centro
            sector.lng_centro = lng_centro
            sector.poligono_json = poligono_validado
            sector.activo = activo
            registrar_auditoria(
                "EDITAR_SECTOR",
                "SectorGeografico",
                sector.id,
                f"Sector '{sector.nombre}' actualizado (riesgo={sector.nivel_riesgo}, activo={sector.activo}).",
            )
            db.session.commit()
            flash(f"Sector '{sector.nombre}' actualizado correctamente.", "success")
        else:
            if SectorGeografico.query.filter_by(nombre=nombre).first():
                flash("Ya existe un sector con ese nombre.", "danger")
                return redirect(url_for("admin.sectors"))
            sector = SectorGeografico(
                nombre=nombre,
                descripcion=descripcion,
                nivel_riesgo=nivel_riesgo,
                lat_centro=lat_centro,
                lng_centro=lng_centro,
                poligono_json=poligono_validado,
                activo=activo,
            )
            db.session.add(sector)
            db.session.flush()
            registrar_auditoria(
                "CREAR_SECTOR",
                "SectorGeografico",
                sector.id,
                f"Sector '{sector.nombre}' creado (riesgo={sector.nivel_riesgo}).",
            )
            db.session.commit()
            flash(f"Sector '{sector.nombre}' creado correctamente.", "success")

        return redirect(url_for("admin.sectors"))

    sectores = SectorGeografico.query.order_by(SectorGeografico.nombre).all()
    sectores_data = [s.to_dict() for s in sectores]
    return render_template(
        "admin/sectors.html",
        sectores=sectores,
        sectores_data=sectores_data,
        niveles_riesgo=NIVELES_RIESGO_SECTOR,
        default_lat=current_app.config["DEFAULT_MAP_LAT"],
        default_lng=current_app.config["DEFAULT_MAP_LNG"],
    )


@admin_bp.route("/auditoria")
@role_required("admin")
def audit():
    accion = (request.args.get("accion") or "").strip()
    query = Auditoria.query
    if accion:
        query = query.filter_by(accion=accion)
    registros = query.order_by(Auditoria.fecha.desc()).limit(150).all()
    acciones_unicas = [r[0] for r in db.session.query(Auditoria.accion).distinct().order_by(Auditoria.accion).all()]
    return render_template(
        "admin/audit.html",
        registros=registros,
        acciones=acciones_unicas,
        accion_actual=accion,
    )

