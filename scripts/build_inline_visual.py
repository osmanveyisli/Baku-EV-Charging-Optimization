"""Build the thread-scoped inline summary map from production outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def compact_feature_collection(frame: gpd.GeoDataFrame, properties: list[str]) -> dict:
    frame = frame.to_crs("EPSG:32639").copy()
    if not all(frame.geometry.geom_type.eq("Point")):
        frame["geometry"] = frame.geometry.simplify(250, preserve_topology=True)
    frame = frame.to_crs("EPSG:4326")
    return json.loads(frame[properties + ["geometry"]].to_json(drop_id=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    districts = gpd.read_file(PROJECT_ROOT / "outputs" / "districts.geojson")
    stats = pd.read_csv(PROJECT_ROOT / "outputs" / "tables" / "district_statistics.csv")
    districts = districts.merge(stats, on="district", how="left")
    existing = gpd.read_file(PROJECT_ROOT / "outputs" / "existing_chargers.geojson")
    recommended = gpd.read_file(PROJECT_ROOT / "outputs" / "recommended_sites.geojson")

    district_json = compact_feature_collection(
        districts,
        [
            "district",
            "baseline_population_coverage_15_pct",
            "after_population_coverage_15_pct",
            "baseline_coverage_15_pct",
            "after_coverage_15_pct",
        ],
    )
    existing_json = compact_feature_collection(existing, ["name", "access_class"])
    recommended_json = compact_feature_collection(
        recommended, ["name", "district", "deployment_rank", "site_type"]
    )

    fragment = f"""<div id="baku-ev-accessibility">
  <div class="viz-grid" aria-label="Coverage summary">
    <div class="card viz-stat"><div class="text-muted">Population ≤15 min</div><div class="viz-stat-value" id="bea-pop">84.1%</div></div>
    <div class="card viz-stat"><div class="text-muted">Land ≤15 min</div><div class="viz-stat-value" id="bea-land">33.3%</div></div>
    <div class="card viz-stat"><div class="text-muted">Lowest baseline district</div><div class="viz-stat-value">Pirallahı</div></div>
  </div>
  <div class="viz-controls" aria-label="Map scenario">
    <button type="button" class="btn btn-primary" id="bea-baseline" aria-pressed="true">Baseline</button>
    <button type="button" class="btn" id="bea-after" aria-pressed="false">After 10 sites</button>
  </div>
  <div class="bea-map-wrap">
    <svg class="bea-map" viewBox="0 0 720 410" role="img" aria-labelledby="bea-map-title bea-map-desc">
      <title id="bea-map-title">Baku district EV charger accessibility</title>
      <desc id="bea-map-desc">District population coverage within fifteen minutes, with existing and ten recommended charger locations.</desc>
    </svg>
    <div class="tooltip" id="bea-tip" hidden></div>
  </div>
  <div class="viz-row text-small" aria-label="Map legend">
    <span><i class="bea-swatch bea-b1"></i>&lt;50%</span><span><i class="bea-swatch bea-b2"></i>50–75%</span><span><i class="bea-swatch bea-b3"></i>75–90%</span><span><i class="bea-swatch bea-b4"></i>90–100%</span><span><i class="bea-dot bea-existing"></i>Existing</span><span><i class="bea-dot bea-recommended"></i>Recommended</span>
  </div>
  <p class="text-small text-muted" id="bea-detail">Select a district to compare its population and land coverage.</p>
</div>
<style>
#baku-ev-accessibility{{position:relative;display:grid;gap:12px;color:var(--foreground)}}
#baku-ev-accessibility .viz-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}
#baku-ev-accessibility .bea-map-wrap{{position:relative;min-height:280px}}
#baku-ev-accessibility .bea-map{{display:block;width:100%;height:auto;max-height:520px}}
#baku-ev-accessibility .bea-district{{stroke:var(--border);stroke-width:1.1;vector-effect:non-scaling-stroke;cursor:pointer}}
#baku-ev-accessibility .bea-district:hover{{stroke:var(--foreground);stroke-width:2}}
#baku-ev-accessibility .bea-b1{{fill:var(--viz-series-4);background:var(--viz-series-4)}}
#baku-ev-accessibility .bea-b2{{fill:var(--viz-series-3);background:var(--viz-series-3)}}
#baku-ev-accessibility .bea-b3{{fill:var(--viz-series-2);background:var(--viz-series-2)}}
#baku-ev-accessibility .bea-b4{{fill:var(--viz-series-1);background:var(--viz-series-1)}}
#baku-ev-accessibility .bea-waterline{{fill:none;stroke:var(--muted-foreground);stroke-width:.7;vector-effect:non-scaling-stroke}}
#baku-ev-accessibility .bea-site-existing{{fill:var(--viz-series-5);stroke:var(--background);stroke-width:1.2;vector-effect:non-scaling-stroke}}
#baku-ev-accessibility .bea-site-recommended{{fill:var(--primary);stroke:var(--primary-foreground);stroke-width:1.2;vector-effect:non-scaling-stroke}}
#baku-ev-accessibility .bea-rank{{fill:var(--primary-foreground);font-weight:500;text-anchor:middle;dominant-baseline:central;pointer-events:none}}
#baku-ev-accessibility .bea-swatch{{display:inline-block;width:12px;height:12px;margin-right:5px;vertical-align:-2px;opacity:.65}}
#baku-ev-accessibility .bea-dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}}
#baku-ev-accessibility .bea-existing{{background:var(--viz-series-5)}}
#baku-ev-accessibility .bea-recommended{{background:var(--primary)}}
#baku-ev-accessibility .tooltip{{position:absolute;pointer-events:none}}
@media(max-width:520px){{#baku-ev-accessibility .viz-grid{{grid-template-columns:1fr}}}}
</style>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script>
(() => {{
  const root = document.getElementById('baku-ev-accessibility');
  const districts = {json.dumps(district_json, ensure_ascii=False, separators=(',', ':'))};
  const existing = {json.dumps(existing_json, ensure_ascii=False, separators=(',', ':'))};
  const recommended = {json.dumps(recommended_json, ensure_ascii=False, separators=(',', ':'))};
  const svg = d3.select(root.querySelector('.bea-map'));
  const projection = d3.geoMercator().fitExtent([[18,18],[702,392]], districts);
  const path = d3.geoPath(projection);
  const tip = root.querySelector('#bea-tip');
  const detail = root.querySelector('#bea-detail');
  let scenario = 'baseline';
  const band = value => value < 50 ? 'bea-b1' : value < 75 ? 'bea-b2' : value < 90 ? 'bea-b3' : 'bea-b4';
  const valueFor = feature => Number(feature.properties[scenario + '_population_coverage_15_pct']);
  const landFor = feature => Number(feature.properties[scenario + '_coverage_15_pct']);
  const districtsLayer = svg.append('g').attr('aria-label','Districts');
  const marks = districtsLayer.selectAll('path').data(districts.features).join('path')
    .attr('d', path).attr('class', d => 'bea-district ' + band(valueFor(d)))
    .attr('opacity', .68)
    .on('mouseenter', function(event,d) {{
      const box = root.querySelector('.bea-map-wrap').getBoundingClientRect();
      tip.hidden = false;
      tip.textContent = `${{d.properties.district}} · ${{valueFor(d).toFixed(1)}}% population`;
      tip.style.left = Math.max(6, Math.min(box.width - tip.offsetWidth - 6, event.clientX - box.left + 10)) + 'px';
      tip.style.top = Math.max(6, event.clientY - box.top - tip.offsetHeight - 10) + 'px';
    }})
    .on('mouseleave', () => {{ tip.hidden = true; }})
    .on('click', (event,d) => {{
      detail.textContent = `${{d.properties.district}}: ${{valueFor(d).toFixed(1)}}% of modelled population and ${{landFor(d).toFixed(1)}}% of land within 15 minutes (${{scenario}}).`;
    }});
  svg.append('g').attr('aria-label','Existing charging sites').selectAll('circle').data(existing.features).join('circle')
    .attr('class','bea-site-existing').attr('r',3.2).attr('cx',d=>projection(d.geometry.coordinates)[0]).attr('cy',d=>projection(d.geometry.coordinates)[1]);
  const proposed = svg.append('g').attr('aria-label','Recommended charging sites').selectAll('g').data(recommended.features).join('g')
    .attr('transform',d=>`translate(${{projection(d.geometry.coordinates)[0]}},${{projection(d.geometry.coordinates)[1]}})`);
  proposed.append('circle').attr('class','bea-site-recommended').attr('r',7);
  proposed.append('text').attr('class','bea-rank text-small').text(d=>d.properties.deployment_rank);
  function update(next) {{
    scenario = next;
    marks.attr('class',d=>'bea-district '+band(valueFor(d)));
    root.querySelector('#bea-pop').textContent = scenario === 'baseline' ? '84.1%' : '99.2%';
    root.querySelector('#bea-land').textContent = scenario === 'baseline' ? '33.3%' : '67.8%';
    const baselineButton = root.querySelector('#bea-baseline');
    const afterButton = root.querySelector('#bea-after');
    baselineButton.setAttribute('aria-pressed', String(scenario === 'baseline'));
    afterButton.setAttribute('aria-pressed', String(scenario === 'after'));
    baselineButton.classList.toggle('btn-primary', scenario === 'baseline');
    afterButton.classList.toggle('btn-primary', scenario === 'after');
    detail.textContent = scenario === 'baseline' ? 'Select a district to compare its baseline population and land coverage.' : 'Select a district to inspect coverage after all 10 recommended sites.';
  }}
  root.querySelector('#bea-baseline').addEventListener('click',()=>update('baseline'));
  root.querySelector('#bea-after').addEventListener('click',()=>update('after'));
}})();
</script>
"""
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(fragment, encoding="utf-8")
    print(args.destination)
    print(f"bytes={args.destination.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

