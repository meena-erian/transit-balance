# Transit Balance — Abu Dhabi Bus Demand vs. Allocation

Prototype for the Cursor × eVoost Abu Dhabi AI PropTech Challenge (Future Communities track).

**Problem:** Which Abu Dhabi bus stops/routes are *under-served* (high travel demand, few buses)
and which are *over-served* — and how should allocation be rebalanced? No public per-stop
ridership exists, so we **estimate demand** from real population + real trip generators and
compare it against the city's **current allocation** (GTFS service frequency).

## Data sources

All real except the synthetic baseline (kept only to show it does *not* work).

| Source | What it provides | Resolution | License / attribution |
|---|---|---|---|
| **ITC Abu Dhabi GTFS** (DMT, via transitpdf/busmaps mirror) | Network: 135 routes, 3,368 stops, 27,926 trips. `trips/stop` = current allocation | stop-level | DMT/ITC, 2022 feed; attribute ITC Abu Dhabi |
| **WorldPop 2025 100m** | Modeled population grid (demand origins) | ~100 m | CC-BY 4.0 — © WorldPop, Univ. of Southampton, DOI:10.5258/SOTON/WP00803 |
| **Meta HRSL 2020** (general / children<5 / youth15-24) | High-res population + age bands | ~30 m | CC-BY — © Meta Data for Good / CIESIN Columbia |
| **OSM amenities** (from starter kit) | Trip generators (schools, malls, clinics…) — demand destinations | point | © OpenStreetMap contributors, ODbL |
| **Synthetic listings / communities** (starter kit) | Baseline only | district | MIT (synthetic) |

## How to reproduce

```bash
pip install -r requirements.txt
python3 scripts/download_data.py           # 1. pull all sources -> data/raw/
python3 scripts/compare_demand_sources.py  # 2. (optional) demand-source quality comparison
python3 scripts/build_model.py             # 3. demand model + gap engine -> webapp/data/*.geojson
python3 scripts/ai_brief.py                # 4. per-route AI briefings -> webapp/data/briefs.json
python3 -m http.server 8765 -d webapp      # 5. open http://localhost:8765
```

Set `OPENAI_API_KEY` before step 4 to generate LLM briefings (otherwise a grounded
template is used, so the demo runs with no keys).

## The app

Interactive map of Abu Dhabi's bus network:
- **Routes view** — every route colored by *service gap* (red = under-served, blue = over-served),
  line weight ∝ |gap|. Click a route for demand vs. supply, a concrete trips/day recommendation,
  estimated annual boardings, and an AI reallocation briefing.
- **Stops view** — 1,688 stops sized by demand, colored by gap; click for catchment detail
  (residents, trip generators, and weekly trips within 500 m).
- Ranked "most under-served / over-served" lists in the sidebar.

It's a static site (Leaflet + precomputed GeoJSON), so it deploys as-is to Vercel, Netlify,
GitHub Pages, or Hugging Face Spaces — just publish the `webapp/` folder.

## Method (how demand is modeled)

For each stop we sum **real population** (WorldPop, origins) and **weighted trip generators**
(OSM amenities, destinations) within a 500 m walking catchment, percentile-rank both, and blend
them into a demand index. Routes inherit the mean demand of the stops they serve (this avoids
the interchange-inflation problem that hits raw stop-level trip counts). The **gap** is the
demand percentile minus the supply percentile (GTFS trips). Demand share is calibrated to ITC's
published 95.2M annual boardings (2024) for the per-route estimates.

## Demand-source quality comparison (500 m catchment around 1,688 AD-metro stops)

Anchor = correlation of each demand signal vs. **current bus allocation** (GTFS trips/stop).
A good proxy should agree with where the city already runs buses.

| Source | Coverage | Granularity (distinct values) | Corr vs current allocation |
|---|---|---|---|
| **OSM trip generators** | 88% | 227 | **+0.56 (best)** |
| HRSL children / youth / general | 100% | 618 | +0.33 (all identical — age bands are scaled copies, redundant) |
| Listings (synthetic) | 56% | 51 | +0.33 |
| **WorldPop 2025** | 100% | 1,686 (finest) | +0.31 |
| Synthetic `service_demand_index` | 100% | 20 (district only) | **−0.06 (no signal / negative)** |

**Conclusions**
- **OSM trip generators** is the strongest single demand proxy and the most decision-relevant
  (it explains *why* people travel). Use it as the destination/attraction term.
- **WorldPop** has the best spatial resolution (near-unique per stop) and 100% coverage — use it
  as the population/origin term. HRSL agrees (r≈0.63) but its age bands are perfectly collinear
  (r=1.00), so they add no independent signal; WorldPop is the cleaner primary.
- The **synthetic** kit demand index is *negatively* correlated with real allocation — confirming
  the pivot to real data. Kept only as a baseline foil.
- → Model: **gravity demand = WorldPop population (origins) × OSM generators (destinations)**,
  compared to GTFS allocation, calibrated to ITC's published ~95M trips/yr.

**Known caveat:** raw `trips/stop` overstates demand at interchanges (many routes pass through),
so naive "over-served" flags catch hubs like Khaleej Al Arabi Interchange. Route-level
normalization is the next refinement.
