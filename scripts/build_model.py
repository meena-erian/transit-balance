"""Demand model + gap engine for Abu Dhabi buses.

Pipeline
--------
1. SUPPLY  (real)     : GTFS trips serving each stop / route  = current allocation.
2. DEMAND  (modeled)  : gravity blend of real population (WorldPop, origins) and real
                        trip generators (OSM amenities, destinations) in a walking catchment.
3. GAP                : demand percentile - supply percentile.
                        +ve = under-served (add buses), -ve = over-served (redeploy).
4. CALIBRATION        : demand share x ITC's published ~95.2M annual bus trips (2024).
5. OUTPUT             : stops.geojson, routes.geojson, summary.json for the web app.

Run:  python3 scripts/build_model.py
"""
from __future__ import annotations

import json
from pathlib import Path

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
CATCHMENT_M = 500.0
ITC_ANNUAL_TRIPS_2024 = 95_200_000  # admobility.gov.ae, 2024

LAT0 = 24.45
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * np.cos(np.radians(LAT0))

# Trip-generation weights (relative pull of each amenity category).
GEN_WEIGHTS = {"education": 3.0, "healthcare": 2.5, "retail": 2.0,
               "community": 1.5, "services": 1.5, "mobility": 0.5}
# Demand blend: population (origins) vs generators (destinations).
W_POP, W_GEN = 0.5, 0.5


def to_xy(lon, lat):
    x = (np.asarray(lon, float) - BBOX["lon_min"]) * M_PER_DEG_LON
    y = (np.asarray(lat, float) - BBOX["lat_min"]) * M_PER_DEG_LAT
    return np.column_stack([x, y])


def in_bbox(lat, lon):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return ((lat >= BBOX["lat_min"]) & (lat <= BBOX["lat_max"])
            & (lon >= BBOX["lon_min"]) & (lon <= BBOX["lon_max"]))


def pct_rank(s: pd.Series) -> pd.Series:
    """0-100 percentile rank."""
    return s.rank(pct=True) * 100.0


def catchment_sum(stop_lon, stop_lat, cloud, radius_m):
    if len(cloud) == 0:
        return np.zeros(len(stop_lon))
    tree = cKDTree(to_xy(cloud[:, 0], cloud[:, 1]))
    stop_xy = to_xy(stop_lon, stop_lat)
    w = cloud[:, 2]
    out = np.zeros(len(stop_lon))
    for i, nbrs in enumerate(tree.query_ball_point(stop_xy, r=radius_m)):
        if nbrs:
            out[i] = w[nbrs].sum()
    return out


# ---------------------------------------------------------------------------
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
    m = in_bbox(df.latitude, df.longitude)
    df = df[m].copy()
    df["w"] = df["category"].map(GEN_WEIGHTS).fillna(1.0)
    return np.column_stack([df.longitude.values, df.latitude.values, df["w"].values]), df


def main():
    print("Loading GTFS...")
    stops = pd.read_csv(GTFS / "stops.txt")
    stops = stops[stops.location_type.fillna(0) == 0].copy()
    stops = stops[in_bbox(stops.stop_lat, stops.stop_lon)].reset_index(drop=True)

    trips = pd.read_csv(GTFS / "trips.txt")
    routes = pd.read_csv(GTFS / "routes.txt")
    st = pd.read_csv(GTFS / "stop_times.txt", usecols=["trip_id", "stop_id"])

    # Representative weekday daily trips (dominant service_id pattern).
    typical_service = trips.service_id.value_counts().idxmax()
    trips_typ = trips[trips.service_id == typical_service]
    print(f"  typical weekday service: {typical_service} ({len(trips_typ)} trips)")

    # SUPPLY: trips serving each stop (feed total) and per route.
    st_route = st.merge(trips[["trip_id", "route_id"]], on="trip_id", how="left")
    trips_per_stop = st.groupby("stop_id").size()
    stops["supply_trips"] = stops.stop_id.map(trips_per_stop).fillna(0)

    # DEMAND: population + generators in catchment.
    print("Building demand model (WorldPop x OSM generators)...")
    pop_cloud = load_population_cloud()
    gen_cloud, gen_df = load_generator_cloud()
    stops["pop_catch"] = catchment_sum(stops.stop_lon, stops.stop_lat, pop_cloud, CATCHMENT_M)
    stops["gen_catch"] = catchment_sum(stops.stop_lon, stops.stop_lat, gen_cloud, CATCHMENT_M)

    stops["pop_pct"] = pct_rank(stops["pop_catch"])
    stops["gen_pct"] = pct_rank(stops["gen_catch"])
    stops["demand_index"] = W_POP * stops["pop_pct"] + W_GEN * stops["gen_pct"]

    stops["demand_pct"] = pct_rank(stops["demand_index"])
    stops["supply_pct"] = pct_rank(stops["supply_trips"])
    stops["gap"] = stops["demand_pct"] - stops["supply_pct"]

    # ---- ROUTE LEVEL (handles interchange inflation) ----
    print("Aggregating to routes...")
    # stops per route (distinct)
    route_stops = st_route.merge(
        stops[["stop_id", "demand_index"]], on="stop_id", how="inner"
    ).drop_duplicates(["route_id", "stop_id"])
    route_demand = route_stops.groupby("route_id")["demand_index"].mean()  # avg corridor demand
    route_nstops = route_stops.groupby("route_id")["stop_id"].nunique()

    # supply per route: total feed trips + typical weekday daily trips
    route_trips_total = trips.groupby("route_id").size()
    route_trips_daily = trips_typ.groupby("route_id").size()

    rt = routes[["route_id", "route_short_name", "route_long_name", "route_type"]].copy()
    rt["demand_corridor"] = rt.route_id.map(route_demand)
    rt["n_stops_metro"] = rt.route_id.map(route_nstops).fillna(0)
    rt["trips_total"] = rt.route_id.map(route_trips_total).fillna(0)
    rt["daily_trips"] = rt.route_id.map(route_trips_daily).fillna(0)
    rt = rt[rt["n_stops_metro"] >= 3].copy()  # routes meaningfully in the metro

    rt["demand_pct"] = pct_rank(rt["demand_corridor"])
    rt["supply_pct"] = pct_rank(rt["daily_trips"].replace(0, np.nan)).fillna(0)
    rt["gap"] = rt["demand_pct"] - rt["supply_pct"]

    # Calibrated estimated annual boardings (demand share of ITC total).
    share = rt["demand_corridor"] * rt["n_stops_metro"]
    rt["est_annual_trips"] = (share / share.sum() * ITC_ANNUAL_TRIPS_2024).round(0)

    # Recommendation: scale daily trips toward demand/supply balance.
    ratio = (rt["demand_pct"] + 1) / (rt["supply_pct"] + 1)
    rt["rec_factor"] = ratio.clip(0.6, 1.8)
    rt["rec_daily_trips"] = (rt["daily_trips"] * rt["rec_factor"]).round(0)
    rt["rec_delta"] = rt["rec_daily_trips"] - rt["daily_trips"]

    def action(g):
        if g >= 25:
            return "increase"
        if g <= -25:
            return "reduce"
        return "maintain"
    rt["action"] = rt["gap"].apply(action)

    # ---- route geometry: representative (longest) shape per route ----
    print("Building route geometries...")
    shapes = pd.read_csv(GTFS / "shapes.txt")
    # most common shape per route
    rep_shape = (trips.groupby("route_id")["shape_id"]
                 .agg(lambda s: s.value_counts().idxmax()))
    shp_group = {sid: g.sort_values("shape_pt_sequence")[["shape_pt_lon", "shape_pt_lat"]].values
                 for sid, g in shapes.groupby("shape_id")}

    route_features = []
    for _, r in rt.iterrows():
        sid = rep_shape.get(r.route_id)
        coords = shp_group.get(sid)
        if coords is None or len(coords) < 2:
            continue
        route_features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[float(x), float(y)] for x, y in coords]},
            "properties": {
                "route_id": r.route_id,
                "name": str(r.route_short_name),
                "headsign": (str(r.route_long_name) if pd.notna(r.route_long_name) else ""),
                "demand_pct": round(float(r.demand_pct), 1),
                "supply_pct": round(float(r.supply_pct), 1),
                "gap": round(float(r.gap), 1),
                "daily_trips": int(r.daily_trips),
                "rec_daily_trips": int(r.rec_daily_trips),
                "rec_delta": int(r.rec_delta),
                "est_annual_trips": int(r.est_annual_trips),
                "n_stops": int(r.n_stops_metro),
                "action": r.action,
            },
        })

    # ---- stop features ----
    stop_features = []
    for _, s in stops.iterrows():
        stop_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(s.stop_lon), float(s.stop_lat)]},
            "properties": {
                "stop_id": s.stop_id,
                "name": s.stop_name,
                "demand_pct": round(float(s.demand_pct), 1),
                "supply_pct": round(float(s.supply_pct), 1),
                "gap": round(float(s.gap), 1),
                "pop_catch": int(s.pop_catch),
                "gen_catch": round(float(s.gen_catch), 1),
                "supply_trips": int(s.supply_trips),
            },
        })

    (OUT / "stops.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": stop_features}))
    (OUT / "routes.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": route_features}))

    # ---- summary ----
    under = rt.sort_values("gap", ascending=False).head(10)
    over = rt.sort_values("gap").head(10)

    def route_row(r):
        return {
            "route": str(r.route_short_name), "headsign": (str(r.route_long_name) if pd.notna(r.route_long_name) else ""),
            "gap": round(float(r.gap), 1), "daily_trips": int(r.daily_trips),
            "rec_daily_trips": int(r.rec_daily_trips), "rec_delta": int(r.rec_delta),
            "est_annual_trips": int(r.est_annual_trips), "demand_pct": round(float(r.demand_pct), 1),
            "supply_pct": round(float(r.supply_pct), 1),
        }

    summary = {
        "city": "Abu Dhabi",
        "generated_from": {
            "gtfs": "ITC Abu Dhabi (DMT), 135 routes / 3,368 stops",
            "population": "WorldPop 2025 100m (CC-BY)",
            "generators": "OpenStreetMap amenities (ODbL)",
            "calibration": f"ITC 2024 annual bus trips = {ITC_ANNUAL_TRIPS_2024:,}",
        },
        "n_stops_metro": int(len(stops)),
        "n_routes_metro": int(len(rt)),
        "n_underserved": int((rt.action == "increase").sum()),
        "n_overserved": int((rt.action == "reduce").sum()),
        "buses_to_add_daily_trips": int(rt.loc[rt.rec_delta > 0, "rec_delta"].sum()),
        "buses_to_cut_daily_trips": int(-rt.loc[rt.rec_delta < 0, "rec_delta"].sum()),
        "top_underserved": [route_row(r) for _, r in under.iterrows()],
        "top_overserved": [route_row(r) for _, r in over.iterrows()],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))

    print("\n=== RESULTS ===")
    print(f"  stops: {len(stops)} | routes: {len(rt)}")
    print(f"  under-served routes: {summary['n_underserved']} | over-served: {summary['n_overserved']}")
    print("\n  Top 5 UNDER-served routes (add service):")
    for r in summary["top_underserved"][:5]:
        print(f"    Route {r['route']:>4}  gap {r['gap']:+.0f}  daily {r['daily_trips']}->{r['rec_daily_trips']} "
              f"(+{r['rec_delta']})  ~{r['est_annual_trips']:,}/yr  {r['headsign'][:40]}")
    print("\n  Top 5 OVER-served routes (redeploy):")
    for r in summary["top_overserved"][:5]:
        print(f"    Route {r['route']:>4}  gap {r['gap']:+.0f}  daily {r['daily_trips']}->{r['rec_daily_trips']} "
              f"({r['rec_delta']})  {r['headsign'][:40]}")
    print(f"\nWrote -> {OUT.relative_to(ROOT)}/stops.geojson, routes.geojson, summary.json")


if __name__ == "__main__":
    main()
