// Initialize Map
const map = L.map('map').setView([25.4484, 78.5685], 13); // Centered near Jhansi as default

// Add Basemaps
const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community'
});

const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
});

satellite.addTo(map);

const baseMaps = {
    "Satellite": satellite,
    "OpenStreetMap": osm
};

L.control.layers(baseMaps).addTo(map);

// Initialize Leaflet.draw
const drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

const drawControl = new L.Control.Draw({
    draw: {
        polyline: false,
        polygon: true,
        circle: false,
        rectangle: true,
        marker: false,
        circlemarker: false
    },
    edit: {
        featureGroup: drawnItems
    }
});
map.addControl(drawControl);

// UI Elements
const sidePanel = document.getElementById('side-panel');
const emptyState = document.getElementById('empty-state');
const loading = document.getElementById('loading');
const results = document.getElementById('results');

// Value Elements
const valNdvi = document.getElementById('val-ndvi');
const valLswi = document.getElementById('val-lswi');
const valSoil = document.getElementById('val-soil');
const valSowing = document.getElementById('val-sowing');
const valHarvest = document.getElementById('val-harvest');
const valAdvisory = document.getElementById('val-advisory');

map.on(L.Draw.Event.CREATED, async function (e) {
    const layer = e.layer;
    drawnItems.clearLayers();
    if (geoRasterLayer) map.removeLayer(geoRasterLayer);
    if (fccRasterLayer) map.removeLayer(fccRasterLayer);
    if (ndviRasterLayer) map.removeLayer(ndviRasterLayer);
    geoRasterLayer = null;
    fccRasterLayer = null;
    ndviRasterLayer = null;
    if (toggleRaster) toggleRaster.checked = false;
    if (toggleFcc) toggleFcc.checked = false;
    if (toggleNdvi) toggleNdvi.checked = false;

    drawnItems.addLayer(layer);

    // Only show minimum metadata (image name, cloud cover) without fetching/showing images
    fetchAndShowMetadata(layer);
});

async function fetchAndShowMetadata(layer) {
    const geojson = layer.toGeoJSON();

    if (emptyState) emptyState.classList.add('hidden');
    if (results) results.classList.add('hidden');
    if (loading) loading.classList.add('hidden');

    const sceneInfoEl = document.getElementById('scene-info');
    if (sceneInfoEl) sceneInfoEl.classList.add('hidden');

    const metadataCard = document.getElementById('minimum-metadata-card');
    if (metadataCard) metadataCard.classList.remove('hidden');

    const metaImgName = document.getElementById('meta-image-name');
    const metaCloudCover = document.getElementById('meta-cloud-cover');
    const metaDate = document.getElementById('meta-date');

    if (metaImgName) {
        metaImgName.textContent = 'Searching…';
        metaImgName.title = '';
    }
    if (metaCloudCover) metaCloudCover.textContent = '—';
    if (metaDate) metaDate.textContent = '—';

    // Build date range from current season selection
    const season = document.getElementById('season-select')?.value || 'rabi';
    const year = new Date().getFullYear();
    let dates = {};
    if (season === 'kharif') {
        dates = { start: `${year}-06-01`, end: `${year}-10-31` };
    } else if (season === 'rabi') {
        dates = { start: `${year - 1}-10-01`, end: `${year}-04-30` };
    } else {
        dates = {
            start: document.getElementById('start-date')?.value || `${year - 1}-10-01`,
            end: document.getElementById('end-date')?.value || `${year}-04-30`
        };
    }

    // Cache search dates so Fetch Images button can use them if needed
    cachedSearchDates = dates;

    const statusEl = document.getElementById('upload-status');
    if (statusEl) {
        statusEl.textContent = '🔍 Querying minimum scene metadata…';
        statusEl.style.color = '#38bdf8';
    }

    try {
        const cloudCover = parseInt(document.getElementById('cloud-cover')?.value || '20');

        const stacResp = await fetch('http://localhost:8001/search-stac', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                geometry: geojson.geometry,
                start_date: dates.start,
                end_date: dates.end,
                cloud_cover: cloudCover
            })
        });

        if (!stacResp.ok) {
            const err = await stacResp.json();
            throw new Error(err.detail || 'Metadata search failed');
        }
        let stacData = await stacResp.json();

        // Auto-retry with 90% if nothing found
        if (stacData.count === 0 && cloudCover < 90) {
            const retryResp = await fetch('http://localhost:8001/search-stac', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    geometry: geojson.geometry,
                    start_date: dates.start,
                    end_date: dates.end,
                    cloud_cover: 90
                })
            });
            if (retryResp.ok) {
                stacData = await retryResp.json();
            }
        }

        if (stacData.count > 0 && stacData.scenes && stacData.scenes.length > 0) {
            const bestScene = stacData.scenes[0];
            if (metaImgName) {
                metaImgName.textContent = bestScene.scene_id;
                metaImgName.title = bestScene.scene_id;
            }
            if (metaCloudCover) metaCloudCover.textContent = `${bestScene.cloud_cover}%`;
            if (metaDate) metaDate.textContent = bestScene.date;

            if (statusEl) {
                statusEl.textContent = `✅ Minimal metadata loaded. Click 'Fetch Images' to download rasters.`;
                statusEl.style.color = '#27ae60';
            }

            // Enable Fetch Images button
            const fetchBtn = document.getElementById('fetch-cloud-btn');
            if (fetchBtn) {
                fetchBtn.disabled = false;
                fetchBtn.style.background = '#2ecc71';
                fetchBtn.style.color = '#000';
                fetchBtn.style.cursor = 'pointer';
            }

            // Optional: populates scene list without thumbnails so no fetched images are shown
            const sceneResults = document.getElementById('scene-results');
            const sceneCountLabel = document.getElementById('scene-count-label');
            const sceneListEl = document.getElementById('scene-list');
            if (sceneResults && sceneCountLabel && sceneListEl) {
                sceneResults.classList.remove('hidden');
                sceneCountLabel.innerHTML = `✅ <span style="color:#2ecc71">${stacData.count} scene(s) found</span> — best first.`;
                sceneListEl.innerHTML = stacData.scenes.map((s, i) => `
                    <div style="display:flex;align-items:center;gap:8px;padding:5px 3px;border-bottom:1px solid #2a2a3a;${i === 0 ? 'background:rgba(46,204,113,0.08);border-radius:4px;' : ''}">
                        <div>
                            <div style="font-weight:600;color:${i === 0 ? '#2ecc71' : '#ccc'};">${i === 0 ? '⭐ ' : ''}${s.date}</div>
                            <div style="color:#aaa;">☁️ ${s.cloud_cover}% cloud</div>
                            <div style="color:#888;font-size:0.72em;word-break:break-all;">${s.scene_id}</div>
                        </div>
                    </div>
                `).join('');
            }
        } else {
            if (metaImgName) metaImgName.textContent = 'No scenes found';
            if (metaCloudCover) metaCloudCover.textContent = '—';
            if (metaDate) metaDate.textContent = '—';
            if (statusEl) {
                statusEl.textContent = `❌ No scenes found for this date range.`;
                statusEl.style.color = '#e74c3c';
            }
        }
    } catch (err) {
        console.error('Metadata error:', err);
        if (metaImgName) metaImgName.textContent = 'Error fetching metadata';
        if (statusEl) {
            statusEl.textContent = `❌ ${err.message}`;
            statusEl.style.color = '#e74c3c';
        }
    }
}

async function fetchAndShowNDVI(layer) {
    const geojson = layer.toGeoJSON();

    emptyState.classList.add('hidden');
    results.classList.remove('hidden');
    loading.classList.remove('hidden');

    // Build date range from current season selection
    const season = document.getElementById('season-select')?.value || 'rabi';
    const year = new Date().getFullYear();
    let dates = {};
    if (season === 'kharif') {
        dates = { start: `${year}-06-01`, end: `${year}-10-31` };
    } else if (season === 'rabi') {
        dates = { start: `${year - 1}-10-01`, end: `${year}-04-30` };
    } else {
        dates = {
            start: document.getElementById('start-date')?.value || `${year - 1}-10-01`,
            end: document.getElementById('end-date')?.value || `${year}-04-30`
        };
    }

    const statusEl = document.getElementById('upload-status');
    if (statusEl) {
        statusEl.textContent = '⏳ Fetching Sentinel-2 from AWS…';
        statusEl.style.color = '#3498db';
    }

    try {
        // 1. Fetch best scene → computes NDVI on server, saves TCI + FCC
        const cloudCover = parseInt(document.getElementById('cloud-cover')?.value || '20');

        const stacResp = await fetch('http://localhost:8001/fetch-stac', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                geometry: geojson.geometry,
                start_date: dates.start,
                end_date: dates.end,
                cloud_cover: cloudCover
            })
        });

        if (!stacResp.ok) {
            const err = await stacResp.json();
            throw new Error(err.detail || 'STAC fetch failed');
        }
        const stacData = await stacResp.json();

        // 2. Show thumbnail scene preview
        const sceneInfo = document.getElementById('scene-info');
        const thumbImg = document.getElementById('scene-thumbnail');
        if (stacData.thumbnail && sceneInfo && thumbImg) {
            thumbImg.src = stacData.thumbnail;
            sceneInfo.classList.remove('hidden');
        }

        // 3. Show NDVI + metrics immediately
        displayResults({
            mean_ndvi: stacData.mean_ndvi,
            mean_lswi: stacData.mean_lswi,
            soil_moisture_proxy: parseFloat(Math.max(0, 0.8 - stacData.mean_ndvi).toFixed(3)),
            sowing_date: stacData.sowing_date,
            harvest_date: stacData.harvest_date
        }, `✅ Scene: ${stacData.date} | Cloud: ${stacData.cloud_cover}% | NDVI: ${stacData.mean_ndvi}`);

        if (statusEl) {
            statusEl.textContent = `✅ Scene ${stacData.date} · Cloud: ${stacData.cloud_cover}% · NDVI: ${stacData.mean_ndvi}`;
            statusEl.style.color = '#27ae60';
        }

        currentSceneId = stacData.scene_id;

        // 3. Reset layers first, then cache filenames + auto-load NDVI raster layer
        resetLayers();
        const rasters = await fetch('http://localhost:8001/list-rasters').then(r => r.json()).catch(() => ({ files: [] }));
        const findRealFile = (suffix, fallback) => {
            const found = rasters.files.find(f => f.includes(currentSceneId) && f.includes(suffix));
            return found ? `http://localhost:8001/data/${found}` : fallback;
        };
        latestTciFile = findRealFile('_TCI', stacData.tci_file || `${currentSceneId}_TCI.tif`);
        latestFccFile = findRealFile('_FCC', stacData.fcc_file || `${currentSceneId}_FCC.tif`);
        latestNdviFile = findRealFile('_NDVI', stacData.ndvi_file || `${currentSceneId}_NDVI.tif`);

        if (toggleNdvi) toggleNdvi.checked = true;
        if (toggleRaster) toggleRaster.checked = false;
        ndviRasterLayer = await loadRasterLayer(latestNdviFile);
        ndviRasterLayer.addTo(map);
        map.fitBounds(ndviRasterLayer.getBounds());

        // 4. Fetch fortnightly NDVI in background → update chart
        fetch('http://localhost:8001/ndvi-time-series', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                geometry: geojson.geometry,
                start_date: dates.start,
                end_date: dates.end,
                cloud_cover: parseInt(document.getElementById('cloud-cover')?.value || '20')
            })
        })
            .then(r => r.json())
            .then(ts => {
                if (ts.labels && ts.labels.length > 0) updateNdviChart(ts);
            })
            .catch(console.error);

        // 5. Request full Gemini advisory in background
        fetch('http://localhost:8001/analyze-plot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                geometry: geojson.geometry,
                scene_id: currentSceneId
            })
        })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    displayResults(data.metrics, data.advisory, data.insurance_advisory);
                }
            })
            .catch(console.error);

    } catch (err) {
        console.error('STAC error:', err);
        if (statusEl) {
            statusEl.textContent = `❌ ${err.message}`;
            statusEl.style.color = '#e74c3c';
        }
        alert(`Failed to fetch Sentinel-2 data:\n${err.message}`);
    } finally {
        loading.classList.add('hidden');
    }
}


function parseSimpleMarkdown(str) {
    if (!str) return '';
    return str
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>')
        .replace(/## (.*?)(<br>|$)/g, '<h4 style="margin:6px 0;color:#38bdf8;">$1</h4>')
        .replace(/# (.*?)(<br>|$)/g, '<h3 style="margin:8px 0;color:#38bdf8;">$1</h3>');
}

function displayResults(metrics, advisory, insuranceAdvisory) {
    // Populate Metrics
    const elNdvi = document.getElementById('val-ndvi');
    const elLswi = document.getElementById('val-lswi');
    const elSoil = document.getElementById('val-soil');
    const elSowing = document.getElementById('val-sowing');
    const elHarvest = document.getElementById('val-harvest');
    const elAdvisory = document.getElementById('val-advisory');
    const elInsuranceAdvisory = document.getElementById('val-insurance-advisory');

    if (elNdvi && metrics.mean_ndvi !== undefined) elNdvi.textContent = metrics.mean_ndvi.toFixed(2);
    if (elLswi && metrics.mean_lswi !== undefined) elLswi.textContent = (metrics.mean_lswi !== undefined ? metrics.mean_lswi : 0.2).toFixed(2);
    if (elSoil && metrics.soil_moisture_proxy !== undefined) elSoil.textContent = metrics.soil_moisture_proxy.toFixed(2);
    if (elSowing && metrics.sowing_date !== undefined) elSowing.textContent = metrics.sowing_date;
    if (elHarvest && metrics.harvest_date !== undefined) elHarvest.textContent = metrics.harvest_date;

    // Populate Advisory with parsed premium markdown styling
    if (elAdvisory) elAdvisory.innerHTML = parseSimpleMarkdown(advisory);
    if (elInsuranceAdvisory) elInsuranceAdvisory.innerHTML = parseSimpleMarkdown(insuranceAdvisory || 'No insurance data generated.');

    // Show Results
    const elLoading = document.getElementById('loading');
    const elResults = document.getElementById('results');
    if (elLoading) elLoading.classList.add('hidden');
    if (elResults) elResults.classList.remove('hidden');
}

// ─── Track the filenames returned by /fetch-stac ───────────────────────────
let latestTciFile = null;
let latestFccFile = null;
let latestNdviFile = null;
let currentSceneId = null;

const toggleRaster = document.getElementById('toggle-raster');
const toggleFcc = document.getElementById('toggle-fcc');
const toggleNdvi = document.getElementById('toggle-ndvi');

let geoRasterLayer = null;
let fccRasterLayer = null;
let ndviRasterLayer = null;

// Helper: load a GeoTIFF from the backend and add it as a GeoRasterLayer
async function loadRasterLayer(fileUrl) {

    if (!fileUrl) {
        throw new Error("Raster URL missing");
    }

    let url = fileUrl;

    // Normalize backend URLs
    if (!url.startsWith("http")) {

        if (url.startsWith("/data/")) {
            url = `http://localhost:8001${url}`;
        } else {
            url = `http://localhost:8001/data/${url}`;
        }
    }

    console.log("Loading raster:", url);

    const response = await fetch(url);

    if (!response.ok) {
        throw new Error(
            `Failed to fetch raster: ${response.status} ${response.statusText}`
        );
    }

    const arrayBuffer = await response.arrayBuffer();

    if (!arrayBuffer || arrayBuffer.byteLength === 0) {
        throw new Error("Empty TIFF received");
    }

    const georaster = await parseGeoraster(arrayBuffer);

    return new GeoRasterLayer({
        georaster,
        opacity: 0.85,
        resolution: 128,

        pixelValuesToColorFn: values => {

            if (
                !values ||
                values.length < 3 ||
                (
                    values[0] === 0 &&
                    values[1] === 0 &&
                    values[2] === 0
                )
            ) {
                return null;
            }

            return `rgb(${values[0]}, ${values[1]}, ${values[2]})`;
        }
    });
}
// ─── UI Toggles ─────────────────────────────────────────────────────────────
const seasonSelect = document.getElementById('season-select');
const customDates = document.getElementById('custom-dates');
const uploadStatus = document.getElementById('upload-status');
const cloudSlider = document.getElementById('cloud-cover');
const cloudValEl = document.getElementById('cloud-val');

seasonSelect.addEventListener('change', (e) => {
    customDates.classList.toggle('hidden', e.target.value !== 'custom');
});
cloudSlider.addEventListener('input', () => {
    cloudValEl.textContent = cloudSlider.value;
});

// ─── Search Scenes ────────────────────────────────────────────────────────────
const searchScenesBtn = document.getElementById('search-scenes-btn');
const fetchCloudBtnEl = document.getElementById('fetch-cloud-btn');
const sceneResults = document.getElementById('scene-results');
const sceneCountLabel = document.getElementById('scene-count-label');
const sceneListEl = document.getElementById('scene-list');

// Cache search results so Fetch Images can use the top scene
let cachedSearchDates = null;

searchScenesBtn.addEventListener('click', async () => {
    const layer = drawnItems.getLayers()[0];
    if (!layer) {
        alert('Please draw a polygon on the map first to define your Area of Interest.');
        return;
    }

    const geojson = layer.toGeoJSON();
    const season = seasonSelect.value;
    const year = new Date().getFullYear();
    let dates = {};

    if (season === 'kharif') {
        dates = { start: `${year}-06-01`, end: `${year}-10-31` };
    } else if (season === 'rabi') {
        dates = { start: `${year - 1}-10-01`, end: `${year}-04-30` };
    } else {
        dates = {
            start: document.getElementById('start-date')?.value || `${year - 1}-10-01`,
            end: document.getElementById('end-date')?.value || `${year}-04-30`
        };
    }
    cachedSearchDates = dates;

    searchScenesBtn.disabled = true;
    searchScenesBtn.textContent = '⏳ Searching…';
    sceneResults.classList.add('hidden');
    fetchCloudBtnEl.disabled = true;
    fetchCloudBtnEl.style.background = '#555';
    fetchCloudBtnEl.style.color = '#aaa';
    fetchCloudBtnEl.style.cursor = 'not-allowed';

    try {
        const cloudPct = parseInt(cloudSlider.value || '20');

        // Show what we're querying
        if (uploadStatus) {
            uploadStatus.textContent = `⏳ Searching ${dates.start} → ${dates.end}  ☁️ <${cloudPct}%…`;
            uploadStatus.style.color = '#3498db';
        }

        const queryAndReturn = async (pct) => {
            const res = await fetch('http://localhost:8001/search-stac', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    geometry: geojson.geometry,
                    start_date: dates.start,
                    end_date: dates.end,
                    cloud_cover: pct
                })
            });
            if (!res.ok) throw new Error((await res.json()).detail || 'Search failed');
            return res.json();
        };

        let data = await queryAndReturn(cloudPct);

        // Auto-retry with 90% if nothing found
        let retried = false;
        if (data.count === 0 && cloudPct < 90) {
            retried = true;
            data = await queryAndReturn(90);
        }

        sceneResults.classList.remove('hidden');

        if (data.count === 0) {
            sceneCountLabel.innerHTML = `❌ <span style="color:#e74c3c">No scenes found</span> for <strong>${dates.start} → ${dates.end}</strong>.<br>Try a wider date range.`;
            sceneListEl.innerHTML = '';
            if (uploadStatus) { uploadStatus.textContent = ''; }
        } else {
            const note = retried ? ` (relaxed to 90% cloud cover — no results at ${cloudPct}%)` : '';
            sceneCountLabel.innerHTML = `✅ <span style="color:#2ecc71">${data.count} scene(s) found</span>${note} — best first.`;

            sceneListEl.innerHTML = data.scenes.map((s, i) => `
                <div style="display:flex;align-items:center;gap:8px;padding:5px 3px;border-bottom:1px solid #2a2a3a;${i === 0 ? 'background:rgba(46,204,113,0.08);border-radius:4px;' : ''}">
                    <div>
                        <div style="font-weight:600;color:${i === 0 ? '#2ecc71' : '#ccc'};">${i === 0 ? '⭐ ' : ''}${s.date}</div>
                        <div style="color:#aaa;">☁️ ${s.cloud_cover}% cloud</div>
                        <div style="color:#888;font-size:0.72em;word-break:break-all;">${s.scene_id}</div>
                    </div>
                </div>
            `).join('');

            fetchCloudBtnEl.disabled = false;
            fetchCloudBtnEl.style.background = '#2ecc71';
            fetchCloudBtnEl.style.color = '#000';
            fetchCloudBtnEl.style.cursor = 'pointer';
            if (data.best_phenology) {
                displayResults({
                    sowing_date: data.best_phenology.sowing_date,
                    harvest_date: data.best_phenology.harvest_date
                }, "✅ Available scenes found. Click 'Fetch Images' for full analysis.", "Sowing and harvest dates estimated from metadata.");
                if (emptyState) emptyState.classList.add('hidden');
                if (results) results.classList.remove('hidden');
            }

            if (uploadStatus) {
                uploadStatus.textContent = `Found ${data.count} scenes from ${dates.start} → ${dates.end}`;
                uploadStatus.style.color = '#27ae60';
            }
        }
    } catch (err) {
        sceneResults.classList.remove('hidden');
        sceneCountLabel.innerHTML = `❌ <span style="color:#e74c3c">Error: ${err.message}</span>`;
        sceneListEl.innerHTML = '';
        if (uploadStatus) { uploadStatus.textContent = ''; }
    } finally {
        searchScenesBtn.disabled = false;
        searchScenesBtn.textContent = '🔍 Search Available Scenes';
    }
});

// ─── Cloud Fetch ─────────────────────────────────────────────────────────────
// fetchCloudBtnEl is already declared above (from Search Scenes block)
fetchCloudBtnEl.addEventListener('click', async () => {
    const layer = drawnItems.getLayers()[0];
    if (!layer) {
        alert('Please draw a polygon on the map first to define your Area of Interest.');
        return;
    }

    const geojson = layer.toGeoJSON();
    const season = seasonSelect.value;
    const year = new Date().getFullYear();
    let dates = {};

    if (season === 'kharif') {
        dates = { start: `${year}-06-01`, end: `${year}-10-31` };
    } else if (season === 'rabi') {
        dates = { start: `${year - 1}-10-01`, end: `${year}-04-30` };
    } else {
        dates = {
            start: document.getElementById('start-date').value,
            end: document.getElementById('end-date').value
        };
        if (!dates.start || !dates.end) {
            if (cachedSearchDates && cachedSearchDates.start && cachedSearchDates.end) {
                dates = cachedSearchDates;
            } else {
                alert('Please select both start and end dates.');
                return;
            }
        }
    }

    const currentStatusEl = document.getElementById('upload-status');
    if (currentStatusEl) {
        currentStatusEl.textContent = '⏳ Fetching Sentinel-2 from AWS…';
        currentStatusEl.style.color = '#3498db';
    }
    fetchCloudBtnEl.disabled = true;
    resetLayers();

    const elLoading = document.getElementById('loading');
    const elResults = document.getElementById('results');
    const elEmpty = document.getElementById('empty-state');
    if (elLoading) elLoading.classList.remove('hidden');
    if (elResults) elResults.classList.remove('hidden');
    if (elEmpty) elEmpty.classList.add('hidden');

    try {
        // ── 1. Fetch best scene + generate TCI & FCC on the server ──────────
        const stacResp = await fetch('http://localhost:8001/fetch-stac', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                geometry: geojson.geometry,
                start_date: dates.start,
                end_date: dates.end,
                cloud_cover: parseInt(cloudSlider.value || '20')
            })
        });
        if (!stacResp.ok) {
            const err = await stacResp.json();
            throw new Error(err.detail || 'STAC fetch failed');
        }
        const stacData = await stacResp.json();

        currentSceneId = stacData.scene_id;

        // Prioritize actual generated MD5-hashed files on disk to bypass stale/legacy server URL strings
        const rasters = await fetch('http://localhost:8001/list-rasters').then(r => r.json()).catch(() => ({ files: [] }));
        const findRealFile = (suffix, fallback) => {
            const found = rasters.files.find(f => f.includes(currentSceneId) && f.includes(suffix));
            return found ? `http://localhost:8001/data/${found}` : fallback;
        };
        latestTciFile = findRealFile('_TCI', stacData.tci_file || `${currentSceneId}_TCI.tif`);
        latestFccFile = findRealFile('_FCC', stacData.fcc_file || `${currentSceneId}_FCC.tif`);
        latestNdviFile = findRealFile('_NDVI', stacData.ndvi_file || `${currentSceneId}_NDVI.tif`);

        console.log("TCI URL:", latestTciFile);
        console.log("FCC URL:", latestFccFile);
        console.log("NDVI URL:", latestNdviFile);

        if (currentStatusEl) {
            currentStatusEl.textContent = `✅ Scene ${stacData.date}  ·  Cloud: ${stacData.cloud_cover}%  ·  NDVI: ${stacData.mean_ndvi}`;
            currentStatusEl.style.color = '#27ae60';
        }

        // Show thumbnail scene preview
        const sceneInfo = document.getElementById('scene-info');
        const thumbImg = document.getElementById('scene-thumbnail');
        if (stacData.thumbnail && sceneInfo && thumbImg) {
            thumbImg.src = stacData.thumbnail;
            sceneInfo.classList.remove('hidden');
        }

        // Show initial NDVI/LSWI estimates immediately
        displayResults({
            mean_ndvi: stacData.mean_ndvi,
            mean_lswi: stacData.mean_lswi,
            soil_moisture_proxy: parseFloat(Math.max(0, 0.8 - stacData.mean_ndvi).toFixed(3)),
            sowing_date: stacData.sowing_date,
            harvest_date: stacData.harvest_date
        }, `✅ Scene fetched successfully. Generating agronomic advisory...`);

        // Auto-load Peak NDVI clipped layer instead of TCI
        if (toggleNdvi) toggleNdvi.checked = true;
        toggleRaster.checked = false;
        ndviRasterLayer = await loadRasterLayer(latestNdviFile);
        ndviRasterLayer.addTo(map);
        map.fitBounds(ndviRasterLayer.getBounds());

        // Request full Gemini advisory in background
        fetch('http://localhost:8001/analyze-plot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                geometry: geojson.geometry,
                scene_id: currentSceneId
            })
        })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    displayResults(data.metrics, data.advisory, data.insurance_advisory);
                }
            })
            .catch(console.error);

        // ── 2. Fetch fortnightly NDVI in parallel ──────────────────────────────
        fetch('http://localhost:8001/ndvi-time-series', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                geometry: geojson.geometry,
                start_date: dates.start,
                end_date: dates.end,
                cloud_cover: parseInt(cloudSlider.value || '20')
            })
        })
            .then(r => r.json())
            .then(ts => {
                if (ts.labels && ts.labels.length > 0) {
                    updateNdviChart(ts);
                }
            })
            .catch(console.error);

    } catch (err) {
        console.error('STAC error:', err);
        if (currentStatusEl) {
            currentStatusEl.textContent = `❌ Error: ${err.message}`;
            currentStatusEl.style.color = '#e74c3c';
        }
        const elLoading = document.getElementById('loading');
        if (elLoading) elLoading.classList.add('hidden');
    } finally {
        fetchCloudBtnEl.disabled = false;
    }
});

// Override toggle-raster to use latestTciFile when available
toggleRaster.addEventListener('change', async function () {
    if (this.checked) {
        if (geoRasterLayer) { geoRasterLayer.addTo(map); return; }
        const file = latestTciFile || (await fetch('http://localhost:8001/list-rasters').then(r => r.json()).then(d => d.files.find(f => (!currentSceneId || f.includes(currentSceneId)) && f.includes('_TCI') && !f.includes('TEST_') && !f.includes('dummy'))));
        if (!file) { alert('Please fetch a valid satellite scene first.'); this.checked = false; return; }
        try { geoRasterLayer = await loadRasterLayer(file); geoRasterLayer.addTo(map); map.fitBounds(geoRasterLayer.getBounds()); }
        catch (e) { alert('Failed to load TCI layer: ' + e.message); this.checked = false; }
    } else {
        if (geoRasterLayer) map.removeLayer(geoRasterLayer);
    }
});

// Override toggle-fcc to use latestFccFile when available
toggleFcc.addEventListener('change', async function () {
    if (this.checked) {
        if (fccRasterLayer) { fccRasterLayer.addTo(map); return; }
        const file = latestFccFile || (await fetch('http://localhost:8001/list-rasters').then(r => r.json()).then(d => d.files.find(f => (!currentSceneId || f.includes(currentSceneId)) && f.includes('_FCC') && !f.includes('TEST_') && !f.includes('dummy'))));
        if (!file) { alert('Please fetch a valid satellite scene first.'); this.checked = false; return; }
        try { fccRasterLayer = await loadRasterLayer(file); fccRasterLayer.addTo(map); map.fitBounds(fccRasterLayer.getBounds()); }
        catch (e) { alert('Failed to load FCC layer: ' + e.message); this.checked = false; }
    } else {
        if (fccRasterLayer) map.removeLayer(fccRasterLayer);
    }
});

// Override toggle-ndvi to use latestNdviFile when available
if (toggleNdvi) {
    toggleNdvi.addEventListener('change', async function () {
        if (this.checked) {
            if (ndviRasterLayer) { ndviRasterLayer.addTo(map); return; }
            const file = latestNdviFile || (await fetch('http://localhost:8001/list-rasters').then(r => r.json()).then(d => d.files.find(f => (!currentSceneId || f.includes(currentSceneId)) && f.includes('_NDVI') && !f.includes('TEST_') && !f.includes('dummy'))));
            if (!file) { alert('Please fetch a valid satellite scene first.'); this.checked = false; return; }
            try { ndviRasterLayer = await loadRasterLayer(file); ndviRasterLayer.addTo(map); map.fitBounds(ndviRasterLayer.getBounds()); }
            catch (e) { alert('Failed to load NDVI layer: ' + e.message); this.checked = false; }
        } else {
            if (ndviRasterLayer) map.removeLayer(ndviRasterLayer);
        }
    });
}

// ─── Chart.js — Fortnightly NDVI ─────────────────────────────────────────────
let ndviChart = null;
let ndviChartData = null;   // store full API response so radio can re-render

function initNdviChart() {
    const ctx = document.getElementById('ndviChart').getContext('2d');
    ndviChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: '▲ Peak NDVI',
                    data: [],
                    borderColor: '#2ecc71',
                    backgroundColor: 'rgba(46,204,113,0.1)',
                    borderWidth: 2.5,
                    tension: 0.4,
                    fill: true,
                    spanGaps: true,
                    pointRadius: 4,
                    pointBackgroundColor: '#2ecc71',
                },
                {
                    label: '💧 Peak LSWI',
                    data: [],
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52,152,219,0.05)',
                    borderWidth: 2,
                    borderDash: [5, 5],
                    tension: 0.4,
                    fill: false,
                    spanGaps: true,
                    pointRadius: 3,
                    pointBackgroundColor: '#3498db',
                }
            ]
        },
        options: {
            responsive: true,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#94a3b8', font: { size: 10 } }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    titleColor: '#f8fafc',
                    bodyColor: '#f8fafc',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1
                }
            },
            scales: {
                y: {
                    min: 0, max: 1,
                    ticks: { color: '#64748b' },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                },
                x: {
                    ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 45 },
                    grid: { display: false }
                }
            }
        }
    });
}
initNdviChart();

function renderNdviChart() {
    if (!ndviChartData) return;

    ndviChart.data.labels = ndviChartData.labels;
    ndviChart.data.datasets[0].data = ndviChartData.peak_ndvi;
    ndviChart.data.datasets[1].data = ndviChartData.peak_lswi;
    ndviChart.update();

    // Update stat badges
    const statsEl = document.getElementById('ndvi-stats');
    if (statsEl) {
        statsEl.style.display = 'flex';
        statsEl.classList.remove('hidden');
    }
    const peakEl = document.getElementById('stat-peak');
    const minEl = document.getElementById('stat-min');
    if (peakEl) peakEl.textContent = ndviChartData.season_peak ?? '—';
    if (minEl) minEl.textContent = ndviChartData.season_min ?? '—';

    // Hide placeholder text
    const msg = document.getElementById('ndvi-chart-msg');
    if (msg) msg.style.display = 'none';
}

function updateNdviChart(tsData) {
    ndviChartData = tsData;
    renderNdviChart();
}

// ─── Reset ────────────────────────────────────────────────────────────────────
function resetLayers() {

    if (toggleRaster) toggleRaster.checked = false;
    if (toggleFcc) toggleFcc.checked = false;
    if (toggleNdvi) toggleNdvi.checked = false;

    if (geoRasterLayer) {
        map.removeLayer(geoRasterLayer);
        geoRasterLayer = null;
    }

    if (fccRasterLayer) {
        map.removeLayer(fccRasterLayer);
        fccRasterLayer = null;
    }

    if (ndviRasterLayer) {
        map.removeLayer(ndviRasterLayer);
        ndviRasterLayer = null;
    }

    latestTciFile = null;
    latestFccFile = null;
    latestNdviFile = null;
}
// (All STAC fetch, chart, reset, and toggle logic is above ↑)
