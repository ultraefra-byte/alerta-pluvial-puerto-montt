import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from PIL import Image, ImageDraw
import websocket

sys.path.insert(0, r"c:\Users\qrewt\Desktop\Ingeneria de software\sistema_inundaciones")

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE_URL = "http://127.0.0.1:5000"
OUT_DIR = r"c:\Users\qrewt\Desktop\Ingeneria de software\sistema_inundaciones\docs\evidencias"
os.makedirs(OUT_DIR, exist_ok=True)


class CDPClient:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=20)
        self.msg_id = 0

    def call(self, method: str, params=None):
        self.msg_id += 1
        cid = self.msg_id
        self.ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            data = json.loads(self.ws.recv())
            if data.get("id") == cid:
                if "error" in data:
                    raise RuntimeError(f"CDP Error {method}: {data['error']}")
                return data.get("result", {})

    def close(self):
        self.ws.close()


def wait_ready(cdp: CDPClient, extra=1.8):
    time.sleep(extra)
    for _ in range(20):
        res = cdp.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})
        if res.get("result", {}).get("value") == "complete":
            break
        time.sleep(0.3)
    time.sleep(1.0)


def set_vp(cdp: CDPClient, w: int, h: int, mobile=False):
    cdp.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})


def eval_js(cdp: CDPClient, expr: str):
    res = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    if "exceptionDetails" in res:
        raise RuntimeError(f"JS Exception: {res['exceptionDetails']}")
    return res.get("result", {}).get("value")


def shot(cdp: CDPClient, filename: str):
    res = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    dest = os.path.join(OUT_DIR, filename)
    with open(dest, "wb") as f:
        f.write(base64.b64decode(res["data"]))
    print(f"Captured {filename} ({os.path.getsize(dest)} bytes)")


def create_sample_upload_photo(path: str):
    img = Image.new("RGB", (1800, 1200), color=(22, 78, 99))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 720, 1800, 1200], fill=(15, 23, 42))
    draw.rectangle([60, 60, 1740, 260], fill=(11, 37, 64))
    draw.text((100, 110), "EVIDENCIA FOTOGRÁFICA DE PRUEBA E2E - PUERTO MONTT", fill=(255, 255, 255))
    draw.text((100, 165), "Simulación de anegamiento urbano en Av. Pacheco Altamirano (Angelmó)", fill=(186, 230, 253))
    draw.ellipse([700, 820, 1100, 1020], fill=(56, 189, 248))
    img.save(path, format="JPEG", quality=92)
    return os.path.getsize(path)


def login_browser(cdp: CDPClient, email: str, password: str):
    set_vp(cdp, 1366, 900, mobile=False)
    cdp.call("Page.navigate", {"url": f"{BASE_URL}/login"})
    wait_ready(cdp, 1.2)
    eval_js(
        cdp,
        f"""(() => {{
            const f = document.querySelector('form[method="POST"]');
            f.querySelector('input[name="email"]').value = {json.dumps(email)};
            f.querySelector('input[name="password"]').value = {json.dumps(password)};
            f.submit();
            return true;
        }})()""",
    )
    wait_ready(cdp, 1.8)


def logout_browser(cdp: CDPClient):
    ok = eval_js(
        cdp,
        """(() => {
            const f = document.querySelector('form[action="/logout"]');
            if (f) { f.submit(); return true; }
            return false;
        })()""",
    )
    if ok:
        wait_ready(cdp, 1.4)


def main():
    user_data = tempfile.mkdtemp(prefix="edge_e2e_")
    port = 9235
    tmp_photo = os.path.join(user_data, "anegamiento_e2e.jpg")
    orig_photo_bytes = create_sample_upload_photo(tmp_photo)

    proc = subprocess.Popen(
        [
            EDGE_PATH,
            "--headless=new",
            "--disable-gpu",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={user_data}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    results = {"orig_photo_bytes": orig_photo_bytes, "console_errors": []}
    try:
        targets = []
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                    if targets:
                        break
            except Exception:
                time.sleep(0.3)

        page_target = next((t for t in targets if t.get("type") == "page"), targets[0])
        cdp = CDPClient(page_target["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("DOM.enable")

        # CP-19: Mapa público + sincronización horaria Chile + filtro Alerce en JS real
        set_vp(cdp, 1366, 920)
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/"})
        wait_ready(cdp, 4.0)
        initial_count = eval_js(cdp, "document.getElementById('visible-count')?.textContent")
        sync_label = eval_js(cdp, "document.getElementById('last-sync-label')?.textContent")
        assert sync_label and "(Chile)" in sync_label, f"Etiqueta horaria JS sin zona Chile: {sync_label}"
        results["cp19_sync_label_chile"] = sync_label
        shot(cdp, "cap01_mapa_inicial.png")

        # Filtrar por Alerce en el select
        alerce_count = eval_js(
            cdp,
            """(async () => {
                const sel = document.getElementById('filter-sector');
                const opt = Array.from(sel.options).find(o => o.text.includes('Alerce'));
                sel.value = opt.value;
                sel.dispatchEvent(new Event('change'));
                await new Promise(r => setTimeout(r, 900));
                return document.getElementById('visible-count')?.textContent;
            })()""",
        )
        results["cp17_initial_visible"] = initial_count
        results["cp17_alerce_filtered_visible"] = alerce_count

        # CP-18: Viewport móvil (RNF01) en 375x812, 390x844 y 768x1024
        vp_checks = {}
        for w, h in [(375, 812), (390, 844), (768, 1024)]:
            set_vp(cdp, w, h, mobile=(w < 768))
            cdp.call("Page.navigate", {"url": f"{BASE_URL}/reportar"})
            wait_ready(cdp, 2.2)
            dims = eval_js(cdp, "({sw: document.documentElement.scrollWidth, iw: window.innerWidth})")
            vp_checks[f"{w}x{h}"] = dims
            assert dims["sw"] <= dims["iw"], f"Overflow horizontal en {w}x{h}: {dims}"
            if w == 390:
                shot(cdp, "cap03_reportar_mobile_390.png")
        results["cp18_responsive_viewports"] = vp_checks

        # CP-19: Flujo completo de reporte NUEVO CON FOTO desde navegador (Canvas -> /reportar -> /seguimiento -> /operador -> /)
        set_vp(cdp, 1366, 960, mobile=False)
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/reportar"})
        wait_ready(cdp, 3.0)

        # Simular clic en mapa Leaflet (dispara evento map click en coordenadas Angelmó)
        eval_js(
            cdp,
            """(() => {
                const mapEl = document.getElementById('map-report');
                const rect = mapEl.getBoundingClientRect();
                const ev = new MouseEvent('click', {
                    bubbles: true, cancelable: true,
                    clientX: rect.left + rect.width * 0.42,
                    clientY: rect.top + rect.height * 0.62
                });
                mapEl.dispatchEvent(ev);
                document.getElementById('direccion_referencia').value = 'Av. Pacheco Altamirano 250, Angelmó (Prueba E2E)';
                document.querySelector('input[name="nivel_agua"][value="Alto"]').checked = true;
                document.getElementById('descripcion').value = 'Anegamiento en calzada costera simulado durante prueba E2E con fotografía.';
                return {lat: document.getElementById('latitud').value, lng: document.getElementById('longitud').value};
            })()""",
        )

        # Subir foto usando CDP DOM.setFileInputFiles (dispara compresión real JS Canvas!)
        doc_root = cdp.call("DOM.getDocument", {"depth": -1})
        inp_node = cdp.call("DOM.querySelector", {"nodeId": doc_root["root"]["nodeId"], "selector": "#foto"})
        cdp.call("DOM.setFileInputFiles", {"nodeId": inp_node["nodeId"], "files": [tmp_photo]})
        time.sleep(1.5)
        foto_feedback = eval_js(cdp, "document.getElementById('foto-feedback')?.textContent")
        results["cp19_canvas_feedback"] = foto_feedback
        eval_js(cdp, "document.getElementById('foto-preview-wrap').scrollIntoView({block:'center'})")
        time.sleep(0.5)
        shot(cdp, "cap02_reportar_form_completo.png")

        # Enviar formulario real
        eval_js(cdp, "document.querySelector('form[enctype=\"multipart/form-data\"]').submit()")
        wait_ready(cdp, 3.0)
        track_url = eval_js(cdp, "window.location.href")
        m_code = re.search(r"PM-2026-[0-9A-F]{8}", track_url)
        assert m_code, f"No redirigió a seguimiento con código: {track_url}"
        new_code = m_code.group(0)
        results["cp19_new_code"] = new_code
        shot(cdp, "cap04_confirmacion_seguimiento.png")

        # Verificar que NO aparece aún en el mapa público
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/"})
        wait_ready(cdp, 2.0)
        public_has_pending = eval_js(cdp, f"document.body.innerText.includes({json.dumps(new_code)})")
        assert not public_has_pending, "BUG: Reporte Pendiente apareció en vista pública!"

        # Login Operador -> verificar cola Pendiente con foto -> abrir detalle -> validar -> verificar mapa público
        login_browser(cdp, "operador@puertomontt.cl", "Operador123!")
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/operador/"})
        wait_ready(cdp, 2.0)
        shot(cdp, "cap05_operador_cola_pendiente.png")

        # Buscar href del reporte recién creado
        detail_href = eval_js(
            cdp,
            f"""(() => {{
                const rows = Array.from(document.querySelectorAll('tbody tr'));
                const row = rows.find(r => r.innerText.includes({json.dumps(new_code)}));
                return row ? row.querySelector('a[href*="/operador/reporte/"]').getAttribute('href') : null;
            }})()""",
        )
        assert detail_href, f"No se encontró {new_code} en panel operador"
        cdp.call("Page.navigate", {"url": f"{BASE_URL}{detail_href}"})
        wait_ready(cdp, 3.0)
        eval_js(cdp, "window.scrollTo(0, 260)")
        time.sleep(0.4)
        shot(cdp, "cap06_operador_detalle_foto.png")

        # Cambiar estado a Publicado/Validado desde el formulario real del operador
        eval_js(
            cdp,
            """(() => {
                document.getElementById('estado').value = 'Publicado/Validado';
                document.getElementById('nota_operador').value = 'Validado en prueba E2E con respaldo fotográfico verificado.';
                document.querySelector('form[action*="/estado"]').submit();
            })()""",
        )
        wait_ready(cdp, 2.5)
        logout_browser(cdp)

        # Volver al mapa público y abrir popup del nuevo reporte publicado!
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/?focus={new_code}"})
        wait_ready(cdp, 4.0)
        published_visible = eval_js(cdp, f"document.getElementById('incident-list').innerText.includes('Pacheco Altamirano')")
        assert published_visible, "Reporte validado no apareció en mapa público!"
        results["cp19_published_visible_on_map"] = True
        eval_js(cdp, "window.scrollTo(0, 340)")
        time.sleep(0.6)
        shot(cdp, "cap07_mapa_reporte_publicado.png")

        # Capturar /historial y /registro
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/historial"})
        wait_ready(cdp, 2.0)
        shot(cdp, "cap08_historial.png")

        set_vp(cdp, 1366, 1080, mobile=False)
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/registro"})
        wait_ready(cdp, 1.5)
        shot(cdp, "cap09_registro.png")
        set_vp(cdp, 1366, 960, mobile=False)

        # CP-20: Login Ciudadano -> /mis-reportes; Login Admin -> /admin/usuarios, /admin/sectores, /admin/auditoria
        login_browser(cdp, "ciudadano@ejemplo.cl", "Ciudadano123!")
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/mis-reportes"})
        wait_ready(cdp, 1.5)
        shot(cdp, "cap10_mis_reportes.png")

        # Provocar un 403 real en navegador para que quede registrado en Auditoría
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/operador/"})
        wait_ready(cdp, 1.2)
        logout_browser(cdp)

        login_browser(cdp, "admin@puertomontt.cl", "Admin123!")
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/admin/usuarios"})
        wait_ready(cdp, 1.8)
        shot(cdp, "cap11_admin_usuarios.png")

        cdp.call("Page.navigate", {"url": f"{BASE_URL}/admin/sectores"})
        wait_ready(cdp, 3.8)
        shot(cdp, "cap12_admin_sectores.png")

        cdp.call("Page.navigate", {"url": f"{BASE_URL}/admin/auditoria"})
        wait_ready(cdp, 1.8)
        audit_text = eval_js(cdp, "document.querySelector('tbody').innerText")
        assert "CAMBIO_ESTADO_REPORTE" in audit_text and "ACCESO_DENEGADO_403" in audit_text
        results["cp20_audit_events_verified"] = True
        shot(cdp, "cap13_admin_auditoria.png")

        # Captura de fuente HTML /login mostrando campo csrf_token real sin contraseñas
        logout_browser(cdp)
        cdp.call("Page.navigate", {"url": f"{BASE_URL}/login"})
        wait_ready(cdp, 1.2)
        shot(cdp, "cap14_login_sin_claves.png")

        cdp.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(user_data, ignore_errors=True)

    with open(os.path.join(OUT_DIR, "e2e_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("E2E RESULTS:", json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()

