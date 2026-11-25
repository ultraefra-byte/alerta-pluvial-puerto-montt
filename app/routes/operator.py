from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.models import ESTADOS_REPORTE, TRANSICIONES_VALIDAS, Reporte, SectorGeografico
from app.utils import fecha_local_a_utc_naive, registrar_auditoria, role_required, sanitizar_pii_texto

operator_bp = Blueprint("operator", __name__)


@operator_bp.route("/")
@role_required("operador", "admin")
def dashboard():
    estado = (request.args.get("estado") or "").strip()
    sector_id = request.args.get("sector_id", type=int)
    severidad = (request.args.get("severidad") or "").strip()
    busqueda = (request.args.get("q") or "").strip()
    fecha_desde = (request.args.get("fecha_desde") or "").strip()
    fecha_hasta = (request.args.get("fecha_hasta") or "").strip()

    query = Reporte.query
    if estado in ESTADOS_REPORTE:
        query = query.filter_by(estado=estado)
    if sector_id:
        query = query.filter_by(sector_id=sector_id)
    if severidad:
        query = query.filter_by(severidad=severidad)
    if fecha_desde:
        try:
            dt_desde = fecha_local_a_utc_naive(fecha_desde, fin_del_dia=False)
        except ValueError:
            dt_desde = None
        if dt_desde is not None:
            query = query.filter(Reporte.fecha_creacion >= dt_desde)
    if fecha_hasta:
        try:
            dt_hasta = fecha_local_a_utc_naive(fecha_hasta, fin_del_dia=True)
        except ValueError:
            dt_hasta = None
        if dt_hasta is not None:
            query = query.filter(Reporte.fecha_creacion <= dt_hasta)
    if busqueda:
        like_expr = f"%{busqueda}%"
        query = query.filter(
            (Reporte.codigo.ilike(like_expr))
            | (Reporte.direccion_referencia.ilike(like_expr))
            | (Reporte.descripcion.ilike(like_expr))
        )

    reportes = query.order_by(Reporte.fecha_creacion.desc()).all()
    sectores = SectorGeografico.query.order_by(SectorGeografico.nombre).all()

    contadores = {
        est: Reporte.query.filter_by(estado=est).count() for est in ESTADOS_REPORTE
    }

    return render_template(
        "operator/dashboard.html",
        reportes=reportes,
        sectores=sectores,
        estados=ESTADOS_REPORTE,
        contadores=contadores,
        filtros={
            "estado": estado,
            "sector_id": sector_id,
            "severidad": severidad,
            "q": busqueda,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
    )


@operator_bp.route("/reporte/<int:reporte_id>")
@role_required("operador", "admin")
def detail(reporte_id):
    reporte = db.get_or_404(Reporte, reporte_id)
    sectores = SectorGeografico.query.filter_by(activo=True).order_by(SectorGeografico.nombre).all()
    permitidos = sorted(TRANSICIONES_VALIDAS.get(reporte.estado, set()) | {reporte.estado})
    return render_template(
        "operator/detail.html",
        reporte=reporte,
        sectores=sectores,
        estados=permitidos,
    )


@operator_bp.route("/reporte/<int:reporte_id>/estado", methods=["POST"])
@role_required("operador", "admin")
def update_status(reporte_id):
    reporte = db.get_or_404(Reporte, reporte_id)
    nuevo_estado = (request.form.get("estado") or "").strip()
    nota_operador = (request.form.get("nota_operador") or "").strip()
    sector_id_raw = request.form.get("sector_id")

    if nuevo_estado not in ESTADOS_REPORTE:
        flash("Estado seleccionado no es válido.", "danger")
        return redirect(url_for("operator.detail", reporte_id=reporte.id)), 400

    estado_anterior = reporte.estado
    permitidos = TRANSICIONES_VALIDAS.get(estado_anterior, set())
    if nuevo_estado != estado_anterior and nuevo_estado not in permitidos:
        flash(
            f"Transición no permitida: un reporte en estado '{estado_anterior}' no puede pasar directamente a '{nuevo_estado}'.",
            "danger",
        )
        sectores = SectorGeografico.query.filter_by(activo=True).order_by(SectorGeografico.nombre).all()
        return render_template(
            "operator/detail.html",
            reporte=reporte,
            sectores=sectores,
            estados=sorted(permitidos | {estado_anterior}),
        ), 400

    nota_limpia = sanitizar_pii_texto(nota_operador)
    reporte.estado = nuevo_estado
    if "nota_operador" in request.form:
        reporte.nota_operador = nota_limpia or None
    reporte.operador_id = current_user.id

    if sector_id_raw and sector_id_raw.isdigit():
        sector = db.session.get(SectorGeografico, int(sector_id_raw))
        if sector:
            reporte.sector_id = sector.id

    registrar_auditoria(
        accion="CAMBIO_ESTADO_REPORTE",
        entidad="Reporte",
        entidad_id=reporte.id,
        detalle=(
            f"Reporte {reporte.codigo}: '{estado_anterior}' -> '{nuevo_estado}'. "
            f"Nota: {nota_operador or 'Sin observación'}"
        ),
    )
    db.session.commit()

    flash(f"Reporte {reporte.codigo} actualizado a '{nuevo_estado}'.", "success")
    volver = request.form.get("next")
    if volver == "dashboard":
        return redirect(url_for("operator.dashboard"))
    return redirect(url_for("operator.detail", reporte_id=reporte.id))
