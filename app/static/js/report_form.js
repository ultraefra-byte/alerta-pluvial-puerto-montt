document.addEventListener("DOMContentLoaded", () => {
  const mapEl = document.getElementById("map-report");
  if (!mapEl) return;

  const latInput = document.getElementById("latitud");
  const lngInput = document.getElementById("longitud");
  const gpsStatus = document.getElementById("gps-status");
  const btnGps = document.getElementById("btn-gps");
  const sectorSelect = document.getElementById("sector_id");

  const initLat = parseFloat(latInput.value) || parseFloat(mapEl.dataset.lat) || -41.4693;
  const initLng = parseFloat(lngInput.value) || parseFloat(mapEl.dataset.lng) || -72.9424;

  const map = L.map("map-report").setView([initLat, initLng], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);

  let marker = null;

  function setPosition(lat, lng, zoom = 15) {
    latInput.value = lat.toFixed(6);
    lngInput.value = lng.toFixed(6);
    if (!marker) {
      marker = L.marker([lat, lng], { draggable: true }).addTo(map);
      marker.on("dragend", (e) => {
        const pos = e.target.getLatLng();
        latInput.value = pos.lat.toFixed(6);
        lngInput.value = pos.lng.toFixed(6);
        if (gpsStatus) {
          gpsStatus.innerHTML = `<i class="bi bi-check-circle-fill text-success me-1"></i>Ubicación ajustada (${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)}).`;
        }
      });
    } else {
      marker.setLatLng([lat, lng]);
    }
    map.setView([lat, lng], zoom);
  }

  if (latInput.value && lngInput.value) {
    setPosition(parseFloat(latInput.value), parseFloat(lngInput.value), 14);
  }

  map.on("click", (e) => {
    setPosition(e.latlng.lat, e.latlng.lng, Math.max(map.getZoom(), 14));
    if (gpsStatus) {
      gpsStatus.innerHTML = `<i class="bi bi-check-circle-fill text-success me-1"></i>Punto marcado en el mapa (${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}). Puedes arrastrar el marcador para mayor precisión.`;
    }
  });

  // Centrar mapa si el usuario elige un sector del desplegable
  if (sectorSelect) {
    sectorSelect.addEventListener("change", () => {
      const opt = sectorSelect.options[sectorSelect.selectedIndex];
      const slat = parseFloat(opt?.dataset?.lat);
      const slng = parseFloat(opt?.dataset?.lng);
      if (!Number.isNaN(slat) && !Number.isNaN(slng)) {
        map.flyTo([slat, slng], 14, { duration: 0.6 });
      }
    });
  }

  if (btnGps) {
    btnGps.addEventListener("click", () => {
      if (!navigator.geolocation) {
        gpsStatus.innerHTML = '<i class="bi bi-exclamation-triangle text-warning me-1"></i>Tu navegador no soporta GPS. Marca el punto directamente en el mapa.';
        return;
      }
      btnGps.disabled = true;
      gpsStatus.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Obteniendo coordenadas GPS...';
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          btnGps.disabled = false;
          setPosition(pos.coords.latitude, pos.coords.longitude, 16);
          gpsStatus.innerHTML = `<i class="bi bi-check-circle-fill text-success me-1"></i>GPS capturado con precisión (±${Math.round(pos.coords.accuracy)} m).`;
        },
        () => {
          btnGps.disabled = false;
          gpsStatus.innerHTML = '<i class="bi bi-exclamation-triangle text-warning me-1"></i>No se pudo obtener GPS automático. Haz clic en el mapa para fijar la ubicación.';
        },
        { enableHighAccuracy: true, timeout: 10000 }
      );
    });
  }

  // Dropzone + compresión cliente de foto (RNF02 <= 2MB)
  const fotoInput = document.getElementById("foto");
  const dropzone = document.getElementById("photo-dropzone");
  const fotoFeedback = document.getElementById("foto-feedback");
  const fotoPreviewWrap = document.getElementById("foto-preview-wrap");
  const fotoPreview = document.getElementById("foto-preview");
  const btnRemoveFoto = document.getElementById("btn-remove-foto");

  function clearPhoto() {
    if (fotoInput) fotoInput.value = "";
    if (fotoPreviewWrap) fotoPreviewWrap.classList.add("d-none");
    if (fotoFeedback) fotoFeedback.textContent = "";
  }

  function handleFile(file) {
    if (!file) {
      clearPhoto();
      return;
    }
    if (!file.type.startsWith("image/")) {
      clearPhoto();
      if (fotoFeedback) {
        fotoFeedback.innerHTML = '<span class="text-danger"><i class="bi bi-x-circle me-1"></i>Solo se permiten imágenes JPG, PNG o WEBP.</span>';
      }
      return;
    }

    const reader = new FileReader();
    reader.onload = (evt) => {
      const img = new Image();
      img.onload = () => {
        const maxDim = 1400;
        let { width, height } = img;
        if (width > maxDim || height > maxDim) {
          if (width > height) {
            height = Math.round((height * maxDim) / width);
            width = maxDim;
          } else {
            width = Math.round((width * maxDim) / height);
            height = maxDim;
          }
        }

        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, width, height);

        canvas.toBlob(
          (blob) => {
            if (!blob) return;
            const compressedFile = new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), {
              type: "image/jpeg",
            });
            const dt = new DataTransfer();
            dt.items.add(compressedFile);
            fotoInput.files = dt.files;

            const sizeKb = Math.round(compressedFile.size / 1024);
            if (fotoFeedback) {
              fotoFeedback.innerHTML = `<span class="text-success"><i class="bi bi-check-circle-fill me-1"></i>Imagen comprimida y lista (${sizeKb} KB / máx. 2048 KB).</span>`;
            }
            if (fotoPreview && fotoPreviewWrap) {
              fotoPreview.src = URL.createObjectURL(compressedFile);
              fotoPreviewWrap.classList.remove("d-none");
            }
          },
          "image/jpeg",
          0.8
        );
      };
      img.src = evt.target.result;
    };
    reader.readAsDataURL(file);
  }

  if (dropzone && fotoInput) {
    dropzone.addEventListener("click", () => fotoInput.click());
    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
      if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fotoInput.addEventListener("change", () => handleFile(fotoInput.files[0]));
  }

  btnRemoveFoto?.addEventListener("click", (e) => {
    e.stopPropagation();
    clearPhoto();
  });
});
