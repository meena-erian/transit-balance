"""Multi-method demand model + gap engine for Abu Dhabi buses.

Demand for bus service cannot be measured (ridership/OD is ITC-internal), so we estimate
it FIVE independent ways and let the user compare them in the UI. Each method is principled
and standalone -- none is tuned to any external answer.

  residents     - WorldPop population within catchment      (trip origins / production)
  destinations  - OSM trip generators within catchment       (trip attraction)
  gravity       - spatial-interaction accessibility: sum of   (how much activity a stop
                  reachable attraction with distance decay     can reach)
  centrality    - betweenness on the bus-stop network         (structural through-traffic,
                                                                land-use independent)
  composite     - mean percentile of the four above           (ensemble)

For every method we compute, per stop and per route:
  demand percentile, gap = demand_pct - supply_pct, and (routes) calibrated annual
  boardings + a frequency recommendation based on load (boardings per trip).

SUPPLY is real throughout: GTFS trips (frequency) per stop / route.

Run:  python3 scripts/build_model.py
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import rasterio
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GTFS = RAW / "gtfs_itc_abudhabi"
KIT = RAW / "starter_kit"
OUT = ROOT / "webapp" / "data"
OUT.mkdir(parents=True, exist_ok=True)

BBOX = dict(lat_min=24.20, lat_max=24.62, lon_min=54.25, lon_max=54.80)
CATCHMENT_M = 500.0      # walking catchment for residents/destinations
GRAVITY_RADIUS_M = 2500.0
GRAVITY_DECAY_M = 800.0  # distance at which attraction weight falls to 1/e
ITC_ANNUAL_TRIPS_2024 = 95_200_000

LAT0 = 24.45
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * np.cos(np.radians(LAT0))

GEN_WEIGHTS = {"education": 3.0, "healthcare": 2.5, "retail": 2.0,
               "community": 1.5, "services": 1.5, "mobility": 0.5}

# Real Abu Dhabi Link (on-demand feeder) ride totals -- the only PUBLIC revealed-demand
# data for AD. Sources: ITC 2022 release (Zawya), AD Media Office 2023 ("1M passengers").
# Link runs where fixed buses are thin, so these are a proxy for latent demand by zone.
LINK_ZONES = [
    {"name": "Al Shahama",      "lat": 24.563, "lon": 54.692, "radius_km": 5.5, "rides_2022": 146951, "rides_2023": 146000},
    {"name": "Yas Island",      "lat": 24.497, "lon": 54.607, "radius_km": 5.0, "rides_2022": 97400,  "rides_2023": 84000},
    {"name": "Saadiyat Island", "lat": 24.545, "lon": 54.435, "radius_km": 4.5, "rides_2022": 34826,  "rides_2023": 53000},
    {"name": "Khalifa City",    "lat": 24.419, "lon": 54.578, "radius_km": 6.0, "rides_2022": None,    "rides_2023": 84000},
]

METHODS = [
    {"key": "residents",    "label": "Residents (origins)",      "desc": "WorldPop population within 500 m of the stop \u2014 where trips begin."},
    {"key": "destinations", "label": "Destinations (attraction)", "desc": "OSM trip generators (schools, malls, clinics\u2026) within 500 m \u2014 where people go."},
    {"key": "gravity",      "label": "Gravity accessibility",     "desc": "Sum of reachable activity with distance decay (\u2264 2.5 km) \u2014 how much a stop can reach."},
    {"key": "centrality",   "label": "Network centrality",        "desc": "Betweenness on the bus network \u2014 structural through-traffic, independent of land use."},
    {"key": "composite",    "label": "Composite (ensemble)",      "desc": "Average percentile of the other four methods."},
]


def to_xy(lon, lat):
    x = (np.asarray(lon, float) - BBOX["lon_min"]) * M_PER_DEG_LON
    y = (np.asarray(lat, float) - BBOX["lat_min"]) * M_PER_DEG_LAT
    return np.column_stack([x, y])


def dist_km(lat, lon, lat0, lon0):
    p = to_xy(lon, lat)
    c = to_xy([lon0], [lat0])[0]
    return np.linalg.norm(p - c, axis=1) / 1000.0


def in_bbox(lat, lon):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return ((lat >= BBOX["lat_min"]) & (lat <= BBOX["lat_max"])
            & (lon >= BBOX["lon_min"]) & (lon <= BBOX["lon_max"]))


def pct_rank(s):
    return pd.Series(s).rank(pct=True).values * 100.0


def load_population_cloud():
    with rasterio.open(RAW / "worldpop_are_2025_100m.tif") as ds:
        band = ds.read(1)
        nd = ds.nodata
        mask = (band > 0) if nd is None else ((band > 0) & (band != nd))
        rows, cols = np.where(mask)
        xs, ys = rasterio.transform.xy(ds.transform, rows, cols)
        vals = band[rows, cols]
    xs, ys, vals = np.array(xs), np.array(ys), np.array(vals, float)
    m = in_bbox(ys, xs)
    return np.column_stack([xs[m], ys[m], vals[m]])


def load_generator_cloud():
    df = pd.read_csv(KIT / "osm_amenities.csv")
    df = df[in_bbox(df.latitude, df.longitude)].copy()
    df["w"] = df["category"].map(GEN_WEIGHTS).fillna(1.0)
    return np.column_stack([df.longitude.values, df.latitude.values, df["w"].values])


def catchment_sum(stop_lon, stop_lat, cloud, radius_m):
    tree = cKDTree(to_xy(cloud[:, 0], cloud[:, 1]))
    stop_xy = to_xy(stop_lon, stop_lat)
    w = cloud[:, 2]
    out = np.zeros(len(stop_lon))
    for i, nbrs in enumerate(tree.query_ball_point(stop_xy, r=radius_m)):
        if nbrs:
            out[i] = w[nbrs].sum()
    return out


def gravity_access(stop_lon, stop_lat, cloud, radius_m, decay_m):
    """For each stop: sum_j w_j * exp(-d_ij / decay) over generators within radius."""
    gen_xy = to_xy(cloud[:, 0], cloud[:, 1])
    tree = cKDTree(gen_xy)
    stop_xy = to_xy(stop_lon, stop_lat)
    w = cloud[:, 2]
    out = np.zeros(len(stop_lon))
    for i, nbrs in enumerate(tree.query_ball_point(stop_xy, r=radius_m)):
        if nbrs:
            d = np.linalg.norm(gen_xy[nbrs] - stop_xy[i], axis=1)
            out[i] = (w[nbrs] * np.exp(-d / decay_m)).sum()
    return out


def betweenness(stops, st_seq):
    """Betweenness centrality on the bus-stop network (edges = consecutive stops on trips)."""
    metro = set(stops.stop_id)
    st_seq = st_seq.sort_values(["trip_id", "stop_sequence"])
    a = st_seq.stop_id.values
    t = st_seq.trip_id.values
    edges = set()
    for i in range(len(a) - 1):
        if t[i] == t[i + 1] and a[i] in metro and a[i + 1] in metro and a[i] != a[i + 1]:
            edges.add((a[i], a[i + 1]))
    G = nx.Graph()
    G.add_nodes_from(metro)
    G.add_edges_from(edges)
    # approximate betweenness for speed (sample of source nodes)
    k = min(500, G.number_of_nodes())
    bc = nx.betweenness_centrality(G, k=k, seed=42, normalized=True)
    return stops.stop_id.map(bc).fillna(0.0).values


def build_validation(stops, raw_keys):
    """Score each demand model against real Abu Dhabi Link ride totals (2023)."""
    zones = []
    for z in LINK_ZONES:
        d = dist_km(stops.stop_lat.values, stops.stop_lon.values, z["lat"], z["lon"])
        m = d <= z["radius_km"]
        rec = {**z, "n_stops": int(m.sum()),
               "supply_trips": int(stops.loc[m, "supply_trips"].sum())}
        for k in raw_keys:
            rec[f"pred_{k}"] = float(stops.loc[m, f"raw_{k}"].sum())
        zones.append(rec)

    obs = np.array([z["rides_2023"] for z in zones], float)
    obs_share = obs / obs.sum()
    for z, s in zip(zones, obs_share):
        z["obs_share"] = round(float(s), 3)

    fits = []
    for k in raw_keys:
        pred = np.array([z[f"pred_{k}"] for z in zones], float)
        if pred.sum() <= 0:
            continue
        pred_share = pred / pred.sum()
        for z, ps in zip(zones, pred_share):
            z[f"share_{k}"] = round(float(ps), 3)
        r = float(np.corrcoef(obs, pred)[0, 1]) if pred.std() > 0 else 0.0
        # Spearman (rank) correlation -- robust with only 4 zones
        ro = pd.Series(obs).rank().values
        rp = pd.Series(pred).rank().values
        rs = float(np.corrcoef(ro, rp)[0, 1]) if rp.std() > 0 else 0.0
        fits.append({"key": k, "pearson": round(r, 3), "spearman": round(rs, 3),
                     "share_err": round(float(np.abs(obs_share - pred_share).mean()), 3)})
    fits.sort(key=lambda f: (f["spearman"], f["pearson"]), reverse=True)
    return {"zones": zones, "fits": fits, "obs_total_2023": int(obs.sum()),
            "note": "Abu Dhabi Link on-demand ride totals (2023). Public revealed-demand "
                    "proxy; Link serves areas with sparse fixed-bus coverage."}


def main():
    print("Loading GTFS...")
    stops = pd.read_csv(GTFS / "stops.txt")
    stops = stops[stops.location_type.fillna(0) == 0].copy()
    stops = stops[in_bbox(stops.stop_lat, stops.stop_lon)].reset_index(drop=True)

    trips = pd.read_csv(GTFS / "trips.txt")
    routes = pd.read_csv(GTFS / "routes.txt")
    st = pd.read_csv(GTFS / "stop_times.txt",
                     usecols=["trip_id", "stop_id", "stop_sequence"])

    typical_service = trips.service_id.value_counts().idxmax()
    trips_typ = trips[trips.service_id == typical_service]

    st_route = st.merge(trips[["trip_id", "route_id"]], on="trip_id", how="left")
    stops["supply_trips"] = stops.stop_id.map(st.groupby("stop_id").size()).fillna(0)
    stops["supply_pct"] = pct_rank(stops["supply_trips"])

    # ---- per-stop raw demand for each method ----
    print("Computing demand methods...")
    pop_cloud = load_population_cloud()
    gen_cloud = load_generator_cloud()
    raw = {}
    raw["residents"] = catchment_sum(stops.stop_lon, stops.stop_lat, pop_cloud, CATCHMENT_M)
    raw["destinations"] = catchment_sum(stops.stop_lon, stops.stop_lat, gen_cloud, CATCHMENT_M)
    raw["gravity"] = gravity_access(stops.stop_lon, stops.stop_lat, gen_cloud,
                                    GRAVITY_RADIUS_M, GRAVITY_DECAY_M)
    print("  betweenness centrality...")
    raw["centrality"] = betweenness(stops, st_route[["trip_id", "stop_id", "stop_sequence"]])
    # composite = mean of percentile ranks of the four
    comp = np.mean([pct_rank(raw[k]) for k in ("residents", "destinations", "gravity", "centrality")], axis=0)
    raw["composite"] = comp

    for k in raw:
        stops[f"raw_{k}"] = raw[k]
        stops[f"d_{k}"] = pct_rank(raw[k])               # demand percentile
        stops[f"g_{k}"] = (stops[f"d_{k}"] - stops["supply_pct"]).round(1)  # gap

    stops["pop_catch"] = raw["residents"].astype(int)
    stops["gen_catch"] = raw["destinations"].round(1)

    print("Validating + calibrating models vs Abu Dhabi Link real ride totals...")
    validation = build_validation(stops, list(raw.keys()))

    # Calibrate each model to REAL rides: least-squares through origin over the 4 zones,
    # so model demand is expressed in real Link-anchored annual rides.
    zones = validation["zones"]
    R = np.array([z["rides_2023"] for z in zones], float)
    cal_factor = {}
    for k in raw:
        P = np.array([z[f"pred_{k}"] for z in zones], float)
        denom = float((P ** 2).sum())
        cal_factor[k] = float((P * R).sum() / denom) if denom > 0 else 0.0
        stops[f"cal_{k}"] = (stops[f"raw_{k}"] * cal_factor[k]).round(0)
        for z in zones:
            z[f"cal_{k}"] = int(round(z[f"pred_{k}"] * cal_factor[k]))
    validation["cal_factor"] = {k: cal_factor[k] for k in raw}
    (OUT / "validation.json").write_text(json.dumps(validation, indent=2))
    print("  model fit vs observed Link rides (best first):")
    for f in validation["fits"]:
        print(f"    {f['key']:13s} spearman {f['spearman']:+.2f}  pearson {f['pearson']:+.2f}  share_err {f['share_err']:.2f}")

    # ---- routes ----
    print("Aggregating to routes...")
    rs = st_route.drop_duplicates(["route_id", "stop_id"]).merge(
        stops[["stop_id"] + [f"raw_{k}" for k in raw]], on="stop_id", how="inner")
    route_nstops = rs.groupby("route_id")["stop_id"].nunique()
    route_daily = trips_typ.groupby("route_id").size()

    rt = routes[["route_id", "route_short_name", "route_long_name"]].copy()
    rt["n_stops"] = rt.route_id.map(route_nstops).fillna(0)
    rt["daily_trips"] = rt.route_id.map(route_daily).fillna(0)
    rt = rt[(rt.n_stops >= 3) & (rt.daily_trips >= 1)].copy()
    rt["supply_pct"] = pct_rank(rt["daily_trips"])

    for k in raw:
        dem_sum = rs.groupby("route_id")[f"raw_{k}"].sum()
        rt[f"raw_{k}"] = rt.route_id.map(dem_sum).fillna(0)
        rt[f"d_{k}"] = pct_rank(rt[f"raw_{k}"])
        rt[f"g_{k}"] = (rt[f"d_{k}"] - rt["supply_pct"]).round(1)
        # demand in REAL Link-anchored annual rides (route = sum of calibrated stop demand)
        est = rt[f"raw_{k}"] * cal_factor[k]
        rt[f"e_{k}"] = est.round(0)
        load = est / (rt["daily_trips"] * 365.0)
        factor = (load / load.median()).clip(0.6, 1.8)
        rt[f"rd_{k}"] = (rt["daily_trips"] * factor).round(0)
        rt[f"rx_{k}"] = (rt[f"rd_{k}"] - rt["daily_trips"]).astype(int)

    # ---- route geometry ----
    print("Route geometries...")
    shapes = pd.read_csv(GTFS / "shapes.txt")
    rep_shape = trips.groupby("route_id")["shape_id"].agg(lambda s: s.value_counts().idxmax())
    shp = {sid: g.sort_values("shape_pt_sequence")[["shape_pt_lon", "shape_pt_lat"]].values
           for sid, g in shapes.groupby("shape_id")}

    def mk_props(r):
        p = {"route_id": r.route_id, "name": str(r.route_short_name),
             "headsign": (str(r.route_long_name) if pd.notna(r.route_long_name) else ""),
             "daily_trips": int(r.daily_trips), "supply_pct": round(float(r.supply_pct), 1),
             "n_stops": int(r.n_stops)}
        for k in raw:
            p[f"d_{k}"] = round(float(r[f"d_{k}"]), 1)
            p[f"g_{k}"] = round(float(r[f"g_{k}"]), 1)
            p[f"e_{k}"] = int(r[f"e_{k}"])
            p[f"rd_{k}"] = int(r[f"rd_{k}"])
            p[f"rx_{k}"] = int(r[f"rx_{k}"])
        return p

    route_features = []
    for _, r in rt.iterrows():
        coords = shp.get(rep_shape.get(r.route_id))
        if coords is None or len(coords) < 2:
            continue
        route_features.append({"type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[float(x), float(y)] for x, y in coords]},
            "properties": mk_props(r)})

    stop_features = []
    for _, s in stops.iterrows():
        p = {"stop_id": s.stop_id, "name": s.stop_name if pd.notna(s.stop_name) else "(unnamed stop)",
             "supply_trips": int(s.supply_trips), "supply_pct": round(float(s.supply_pct), 1),
             "pop_catch": int(s.pop_catch), "gen_catch": round(float(s.gen_catch), 1)}
        for k in raw:
            p[f"d_{k}"] = round(float(s[f"d_{k}"]), 1)
            p[f"g_{k}"] = round(float(s[f"g_{k}"]), 1)
            p[f"e_{k}"] = int(s[f"cal_{k}"])
        stop_features.append({"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(s.stop_lon), float(s.stop_lat)]},
            "properties": p})

    (OUT / "stops.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": stop_features}))
    (OUT / "routes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": route_features}))
    (OUT / "methods.json").write_text(json.dumps({
        "methods": METHODS,
        "meta": {
            "gtfs": "ITC Abu Dhabi (DMT) \u2014 135 routes / 3,368 stops",
            "population": "WorldPop 2025 100m (CC-BY)",
            "generators": "OpenStreetMap amenities (ODbL)",
            "calibration": "Demand calibrated to real Abu Dhabi Link ride totals (2023, 4 zones)",
            "n_stops": int(len(stops)), "n_routes": int(len(rt)),
            "catchment_m": CATCHMENT_M,
        },
    }, indent=2))

    print(f"\nDone. {len(stops)} stops, {len(rt)} routes, {len(raw)} methods.")
    for m in METHODS:
        k = m["key"]
        u = (rt[f"g_{k}"] >= 25).sum()
        o = (rt[f"g_{k}"] <= -25).sum()
        top = rt.sort_values(f"g_{k}", ascending=False).iloc[0]
        print(f"  {k:13s} under {u:2d} / over {o:2d} | top under-served: route {top.route_short_name} (gap {top[f'g_{k}']:+.0f})")


if __name__ == "__main__":
    main()
