document.addEventListener("DOMContentLoaded", () => {
  const mapEl = document.getElementById("map-public");
  if (!mapEl) return;

  const defaultLat = parseFloat(mapEl.dataset.lat) || -41.4693;
  const defaultLng = parseFloat(mapEl.dataset.lng) || -72.9424;
  const defaultZoom = parseInt(mapEl.dataset.zoom, 10) || 13;

  const map = L.map("map-public", { zoomControl: true }).setView([defaultLat, defaultLng], defaultZoom);

  // Capas base GIS 100% abiertas (sin API key requerida)
  const osmStandard = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contribuyentes',
  });
  const osmHot = L.tileLayer("https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contribuyentes, Tiles style by Humanitarian OpenStreetMap Team',
  });
  const satelliteLayer = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Tiles &copy; Esri &mdash; Fuente: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, y la comunidad GIS" }
  );

  osmStandard.addTo(map);
  L.control
    .layers(
      { "Mapa Estándar (OSM)": osmStandard, "Mapa Humanitario (HOT)": osmHot, "Vista Satelital (Esri)": satelliteLayer },
      {},
      { position: "topright" }
    )
    .addTo(map);

  const sectorsLayer = L.layerGroup().addTo(map);
  const markersLayer = L.layerGroup().addTo(map);
  let userLocationMarker = null;

  const SEVERITY_COLORS = {
    Baja: "#0ea5e9",
    Media: "#f59e0b",
    Alta: "#f97316",
    Critica: "#dc2626",
  };

  const RISK_COLORS = {
    Bajo: "#22c55e",
    Medio: "#f59e0b",
    Alto: "#ef4444",
  };

  let sectoresCache = [];
  let reportesCache = [];
  let markersByCode = {};
  const urlParams = new URLSearchParams(window.location.search);
  let pendingFocusCode = urlParams.get("focus") || null;

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function createSeverityIcon(severidad, estado) {
    const isResolved = estado === "Resuelto/Despejado";
    const color = isResolved ? "#0f766e" : SEVERITY_COLORS[severidad] || "#0e4a6b";
    const pulseClass = severidad === "Critica" && !isResolved ? "marker-critical-pulse" : "";
    const iconHtml = isResolved
      ? '<i class="bi bi-check" style="color:#fff;font-size:13px;line-height:1;"></i>'
      : '<i class="bi bi-droplet-fill" style="color:#fff;font-size:10px;line-height:1;"></i>';

    return L.divIcon({
      className: "custom-flood-marker",
      html: `<div class="${pulseClass}" style="
        width: 26px; height: 26px; border-radius: 50%;
        background: ${color}; border: 3px solid #ffffff;
        box-shadow: 0 3px 10px rgba(0,0,0,0.35);
        display: flex; align-items: center; justify-content: center;
      ">${iconHtml}</div>`,
      iconSize: [26, 26],
      iconAnchor: [13, 13],
      popupAnchor: [0, -13],
    });
  }

  async function refreshKpis() {
    try {
      const res = await fetch("/api/resumen");
      if (!res.ok) return;
      const data = await res.json();
      const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.textContent = v;
      };
      setVal("kpi-activos", data.activos);
      setVal("kpi-criticos", data.criticos);
      setVal("kpi-sectores", data.sectores_con_alerta);
      setVal("kpi-resueltos", data.resueltos);
    } catch {
      // silencioso
    }
  }

  async function loadSectors() {
    const res = await fetch("/api/sectores");
    sectoresCache = await res.json();
    renderSectors();
  }

  function renderSectors() {
    sectorsLayer.clearLayers();
    const showZones = document.getElementById("toggle-zonas")?.checked ?? true;
    if (!showZones) return;

    const selectedSectorId = document.getElementById("filter-sector")?.value || "";

    sectoresCache.forEach((sec) => {
      if (!sec.poligono || sec.poligono.length < 3) return;
      const isSelected = selectedSectorId && String(sec.id) === selectedSectorId;
      const color = RISK_COLORS[sec.nivel_riesgo] || "#64748b";
      const poly = L.polygon(sec.poligono, {
        color: color,
        weight: isSelected ? 3 : 1.5,
        fillColor: color,
        fillOpacity: isSelected ? 0.2 : 0.08,
        dashArray: isSelected ? null : "5 5",
      });
      poly.bindPopup(`
        <div style="min-width: 210px;">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <h6 class="mb-0 fw-bold">${escapeHtml(sec.nombre)}</h6>
            <span class="badge rounded-pill" style="background:${color};">Riesgo ${escapeHtml(sec.nivel_riesgo)}</span>
          </div>
          <p class="small mb-2 text-muted">${escapeHtml(sec.descripcion)}</p>
          <div class="d-flex gap-3 small pt-2 border-top">
            <div><strong class="text-primary">${sec.incidentes_activos}</strong> activos</div>
            <div><strong class="text-secondary">${sec.incidentes_historicos}</strong> históricos</div>
          </div>
        </div>
      `);
      poly.addTo(sectorsLayer);

      if (isSelected) {
        map.fitBounds(poly.getBounds(), { padding: [30, 30] });
      }
    });
  }

  function renderIncidentList(reportes) {
    const listContainer = document.getElementById("incident-list");
    const countBadge = document.getElementById("visible-count");
    const quickSearch = (document.getElementById("quick-search")?.value || "").trim().toLowerCase();

    const filtered = quickSearch
      ? reportes.filter(
          (r) =>
            r.direccion_referencia.toLowerCase().includes(quickSearch) ||
            r.sector_nombre.toLowerCase().includes(quickSearch) ||
            r.codigo.toLowerCase().includes(quickSearch)
        )
      : reportes;

    if (countBadge) countBadge.textContent = filtered.length;
    if (!listContainer) return;

    if (filtered.length === 0) {
      listContainer.innerHTML = `
        <div class="text-center text-muted py-5 px-3">
          <i class="bi bi-check-circle-fill fs-2 d-block mb-2 text-success opacity-75"></i>
          <div class="fw-semibold text-dark small mb-1">Sin alertas activas aquí</div>
          <div class="small">No se encontraron incidentes validados para los filtros actuales.</div>
        </div>`;
      return;
    }

    listContainer.innerHTML = "";
    filtered.forEach((rep) => {
      const isResolved = rep.estado === "Resuelto/Despejado";
      const accentColor = isResolved ? "#0f766e" : SEVERITY_COLORS[rep.severidad] || "#0e4a6b";
      const item = document.createElement("button");
      item.type = "button";
      item.className = "list-group-item list-group-item-action text-start incident-item d-flex gap-2 align-items-start";
      item.innerHTML = `
        <span class="incident-accent-bar" style="background:${accentColor};"></span>
        <div class="flex-grow-1 min-w-0">
          <div class="d-flex justify-content-between align-items-start gap-2 mb-1">
            <span class="fw-semibold small text-dark" style="line-height: 1.3; word-break: break-word;">${escapeHtml(rep.direccion_referencia)}</span>
            <span class="badge badge-pill-soft badge-sev-${escapeHtml(rep.severidad)} flex-shrink-0">${escapeHtml(rep.severidad)}</span>
          </div>
          <div class="small text-muted d-flex flex-wrap gap-2 align-items-center" style="font-size:0.76rem;">
            <span><i class="bi bi-geo-alt me-1"></i>${escapeHtml(rep.sector_nombre)}</span>
            <span>·</span>
            <span><i class="bi bi-clock me-1"></i>${escapeHtml(rep.fecha_creacion)}</span>
          </div>
          ${isResolved ? '<span class="badge badge-pill-soft badge-estado-Resuelto-Despejado mt-1">Resuelto</span>' : ""}
        </div>
      `;
      item.addEventListener("click", () => {
        const marker = markersByCode[rep.codigo];
        map.flyTo([rep.latitud, rep.longitud], 15, { duration: 0.7 });
        if (marker) {
          setTimeout(() => marker.openPopup(), 450);
        }
      });
      listContainer.appendChild(item);
    });
  }

  async function loadReportes() {
    const params = new URLSearchParams();
    const sectorId = document.getElementById("filter-sector")?.value;
    const severidad = document.getElementById("filter-severidad")?.value;
    const fechaDesde = document.getElementById("filter-desde")?.value;
    const fechaHasta = document.getElementById("filter-hasta")?.value;
    const incluirResueltos = document.getElementById("toggle-resueltos")?.checked;

    if (sectorId) params.set("sector_id", sectorId);
    if (severidad) params.set("severidad", severidad);
    if (fechaDesde) params.set("fecha_desde", fechaDesde);
    if (fechaHasta) params.set("fecha_hasta", fechaHasta);
    if (incluirResueltos) params.set("incluir_resueltos", "1");

    const res = await fetch(`/api/reportes?${params.toString()}`);
    reportesCache = await res.json();

    markersLayer.clearLayers();
    markersByCode = {};

    reportesCache.forEach((rep) => {
      const marker = L.marker([rep.latitud, rep.longitud], {
        icon: createSeverityIcon(rep.severidad, rep.estado),
      });

      const photoHtml = rep.foto_url
        ? `<img src="${rep.foto_url}" alt="Foto del reporte" class="popup-photo">`
        : "";
      const noteHtml = rep.nota_operador
        ? `<div class="p-2 mt-2 rounded bg-light border small"><strong class="text-primary"><i class="bi bi-shield-check me-1"></i>Operador:</strong> ${escapeHtml(rep.nota_operador)}</div>`
        : "";

      marker.bindTooltip(`${rep.direccion_referencia} (${rep.severidad})`, { direction: "top", offset: [0, -10] });
      marker.bindPopup(`
        <div style="min-width: 245px; max-width: 285px;">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <span class="badge badge-pill-soft badge-sev-${escapeHtml(rep.severidad)}">Severidad ${escapeHtml(rep.severidad)}</span>
            <code class="small text-muted">${escapeHtml(rep.codigo)}</code>
          </div>
          <h6 class="fw-bold mb-1 mt-1">${escapeHtml(rep.direccion_referencia)}</h6>
          <div class="small text-muted mb-2">
            <i class="bi bi-geo-alt-fill text-primary"></i> ${escapeHtml(rep.sector_nombre)} &middot; Agua: <strong>${escapeHtml(rep.nivel_agua)}</strong>
          </div>
          <p class="small mb-1 text-body">${escapeHtml(rep.descripcion)}</p>
          ${photoHtml}
          ${noteHtml}
          <div class="d-flex justify-content-between align-items-center small text-muted mt-2 pt-2 border-top">
            <span><i class="bi bi-clock me-1"></i>${escapeHtml(rep.fecha_creacion)}</span>
            <a href="/seguimiento?codigo=${encodeURIComponent(rep.codigo)}" class="fw-semibold text-decoration-none">Detalle &rarr;</a>
          </div>
        </div>
      `);
      marker.addTo(markersLayer);
      markersByCode[rep.codigo] = marker;
    });

    renderIncidentList(reportesCache);

    const lastSyncEl = document.getElementById("last-sync-label");
    if (lastSyncEl) {
      const now = new Date();
      const chileTime = now.toLocaleTimeString("es-CL", {
        timeZone: "America/Santiago",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      lastSyncEl.textContent = `Actualizado ${chileTime} (Chile)`;
    }

    if (pendingFocusCode && markersByCode[pendingFocusCode]) {
      const target = reportesCache.find((r) => r.codigo === pendingFocusCode);
      if (target) {
        map.flyTo([target.latitud, target.longitud], 15, { duration: 0.7 });
        setTimeout(() => markersByCode[pendingFocusCode]?.openPopup(), 500);
      }
      pendingFocusCode = null;
    }
  }

  ["filter-sector", "filter-severidad", "filter-desde", "filter-hasta", "toggle-resueltos"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("change", () => {
        if (id === "filter-sector") renderSectors();
        loadReportes();
      });
    }
  });

  document.getElementById("toggle-zonas")?.addEventListener("change", renderSectors);
  document.getElementById("quick-search")?.addEventListener("input", () => renderIncidentList(reportesCache));

  document.getElementById("btn-refresh-map")?.addEventListener("click", () => {
    loadSectors().then(loadReportes);
    refreshKpis();
  });

  document.getElementById("btn-my-location")?.addEventListener("click", () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((pos) => {
      const { latitude, longitude } = pos.coords;
      if (userLocationMarker) map.removeLayer(userLocationMarker);
      userLocationMarker = L.circleMarker([latitude, longitude], {
        radius: 8,
        color: "#0284c7",
        fillColor: "#38bdf8",
        fillOpacity: 0.9,
        weight: 3,
      })
        .bindPopup("Tu ubicación actual")
        .addTo(map);
      map.flyTo([latitude, longitude], 15, { duration: 0.8 });
    });
  });

  document.getElementById("btn-reset-filters")?.addEventListener("click", () => {
    document.getElementById("filter-sector").value = "";
    document.getElementById("filter-severidad").value = "";
    document.getElementById("filter-desde").value = "";
    document.getElementById("filter-hasta").value = "";
    document.getElementById("toggle-resueltos").checked = false;
    const qs = document.getElementById("quick-search");
    if (qs) qs.value = "";
    map.setView([defaultLat, defaultLng], defaultZoom);
    renderSectors();
    loadReportes();
  });

  // Si viene ?focus= de un incidente resuelto, activar resueltos automáticamente
  if (urlParams.get("resueltos") === "1") {
    const t = document.getElementById("toggle-resueltos");
    if (t) t.checked = true;
  }

  loadSectors().then(loadReportes);
  setInterval(() => {
    loadReportes();
    refreshKpis();
  }, 30000);
});
