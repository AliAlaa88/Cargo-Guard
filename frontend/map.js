// Initialize map centered on Rhode Island
const map = L.map('map').setView([41.5801, -71.4774], 10);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

// Colors for the 3 routes
const routeColors = ['#3b82f6', '#f97316', '#a855f7']; // Blue, Orange, Purple

let sourceMarker = null;
let destMarker = null;
let routeLayers = [];

const statusEl = document.getElementById('status');
const routesContainerEl = document.getElementById('routes-container');
const routesListEl = document.getElementById('routes-list');

// Custom icons
const createIcon = (color) => L.divIcon({
    className: 'custom-icon',
    html: `<div style="background-color: ${color}; width: 14px; height: 14px; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7]
});

const sourceIcon = createIcon('#22c55e'); // Green
const destIcon = createIcon('#ef4444');   // Red

map.on('click', function(e) {
    const lat = e.latlng.lat;
    const lng = e.latlng.lng;

    if (!sourceMarker) {
        // Set source
        sourceMarker = L.marker([lat, lng], {icon: sourceIcon}).addTo(map);
        statusEl.textContent = 'Source set. Click for destination.';
    } else if (!destMarker) {
        // Set destination
        destMarker = L.marker([lat, lng], {icon: destIcon}).addTo(map);
        statusEl.textContent = 'Finding routes...';
        findRoutes();
    } else {
        // Reset
        resetMap();
        sourceMarker = L.marker([lat, lng], {icon: sourceIcon}).addTo(map);
        statusEl.textContent = 'Source set. Click for destination.';
    }
});

function resetMap() {
    if (sourceMarker) map.removeLayer(sourceMarker);
    if (destMarker) map.removeLayer(destMarker);
    sourceMarker = null;
    destMarker = null;
    
    routeLayers.forEach(layer => map.removeLayer(layer));
    routeLayers = [];
    
    routesContainerEl.style.display = 'none';
    routesListEl.innerHTML = '';
    statusEl.textContent = 'Ready';
}

async function findRoutes() {
    const src = sourceMarker.getLatLng();
    const dst = destMarker.getLatLng();

    try {
        const response = await fetch('/route', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                src_lat: src.lat,
                src_lng: src.lng,
                dst_lat: dst.lat,
                dst_lng: dst.lng
            })
        });

        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'Failed to find route');
        }

        displayRoutes(data.routes);
        statusEl.textContent = `Found ${data.routes.length} routes.`;
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
    }
}

function displayRoutes(routes) {
    routeLayers.forEach(layer => map.removeLayer(layer));
    routeLayers = [];
    routesListEl.innerHTML = '';

    if (routes.length === 0) {
        statusEl.textContent = 'No routes found.';
        return;
    }

    // Draw routes in reverse order so the shortest (index 0) is on top
    for (let i = routes.length - 1; i >= 0; i--) {
        const route = routes[i];
        const color = routeColors[i % routeColors.length];
        
        const polyline = L.polyline(route.coords, {
            color: color,
            weight: i === 0 ? 6 : 4,
            opacity: i === 0 ? 0.9 : 0.7
        }).addTo(map);
        
        // Store layer for later manipulation
        // We put them in an array matching the route index
        routeLayers[i] = polyline;
    }
    
    // Fit map to show all routes
    if (routeLayers[0]) {
        map.fitBounds(routeLayers[0].getBounds(), {padding: [50, 50]});
    }

    // Create sidebar items
    routes.forEach((route, i) => {
        const color = routeColors[i % routeColors.length];
        const distanceKm = (route.distance_m / 1000).toFixed(2);
        
        const item = document.createElement('div');
        item.className = 'route-item';
        item.innerHTML = `
            <div class="route-title">
                <span class="route-color" style="background-color: ${color}"></span>
                Route ${i + 1}
            </div>
            <div class="route-dist">${distanceKm} km</div>
        `;
        
        item.addEventListener('mouseenter', () => highlightRoute(i));
        item.addEventListener('mouseleave', () => resetHighlights());
        
        routesListEl.appendChild(item);
    });

    routesContainerEl.style.display = 'flex';
    routesContainerEl.style.flexDirection = 'column';
}

function highlightRoute(index) {
    routeLayers.forEach((layer, i) => {
        if (i === index) {
            layer.setStyle({ weight: 8, opacity: 1 });
            layer.bringToFront();
        } else {
            layer.setStyle({ opacity: 0.3 });
        }
    });
    
    // Ensure pins are always on top
    if (sourceMarker) sourceMarker.bringToFront();
    if (destMarker) destMarker.bringToFront();
}

function resetHighlights() {
    routeLayers.forEach((layer, i) => {
        layer.setStyle({
            weight: i === 0 ? 6 : 4,
            opacity: i === 0 ? 0.9 : 0.7
        });
    });
    
    // Reset z-index so shortest is on top of longer ones
    for (let i = routeLayers.length - 1; i >= 0; i--) {
        if (routeLayers[i]) routeLayers[i].bringToFront();
    }
    
    // Pins on top
    if (sourceMarker) sourceMarker.bringToFront();
    if (destMarker) destMarker.bringToFront();
}
