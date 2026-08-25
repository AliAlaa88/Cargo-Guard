// ── Map initialisation ────────────────────────────────────────────────────────
const map = L.map('map').setView([41.5801, -71.4774], 10);

// Bright colorful basemap for Rhode Island interior
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/">CARTO</a>',
    maxZoom: 19,
}).addTo(map);

// ── Route colours ─────────────────────────────────────────────────────────────
const ROUTE_COLORS = ['#2563eb', '#d97706', '#9333ea'];

// ── State ─────────────────────────────────────────────────────────────────────
let sourceMarker       = null;
let destMarker         = null;
let routeLayers        = [];
let maskLayer          = null;
let thermalLayer       = null;
let activeRouteIndex   = -1;
let currentMode        = 'shortest';
let activeCargoProfile = null;
let builtinProfiles    = [];

// ── DOM refs ──────────────────────────────────────────────────────────────────
const statusEl          = document.getElementById('status');
const graphMetaEl       = document.getElementById('graph-meta');
const routesContainerEl = document.getElementById('routes-container');
const routesListEl      = document.getElementById('routes-list');
const loadingEl         = document.getElementById('loading');
const resetBtn          = document.getElementById('reset-btn');
const thermalToggle     = document.getElementById('thermal-toggle');
const modeBtns          = document.querySelectorAll('.mode-btn');

const cargoSelect       = document.getElementById('cargo-select');
const customCargoBox    = document.getElementById('custom-cargo-box');
const aiCargoPrompt     = document.getElementById('ai-cargo-prompt');
const aiParseBtn        = document.getElementById('ai-parse-btn');
const cargoNameEl       = document.getElementById('cargo-name');
const cargoBadgeEl      = document.getElementById('cargo-category-badge');
const cargoSafeRangeEl  = document.getElementById('cargo-safe-range');
const cargoTriggerEl    = document.getElementById('cargo-trigger');
const cargoSensitivityEl= document.getElementById('cargo-sensitivity');
const cargoDescEl       = document.getElementById('cargo-desc');

// ── Cargo Profile Handlers ────────────────────────────────────────────────────
async function loadCargoProfiles() {
    try {
        const res = await fetch('/cargo/profiles');
        const data = await res.json();
        builtinProfiles = data.profiles || [];
        if (builtinProfiles.length > 0) {
            setCargoProfile(builtinProfiles[0]);
        }
    } catch (err) {
        console.warn('Failed to load cargo profiles:', err);
    }
}
loadCargoProfiles();

function setCargoProfile(profile) {
    activeCargoProfile = profile;
    cargoNameEl.textContent = profile.name;
    cargoSafeRangeEl.textContent = `${profile.safe_min_c}°C – ${profile.safe_max_c}°C`;
    cargoTriggerEl.textContent = `> ${profile.ambient_trigger_c}°C`;
    cargoSensitivityEl.textContent = `${profile.thermal_sensitivity} (${profile.risk_tolerance})`;
    cargoDescEl.textContent = profile.description || '';

    cargoBadgeEl.className = `badge-${profile.risk_tolerance || 'strict'}`;
    cargoBadgeEl.textContent = (profile.risk_tolerance || 'strict').toUpperCase();

    // If points are selected, recalculate routes with the new cargo profile constraints
    if (sourceMarker && destMarker) findRoutes();
}

cargoSelect.addEventListener('change', () => {
    const val = cargoSelect.value;
    if (val === 'custom') {
        customCargoBox.style.display = 'block';
    } else {
        customCargoBox.style.display = 'none';
        const match = builtinProfiles.find(p => p.id === val);
        if (match) setCargoProfile(match);
    }
});

aiParseBtn.addEventListener('click', async () => {
    const promptText = aiCargoPrompt.value.trim();
    if (!promptText) return;

    aiParseBtn.disabled = true;
    aiParseBtn.textContent = '🤖 Analyzing…';
    setStatus('AI Agent analyzing cargo requirements with knowledge base tool…');

    try {
        const res = await fetch('/cargo/parse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: promptText })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to parse cargo');

        const profile = data.cargo_profile;
        setCargoProfile(profile);
        setStatus(`AI Agent configured profile: ${profile.name}`);
    } catch (err) {
        setStatus(`⚠ AI Agent error: ${err.message}`);
    } finally {
        aiParseBtn.disabled = false;
        aiParseBtn.textContent = '🤖 Analyze';
    }
});

// ── Mode toggle ───────────────────────────────────────────────────────────────
modeBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        modeBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentMode = btn.dataset.mode;
        // If we have markers already placed, re-run route search
        if (sourceMarker && destMarker) findRoutes();
    });
});

// ── Thermal overlay ───────────────────────────────────────────────────────────
thermalToggle.addEventListener('change', () => {
    if (thermalToggle.checked) {
        fetchAndShowThermal();
    } else {
        if (thermalLayer) { map.removeLayer(thermalLayer); thermalLayer = null; }
    }
});

async function fetchAndShowThermal() {
    try {
        if (thermalLayer) {
            map.addLayer(thermalLayer);
            if (maskLayer) maskLayer.bringToFront();
            routeLayers.forEach(l => l.bringToFront());
            bringPinsToFront();
            return;
        }

        statusEl.textContent = 'Loading thermal layer…';
        const res  = await fetch('/temperature/heatmap');
        const data = await res.json();
        if (!data.points || !data.points.length) {
            statusEl.textContent = 'No thermal data available';
            return;
        }

        // Calculate min/max for normalization
        const temps = data.points.map(p => p[2]);
        const lo = Math.min(...temps);
        const hi = Math.max(...temps);

        // Normalize intensity between 0.1 and 1.0
        const heatPoints = data.points.map(p => {
            const intensity = hi === lo ? 0.5 : 0.2 + 0.8 * ((p[2] - lo) / (hi - lo));
            return [p[0], p[1], intensity];
        });

        thermalLayer = L.heatLayer(heatPoints, {
            radius: 18,
            blur: 14,
            maxZoom: 16,
            minOpacity: 0.35,
            gradient: {
                0.0: '#3b82f6', // Blue (cool)
                0.3: '#06b6d4', // Cyan
                0.55: '#eab308', // Yellow
                0.8: '#f97316', // Orange
                1.0: '#ef4444'  // Red (hot)
            }
        }).addTo(map);

        statusEl.textContent = `Thermal overlay active (${lo.toFixed(1)}°C – ${hi.toFixed(1)}°C)`;

        // Keep routes and mask on top
        if (maskLayer) maskLayer.bringToFront();
        routeLayers.forEach(l => l.bringToFront());
        bringPinsToFront();
    } catch (err) {
        console.warn('Thermal overlay error:', err);
        statusEl.textContent = `⚠ Thermal error: ${err.message}`;
    }
}

// ── Graph metadata & Rhode Island boundary mask ───────────────────────────────
async function loadGraphInfo() {
    try {
        const res  = await fetch('/graph/info');
        const info = await res.json();
        graphMetaEl.textContent =
            `${info.place} · ${info.nodes.toLocaleString()} nodes · ${info.edges.toLocaleString()} edges`;
        if (info.boundary) renderRhodeIslandMask(info.boundary);
    } catch {
        graphMetaEl.textContent = 'Rhode Island road network';
    }
}
loadGraphInfo();

function renderRhodeIslandMask(geom) {
    if (maskLayer) map.removeLayer(maskLayer);
    const outerWorld = [[90,-180],[90,180],[-90,180],[-90,-180]];
    const holes = [];
    if (geom.type === 'Polygon') {
        holes.push(geom.coordinates[0].map(pt => [pt[1], pt[0]]));
    } else if (geom.type === 'MultiPolygon') {
        geom.coordinates.forEach(poly => holes.push(poly[0].map(pt => [pt[1], pt[0]])));
    }
    maskLayer = L.polygon([outerWorld, ...holes], {
        color: '#0284c7', weight: 2.5, opacity: 0.9,
        fillColor: '#0b0f19', fillOpacity: 0.82,
        interactive: false,
    }).addTo(map);
}

// ── Custom marker icons ───────────────────────────────────────────────────────
function makePin(color) {
    return L.divIcon({
        className: '',
        html: `<div style="width:16px;height:16px;border-radius:50%;background:${color};
               border:2.5px solid #fff;box-shadow:0 0 0 2px ${color}44,0 2px 6px rgba(0,0,0,.5);">
               </div>`,
        iconSize: [16, 16], iconAnchor: [8, 8],
    });
}
const srcIcon  = makePin('#22c55e');
const destIcon = makePin('#ef4444');

// ── Map click handler ─────────────────────────────────────────────────────────
map.on('click', ({ latlng: { lat, lng } }) => {
    if (!sourceMarker) {
        sourceMarker = L.marker([lat, lng], { icon: srcIcon }).addTo(map);
        setStatus('Origin set — click to place destination');
    } else if (!destMarker) {
        destMarker = L.marker([lat, lng], { icon: destIcon }).addTo(map);
        findRoutes();
    } else {
        resetMap();
        sourceMarker = L.marker([lat, lng], { icon: srcIcon }).addTo(map);
        setStatus('Origin set — click to place destination');
    }
});

// ── Reset ─────────────────────────────────────────────────────────────────────
resetBtn.addEventListener('click', resetMap);

function resetMap() {
    if (sourceMarker) { map.removeLayer(sourceMarker); sourceMarker = null; }
    if (destMarker)   { map.removeLayer(destMarker);   destMarker   = null; }
    routeLayers.forEach(l => map.removeLayer(l));
    routeLayers = [];
    activeRouteIndex = -1;
    routesContainerEl.classList.remove('visible');
    routesListEl.innerHTML = '';
    loadingEl.classList.remove('visible');
    setStatus('Ready — click the map to begin');
}

// ── Route fetch ───────────────────────────────────────────────────────────────
async function findRoutes() {
    setStatus('Finding routes…');
    loadingEl.classList.add('visible');
    routesContainerEl.classList.remove('visible');

    const src = sourceMarker.getLatLng();
    const dst = destMarker.getLatLng();

    try {
        const res  = await fetch('/route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                src_lat: src.lat, src_lng: src.lng,
                dst_lat: dst.lat, dst_lng: dst.lng,
                mode: currentMode,
                cargo_profile: activeCargoProfile,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Request failed');

        displayRoutes(data.routes);
        setStatus(`${data.routes.length} route${data.routes.length !== 1 ? 's' : ''} found`);
    } catch (err) {
        setStatus(`⚠ ${err.message}`);
    } finally {
        loadingEl.classList.remove('visible');
    }
}

// ── Display routes ────────────────────────────────────────────────────────────
function displayRoutes(routes) {
    routeLayers.forEach(l => map.removeLayer(l));
    routeLayers = [];
    routesListEl.innerHTML = '';
    activeRouteIndex = -1;

    if (!routes.length) {
        setStatus('No routes found between those points.');
        return;
    }

    for (let i = routes.length - 1; i >= 0; i--) {
        const color = ROUTE_COLORS[i % ROUTE_COLORS.length];
        const poly  = L.polyline(routes[i].coords, {
            color, weight: i === 0 ? 6 : 4, opacity: i === 0 ? 0.9 : 0.65,
        }).addTo(map);
        poly.on('click', () => activateRoute(i));
        routeLayers[i] = poly;
    }

    if (routeLayers[0]) map.fitBounds(routeLayers[0].getBounds(), { padding: [60, 60] });
    routes.forEach((route, i) => buildRouteCard(route, i));
    routesContainerEl.classList.add('visible');

    // Ensure mask stays on top of thermal layer but routes are above mask
    if (maskLayer) maskLayer.bringToFront();
    routeLayers.forEach(l => l.bringToFront());
    bringPinsToFront();
}

// ── Build route card ──────────────────────────────────────────────────────────
function buildRouteCard(route, i) {
    const color   = ROUTE_COLORS[i % ROUTE_COLORS.length];
    const distKm  = (route.distance_m / 1000).toFixed(1);
    const durText = formatDuration(route.duration_s);
    const hasTemp = route.avg_temp_c != null;

    const card = document.createElement('div');
    card.className = 'route-item';
    card.id = `route-card-${i}`;
    card.style.setProperty('--route-color', color);

    let thermalDetails = '';
    let riskBadge = '';

    if (hasTemp) {
        const exposureDegMin = route.thermal_exposure ?? 0;
        const exposurePct = route.exposure_pct ?? 0;
        const maxTemp = route.max_temp_c ?? route.avg_temp_c;
        const stressScore = route.thermal_stress_score ?? 0;

        thermalDetails = `
            <div class="route-stats thermal-row">
                <div class="stat" title="Peak road surface temperature">
                    <div class="stat-label">Peak Temp</div>
                    <div class="stat-value">${maxTemp}<span class="stat-unit">°C</span></div>
                </div>
                <div class="stat" title="Cumulative degree-minutes above threshold">
                    <div class="stat-label">Exposure</div>
                    <div class="stat-value">${exposureDegMin}<span class="stat-unit">°C·m</span></div>
                </div>
                <div class="stat" title="Percentage of journey spent above safe threshold">
                    <div class="stat-label">In Hotspot</div>
                    <div class="stat-value">${exposurePct}<span class="stat-unit">%</span></div>
                </div>
            </div>`;

        const riskInfo = {
            low:      { cls: 'risk-low',      icon: '🟢', label: 'Low Thermal Stress' },
            moderate: { cls: 'risk-moderate', icon: '🟡', label: 'Moderate Thermal Stress' },
            high:     { cls: 'risk-high',     icon: '🔴', label: 'High Thermal Stress' },
        }[route.risk_level] || { cls: 'risk-low', icon: '🟢', label: 'Low Stress' };

        riskBadge = `
            <div class="risk-badge ${riskInfo.cls}">
                ${riskInfo.icon} ${riskInfo.label} · Stress Score: ${stressScore}
            </div>`;
    }

    card.innerHTML = `
        <div class="route-header">
            <div class="route-dot"></div>
            <span class="route-label">Route ${i + 1}</span>
            ${i === 0 ? '<span class="route-badge">Best</span>' : ''}
        </div>
        <div class="route-stats">
            <div class="stat">
                <div class="stat-label">Distance</div>
                <div class="stat-value">${distKm}<span class="stat-unit">km</span></div>
            </div>
            <div class="stat">
                <div class="stat-label">Est. Time</div>
                <div class="stat-value">${durText}</div>
            </div>
            ${hasTemp ? `
            <div class="stat">
                <div class="stat-label">Avg Temp</div>
                <div class="stat-value">${route.avg_temp_c}<span class="stat-unit">°C</span></div>
            </div>` : ''}
        </div>
        ${thermalDetails}
        ${riskBadge}
    `;

    card.addEventListener('mouseenter', () => highlightRoute(i));
    card.addEventListener('mouseleave', () => { if (activeRouteIndex !== i) restoreHighlights(); });
    card.addEventListener('click', () => activateRoute(i));
    routesListEl.appendChild(card);
}

// ── Highlight / activate ──────────────────────────────────────────────────────
function highlightRoute(index) {
    routeLayers.forEach((l, i) => {
        l.setStyle(i === index
            ? { weight: 8, opacity: 1 }
            : { opacity: 0.2 });
        if (i === index) l.bringToFront();
    });
    bringPinsToFront();
}

function activateRoute(index) {
    activeRouteIndex = index;
    document.querySelectorAll('.route-item').forEach((card, i) =>
        card.classList.toggle('active', i === index));
    if (routeLayers[index]) map.fitBounds(routeLayers[index].getBounds(), { padding: [60, 60] });
    highlightRoute(index);
}

function restoreHighlights() {
    routeLayers.forEach((l, i) => {
        l.setStyle({ weight: i === 0 ? 6 : 4, opacity: i === 0 ? 0.9 : 0.65 });
    });
    for (let i = routeLayers.length - 1; i >= 0; i--) {
        if (routeLayers[i]) routeLayers[i].bringToFront();
    }
    bringPinsToFront();
}

function bringPinsToFront() {
    if (sourceMarker) sourceMarker.setZIndexOffset(1000);
    if (destMarker)   destMarker.setZIndexOffset(1000);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(text) { statusEl.textContent = text; }

function formatDuration(seconds) {
    if (seconds == null) return '–';
    const h = Math.floor(seconds / 3600);
    const m = Math.round((seconds % 3600) / 60);
    if (h > 0) return `${h}<span class="stat-unit">h</span> ${m}<span class="stat-unit">m</span>`;
    return `${m}<span class="stat-unit">m</span>`;
}
