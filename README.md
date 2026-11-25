# Sistema de Monitoreo y Reporte Ciudadano de Inundaciones Urbanas ("Alerta Pluvial Puerto Montt")

Prototipo web GIS desarrollado en **Python 3.13 (Flask 3.1)**, **SQLAlchemy 2.1**, **Bootstrap 5.3 (Mobile-First)** y **Leaflet.js 1.9 + OpenStreetMap**, correspondiente a la **Evaluación 3** de la asignatura **Ingeniería de Software (TI3043)** — Sede Puerto Montt (Sección Diurna 1), desarrollado por **Efraín Ramírez**.

---

## Requerimientos y Controles Implementados

| Código | Funcionalidad / Control | Estado |
| :--- | :--- | :--- |
| **RF01** | Envío de reporte ciudadano (anónimo o autenticado) validado dentro del cuadrante urbano de Puerto Montt (`[-41.515..-41.370] × [-73.030..-72.840]`, excluyendo Puerto Varas y aguas abiertas del Seno de Reloncaví), con estado inicial `Pendiente` y código aleatorio CSPRNG (`secrets.token_hex`). | Implementado |
| **RF02** | Mapa GIS interactivo público sobre OpenStreetMap (sin API key) con marcadores por severidad, polígonos de riesgo y sincronización horaria `America/Santiago`. Expone únicamente reportes `Publicado/Validado`. | Implementado |
| **RF03** | Panel de Operador con máquina de estados estricta (`TRANSICIONES_VALIDAS`), moderación fotográfica privada (`/evidencias/<archivo>`) y bitácora de auditoría. | Implementado |
| **RF04** | Filtros combinados por sector geográfico (Ray-Casting + desempate geodésico Haversine), severidad, estado y rango de fechas locales de Chile (`ZoneInfo("America/Santiago")`). | Implementado |
| **RNF01** | Diseño responsivo **Mobile-First** verificado sin desbordamiento horizontal en `375x812`, `390x844` y `768x1024` px. | Implementado |
| **RNF02** | Límite de carga HTTP (`MAX_CONTENT_LENGTH = 12 MB`, HTTP `413`), compresión en cliente (Canvas) y servidor (Pillow `<= 2 MB`), protección anti *decompression-bomb* y borrado completo de metadatos EXIF. | Implementado |
| **Privacidad y OWASP** | Aviso y consentimiento explícito Leyes N° 19.628 / N° 21.719 en `/registro`, sanitización automática de RUT/teléfono/correo en textos libres, hashing `scrypt`, expiración de sesión (`8 h`), rotación obligatoria de clave temporal (`/cambiar-clave`), CSRF `hmac.compare_digest`, 3 *rate limiters* (`429`), cabeceras `CSP`/`HSTS`/`nosniff` y hashes SRI `SHA-384`. | Implementado |

---

## Requisitos e Instalación Local (Windows / Linux / macOS)

1. Entrar al directorio del proyecto y crear el entorno virtual:
   ```powershell
   cd sistema_inundaciones
   python -m venv .venv
   ```

2. Activar el entorno virtual e instalar las dependencias fijadas y auditadas (`0` CVEs en `pip-audit`):
   ```powershell
   # Windows PowerShell
   .\.venv\Scripts\Activate.ps1
   # Linux / macOS: source .venv/bin/activate

   pip install -r requirements.txt
   ```

3. Configurar variables de entorno a partir de la plantilla (nunca subir `.env` al repositorio):
   ```powershell
   copy .env.example .env
   ```

4. Iniciar el servidor local (SQLite en `instance/inundaciones.db`):
   ```powershell
   python app.py
   ```
   Abrir en el navegador: **http://127.0.0.1:5000**

> **Nota sobre credenciales de demostración:** En modo desarrollo (`FLASK_ENV=development` y `SEED_DEMO_DATA=1`), `app/seed.py` inicializa 8 sectores de Puerto Montt y 3 cuentas ficticias (`admin@puertomontt.cl`, `operador@puertomontt.cl`, `ciudadano@ejemplo.cl`) cuyas claves se leen desde las variables definidas en `.env.example`. En producción (`FLASK_ENV=production`), `SEED_DEMO_DATA` queda desactivado por defecto (`0`).

---

## Ejecución de Pruebas y Auditoría de Seguridad

```powershell
# Suite automatizada completa (18 casos pytest + cobertura + 0 ResourceWarnings)
pytest --cov=app --cov-report=term-missing -v

# Análisis estático SAST (severidades Baja, Media y Alta) y auditoría de dependencias
bandit -r app/
pip-audit -r requirements.txt

# Suite End-to-End en navegador real (requiere servidor corriendo en 127.0.0.1:5000 y Microsoft Edge)
python tests/e2e_browser.py
```

---

## Despliegue Objetivo en PaaS Linux (Evaluación 4)

El proyecto incluye `Procfile` y `render.yaml` preparados para despliegue en Linux con `gunicorn app:app` y PostgreSQL (`DATABASE_URL`). En Windows el desarrollo local se ejecuta mediante `python app.py` dado que `gunicorn` emplea llamadas POSIX (`fcntl`/`fork`) exclusivas de sistemas Unix/Linux.
