"""Download all real datasets for the Abu Dhabi transit demand/allocation project.

Each source is a different *demand signal* so we can compare their quality:
  - GTFS (ITC Abu Dhabi)        -> transit network + current bus allocation (revealed supply)
  - WorldPop 100m               -> modeled population grid (demand denominator, 2025)
  - Meta HRSL (general + ages)  -> alternative population grid (demand denominator, 2020)
  - OSM amenities (starter kit) -> trip generators (where people actually go)
  - Synthetic communities       -> the kit's baseline demand index (to benchmark against)

Run:  python3 scripts/download_data.py
"""
from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
STARTER = Path("/Users/Shared/eVoost/starter-kit/data")

SOURCES = {
    # ITC Abu Dhabi GTFS. Original DMT source feed (2022) is the only public release; the
    # official ftp weblink is tokenized/expires, so we use the validated mirror (transitpdf/busmaps).
    # Enhanced = cleaned & GTFS-validated; source = unmodified original.
    "gtfs": {
        "url": "https://blues3.transitpdf.com/files/uran/improved-gtfs-itc-abudhabi-ae.zip",
        "out": RAW / "gtfs_itc_abudhabi.zip",
        "kind": "binary",
    },
    "gtfs_source": {
        "url": "https://blues3.transitpdf.com/files/sourcedata/itc-abudhabi-ae.zip",
        "out": RAW / "gtfs_itc_abudhabi_source.zip",
        "kind": "binary",
    },
    # WorldPop constrained 2025, 100m, UAE (CC-BY 4.0).
    "worldpop": {
        "url": "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2024B/2025/ARE/v1/100m/constrained/are_pop_2025_CN_100m_R2024B_v1.tif",
        "out": RAW / "worldpop_are_2025_100m.tif",
        "kind": "binary",
    },
    # Meta / Data for Good HRSL — general population, 2020, CSV (lat, lon, population).
    "hrsl_general": {
        "url": "https://data.humdata.org/dataset/92e524f6-c0a9-4e9a-95df-5fefb6a340e1/resource/858e7faf-eb94-48e4-8a6c-a2dacae03034/download/are_general_2020_csv.zip",
        "out": RAW / "hrsl_general_2020_csv.zip",
        "kind": "binary",
    },
    # Meta HRSL — children under five (school/nursery demand proxy).
    "hrsl_children": {
        "url": "https://data.humdata.org/dataset/92e524f6-c0a9-4e9a-95df-5fefb6a340e1/resource/476e2e0d-7376-4ba8-b40a-c9e5632eaa0a/download/are_children_under_five_2020_csv.zip",
        "out": RAW / "hrsl_children_2020_csv.zip",
        "kind": "binary",
    },
    # Meta HRSL — youth 15-24 (commuter/student demand proxy).
    "hrsl_youth": {
        "url": "https://data.humdata.org/dataset/92e524f6-c0a9-4e9a-95df-5fefb6a340e1/resource/97c8ff94-915f-4f20-b2f6-52a8fe93685e/download/are_youth_15_24_2020_csv.zip",
        "out": RAW / "hrsl_youth_2020_csv.zip",
        "kind": "binary",
    },
}

HEADERS = {"User-Agent": "Mozilla/5.0 (transit-balance hackathon data fetch)"}


def download(name: str, spec: dict) -> bool:
    out: Path = spec["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{name}] GET {spec['url'][:90]}...")
    try:
        r = requests.get(spec["url"], headers=HEADERS, timeout=120, stream=True)
        r.raise_for_status()
        total = 0
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                total += len(chunk)
        print(f"[{name}] OK -> {out.name} ({total/1e6:.2f} MB)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] FAILED: {e}")
        return False


def unzip(path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)
    print(f"  unzipped {path.name} -> {dest.relative_to(ROOT)}/")


def copy_starter_kit() -> None:
    """Copy the real OSM amenities + synthetic baseline from the starter kit."""
    if not STARTER.exists():
        print(f"[starter] NOT FOUND at {STARTER}; skipping")
        return
    wanted = [
        "osm_amenities.csv",       # REAL trip generators
        "sample_communities.csv",  # synthetic demand baseline
        "districts.csv",           # join spine + centroids
        "sample_listings.csv",     # residential density (alt demand proxy)
    ]
    dest = RAW / "starter_kit"
    dest.mkdir(parents=True, exist_ok=True)
    for fn in wanted:
        src = STARTER / fn
        if src.exists():
            shutil.copy(src, dest / fn)
            print(f"[starter] copied {fn}")
        else:
            print(f"[starter] missing {fn}")


def main() -> int:
    results = {}
    for name, spec in SOURCES.items():
        results[name] = download(name, spec)

    # Unzip the GTFS and HRSL archives.
    if results.get("gtfs"):
        try:
            unzip(SOURCES["gtfs"]["out"], RAW / "gtfs_itc_abudhabi")
        except Exception as e:  # noqa: BLE001
            print(f"  GTFS unzip failed (maybe not a zip): {e}")
    for key in ("hrsl_general", "hrsl_children", "hrsl_youth"):
        if results.get(key):
            try:
                unzip(SOURCES[key]["out"], RAW / key)
            except Exception as e:  # noqa: BLE001
                print(f"  {key} unzip failed: {e}")

    copy_starter_kit()

    print("\n=== SUMMARY ===")
    for name, ok in results.items():
        print(f"  {name:16s} {'OK' if ok else 'FAILED'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
