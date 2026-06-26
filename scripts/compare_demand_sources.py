"""Compare the QUALITY of different demand signals for Abu Dhabi bus stops.

For every real bus stop (from GTFS) we compute a demand score from each candidate
source within a walking catchment, then ask:
  1. Coverage   - what fraction of stops get a non-zero signal from this source?
  2. Resolution - how spatially granular is the source (can it tell two nearby stops apart)?
  3. Agreement  - how well does each source correlate with the others, and with the
                  city's *current* bus allocation (trips/stop from GTFS)?

The current allocation (GTFS trips serving a stop) is our real-world anchor: a good
demand proxy should correlate positively with where the city already runs buses.

Sources compared (all real except the synthetic baseline):
  WorldPop 2025   - 100m modeled population grid        (raster)
  HRSL general    - ~30m Meta/Columbia population        (points)
  HRSL children   - under-5 population (nursery/school)  (points)
  HRSL youth      - 15-24 population (student/commuter)  (points)
  OSM generators  - trip-attracting amenities, weighted  (points)
  Listings        - residential listing density          (points)
  Synthetic index - kit's district service_demand_index  (district-level baseline)

Run:  python3 scripts/compare_demand_sources.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GTFS = RAW / "gtfs_itc_abudhabi"
KIT = RAW / "starter_kit"
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

# Abu Dhabi metro focus area (island + Reem/Saadiyat/Yas + mainland MBZ/Mussafah/Khalifa).
# The GTFS covers the whole emirate incl. Al Ain & Al Dhafra; we scope to the city.
BBOX = dict(lat_min=24.20, lat_max=24.62, lon_min=54.25, lon_max=54.80)
CATCHMENT_M = 500.0  # walking catchment radius around each stop

# Equirectangular metres-per-degree at Abu Dhabi latitude (~24.45N).
LAT0 = 24.45
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * np.cos(np.radians(LAT0))


def to_xy(lon, lat):
    """Project lon/lat to local metres so we can use a Euclidean KDTree."""
    x = (np.asarray(lon) - BBOX["lon_min"]) * M_PER_DEG_LON
    y = (np.asarray(lat) - BBOX["lat_min"]) * M_PER_DEG_LAT
    return np.column_stack([x, y])


def in_bbox(lat, lon):
    return (
        (lat >= BBOX["lat_min"]) & (lat <= BBOX["lat_max"])
        & (lon >= BBOX["lon_min"]) & (lon <= BBOX["lon_max"])
    )


# ---------------------------------------------------------------------------
# 1. Bus stops + current allocation (the anchor)
# ---------------------------------------------------------------------------
def load_stops_with_allocation() -> pd.DataFrame:
    stops = pd.read_csv(GTFS / "stops.txt")
    stops = stops[stops.location_type.fillna(0) == 0]  # actual stops, not stations
    m = in_bbox(stops.stop_lat.values, stops.stop_lon.values)
    stops = stops[m].copy()

    # trips serving each stop = current supply / allocation
    st = pd.read_csv(GTFS / "stop_times.txt", usecols=["trip_id", "stop_id"])
    trips_per_stop = st.groupby("stop_id").size().rename("gtfs_trips")
    stops = stops.merge(trips_per_stop, left_on="stop_id", right_index=True, how="left")
    stops["gtfs_trips"] = stops["gtfs_trips"].fillna(0)
    return stops[["stop_id", "stop_name", "stop_lat", "stop_lon", "gtfs_trips"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Demand sources -> point clouds (lon, lat, weight)
# ---------------------------------------------------------------------------
def src_worldpop() -> np.ndarray:
    with rasterio.open(RAW / "worldpop_are_2025_100m.tif") as ds:
        band = ds.read(1)
        nodata = ds.nodata
        rows, cols = np.where((band > 0) & (band != nodata) if nodata is not None else band > 0)
        xs, ys = rasterio.transform.xy(ds.transform, rows, cols)  # lon, lat
        vals = band[rows, cols]
    xs, ys, vals = np.array(xs), np.array(ys), np.array(vals, dtype=float)
    m = in_bbox(ys, xs)
    return np.column_stack([xs[m], ys[m], vals[m]])


def src_hrsl(folder: str, col: str) -> np.ndarray:
    df = pd.read_csv(RAW / folder / f"{col}.csv")
    df.columns = [c.lower() for c in df.columns]
    val_col = [c for c in df.columns if c not in ("longitude", "latitude")][0]
    m = in_bbox(df.latitude.values, df.longitude.values)
    df = df[m]
    return np.column_stack([df.longitude.values, df.latitude.values, df[val_col].values])


# Trip-generation weights: how many bus trips a venue type tends to attract.
GEN_WEIGHTS = {
    "education": 3.0, "healthcare": 2.5, "retail": 2.0,
    "community": 1.5, "services": 1.5, "mobility": 0.5,
}


def src_osm_generators() -> np.ndarray:
    df = pd.read_csv(KIT / "osm_amenities.csv")
    m = in_bbox(df.latitude.values, df.longitude.values)
    df = df[m].copy()
    df["w"] = df["category"].map(GEN_WEIGHTS).fillna(1.0)
    return np.column_stack([df.longitude.values, df.latitude.values, df["w"].values])


def src_listings() -> np.ndarray:
    df = pd.read_csv(KIT / "sample_listings.csv")
    m = in_bbox(df.latitude.values, df.longitude.values)
    df = df[m]
    return np.column_stack([df.longitude.values, df.latitude.values, np.ones(m.sum())])


def src_synthetic_index(stops: pd.DataFrame) -> np.ndarray:
    """District-level demand index, assigned to each stop by nearest district centroid.

    Returns a per-stop array directly (no catchment) since it has no sub-district detail.
    """
    comm = pd.read_csv(KIT / "sample_communities.csv")
    dist = pd.read_csv(KIT / "districts.csv")
    dem = comm.groupby("district")["service_demand_index"].mean()
    cen = dist.set_index("district")[["latitude", "longitude"]].join(dem).dropna()
    cen_xy = to_xy(cen.longitude.values, cen.latitude.values)
    tree = cKDTree(cen_xy)
    stop_xy = to_xy(stops.stop_lon.values, stops.stop_lat.values)
    _, idx = tree.query(stop_xy, k=1)
    return cen["service_demand_index"].values[idx]


def catchment_sum(stops: pd.DataFrame, cloud: np.ndarray, radius_m: float) -> np.ndarray:
    """Sum source weights within radius_m of each stop."""
    if len(cloud) == 0:
        return np.zeros(len(stops))
    tree = cKDTree(to_xy(cloud[:, 0], cloud[:, 1]))
    stop_xy = to_xy(stops.stop_lon.values, stops.stop_lat.values)
    out = np.zeros(len(stops))
    weights = cloud[:, 2]
    for i, nbrs in enumerate(tree.query_ball_point(stop_xy, r=radius_m)):
        if nbrs:
            out[i] = weights[nbrs].sum()
    return out


# ---------------------------------------------------------------------------
# 3. Run + report
# ---------------------------------------------------------------------------
def main() -> None:
    print(f"Catchment radius: {CATCHMENT_M:.0f} m | BBOX: {BBOX}\n")
    stops = load_stops_with_allocation()
    print(f"Bus stops in Abu Dhabi metro: {len(stops)} "
          f"(of full-emirate feed)\n")

    clouds = {
        "worldpop": src_worldpop(),
        "hrsl_general": src_hrsl("hrsl_general", "are_general_2020"),
        "hrsl_children": src_hrsl("hrsl_children", "are_children_under_five_2020"),
        "hrsl_youth": src_hrsl("hrsl_youth", "are_youth_15_24_2020"),
        "osm_generators": src_osm_generators(),
        "listings": src_listings(),
    }
    for name, c in clouds.items():
        print(f"  {name:16s} points/cells in bbox: {len(c):>8,}")

    for name, cloud in clouds.items():
        stops[name] = catchment_sum(stops, cloud, CATCHMENT_M)
    stops["synthetic_index"] = src_synthetic_index(stops)

    signal_cols = list(clouds.keys()) + ["synthetic_index"]

    # --- 1. Coverage ---
    print("\n=== COVERAGE: % of stops with a non-zero signal ===")
    for c in signal_cols:
        cov = (stops[c] > 0).mean() * 100
        print(f"  {c:16s} {cov:6.1f}%   (mean={stops[c].mean():.2f}, max={stops[c].max():.0f})")

    # --- 2. Resolution: how many DISTINCT values (ability to separate nearby stops) ---
    print("\n=== RESOLUTION: distinct signal values across stops (higher = finer) ===")
    for c in signal_cols:
        print(f"  {c:16s} {stops[c].round(3).nunique():>5} distinct / {len(stops)} stops")

    # --- 3. Agreement with current allocation + each other ---
    def safe_corr(a, b):
        a, b = stops[a], stops[b]
        if a.std() == 0 or b.std() == 0:
            return np.nan
        return np.corrcoef(np.log1p(a), np.log1p(b))[0, 1]

    print("\n=== AGREEMENT with CURRENT bus allocation (corr of log signal vs gtfs_trips) ===")
    anchor = []
    for c in signal_cols:
        r = safe_corr(c, "gtfs_trips")
        anchor.append((c, r))
    for c, r in sorted(anchor, key=lambda t: (np.nan_to_num(t[1], nan=-9)), reverse=True):
        print(f"  {c:16s} r = {r:+.3f}")

    print("\n=== CROSS-CORRELATION between demand sources (log) ===")
    cc = pd.DataFrame(index=signal_cols, columns=signal_cols, dtype=float)
    for a in signal_cols:
        for b in signal_cols:
            cc.loc[a, b] = safe_corr(a, b)
    pd.set_option("display.width", 200, "display.max_columns", 20)
    print(cc.astype(float).round(2).to_string())

    # --- Example: top under-served stops by best source vs allocation ---
    best = max((a for a in anchor if not np.isnan(a[1])), key=lambda t: t[1])[0]
    print(f"\nBest single demand proxy vs current allocation: {best}")
    z = stops.copy()
    z["demand_z"] = (np.log1p(z[best]) - np.log1p(z[best]).mean()) / np.log1p(z[best]).std()
    z["supply_z"] = (np.log1p(z.gtfs_trips) - np.log1p(z.gtfs_trips).mean()) / np.log1p(z.gtfs_trips).std()
    z["gap"] = z["demand_z"] - z["supply_z"]  # high demand, low supply = under-served
    cols = ["stop_name", best, "gtfs_trips", "gap"]
    print("\nTop 8 UNDER-served stops (high modeled demand, low current allocation):")
    print(z.sort_values("gap", ascending=False)[cols].head(8).to_string(index=False))
    print("\nTop 8 OVER-served stops (low modeled demand, high current allocation):")
    print(z.sort_values("gap")[cols].head(8).to_string(index=False))

    out_path = OUT / "stop_demand_comparison.csv"
    stops.to_csv(out_path, index=False)
    print(f"\nSaved per-stop comparison -> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
