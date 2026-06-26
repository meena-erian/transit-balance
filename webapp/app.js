/* Transit Balance — Abu Dhabi bus demand vs. allocation */
const MAP = L.map("map", { zoomControl: true, preferCanvas: true }).setView([24.43, 54.45], 11);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '© OpenStreetMap © CARTO', subdomains: "abcd", maxZoom: 19,
}).addTo(MAP);

const state = { layer: "routes", routes: null, stops: null, briefs: {}, byId: {}, selected: null };

/* gap (-100..100) -> color: blue (over) … grey … red (under) */
function gapColor(g) {
  const t = Math.max(-100, Math.min(100, g)) / 100; // -1..1
  if (t >= 0) { // under-served -> red
    const r = 255, gc = Math.round(150 - 90 * t), b = Math.round(150 - 90 * t);
    return `rgb(${r},${gc},${b})`;
  }
  const r = Math.round(150 + 90 * t), gc = Math.round(150 + 30 * t), b = 255; // over -> blue
  return `rgb(${r},${gc},${b})`;
}
const fmt = (n) => n.toLocaleString();

async function load() {
  const [summary, routes, stops, briefs] = await Promise.all([
    fetch("data/summary.json").then(r => r.json()),
    fetch("data/routes.geojson").then(r => r.json()),
    fetch("data/stops.geojson").then(r => r.json()),
    fetch("data/briefs.json").then(r => r.ok ? r.json() : {}).catch(() => ({})),
  ]);
  state.briefs = briefs;
  renderKpis(summary);
  renderLists(summary);
  buildRoutes(routes);
  buildStops(stops);
  showLayer("routes");
}

function renderKpis(s) {
  const el = document.getElementById("kpis");
  el.innerHTML = `
    <div class="kpi"><div class="n">${s.n_routes_metro}</div><div class="l">routes analysed</div></div>
    <div class="kpi"><div class="n">${fmt(s.n_stops_metro)}</div><div class="l">stops</div></div>
    <div class="kpi add"><div class="n">${s.n_underserved}</div><div class="l">under-served</div></div>
    <div class="kpi cut"><div class="n">${s.n_overserved}</div><div class="l">over-served</div></div>`;
}

function renderLists(s) {
  const mk = (r, kind) => {
    const d = r.rec_delta;
    const cls = d > 0 ? "add" : "cut";
    const sign = d > 0 ? "+" : "";
    return `<li data-route="${r.route}">
      <span><span class="rid">${r.route}</span> <span style="color:var(--mut)">${(r.headsign||"").slice(0,26)}</span></span>
      <span class="delta ${cls}">${sign}${d}/day</span></li>`;
  };
  document.getElementById("list-under").innerHTML = s.top_underserved.slice(0, 6).map(r => mk(r, "add")).join("");
  document.getElementById("list-over").innerHTML = s.top_overserved.slice(0, 6).map(r => mk(r, "cut")).join("");
  document.querySelectorAll(".routelist li").forEach(li => {
    li.onclick = () => selectRoute(li.dataset.route);
  });
}

function buildRoutes(geo) {
  state.routes = L.geoJSON(geo, {
    style: f => ({
      color: gapColor(f.properties.gap),
      weight: 2 + Math.abs(f.properties.gap) / 18,
      opacity: 0.85,
    }),
    onEachFeature: (f, lyr) => {
      state.byId[f.properties.name] = lyr;
      lyr.on("click", () => selectRoute(f.properties.name));
      lyr.bindTooltip(`Route ${f.properties.name} · gap ${f.properties.gap > 0 ? "+" : ""}${f.properties.gap}`, { sticky: true });
    },
  });
}

function buildStops(geo) {
  state.stops = L.geoJSON(geo, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: 2.5 + f.properties.demand_pct / 22,
      fillColor: gapColor(f.properties.gap),
      color: "#0b0f14", weight: 0.5, fillOpacity: 0.85,
    }),
    onEachFeature: (f, lyr) => {
      const p = f.properties;
      lyr.bindPopup(`<b>${p.name || "(unnamed stop)"}</b><br/>
        Demand ${p.demand_pct} pct · Supply ${p.supply_pct} pct<br/>
        Gap <b>${p.gap > 0 ? "+" : ""}${p.gap}</b><br/>
        <span style="color:var(--mut)">${fmt(p.pop_catch)} residents · ${p.gen_catch} generators · ${p.supply_trips} trips/wk in 500m</span>`);
    },
  });
}

function showLayer(name) {
  state.layer = name;
  document.querySelectorAll("#layer-toggle button").forEach(b => b.classList.toggle("active", b.dataset.layer === name));
  document.getElementById("layer-hint").textContent = name === "routes"
    ? "Lines colored by service gap. Red = under-served, blue = over-served."
    : "Dots sized by demand, colored by gap. Click for catchment detail.";
  if (name === "routes") { MAP.removeLayer(state.stops); state.routes.addTo(MAP); }
  else { MAP.removeLayer(state.routes); state.stops.addTo(MAP); }
}

function selectRoute(name) {
  if (state.layer !== "routes") showLayer("routes");
  const lyr = state.byId[name];
  if (!lyr) return;
  const p = lyr.feature.properties;
  // highlight
  state.routes.setStyle(f => ({
    color: gapColor(f.properties.gap),
    weight: 2 + Math.abs(f.properties.gap) / 18,
    opacity: f.properties.name === name ? 1 : 0.18,
  }));
  lyr.setStyle({ weight: 7, opacity: 1, color: "#fff" });
  lyr.bringToFront();
  MAP.fitBounds(lyr.getBounds(), { padding: [60, 60], maxZoom: 13 });
  renderDetail(p);
}

function renderDetail(p) {
  const el = document.getElementById("detail");
  el.classList.remove("hidden");
  const add = p.action === "increase", cut = p.action === "reduce";
  const recClass = add ? "add" : cut ? "cut" : "maintain";
  const verb = add ? "Add service" : cut ? "Redeploy capacity" : "Maintain";
  const deltaTxt = p.rec_delta > 0 ? `+${p.rec_delta}` : `${p.rec_delta}`;
  const brief = state.briefs[p.route_id];
  el.innerHTML = `
    <div class="rname">Route ${p.name}</div>
    <div class="rsign">${p.headsign || ""}</div>
    <div class="row"><span>Demand percentile</span><b>${p.demand_pct}</b></div>
    <div class="row"><span>Supply percentile</span><b>${p.supply_pct}</b></div>
    <div class="row"><span>Service gap</span><b style="color:${gapColor(p.gap)}">${p.gap > 0 ? "+" : ""}${p.gap}</b></div>
    <div class="row"><span>Current daily trips</span><b>${p.daily_trips}</b></div>
    <div class="row"><span>Est. annual boardings</span><b>${fmt(p.est_annual_trips)}</b></div>
    <div class="rec ${recClass}">
      <span class="big">${verb}: ${p.daily_trips} → ${p.rec_daily_trips} trips/day (${deltaTxt})</span>
      ${add ? "High corridor demand is outrunning current frequency." :
         cut ? "Frequency exceeds modeled corridor demand — capacity can move to under-served routes." :
         "Supply roughly matches demand on this corridor."}
    </div>
    ${brief ? `<div class="brief"><span class="ai">AI briefing</span><br/>${brief}</div>` : ""}`;
}

document.querySelectorAll("#layer-toggle button").forEach(b => {
  b.onclick = () => showLayer(b.dataset.layer);
});

load();
