# Optimizing Public EV Charging Infrastructure in Baku

This repository is a complete, reproducible geospatial study of public EV-charger accessibility in Baku. It evaluates existing charger coverage on a directed OpenStreetMap driving graph, ranks the least-accessible districts, maps >15-minute blind spots, and solves population-weighted maximum-coverage location models for 1–10 additional sites.

The completed snapshot is dated **2026-07-12**. It uses:

- official Azerbaijan IDDA polygons for Baku's 12 districts;
- OpenStreetMap chargers and candidate host POIs from cached Overpass responses;
- a local directed road graph extracted from Geofabrik's Azerbaijan OSM PBF;
- Natural Earth 1:10m land to remove Caspian Sea area; and
- WorldPop 2026 constrained 100 m population for demand weighting.

## Headline results

| Measure | Baseline | After 10 sites | Change |
|---|---:|---:|---:|
| Land within 15 minutes | 33.3% | 67.8% | +34.5 pp |
| Modelled population within 15 minutes | 84.1% | 99.2% | +15.1 pp |
| Land within 10 minutes | 16.3% | 41.6% | +25.3 pp |
| Modelled population within 10 minutes | 58.8% | 76.7% | +17.9 pp |

Pirallahı has the lowest baseline population-weighted 15-minute accessibility. The recommendations are desktop planning shortlists; they are not construction approvals.

## Main deliverables

- [Interactive combined map](outputs/maps/interactive_map.html)
- [Coverage heatmap](outputs/maps/coverage_heatmap.html)
- [Blind-spot map](outputs/maps/blind_spot_map.html)
- [Eight-page PDF technical report](reports/baku_ev_charging_technical_report.pdf)
- [Editable Markdown report](reports/baku_ev_charging_technical_report.md)
- [Print-friendly HTML report](reports/baku_ev_charging_technical_report.html)
- [Recommended sites table](outputs/tables/recommended_sites.csv)
- [District statistics](outputs/tables/district_statistics.csv)
- [City coverage comparison](outputs/tables/city_coverage.csv)
- [Full 1–10 site scenario curve](outputs/tables/scenario_summary.csv)
- [Run manifest](outputs/run_manifest.json) and [QA results](outputs/quality_checks.json)

Canonical GeoJSON layers are in `outputs/`: districts, existing chargers, analysis-grid accessibility, blind spots, candidate sites, and recommended sites.

## Reproduce the analysis

Python 3.12 is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe scripts\run_analysis.py
```

The default command reuses cached source files and the local GraphML network. Use `--refresh` only when a new external snapshot is intentionally required:

```powershell
.\.venv\Scripts\python.exe scripts\run_analysis.py --refresh
```

Run automated tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Configuration is frozen in [config.json](config.json). The method and field contracts are documented in [docs/methodology.md](docs/methodology.md) and [docs/data_dictionary.md](docs/data_dictionary.md).

## Analytical interpretation

Grid origins are snapped to the road graph and receive a distance-based access penalty. A reverse multi-source Dijkstra calculates directed origin-to-charger time while respecting one-way roads. Coverage is measured independently with clipped land area and WorldPop population weights at 5, 10, and 15 minutes.

Candidate hosts are screened OSM parking, fuel, retail, transport, university, marketplace, town-hall, community-centre, and similar vehicle-accessible POIs near underserved population clusters. A SciPy/HiGHS binary location model lexicographically maximizes new population coverage at 15, then 10, then 5 minutes, with a 1.5 km minimum spacing rule. Independent budgets from 1 through 10 are solved; the final 10-site set is given a marginal-gain deployment order.

## Important limitations

- The 18-site baseline is an inclusive OSM scenario: explicit `private/no` sites are excluded, while missing access tags remain unknown. Operator verification is required.
- OSM speeds are static modeled/free-flow times, not observed Baku traffic.
- WorldPop R2025A is an alpha modeled population surface, not EV ownership, employment, visitors, or trip demand.
- Results do not model charger uptime, queues, number of plugs, power, connectors, price, opening hours, capital cost, electrical hosting capacity, land ownership, permits, or safety.
- Every proposed POI needs field, utility, commercial, and engineering review.

## Attribution

- District geometry: Azerbaijan IDDA Open Data Portal, *Regions of Azerbaijan*; catalog declares Creative Commons Attribution.
- Roads, chargers, and POIs: © OpenStreetMap contributors, ODbL 1.0. The PBF was distributed by Geofabrik.
- Population: WorldPop 2026 constrained 100 m, DOI `10.5258/SOTON/WP00839`.
- Land mask: Natural Earth, public domain.

