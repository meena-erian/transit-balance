"""Generate a natural-language reallocation briefing per route.

Uses an LLM when OPENAI_API_KEY is set (richer phrasing); otherwise falls back to a
grounded template so the demo runs with no keys. Output: webapp/data/briefs.json
keyed by route_id.

Run:  python3 scripts/ai_brief.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "webapp" / "data"


def facts(p: dict) -> str:
    return (
        f"Route {p['name']} ({p.get('headsign') or 'Abu Dhabi'}). "
        f"Demand percentile {p['demand_pct']}, supply percentile {p['supply_pct']}, "
        f"service gap {p['gap']:+}. Current {p['daily_trips']} trips/day; "
        f"model recommends {p['rec_daily_trips']} ({p['rec_delta']:+}). "
        f"Estimated {p['est_annual_trips']:,} annual boardings; {p['n_stops']} stops in metro."
    )


def template_brief(p: dict) -> str:
    g = p["gap"]
    if g >= 25:
        lead = (f"Route {p['name']} serves a high-demand corridor (demand pctile "
                f"{p['demand_pct']}) but runs below-average frequency (supply pctile "
                f"{p['supply_pct']}).")
        rec = (f"Recommend raising service from <b>{p['daily_trips']} to "
               f"{p['rec_daily_trips']} trips/day</b> ({p['rec_delta']:+}) to relieve "
               f"likely crowding and capture unmet demand.")
    elif g <= -25:
        lead = (f"Route {p['name']} runs frequent service (supply pctile {p['supply_pct']}) "
                f"into a corridor with comparatively modest modeled demand (demand pctile "
                f"{p['demand_pct']}).")
        rec = (f"Recommend trimming from <b>{p['daily_trips']} to {p['rec_daily_trips']} "
               f"trips/day</b> ({p['rec_delta']:+}) and redeploying the freed buses to "
               f"under-served corridors.")
    else:
        lead = (f"Route {p['name']} is roughly balanced — demand pctile {p['demand_pct']} "
                f"vs supply pctile {p['supply_pct']}.")
        rec = "No reallocation needed; monitor as land use changes."
    return (f"{lead} {rec} Demand is modeled from real population (WorldPop) and trip "
            f"generators (OpenStreetMap) within 500&nbsp;m of each stop, calibrated to ITC's "
            f"95.2&nbsp;M annual boardings; estimated <b>{p['est_annual_trips']:,}</b> for this route.")


def llm_brief(p: dict):
    """Connect a real model here. Returns None if unavailable so we fall back."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key=key)
        prompt = (
            "You are a transit planning analyst for Abu Dhabi. In 2-3 sentences, write a crisp "
            "reallocation recommendation for a city official. Be specific with the numbers, name "
            "the action (add/redeploy/maintain buses), and stay grounded ONLY in these facts. "
            "Note demand is modeled, not measured.\n\nFACTS: " + facts(p)
        )
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=160,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:  # noqa: BLE001
        print(f"  LLM failed ({e}); using template")
        return None


def main():
    routes = json.loads((DATA / "routes.geojson").read_text())["features"]
    use_llm = bool(os.environ.get("OPENAI_API_KEY"))
    print(f"Generating briefs for {len(routes)} routes "
          f"({'LLM' if use_llm else 'template'} mode)...")
    briefs = {}
    for f in routes:
        p = f["properties"]
        briefs[p["route_id"]] = (llm_brief(p) if use_llm else None) or template_brief(p)
    (DATA / "briefs.json").write_text(json.dumps(briefs, indent=2))
    print(f"Wrote {len(briefs)} briefs -> {(DATA / 'briefs.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
