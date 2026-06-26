/* Transit Balance — Abu Dhabi bus demand vs. allocation */
const MAP = L.map("map", { zoomControl: true, preferCanvas: true }).setView([24.43, 54.45], 11);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '© OpenStreetMap © CARTO', subdomains: "abcd", maxZoom: 19,
}).addTo(MAP);

const state = {
  layer: "routes", summary: null, briefs: {},
  routes: null, stops: null, routeById: {}, stopById: {},
};

/* gap (-100..100) -> blue (over) … grey … red (under) */
function gapColor(g) {
  const t = Math.max(-100, Math.min(100, g)) / 100;
  if (t >= 0) return `rgb(255,${Math.round(150 - 90 * t)},${Math.round(150 - 90 * t)})`;
  return `rgb(${Math.round(150 + 90 * t)},${Math.round(150 + 30 * t)},255)`;
}
const fmt = (n) => Math.round(n).toLocaleString();

async function load() {
  const [summary, routes, stops, briefs] = await Promise.all([
    fetch("data/summary.json").then(r => r.json()),
    fetch("data/routes.geojson").then(r => r.json()),
    fetch("data/stops.geojson").then(r => r.json()),
    fetch("data/briefs.json").then(r => r.ok ? r.json() : {}).catch(() => ({})),
  ]);
  state.summary = summary;
  state.briefs = briefs;
  renderKpis(summary);
  buildRoutes(routes);
  buildStops(stops);
  showLayer("routes");
}

function renderKpis(s) {
  document.getElementById("kpis").innerHTML = `
    <div class="kpi"><div class="n">${s.n_routes_metro}</div><div class="l">routes analysed</div></div>
    <div class="kpi"><div class="n">${fmt(s.n_stops_metro)}</div><div class="l">stops</div></div>
    <div class="kpi add"><div class="n">${s.n_underserved}</div><div class="l">under-served routes</div></div>
    <div class="kpi cut"><div class="n">${s.n_overserved}</div><div class="l">over-served routes</div></div>`;
}

/* ----- lists update with the active layer ----- */
function renderLists() {
  const s = state.summary;
  const under = document.getElementById("list-under");
  const over = document.getElementById("list-over");
  document.getElementById("h-under").innerHTML =
    `Most under-served ${state.layer === "routes" ? "routes" : "stops"} <span class="pill add">add buses</span>`;
  document.getElementById("h-over").innerHTML =
    `Most over-served ${state.layer === "routes" ? "routes" : "stops"} <span class="pill cut">redeploy</span>`;

  if (state.layer === "routes") {
    const mk = (r) => {
      const cls = r.rec_delta > 0 ? "add" : "cut";
      const sign = r.rec_delta > 0 ? "+" : "";
      return `<li data-kind="route" data-key="${r.route}">
        <span><span class="rid">${r.route}</span> <span class="sub">${(r.headsign || "").slice(0, 24)}</span></span>
        <span class="delta ${cls}">${sign}${r.rec_delta}/day</span></li>`;
    };
    under.innerHTML = s.top_underserved.slice(0, 6).map(mk).join("");
    over.innerHTML = s.top_overserved.slice(0, 6).map(mk).join("");
  } else {
    const mk = (st) => {
      const cls = st.gap > 0 ? "add" : "cut";
      const sign = st.gap > 0 ? "+" : "";
      return `<li data-kind="stop" data-key="${st.stop_id}">
        <span><span class="rid" style="font-weight:600">${(st.name || "(unnamed)").slice(0, 28)}</span></span>
        <span class="delta ${cls}">${sign}${st.gap}</span></li>`;
    };
    under.innerHTML = s.top_underserved_stops.slice(0, 6).map(mk).join("");
    over.innerHTML = s.top_overserved_stops.slice(0, 6).map(mk).join("");
  }
  document.querySelectorAll(".routelist li").forEach(li => {
    li.onclick = () => li.dataset.kind === "route"
      ? selectRoute(li.dataset.key) : selectStop(li.dataset.key);
  });
}

function buildRoutes(geo) {
  state.routes = L.geoJSON(geo, {
    style: f => ({ color: gapColor(f.properties.gap), weight: 2 + Math.abs(f.properties.gap) / 18, opacity: 0.85 }),
    onEachFeature: (f, lyr) => {
      state.routeById[f.properties.name] = lyr;
      lyr.on("click", () => selectRoute(f.properties.name));
      lyr.bindTooltip(`Route ${f.properties.name} · gap ${f.properties.gap > 0 ? "+" : ""}${f.properties.gap}`, { sticky: true });
    },
  });
}

function buildStops(geo) {
  state.stops = L.geoJSON(geo, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: 2.5 + f.properties.demand_pct / 22, fillColor: gapColor(f.properties.gap),
      color: "#0b0f14", weight: 0.5, fillOpacity: 0.85,
    }),
    onEachFeature: (f, lyr) => {
      const p = f.properties;
      state.stopById[p.stop_id] = lyr;
      lyr.bindPopup(`<b>${p.name || "(unnamed stop)"}</b><br/>
        Demand ${p.demand_pct} pct · Supply ${p.supply_pct} pct · Gap <b>${p.gap > 0 ? "+" : ""}${p.gap}</b><br/>
        <span style="color:var(--mut)">${fmt(p.pop_catch)} residents · ${p.gen_catch} generators · ${p.supply_trips} trips/wk within 500m</span>`);
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
  if (name === "routes") { MAP.removeLayer(state.stops); state.routes.addTo(MAP); }
  else { MAP.removeLayer(state.routes); state.stops.addTo(MAP); }
  renderLists();
}

function selectRoute(name) {
  if (state.layer !== "routes") showLayer("routes");
  const lyr = state.routeById[name];
  if (!lyr) return;
  state.routes.setStyle(f => ({
    color: gapColor(f.properties.gap), weight: 2 + Math.abs(f.properties.gap) / 18,
    opacity: f.properties.name === name ? 1 : 0.15,
  }));
  lyr.setStyle({ weight: 7, opacity: 1, color: "#fff" });
  lyr.bringToFront();
  MAP.fitBounds(lyr.getBounds(), { padding: [60, 60], maxZoom: 13 });
  renderRouteDetail(lyr.feature.properties);
}

function selectStop(stopId) {
  if (state.layer !== "stops") showLayer("stops");
  const lyr = state.stopById[stopId];
  if (!lyr) return;
  MAP.setView(lyr.getLatLng(), 15);
  lyr.openPopup();
  renderStopDetail(lyr.feature.properties);
}

function renderRouteDetail(p) {
  const el = document.getElementById("detail");
  el.classList.remove("hidden");
  const add = p.action === "increase", cut = p.action === "reduce";
  const recClass = add ? "add" : cut ? "cut" : "maintain";
  const verb = add ? "Add service" : cut ? "Redeploy capacity" : "Maintain";
  const deltaTxt = p.rec_delta > 0 ? `+${p.rec_delta}` : `${p.rec_delta}`;
  const brief = state.briefs[p.route_id];
  el.innerHTML = `
    <div class="rname">Route ${p.name}</div>
    <div class="rsign">${p.headsign || "Abu Dhabi"}</div>
    <div class="row"><span>Demand percentile (total ridership)</span><b>${p.demand_pct}</b></div>
    <div class="row"><span>Supply percentile (frequency)</span><b>${p.supply_pct}</b></div>
    <div class="row"><span>Service gap</span><b style="color:${gapColor(p.gap)}">${p.gap > 0 ? "+" : ""}${p.gap}</b></div>
    <div class="row"><span>Current daily trips</span><b>${p.daily_trips}</b></div>
    <div class="row"><span>Est. boardings / trip (load)</span><b>${fmt(p.boardings_per_trip)}</b></div>
    <div class="row"><span>Est. annual boardings</span><b>${fmt(p.est_annual_trips)}</b></div>
    <div class="rec ${recClass}">
      <span class="big">${verb}: ${p.daily_trips} → ${p.rec_daily_trips} trips/day (${deltaTxt})</span>
      ${add ? "Load per trip is above the network median — likely crowding." :
         cut ? "Load per trip is below the network median — capacity can move to busier routes." :
         "Load per trip is near the network median."}
    </div>
    ${brief ? `<div class="brief"><span class="ai">AI briefing</span><br/>${brief}</div>` : ""}`;
}

function renderStopDetail(p) {
  const el = document.getElementById("detail");
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="rname">${p.name || "(unnamed stop)"}</div>
    <div class="rsign">Bus stop</div>
    <div class="row"><span>Demand percentile</span><b>${p.demand_pct}</b></div>
    <div class="row"><span>Supply percentile</span><b>${p.supply_pct}</b></div>
    <div class="row"><span>Service gap</span><b style="color:${gapColor(p.gap)}">${p.gap > 0 ? "+" : ""}${p.gap}</b></div>
    <div class="row"><span>Residents within 500m</span><b>${fmt(p.pop_catch)}</b></div>
    <div class="row"><span>Trip generators within 500m</span><b>${p.gen_catch}</b></div>
    <div class="row"><span>Bus trips / week here</span><b>${p.supply_trips}</b></div>
    <div class="rec ${p.gap > 25 ? "add" : p.gap < -25 ? "cut" : "maintain"}">
      ${p.gap > 25 ? "High local demand with light service — a candidate for added frequency or a new route." :
        p.gap < -25 ? "Heavily served relative to local demand (often an interchange/through-route hub)." :
        "Local demand and service are roughly balanced."}
    </div>`;
}

document.querySelectorAll("#layer-toggle button").forEach(b => { b.onclick = () => showLayer(b.dataset.layer); });
load();
