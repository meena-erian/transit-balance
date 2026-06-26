/* Transit Balance — Abu Dhabi bus demand vs. allocation (multi-method) */
const MAP = L.map("map", { zoomControl: true, preferCanvas: true }).setView([24.43, 54.45], 11);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19,
}).addTo(MAP);

const state = {
  layer: "routes", method: "centrality", methods: [], meta: null,
  routes: null, stops: null, zones: null, validation: null,
  routeById: {}, stopById: {}, routeFeat: [], stopFeat: [],
};

/* per-method accessors */
const D = (p) => p[`d_${state.method}`];          // demand percentile
const G = (p) => p[`g_${state.method}`];          // gap = demand - supply
const labelOf = (key) => (state.methods.find(m => m.key === key) || {}).label || key;
const methodLabel = () => labelOf(state.method);

/* gap (-100..100) -> blue (over) … grey … red (under) */
function gapColor(g) {
  const t = Math.max(-100, Math.min(100, g)) / 100;
  if (t >= 0) return `rgb(255,${Math.round(150 - 90 * t)},${Math.round(150 - 90 * t)})`;
  return `rgb(${Math.round(150 + 90 * t)},${Math.round(150 + 30 * t)},255)`;
}
const fmt = (n) => Math.round(n).toLocaleString();
const fmtK = (n) => n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${Math.round(n)}`;

async function load() {
  const [methods, routes, stops, validation] = await Promise.all([
    fetch("data/methods.json").then(r => r.json()),
    fetch("data/routes.geojson").then(r => r.json()),
    fetch("data/stops.geojson").then(r => r.json()),
    fetch("data/validation.json").then(r => r.ok ? r.json() : null).catch(() => null),
  ]);
  state.methods = methods.methods;
  state.meta = methods.meta;
  state.routeFeat = routes.features;
  state.stopFeat = stops.features;
  state.validation = validation;

  const sel = document.getElementById("method");
  sel.innerHTML = state.methods.map(m => `<option value="${m.key}">${m.label}</option>`).join("");
  sel.value = state.method;
  sel.onchange = () => { state.method = sel.value; applyMethod(); };

  buildRoutes(routes);
  buildStops(stops);
  buildZones();
  applyMethod();
  showLayer("routes");
}

/* draw the 4 Abu Dhabi Link service zones (real revealed-demand) */
function buildZones() {
  if (!state.validation) return;
  state.zones = L.layerGroup();
  state.validation.zones.forEach(z => {
    L.circle([z.lat, z.lon], {
      radius: z.radius_km * 1000, color: "#f5c451", weight: 1.5,
      dashArray: "5 6", fill: true, fillColor: "#f5c451", fillOpacity: 0.05, interactive: true,
    }).bindTooltip(() => {
      const cal = z[`cal_${state.method}`] || 0;
      return `<b>${z.name}</b> — Abu Dhabi Link<br/>Real: ${fmt(z.rides_2023)} rides (2023)<br/>${methodLabel()} calibrated: ${fmt(cal)}`;
    }, { sticky: true, direction: "top" }).addTo(state.zones);
    L.marker([z.lat, z.lon], {
      interactive: false,
      icon: L.divIcon({ className: "zone-label", html: `${z.name}<br/>${fmt(z.rides_2023)}`, iconSize: [110, 30] }),
    }).addTo(state.zones);
  });
  state.zones.addTo(MAP);
}

function zonesToFront() {
  if (state.zones) state.zones.eachLayer(l => l.bringToFront && l.bringToFront());
}

/* recompute colors, KPIs, lists for the active method */
function applyMethod() {
  document.getElementById("method-hint").textContent =
    (state.methods.find(m => m.key === state.method) || {}).desc || "";
  if (state.routes) state.routes.setStyle(routeStyle);
  if (state.stops) state.stops.setStyle(stopStyle);
  renderKpis();
  renderLists();
  renderReality();
  zonesToFront();
  document.getElementById("detail").classList.add("hidden");
  hideToast();
}

function renderReality() {
  const v = state.validation;
  const el = document.getElementById("reality");
  if (!v) { el.innerHTML = ""; return; }
  const best = v.fits[0];
  const cur = v.fits.find(f => f.key === state.method);
  const isBest = best.key === state.method;
  const zones = v.zones.slice().sort((a, b) => b.rides_2023 - a.rides_2023);
  const maxR = Math.max(...zones.map(z => Math.max(z.rides_2023, z[`cal_${state.method}`] || 0)));
  const rows = zones.map(z => {
    const cal = z[`cal_${state.method}`] || 0;
    return `<div class="zrow">
      <span class="zn" title="${z.name}">${z.name}</span>
      <span class="zbars"><i class="obs" style="width:${(z.rides_2023 / maxR * 100).toFixed(0)}%"></i><i class="prd" style="width:${Math.min(100, cal / maxR * 100).toFixed(0)}%"></i></span>
      <span class="zv">${fmtK(z.rides_2023)} / ${fmtK(cal)}</span></div>`;
  }).join("");
  el.innerHTML = `
    <h3>Reality check <span class="pill" style="background:rgba(245,196,81,.18);color:#f5c451">Abu Dhabi Link</span></h3>
    <p class="hint" style="margin:0 2px">Demand is calibrated to real Link ride totals (${fmt(v.obs_total_2023)} rides, 2023, 4 zones). Link runs where buses are thin.</p>
    <div class="fitline ${isBest ? "good" : ""}">
      <b>${methodLabel()}</b> vs reality: rank corr <b>${cur ? cur.spearman.toFixed(2) : "–"}</b>
      ${isBest ? "· best fit ✓" : `· best is <b>${labelOf(best.key)}</b> (${best.spearman.toFixed(2)})`}
    </div>
    ${rows}
    <div class="keys"><span><i class="obs"></i>real rides</span><span><i class="prd"></i>${methodLabel()} calibrated</span></div>`;
}

function renderKpis() {
  const under = state.routeFeat.filter(f => G(f.properties) >= 25).length;
  const over = state.routeFeat.filter(f => G(f.properties) <= -25).length;
  document.getElementById("kpis").innerHTML = `
    <div class="kpi"><div class="n">${state.meta.n_routes}</div><div class="l">routes analysed</div></div>
    <div class="kpi"><div class="n">${fmt(state.meta.n_stops)}</div><div class="l">stops</div></div>
    <div class="kpi add"><div class="n">${under}</div><div class="l">under-served routes</div></div>
    <div class="kpi cut"><div class="n">${over}</div><div class="l">over-served routes</div></div>`;
}

function renderLists() {
  const under = document.getElementById("list-under");
  const over = document.getElementById("list-over");
  const isRoutes = state.layer === "routes";
  document.getElementById("h-under").innerHTML =
    `Most under-served ${isRoutes ? "routes" : "stops"} <span class="pill add">add buses</span>`;
  document.getElementById("h-over").innerHTML =
    `Most over-served ${isRoutes ? "routes" : "stops"} <span class="pill cut">redeploy</span>`;

  const feats = (isRoutes ? state.routeFeat : state.stopFeat)
    .map(f => f.properties).slice();
  feats.sort((a, b) => G(b) - G(a));

  if (isRoutes) {
    const mk = (p) => {
      const dx = p[`rx_${state.method}`];
      const cls = dx > 0 ? "add" : "cut";
      return `<li data-kind="route" data-key="${p.name}">
        <span><span class="rid">${p.name}</span> <span class="sub">${(p.headsign || "").slice(0, 24)}</span></span>
        <span class="delta ${cls}">${dx > 0 ? "+" : ""}${dx}/day</span></li>`;
    };
    under.innerHTML = feats.slice(0, 6).map(mk).join("");
    over.innerHTML = feats.slice(-6).reverse().map(mk).join("");
  } else {
    const mk = (p) => {
      const g = G(p);
      const cls = g > 0 ? "add" : "cut";
      return `<li data-kind="stop" data-key="${p.stop_id}">
        <span><span class="rid" style="font-weight:600">${(p.name || "(unnamed)").slice(0, 28)}</span></span>
        <span class="delta ${cls}">${g > 0 ? "+" : ""}${g}</span></li>`;
    };
    under.innerHTML = feats.slice(0, 6).map(mk).join("");
    over.innerHTML = feats.slice(-6).reverse().map(mk).join("");
  }
  document.querySelectorAll(".routelist li").forEach(li => {
    li.onclick = () => li.dataset.kind === "route"
      ? selectRoute(li.dataset.key) : selectStop(li.dataset.key);
  });
}

const routeStyle = (f) => ({ color: gapColor(G(f.properties)), weight: 2 + Math.abs(G(f.properties)) / 18, opacity: 0.85 });
const stopStyle = (f) => ({ radius: 2.5 + D(f.properties) / 22, fillColor: gapColor(G(f.properties)), color: "#0b0f14", weight: 0.5, fillOpacity: 0.85 });

function buildRoutes(geo) {
  state.routes = L.geoJSON(geo, {
    style: routeStyle,
    onEachFeature: (f, lyr) => {
      state.routeById[f.properties.name] = lyr;
      lyr.on("click", () => selectRoute(f.properties.name));
      lyr.bindTooltip(() => `Route ${f.properties.name} · gap ${G(f.properties) > 0 ? "+" : ""}${G(f.properties)}`, { sticky: true });
    },
  });
}

function buildStops(geo) {
  state.stops = L.geoJSON(geo, {
    pointToLayer: (f, ll) => L.circleMarker(ll, stopStyle(f)),
    onEachFeature: (f, lyr) => {
      state.stopById[f.properties.stop_id] = lyr;
      lyr.on("click", () => selectStop(f.properties.stop_id));
    },
  });
}

function showLayer(name) {
  state.layer = name;
  document.querySelectorAll("#layer-toggle button").forEach(b => b.classList.toggle("active", b.dataset.layer === name));
  document.getElementById("layer-hint").textContent = name === "routes"
    ? "Lines colored by service gap. Red = under-served, blue = over-served."
    : "Dots sized by demand, colored by gap. Click for catchment detail.";
  document.getElementById("detail").classList.add("hidden");
  hideToast();
  if (name === "routes") { MAP.removeLayer(state.stops); state.routes.addTo(MAP); }
  else { MAP.removeLayer(state.routes); state.stops.addTo(MAP); }
  zonesToFront();
  renderLists();
}

function selectRoute(name) {
  if (state.layer !== "routes") showLayer("routes");
  const lyr = state.routeById[name];
  if (!lyr) return;
  state.routes.setStyle(f => ({ ...routeStyle(f), opacity: f.properties.name === name ? 1 : 0.15 }));
  lyr.setStyle({ weight: 7, opacity: 1, color: "#fff" });
  lyr.bringToFront();
  const p = lyr.feature.properties;
  renderRouteDetail(p);
  if (isMobile()) {
    setCollapsed(true);                       // focus the map
    const r = routeRecInfo(p);
    showToast(`Route ${p.name}`, r.headline, r.cls);
  }
  setTimeout(() => MAP.fitBounds(lyr.getBounds(), { padding: [60, 60], maxZoom: 13 }), isMobile() ? 80 : 0);
}

function selectStop(stopId) {
  if (state.layer !== "stops") showLayer("stops");
  const lyr = state.stopById[stopId];
  if (!lyr) return;
  const p = lyr.feature.properties;
  renderStopDetail(p);
  if (isMobile()) {
    setCollapsed(true);
    const g = G(p);
    const cls = g > 25 ? "add" : g < -25 ? "cut" : "maintain";
    const txt = g > 25 ? `Under-served · gap +${g}` : g < -25 ? `Over-served · gap ${g}` : `Balanced · gap ${g > 0 ? "+" : ""}${g}`;
    showToast(p.name || "Bus stop", txt, cls);
  }
  setTimeout(() => MAP.setView(lyr.getLatLng(), 15), isMobile() ? 80 : 0);
}

/* recommendation summary for a route under the active method */
function routeRecInfo(p) {
  const dx = p[`rx_${state.method}`], recd = p[`rd_${state.method}`];
  const add = dx > 0, cut = dx < 0;
  const verb = add ? "Add service" : cut ? "Redeploy capacity" : "Maintain";
  return {
    dx, recd, add, cut, verb,
    cls: add ? "add" : cut ? "cut" : "maintain",
    headline: `${verb}: ${p.daily_trips} → ${recd} trips/day (${dx > 0 ? "+" : ""}${dx})`,
  };
}

function renderRouteDetail(p) {
  const el = document.getElementById("detail");
  el.classList.remove("hidden");
  const g = G(p), est = p[`e_${state.method}`];
  const r = routeRecInfo(p);
  el.innerHTML = `
    <div class="rname">Route ${p.name}</div>
    <div class="rsign">${p.headsign || "Abu Dhabi"} · model: ${methodLabel()}</div>
    <div class="row"><span>Est. annual demand <span style="color:#f5c451">(Link-calibrated)</span></span><b>${fmt(est)}</b></div>
    <div class="row"><span>Demand percentile</span><b>${D(p)}</b></div>
    <div class="row"><span>Supply percentile (frequency)</span><b>${p.supply_pct}</b></div>
    <div class="row"><span>Service gap</span><b style="color:${gapColor(g)}">${g > 0 ? "+" : ""}${g}</b></div>
    <div class="row"><span>Current daily trips</span><b>${p.daily_trips}</b></div>
    <div class="rec ${r.cls}">
      <span class="big">${r.headline}</span>
      ${r.add ? "Modeled demand outruns frequency — likely crowding, candidate for more buses." :
         r.cut ? "Frequency outruns modeled demand — capacity can move to busier routes." :
         "Demand and frequency are roughly balanced."}
    </div>
    <div class="brief"><span class="ai">Why this model</span><br/>${(state.methods.find(m => m.key === state.method) || {}).desc || ""}</div>`;
}

function renderStopDetail(p) {
  const el = document.getElementById("detail");
  el.classList.remove("hidden");
  const g = G(p);
  el.innerHTML = `
    <div class="rname">${p.name || "(unnamed stop)"}</div>
    <div class="rsign">Bus stop · model: ${methodLabel()}</div>
    <div class="row"><span>Est. annual demand <span style="color:#f5c451">(Link-calibrated)</span></span><b>${fmt(p[`e_${state.method}`])}</b></div>
    <div class="row"><span>Demand percentile</span><b>${D(p)}</b></div>
    <div class="row"><span>Supply percentile</span><b>${p.supply_pct}</b></div>
    <div class="row"><span>Service gap</span><b style="color:${gapColor(g)}">${g > 0 ? "+" : ""}${g}</b></div>
    <div class="row"><span>Residents within 500m</span><b>${fmt(p.pop_catch)}</b></div>
    <div class="row"><span>Trip generators within 500m</span><b>${p.gen_catch}</b></div>
    <div class="row"><span>Bus trips / week here</span><b>${p.supply_trips}</b></div>
    <div class="rec ${g > 25 ? "add" : g < -25 ? "cut" : "maintain"}">
      ${g > 25 ? "High modeled demand with light service — candidate for added frequency or a new route." :
        g < -25 ? "Heavily served relative to modeled demand (often an interchange/through hub)." :
        "Modeled demand and service are roughly balanced."}
    </div>`;
}

document.querySelectorAll("#layer-toggle button").forEach(b => { b.onclick = () => showLayer(b.dataset.layer); });

/* ---------- mobile panel + toast ---------- */
const isMobile = () => window.matchMedia("(max-width: 760px)").matches;
const panelToggle = document.getElementById("panel-toggle");
const toastEl = document.getElementById("toast");

function setCollapsed(collapsed) {
  const app = document.getElementById("app");
  app.classList.toggle("panel-collapsed", collapsed);
  panelToggle.innerHTML = collapsed ? "&#9650; Details" : "&#9660; Map";
  if (!collapsed) {
    hideToast();
    const d = document.getElementById("detail");
    if (!d.classList.contains("hidden")) setTimeout(() => d.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
  }
  setTimeout(() => MAP.invalidateSize(), 60);
}

function showToast(title, rec, cls) {
  toastEl.className = `toast ${cls} show`;
  toastEl.innerHTML = `<div class="t-title">${title}</div><div class="t-rec">${rec}</div><div class="t-hint">tap for details</div>`;
}
function hideToast() { toastEl.classList.remove("show"); }

panelToggle.onclick = () => setCollapsed(!document.getElementById("app").classList.contains("panel-collapsed"));
toastEl.onclick = () => setCollapsed(false);

let rsz;
window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => MAP.invalidateSize(), 150); });

load();
