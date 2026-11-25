import os
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func

from app import db
from app.models import (
    NIVEL_A_SEVERIDAD,
    NIVELES_AGUA,
    Reporte,
    SectorGeografico,
)
from app.security import (
    get_client_ip,
    is_report_rate_limited,
    is_track_rate_limited,
    record_report_submission,
)
from app.utils import (
    coordenadas_en_puerto_montt,
    detectar_sector_por_coordenadas,
    fecha_local_a_utc_naive,
    generar_codigo_reporte,
    procesar_y_guardar_foto,
    registrar_auditoria,
    sanitizar_pii_texto,
)

public_bp = Blueprint("public", __name__)


@public_bp.route("/")
def index():
    sectores = SectorGeografico.query.filter_by(activo=True).order_by(SectorGeografico.nombre).all()
    activos_count = Reporte.query.filter_by(estado="Publicado/Validado").count()
    criticos_count = Reporte.query.filter_by(estado="Publicado/Validado", severidad="Critica").count()
    resueltos_count = Reporte.query.filter_by(estado="Resuelto/Despejado").count()
    sectores_alerta_count = (
        db.session.query(func.count(func.distinct(Reporte.sector_id)))
        .filter(Reporte.estado == "Publicado/Validado", Reporte.sector_id.isnot(None))
        .scalar()
        or 0
    )

    return render_template(
        "public/index.html",
        sectores=sectores,
        activos_count=activos_count,
        criticos_count=criticos_count,
        resueltos_count=resueltos_count,
        sectores_alerta_count=sectores_alerta_count,
        default_lat=current_app.config["DEFAULT_MAP_LAT"],
        default_lng=current_app.config["DEFAULT_MAP_LNG"],
        default_zoom=current_app.config["DEFAULT_MAP_ZOOM"],
    )


@public_bp.route("/evidencias/<path:filename>")
def serve_evidence(filename):
    """
    Sirve fotografías de evidencia con control de autorización (OWASP A01).
    Si el reporte no está publicado/resuelto, solo Operadores, Admin o el dueño pueden verlo.
    """
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        abort(404)
    reporte = Reporte.query.filter_by(foto_filename=safe_name).first_or_404()
    if reporte.estado not in ("Publicado/Validado", "Resuelto/Despejado"):
        es_dueno = current_user.is_authenticated and reporte.usuario_id == current_user.id
        es_operador = current_user.is_authenticated and current_user.is_operador
        codigo_autorizado = (request.args.get("codigo") or "").strip().upper() == reporte.codigo
        if not (es_dueno or es_operador or codigo_autorizado):
            abort(403)
    resp = send_from_directory(current_app.config["UPLOAD_FOLDER"], safe_name)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@public_bp.route("/reportar", methods=["GET", "POST"])
def report_form():
    sectores = SectorGeografico.query.filter_by(activo=True).order_by(SectorGeografico.nombre).all()

    if request.method == "POST":
        client_ip = get_client_ip()
        if is_report_rate_limited(client_ip):
            registrar_auditoria(
                accion="REPORTE_RATE_LIMIT",
                entidad="Seguridad",
                detalle=f"Límite de envío de reportes superado desde IP {client_ip}.",
            )
            db.session.commit()
            flash("Has enviado demasiados reportes seguidos. Por favor espera unos minutos.", "warning")
            return render_template(
                "public/report.html",
                sectores=sectores,
                niveles_agua=NIVELES_AGUA,
                default_lat=current_app.config["DEFAULT_MAP_LAT"],
                default_lng=current_app.config["DEFAULT_MAP_LNG"],
                form_data=request.form,
            ), 429

        direccion = (request.form.get("direccion_referencia") or "").strip()
        descripcion = (request.form.get("descripcion") or "").strip()
        nivel_agua = (request.form.get("nivel_agua") or "").strip()
        lat_raw = request.form.get("latitud")
        lng_raw = request.form.get("longitud")
        sector_id_raw = request.form.get("sector_id")
        reportante_nombre = (request.form.get("reportante_nombre") or "").strip()

        errores = []
        if not direccion or len(direccion) > 200:
            errores.append("Ingresa una dirección o punto de referencia válido (máx. 200 caracteres).")
        if not descripcion or len(descripcion) < 10 or len(descripcion) > 2000:
            errores.append("Describe brevemente el incidente (entre 10 y 2000 caracteres).")
        if len(reportante_nombre) > 100:
            errores.append("El nombre o alias no puede superar los 100 caracteres.")
        if nivel_agua not in NIVEL_A_SEVERIDAD:
            errores.append("Selecciona un nivel de agua válido.")

        try:
            lat = float(lat_raw)
            lng = float(lng_raw)
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError()
            if not coordenadas_en_puerto_montt(lat, lng):
                errores.append("La ubicación seleccionada está fuera del área urbana y comunal de Puerto Montt.")
        except (TypeError, ValueError):
            errores.append("Debes marcar una ubicación válida en el mapa o mediante GPS.")
            lat, lng = None, None

        foto_filename = None
        foto_file = request.files.get("foto")
        if foto_file and foto_file.filename:
            foto_filename, foto_error = procesar_y_guardar_foto(foto_file)
            if foto_error:
                errores.append(foto_error)

        if errores:
            for e in errores:
                flash(e, "danger")
            return render_template(
                "public/report.html",
                sectores=sectores,
                niveles_agua=NIVELES_AGUA,
                default_lat=current_app.config["DEFAULT_MAP_LAT"],
                default_lng=current_app.config["DEFAULT_MAP_LNG"],
                form_data=request.form,
            ), 400

        sector = None
        if sector_id_raw and sector_id_raw.isdigit():
            sector = db.session.get(SectorGeografico, int(sector_id_raw))
        if sector is None and lat is not None and lng is not None:
            sector = detectar_sector_por_coordenadas(lat, lng)

        codigo = generar_codigo_reporte()
        nombre_final = (
            current_user.nombre
            if current_user.is_authenticated
            else (reportante_nombre or "Ciudadano anónimo")
        )

        reporte = Reporte(
            codigo=codigo,
            direccion_referencia=sanitizar_pii_texto(direccion),
            descripcion=sanitizar_pii_texto(descripcion),
            latitud=lat,
            longitud=lng,
            nivel_agua=nivel_agua,
            severidad=NIVEL_A_SEVERIDAD[nivel_agua],
            foto_filename=foto_filename,
            estado="Pendiente",
            reportante_nombre=nombre_final,
            usuario_id=current_user.id if current_user.is_authenticated else None,
            sector_id=sector.id if sector else None,
        )
        db.session.add(reporte)
        db.session.flush()
        registrar_auditoria(
            accion="CREAR_REPORTE",
            entidad="Reporte",
            entidad_id=reporte.id,
            detalle=f"Reporte {reporte.codigo} creado en estado Pendiente ({reporte.severidad}).",
        )
        db.session.commit()
        record_report_submission(client_ip)

        flash(
            f"¡Reporte enviado con éxito! Código de seguimiento: {reporte.codigo}. "
            "Quedó en estado 'Pendiente' hasta ser validado por un Operador de Emergencias.",
            "success",
        )
        return redirect(url_for("public.track_report", codigo=reporte.codigo))

    return render_template(
        "public/report.html",
        sectores=sectores,
        niveles_agua=NIVELES_AGUA,
        default_lat=current_app.config["DEFAULT_MAP_LAT"],
        default_lng=current_app.config["DEFAULT_MAP_LNG"],
        form_data={},
    )


@public_bp.route("/seguimiento")
def track_report():
    codigo = (request.args.get("codigo") or "").strip().upper()
    reporte = None
    buscado = bool(codigo)
    if buscado:
        client_ip = get_client_ip()
        if is_track_rate_limited(client_ip):
            registrar_auditoria(
                accion="TRACK_RATE_LIMIT",
                entidad="Seguridad",
                detalle=f"Límite de consultas en /seguimiento superado desde IP {client_ip}.",
            )
            db.session.commit()
            flash("Has realizado demasiadas consultas en poco tiempo. Espera un minuto.", "warning")
            return render_template("public/track.html", reporte=None, codigo=codigo, buscado=False, puede_ver_detalle=False), 429
        reporte = Reporte.query.filter_by(codigo=codigo).first()

    puede_ver_detalle = False
    if reporte:
        if reporte.estado != "Rechazado/Duplicado":
            puede_ver_detalle = True
        elif current_user.is_authenticated and (current_user.is_operador or reporte.usuario_id == current_user.id):
            puede_ver_detalle = True

    return render_template(
        "public/track.html",
        reporte=reporte,
        codigo=codigo,
        buscado=buscado,
        puede_ver_detalle=puede_ver_detalle,
    )


@public_bp.route("/historial")
def history():
    sectores = SectorGeografico.query.filter_by(activo=True).order_by(SectorGeografico.nombre).all()
    sector_id = request.args.get("sector_id", type=int)
    severidad = (request.args.get("severidad") or "").strip()
    estado = (request.args.get("estado") or "").strip()
    fecha_desde = (request.args.get("fecha_desde") or "").strip()
    fecha_hasta = (request.args.get("fecha_hasta") or "").strip()

    estados_publicos = ["Publicado/Validado", "Resuelto/Despejado"]
    query = Reporte.query.filter(Reporte.estado.in_(estados_publicos))

    if sector_id:
        query = query.filter_by(sector_id=sector_id)
    if severidad:
        query = query.filter_by(severidad=severidad)
    if estado in estados_publicos:
        query = query.filter_by(estado=estado)
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

    reportes = query.order_by(Reporte.fecha_creacion.desc()).all()

    recurrencia = (
        db.session.query(
            SectorGeografico.id,
            SectorGeografico.nombre,
            SectorGeografico.nivel_riesgo,
            func.count(Reporte.id).label("total_incidentes"),
        )
        .outerjoin(
            Reporte,
            (Reporte.sector_id == SectorGeografico.id)
            & (Reporte.estado.in_(estados_publicos)),
        )
        .filter(SectorGeografico.activo.is_(True))
        .group_by(SectorGeografico.id)
        .order_by(func.count(Reporte.id).desc(), SectorGeografico.nombre.asc())
        .all()
    )

    max_incidentes = max((r.total_incidentes for r in recurrencia), default=1) or 1

    return render_template(
        "public/history.html",
        reportes=reportes,
        sectores=sectores,
        recurrencia=recurrencia,
        max_incidentes=max_incidentes,
        filtros={
            "sector_id": sector_id,
            "severidad": severidad,
            "estado": estado,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
    )


@public_bp.route("/api/reportes")
def api_reportes():
    """
    Endpoint GIS público (RF02 y RF04).
    Regla de negocio: por defecto SOLO devuelve reportes 'Publicado/Validado'.
    """
    sector_id = request.args.get("sector_id", type=int)
    severidad = (request.args.get("severidad") or "").strip()
    fecha_desde = (request.args.get("fecha_desde") or "").strip()
    fecha_hasta = (request.args.get("fecha_hasta") or "").strip()
    incluir_resueltos = request.args.get("incluir_resueltos") == "1"

    estados = ["Publicado/Validado"]
    if incluir_resueltos:
        estados.append("Resuelto/Despejado")

    query = Reporte.query.filter(Reporte.estado.in_(estados))

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

    reportes = query.order_by(Reporte.fecha_creacion.desc()).all()
    return jsonify([r.to_public_dict() for r in reportes])


@public_bp.route("/api/resumen")
def api_resumen():
    """KPIs en tiempo real para el encabezado del mapa público."""
    return jsonify({
        "activos": Reporte.query.filter_by(estado="Publicado/Validado").count(),
        "criticos": Reporte.query.filter_by(estado="Publicado/Validado", severidad="Critica").count(),
        "resueltos": Reporte.query.filter_by(estado="Resuelto/Despejado").count(),
        "sectores_con_alerta": (
            db.session.query(func.count(func.distinct(Reporte.sector_id)))
            .filter(Reporte.estado == "Publicado/Validado", Reporte.sector_id.isnot(None))
            .scalar()
            or 0
        ),
    })


@public_bp.route("/api/sectores")
def api_sectores():
    sectores = SectorGeografico.query.filter_by(activo=True).order_by(SectorGeografico.nombre).all()
    data = []
    for s in sectores:
        d = s.to_dict()
        d["incidentes_activos"] = Reporte.query.filter_by(
            sector_id=s.id, estado="Publicado/Validado"
        ).count()
        d["incidentes_historicos"] = Reporte.query.filter(
            Reporte.sector_id == s.id,
            Reporte.estado.in_(["Publicado/Validado", "Resuelto/Despejado"]),
        ).count()
        data.append(d)
    return jsonify(data)
