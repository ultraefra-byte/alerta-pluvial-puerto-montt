document.addEventListener("DOMContentLoaded", () => {
  const mapEl = document.getElementById("map-admin-sectors");
  if (!mapEl) return;

  const dataScript = document.getElementById("sectores-data");
  const sectores = JSON.parse(dataScript ? dataScript.textContent : "[]");
  const defaultLat = parseFloat(mapEl.dataset.lat) || -41.4693;
  const defaultLng = parseFloat(mapEl.dataset.lng) || -72.9424;

  const map = L.map("map-admin-sectors").setView([defaultLat, defaultLng], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);

  const existingLayer = L.layerGroup().addTo(map);
  const drawLayer = L.layerGroup().addTo(map);
  let draftPoints = [];

  const RISK_COLORS = { Bajo: "#22c55e", Medio: "#f59e0b", Alto: "#ef4444" };

  function renderExistingSectors() {
    existingLayer.clearLayers();
    sectores.forEach((sec) => {
      if (sec.poligono && sec.poligono.length >= 3) {
        const color = RISK_COLORS[sec.nivel_riesgo] || "#64748b";
        L.polygon(sec.poligono, {
          color,
          weight: 2,
          fillOpacity: sec.activo ? 0.16 : 0.04,
          dashArray: sec.activo ? null : "4 4",
        })
          .bindTooltip(`${sec.nombre} (${sec.nivel_riesgo})`)
          .addTo(existingLayer);
      }
    });
  }

  function renderDraftPolygon() {
    drawLayer.clearLayers();
    draftPoints.forEach((pt, idx) => {
      L.circleMarker(pt, { radius: 5, color: "#0b2540", fillColor: "#06b6d4", fillOpacity: 1 })
        .bindTooltip(`Vértice ${idx + 1}`)
        .addTo(drawLayer);
    });
    if (draftPoints.length >= 2) {
      L.polygon(draftPoints, { color: "#0e4a6b", fillOpacity: 0.25 }).addTo(drawLayer);
    }

    const polyInput = document.getElementById("poligono_json");
    if (polyInput) {
      polyInput.value = draftPoints.length >= 3 ? JSON.stringify(draftPoints) : "";
    }
    if (draftPoints.length > 0) {
      const avgLat = draftPoints.reduce((s, p) => s + p[0], 0) / draftPoints.length;
      const avgLng = draftPoints.reduce((s, p) => s + p[1], 0) / draftPoints.length;
      document.getElementById("lat_centro").value = avgLat.toFixed(6);
      document.getElementById("lng_centro").value = avgLng.toFixed(6);
    }
  }

  map.on("click", (e) => {
    draftPoints.push([Number(e.latlng.lat.toFixed(5)), Number(e.latlng.lng.toFixed(5))]);
    renderDraftPolygon();
  });

  document.getElementById("btn-undo-poly")?.addEventListener("click", () => {
    draftPoints.pop();
    renderDraftPolygon();
  });

  document.getElementById("btn-clear-poly")?.addEventListener("click", () => {
    draftPoints = [];
    renderDraftPolygon();
  });

  document.getElementById("btn-new-sector")?.addEventListener("click", () => {
    document.getElementById("sector_id").value = "";
    document.getElementById("nombre").value = "";
    document.getElementById("descripcion").value = "";
    document.getElementById("nivel_riesgo").value = "Medio";
    document.getElementById("lat_centro").value = defaultLat;
    document.getElementById("lng_centro").value = defaultLng;
    document.getElementById("activo").checked = true;
    draftPoints = [];
    renderDraftPolygon();
    document.getElementById("sector-form-title").textContent = "Crear Nuevo Sector Geográfico";
  });

  document.querySelectorAll(".btn-edit-sector").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = parseInt(btn.dataset.id, 10);
      const sec = sectores.find((s) => s.id === id);
      if (!sec) return;
      document.getElementById("sector_id").value = sec.id;
      document.getElementById("nombre").value = sec.nombre;
      document.getElementById("descripcion").value = sec.descripcion || "";
      document.getElementById("nivel_riesgo").value = sec.nivel_riesgo;
      document.getElementById("lat_centro").value = sec.lat_centro;
      document.getElementById("lng_centro").value = sec.lng_centro;
      document.getElementById("activo").checked = !!sec.activo;
      draftPoints = Array.isArray(sec.poligono) ? [...sec.poligono] : [];
      renderDraftPolygon();
      document.getElementById("sector-form-title").textContent = `Editando sector: ${sec.nombre}`;
      map.setView([sec.lat_centro, sec.lng_centro], 13);
    });
  });

  renderExistingSectors();
});
