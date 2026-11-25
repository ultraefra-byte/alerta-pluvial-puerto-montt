import json
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


def utcnow():
    return datetime.now(timezone.utc)


ROLES_VALIDOS = ("ciudadano", "operador", "admin")

ESTADOS_REPORTE = (
    "Pendiente",
    "En Verificación",
    "Publicado/Validado",
    "Rechazado/Duplicado",
    "Resuelto/Despejado",
)

TRANSICIONES_VALIDAS = {
    "Pendiente": {"En Verificación", "Publicado/Validado", "Rechazado/Duplicado"},
    "En Verificación": {"Publicado/Validado", "Rechazado/Duplicado", "Pendiente"},
    "Publicado/Validado": {"Resuelto/Despejado", "En Verificación", "Rechazado/Duplicado"},
    "Rechazado/Duplicado": {"En Verificación"},
    "Resuelto/Despejado": {"En Verificación", "Publicado/Validado"},
}

NIVELES_AGUA = (
    ("Bajo", "Bajo (< 15 cm - Poza superficial)", "Baja"),
    ("Moderado", "Moderado (15 - 40 cm - Dificulta tránsito)", "Media"),
    ("Alto", "Alto (40 - 80 cm - Riesgo para vehículos)", "Alta"),
    ("Critico", "Crítico (> 80 cm - Calle cortada / anegamiento severo)", "Critica"),
)

NIVEL_A_SEVERIDAD = {codigo: sev for codigo, _, sev in NIVELES_AGUA}

NIVELES_RIESGO_SECTOR = ("Bajo", "Medio", "Alto")


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="ciudadano")
    activo = db.Column(db.Boolean, nullable=False, default=True)
    acepta_privacidad = db.Column(db.Boolean, nullable=False, default=True)
    debe_cambiar_clave = db.Column(db.Boolean, nullable=False, default=False)
    fecha_creacion = db.Column(db.DateTime, nullable=False, default=utcnow)

    reportes_enviados = db.relationship(
        "Reporte",
        foreign_keys="Reporte.usuario_id",
        back_populates="usuario",
        lazy="dynamic",
    )
    reportes_validados = db.relationship(
        "Reporte",
        foreign_keys="Reporte.operador_id",
        back_populates="operador",
        lazy="dynamic",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:
        return self.activo

    @property
    def is_admin(self) -> bool:
        return self.rol == "admin"

    @property
    def is_operador(self) -> bool:
        return self.rol in ("operador", "admin")

    @property
    def is_ciudadano(self) -> bool:
        return self.rol == "ciudadano"

    def __repr__(self):
        return f"<Usuario {self.email} ({self.rol})>"


class SectorGeografico(db.Model):
    __tablename__ = "sectores_geograficos"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    descripcion = db.Column(db.String(300), nullable=True)
    nivel_riesgo = db.Column(db.String(20), nullable=False, default="Medio")
    lat_centro = db.Column(db.Float, nullable=False)
    lng_centro = db.Column(db.Float, nullable=False)
    poligono_json = db.Column(db.Text, nullable=True)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    reportes = db.relationship("Reporte", back_populates="sector", lazy="dynamic")

    def get_poligono(self):
        if not self.poligono_json:
            return []
        try:
            return json.loads(self.poligono_json)
        except (ValueError, TypeError):
            return []

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "descripcion": self.descripcion or "",
            "nivel_riesgo": self.nivel_riesgo,
            "lat_centro": self.lat_centro,
            "lng_centro": self.lng_centro,
            "poligono": self.get_poligono(),
            "activo": self.activo,
        }

    def __repr__(self):
        return f"<SectorGeografico {self.nombre} ({self.nivel_riesgo})>"


class Reporte(db.Model):
    __tablename__ = "reportes"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(25), unique=True, nullable=False, index=True)
    direccion_referencia = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    latitud = db.Column(db.Float, nullable=False)
    longitud = db.Column(db.Float, nullable=False)
    nivel_agua = db.Column(db.String(20), nullable=False)
    severidad = db.Column(db.String(20), nullable=False)
    foto_filename = db.Column(db.String(255), nullable=True)

    estado = db.Column(db.String(30), nullable=False, default="Pendiente", index=True)
    nota_operador = db.Column(db.Text, nullable=True)
    reportante_nombre = db.Column(db.String(100), nullable=True)

    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    operador_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    sector_id = db.Column(db.Integer, db.ForeignKey("sectores_geograficos.id"), nullable=True)

    fecha_creacion = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    fecha_actualizacion = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    usuario = db.relationship("Usuario", foreign_keys=[usuario_id], back_populates="reportes_enviados")
    operador = db.relationship("Usuario", foreign_keys=[operador_id], back_populates="reportes_validados")
    sector = db.relationship("SectorGeografico", back_populates="reportes")

    def to_public_dict(self):
        from app.utils import formatear_fecha_chile
        # Contrato público sanitizado: nunca expone usuario_id, email ni reportante_nombre
        return {
            "id": self.id,
            "codigo": self.codigo,
            "direccion_referencia": self.direccion_referencia,
            "descripcion": self.descripcion,
            "latitud": self.latitud,
            "longitud": self.longitud,
            "nivel_agua": self.nivel_agua,
            "severidad": self.severidad,
            "estado": self.estado,
            "sector_id": self.sector_id,
            "sector_nombre": self.sector.nombre if self.sector else "Sin sector asignado",
            "foto_url": f"/evidencias/{self.foto_filename}" if self.foto_filename else None,
            "nota_operador": self.nota_operador or "",
            "fecha_creacion": formatear_fecha_chile(self.fecha_creacion),
            "fecha_iso": self.fecha_creacion.isoformat(),
        }

    def __repr__(self):
        return f"<Reporte {self.codigo} [{self.estado}]>"


class Auditoria(db.Model):
    __tablename__ = "auditoria"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    accion = db.Column(db.String(60), nullable=False)
    entidad = db.Column(db.String(50), nullable=False)
    entidad_id = db.Column(db.Integer, nullable=True)
    detalle = db.Column(db.Text, nullable=True)
    fecha = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    usuario = db.relationship("Usuario")

    def __repr__(self):
        return f"<Auditoria {self.accion} {self.entidad}#{self.entidad_id}>"
