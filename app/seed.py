import json
import os
from datetime import datetime, timedelta, timezone
from flask import current_app
from PIL import Image, ImageDraw

from app import db
from app.models import Auditoria, Reporte, SectorGeografico, Usuario

# Polígonos sin solapamiento aproximados sobre Puerto Montt (referencia académica)
SECTORES_PUERTO_MONTT = [
    {
        "nombre": "Centro / Costanera",
        "descripcion": "Zona céntrica, Av. Diego Portales, Plaza de Armas y borde costero con alta escorrentía pluvial.",
        "nivel_riesgo": "Alto",
        "lat_centro": -41.4718,
        "lng_centro": -72.9396,
        "poligono": [
            [-41.4660, -72.9480],
            [-41.4660, -72.9310],
            [-41.4765, -72.9310],
            [-41.4765, -72.9480],
        ],
    },
    {
        "nombre": "Alerce",
        "descripcion": "Sector norte de alta densidad residencial; históricamente presenta anegamientos en cruces y accesos.",
        "nivel_riesgo": "Alto",
        "lat_centro": -41.3945,
        "lng_centro": -72.9055,
        "poligono": [
            [-41.3800, -72.9260],
            [-41.3800, -72.8850],
            [-41.4120, -72.8850],
            [-41.4120, -72.9260],
        ],
    },
    {
        "nombre": "Mirasol",
        "descripcion": "Sector poniente alto con pendientes pronunciadas y acumulación de agua en ejes colectores.",
        "nivel_riesgo": "Alto",
        "lat_centro": -41.4745,
        "lng_centro": -72.9745,
        "poligono": [
            [-41.4630, -72.9920],
            [-41.4630, -72.9585],
            [-41.4768, -72.9585],
            [-41.4768, -72.9920],
        ],
    },
    {
        "nombre": "Angelmó / Chinquihue",
        "descripcion": "Borde costero poniente expuesto a marejadas combinadas con lluvias intensas.",
        "nivel_riesgo": "Alto",
        "lat_centro": -41.4855,
        "lng_centro": -72.9575,
        "poligono": [
            [-41.4770, -72.9720],
            [-41.4770, -72.9485],
            [-41.4960, -72.9485],
            [-41.4960, -72.9720],
        ],
    },
    {
        "nombre": "Pelluco",
        "descripcion": "Sector costero oriente y acceso a Carretera Austral, vulnerable a esteros y marejadas.",
        "nivel_riesgo": "Medio",
        "lat_centro": -41.4838,
        "lng_centro": -72.8980,
        "poligono": [
            [-41.4740, -72.9180],
            [-41.4740, -72.8780],
            [-41.4950, -72.8780],
            [-41.4950, -72.9180],
        ],
    },
    {
        "nombre": "Cardonal / Ruta 5",
        "descripcion": "Eje industrial y salida norte con pasos bajo nivel propensos a acumulación de agua.",
        "nivel_riesgo": "Alto",
        "lat_centro": -41.4515,
        "lng_centro": -72.9585,
        "poligono": [
            [-41.4400, -72.9750],
            [-41.4400, -72.9425],
            [-41.4625, -72.9425],
            [-41.4625, -72.9750],
        ],
    },
    {
        "nombre": "Valle Volcanes",
        "descripcion": "Sector residencial oriente con drenaje pluvial urbano; riesgo moderado en temporales prolongados.",
        "nivel_riesgo": "Medio",
        "lat_centro": -41.4545,
        "lng_centro": -72.9125,
        "poligono": [
            [-41.4430, -72.9295],
            [-41.4430, -72.8950],
            [-41.4655, -72.8950],
            [-41.4655, -72.9295],
        ],
    },
    {
        "nombre": "La Paloma",
        "descripcion": "Zona alta residencial con escurrimientos hacia la terraza intermedia.",
        "nivel_riesgo": "Bajo",
        "lat_centro": -41.4465,
        "lng_centro": -72.9360,
        "poligono": [
            [-41.4300, -72.9420],
            [-41.4300, -72.9300],
            [-41.4560, -72.9300],
            [-41.4560, -72.9420],
        ],
    },
]


def _ensure_sample_photo(upload_folder: str, filename: str) -> str:
    """Genera una imagen sintética de muestra para demostración académica (sin EXIF)."""
    os.makedirs(upload_folder, exist_ok=True)
    dest = os.path.join(upload_folder, filename)
    if not os.path.exists(dest):
        img = Image.new("RGB", (800, 480), color=(14, 74, 107))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 310, 800, 480], fill=(30, 58, 95))
        draw.rectangle([30, 30, 770, 120], fill=(11, 37, 64))
        draw.text((55, 55), "EVIDENCIA SIMULADA - ENTORNO ACADEMICO TI3043", fill=(255, 255, 255))
        draw.text((55, 82), "Av. Diego Portales / Sector Centro - Puerto Montt", fill=(186, 230, 253))
        draw.text((55, 360), "Simulacion de anegamiento pluvial en calzada urbana", fill=(226, 232, 240))
        img.save(dest, format="JPEG", quality=85, exif=b"")
    return filename


def seed_database():
    """Carga usuarios demo (claves desde env), sectores de Puerto Montt y reportes en los 5 estados."""
    if Usuario.query.first() is not None:
        return

    admin_pwd = os.environ.get("DEMO_ADMIN_PASSWORD", "Admin123!")
    oper_pwd = os.environ.get("DEMO_OPERATOR_PASSWORD", "Operador123!")
    cit_pwd = os.environ.get("DEMO_CITIZEN_PASSWORD", "Ciudadano123!")

    admin = Usuario(
        nombre="Administrador Municipal",
        email="admin@puertomontt.cl",
        rol="admin",
        activo=True,
    )
    admin.set_password(admin_pwd)

    operador = Usuario(
        nombre="Operador Emergencias",
        email="operador@puertomontt.cl",
        rol="operador",
        activo=True,
    )
    operador.set_password(oper_pwd)

    ciudadano = Usuario(
        nombre="Vecino Puerto Montt",
        email="ciudadano@ejemplo.cl",
        rol="ciudadano",
        activo=True,
    )
    ciudadano.set_password(cit_pwd)

    db.session.add_all([admin, operador, ciudadano])
    db.session.flush()

    sectores_map = {}
    for s in SECTORES_PUERTO_MONTT:
        sector = SectorGeografico(
            nombre=s["nombre"],
            descripcion=s["descripcion"],
            nivel_riesgo=s["nivel_riesgo"],
            lat_centro=s["lat_centro"],
            lng_centro=s["lng_centro"],
            poligono_json=json.dumps(s["poligono"]),
            activo=True,
        )
        db.session.add(sector)
        db.session.flush()
        sectores_map[s["nombre"]] = sector

    sample_photo = _ensure_sample_photo(current_app.config["UPLOAD_FOLDER"], "demo_costanera.jpg")

    ahora = datetime.now(timezone.utc)
    reportes_demo = [
        {
            "codigo": "PM-2026-A1F4C201",
            "direccion_referencia": "Av. Diego Portales altura Terminal de Buses",
            "descripcion": "Anegamiento severo en calzada sur por obstrucción de sumideros durante sistema frontal.",
            "latitud": -41.4742,
            "longitud": -72.9445,
            "nivel_agua": "Critico",
            "severidad": "Critica",
            "foto_filename": sample_photo,
            "estado": "Publicado/Validado",
            "nota_operador": "Confirmado en demo. Se sugiere evitar el tramo hacia Angelmó.",
            "reportante_nombre": "Vecino Puerto Montt",
            "usuario_id": ciudadano.id,
            "operador_id": operador.id,
            "sector_id": sectores_map["Centro / Costanera"].id,
            "horas_atras": 2,
        },
        {
            "codigo": "PM-2026-B3E9D412",
            "direccion_referencia": "Av. Transversal 2 con Gabriela Mistral, Alerce Sur",
            "descripcion": "Acumulación de agua sobre los 45 cm en cruce principal. Vehículos pequeños no logran cruzar.",
            "latitud": -41.3975,
            "longitud": -72.9040,
            "nivel_agua": "Alto",
            "severidad": "Alta",
            "foto_filename": None,
            "estado": "Publicado/Validado",
            "nota_operador": "Validado en escenario de prueba. Desvío habilitado.",
            "reportante_nombre": "Ciudadano anónimo",
            "usuario_id": None,
            "operador_id": operador.id,
            "sector_id": sectores_map["Alerce"].id,
            "horas_atras": 4,
        },
        {
            "codigo": "PM-2026-C7A2E803",
            "direccion_referencia": "Av. La Cruz con Los Notros, Mirasol",
            "descripcion": "Poza extensa en pista derecha que dificulta el paso peatonal y de locomoción colectiva.",
            "latitud": -41.4730,
            "longitud": -72.9720,
            "nivel_agua": "Moderado",
            "severidad": "Media",
            "foto_filename": None,
            "estado": "Publicado/Validado",
            "nota_operador": "Verificado en demo. Circular con precaución.",
            "reportante_nombre": "Vecino Puerto Montt",
            "usuario_id": ciudadano.id,
            "operador_id": operador.id,
            "sector_id": sectores_map["Mirasol"].id,
            "horas_atras": 5,
        },
        {
            "codigo": "PM-2026-D9B1F604",
            "direccion_referencia": "Paso bajo nivel Ruta 5 Sur, sector Cardonal",
            "descripcion": "Nivel de agua subiendo rápidamente en el paso inferior, posible corte inminente.",
            "latitud": -41.4525,
            "longitud": -72.9570,
            "nivel_agua": "Alto",
            "severidad": "Alta",
            "foto_filename": None,
            "estado": "En Verificación",
            "nota_operador": "En verificación por operador asignado.",
            "reportante_nombre": "Ciudadano anónimo",
            "usuario_id": None,
            "operador_id": operador.id,
            "sector_id": sectores_map["Cardonal / Ruta 5"].id,
            "horas_atras": 1,
        },
        {
            "codigo": "PM-2026-E4C8A705",
            "direccion_referencia": "Costanera Angelmó frente a caleta",
            "descripcion": "Agua lluvia mezclada con oleaje cubriendo la vereda costera.",
            "latitud": -41.4848,
            "longitud": -72.9548,
            "nivel_agua": "Moderado",
            "severidad": "Media",
            "foto_filename": None,
            "estado": "Pendiente",
            "nota_operador": None,
            "reportante_nombre": "Vecino Puerto Montt",
            "usuario_id": ciudadano.id,
            "operador_id": None,
            "sector_id": sectores_map["Angelmó / Chinquihue"].id,
            "horas_atras": 0.5,
        },
        {
            "codigo": "PM-2026-F2D5B306",
            "direccion_referencia": "Av. Juan Soler Manfredini, Balneario Pelluco",
            "descripcion": "Anegamiento leve por rebalse de alcantarilla de aguas lluvias tras lluvia nocturna.",
            "latitud": -41.4832,
            "longitud": -72.8995,
            "nivel_agua": "Bajo",
            "severidad": "Baja",
            "foto_filename": None,
            "estado": "Resuelto/Despejado",
            "nota_operador": "Sumideros despejados. Vía completamente habilitada.",
            "reportante_nombre": "Ciudadano anónimo",
            "usuario_id": None,
            "operador_id": operador.id,
            "sector_id": sectores_map["Pelluco"].id,
            "horas_atras": 26,
        },
        {
            "codigo": "PM-2026-91E4C507",
            "direccion_referencia": "Av. Cuarta Terraza, Valle Volcanes",
            "descripcion": "Acumulación en rotonda de acceso por hojas obstruyendo rejillas.",
            "latitud": -41.4540,
            "longitud": -72.9140,
            "nivel_agua": "Moderado",
            "severidad": "Media",
            "foto_filename": None,
            "estado": "Resuelto/Despejado",
            "nota_operador": "Limpieza completada. Tránsito normalizado.",
            "reportante_nombre": "Vecino Puerto Montt",
            "usuario_id": ciudadano.id,
            "operador_id": operador.id,
            "sector_id": sectores_map["Valle Volcanes"].id,
            "horas_atras": 48,
        },
        {
            "codigo": "PM-2026-78A3D908",
            "direccion_referencia": "Av. Presidente Ibáñez esquina Sargento Silva",
            "descripcion": "Aviso repetido sobre poza menor ya atendida en reporte anterior.",
            "latitud": -41.4595,
            "longitud": -72.9365,
            "nivel_agua": "Bajo",
            "severidad": "Baja",
            "foto_filename": None,
            "estado": "Rechazado/Duplicado",
            "nota_operador": "Descartado por duplicidad con incidente previo.",
            "reportante_nombre": "Ciudadano anónimo",
            "usuario_id": None,
            "operador_id": operador.id,
            "sector_id": sectores_map["La Paloma"].id,
            "horas_atras": 12,
        },
    ]

    for r in reportes_demo:
        creado = ahora - timedelta(hours=r["horas_atras"])
        rep = Reporte(
            codigo=r["codigo"],
            direccion_referencia=r["direccion_referencia"],
            descripcion=r["descripcion"],
            latitud=r["latitud"],
            longitud=r["longitud"],
            nivel_agua=r["nivel_agua"],
            severidad=r["severidad"],
            foto_filename=r["foto_filename"],
            estado=r["estado"],
            nota_operador=r["nota_operador"],
            reportante_nombre=r["reportante_nombre"],
            usuario_id=r["usuario_id"],
            operador_id=r["operador_id"],
            sector_id=r["sector_id"],
            fecha_creacion=creado,
            fecha_actualizacion=creado,
        )
        db.session.add(rep)

    db.session.add(
        Auditoria(
            usuario_id=admin.id,
            accion="INICIALIZAR_SISTEMA",
            entidad="Sistema",
            detalle="Carga inicial de sectores de Puerto Montt, usuarios RBAC y dataset de demostración.",
        )
    )
    db.session.commit()
