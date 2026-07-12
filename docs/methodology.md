# Methodology

## 1. Purpose and analytical interpretation

This study measures how easily locations on Baku's land area can reach a public electric-vehicle charging site by car. It answers four questions:

1. Which of Baku's 12 districts have the lowest charger accessibility?
2. What share of the study area's land is within a 5-, 10-, or 15-minute drive of an existing charger?
3. Which screening-feasible OpenStreetMap (OSM) points of interest should be considered for additional chargers under a fixed site budget?
4. How much would the selected sites improve the same accessibility measures?

The production analysis reports two distinct results: **land-area coverage** and **population-weighted coverage**. A 750 m land-clipped grid approximates spatial coverage; exact clipped area is the territorial denominator, while a 2026 WorldPop constrained 100 m raster supplies a separate modelled population weight. These denominators are never substituted for one another. Population-weighted results describe modelled residents, not EV ownership or observed charging demand.

## 2. Reproducibility and provenance

Each production run must write a machine-readable run manifest containing at least:

- the run timestamp in UTC;
- the official IDDA boundary resource and feature identifiers, plus the 12 frozen OSM relation IDs used as a crosswalk;
- the Geofabrik PBF timestamp and Overpass query timestamps for roads, chargers, and candidate POIs;
- the Natural Earth land-mask version and WorldPop release/DOI;
- the coordinate reference systems;
- the grid size and alignment origin;
- the driving-network query buffer;
- the road-speed table and all maxspeed parsing rules;
- station inclusion rules;
- candidate POI tag allowlist and screening parameters;
- the optimization threshold and number of sites to select;
- package versions, MILP solver status, and the K-means random seed; and
- checksums for cached source files where practical.

Cached extracts should be reused for all scenarios in a run. Baseline and after-addition results must never be computed from different OSM snapshots. OSM-derived products must retain the required OpenStreetMap attribution and be handled consistently with the ODbL.

## 3. Study area

### 3.1 District boundaries

The study uses the official *Regions of Azerbaijan* GeoJSON published by the Azerbaijan IDDA Open Data Portal. Features with `PARENT_ID="10"` provide the following 12 Baku districts:

- Binəqədi
- Qaradağ
- Xətai
- Xəzər
- Nərimanov
- Nəsimi
- Nizami
- Pirallahı
- Sabunçu
- Səbail
- Suraxanı
- Yasamal

The official feature IDs are the geometry identifiers. Frozen OSM relation IDs are attached as an independent crosswalk and preserved in `districts.geojson`; they are not used as the legal geometry source. The pipeline asserts exactly 12 unique districts and fails on a name/crosswalk mismatch.

Geometries are repaired with a validity operation before analysis. The pipeline checks for empty geometry, self-intersection, unexplained gaps, and overlap among district polygons. Material overlap or missing relations is a fatal quality-control error; it must not be silently hidden by dissolving everything into one city polygon.

### 3.2 Land mask

Administrative polygons in a coastal city can contain water. The analysis therefore intersects the official district union with the Natural Earth 1:10m land layer. Conceptually,

\[
L = \left(\bigcup_{d=1}^{12}D_d\right)\cap C_{\mathrm{land}},
\]

where \(D_d\) is official district \(d\) and \(C_{\mathrm{land}}\) is Natural Earth land. District land is \(L_d=L\cap D_d\). Islands inside the official district polygons remain in scope. The land-mask URL and retrieval date are recorded.

All geometry repair, clipping, distance, and area calculations use WGS 84 / UTM zone 39N (EPSG:32639). Published GeoJSON is transformed to WGS 84 longitude/latitude (EPSG:4326).

## 4. Land-clipped 750 m analysis grid

A regular square grid with side length \(h=750\) m is generated in EPSG:32639. Its origin is fixed in the run manifest so that reruns do not shift the cells. Each square is intersected with each district land polygon. The resulting non-overlapping land fragments are the atomic evaluation units.

For fragment \(i\):

- \(a_i\) is its exact clipped land area in square kilometres;
- \(d(i)\) is its district;
- \(x_i\) is a point guaranteed to lie inside the fragment, calculated with a representative-point operation; and
- \(v_i\) is the nearest eligible node in the projected driving graph.

Small coastal and district-edge fragments are retained. Because every statistic is area weighted, retaining slivers is preferable to counting each fragment as though it represented a full 750 m square. The sum of fragment areas must agree with the district and city land masks within a documented numerical tolerance.

The 750 m grid creates an approximation: travel time is sampled at one point within each fragment and assigned to that fragment. A finer-grid sensitivity run is recommended for locations near threshold boundaries.

## 5. Existing public charging stations

Existing sites are extracted from the same OSM snapshot using amenity=charging_station. Node features are used directly; polygon or multipolygon features are converted to an internal point on surface. OSM type and OSM ID form the stable source identity. Exact duplicate elements are removed, while apparently co-located but distinct OSM elements are retained and flagged for review.

Sites tagged access=private or access=no are excluded from the public-access baseline. Missing access tags are retained as public-status unknown rather than silently interpreted as confirmed public; the report states how many included sites have unknown access. Customer-only sites can be included or excluded through a manifest parameter and should be tested as a sensitivity case.

Each included charger is snapped to the nearest eligible driving node. Snap distance is recorded. A station farther than the configured maximum snap distance is excluded from routing and given an explicit exclusion reason. Charger capacity, connector count, power, price, and real-time availability are descriptive attributes only when present; a site counts as one reachable destination in the coverage model.

## 6. Directed driving network and travel time

### 6.1 Graph construction

The road network is a directed OSM driving graph covering the study land mask plus a documented buffer. The buffer permits realistic trips that briefly leave a district or the city mask. The graph preserves:

- one-way restrictions and direction-specific edges;
- parallel edges where road attributes differ;
- bridges, tunnels, and disconnected land components;
- all relevant connected components, including islands; and
- edge length in metres.

The graph must not be reduced to only the largest connected component. Doing so would erase legitimate remote or island areas and incorrectly improve the reported coverage denominator.

### 6.2 Edge travel time

For directed edge \(e\), free-flow travel time is

\[
t_e = \frac{3.6\,\ell_e}{s_e},
\]

where \(\ell_e\) is length in metres, \(s_e\) is speed in kilometres per hour, and \(t_e\) is seconds. Valid OSM maxspeed values take precedence. The parser documents unit conversion, compound values, and unusable values. Missing speeds are imputed from a fixed highway-class table stored in the run manifest. Implausible values are flagged.

This is a static free-flow or typical-speed model, depending on the chosen speed table. It does not include congestion, queueing at chargers, parking search, turning delay not represented in OSM, or live closures.

### 6.3 Reverse multi-source Dijkstra

Accessibility is defined from each grid origin to any charger destination. Let \(G=(V,E)\) be the directed driving graph, \(Q\subseteq V\) the snapped charger nodes, and \(d_G(u,q)\) the shortest directed travel time from \(u\) to \(q\). The minimum charger time for grid fragment \(i\) is

\[
T_i = \min_{q\in Q} d_G(v_i,q).
\]

Running a conventional multi-source search from chargers on the original graph would answer the wrong directional question: where a car can drive **from** a charger. Instead, the pipeline reverses every directed edge and runs one multi-source Dijkstra search from all charger nodes:

\[
T_i = \min_{q\in Q}d_{G^R}(q,v_i).
\]

This is exactly the origin-to-charger time in the original graph. Full distances are retained so that a node with a path longer than 30 minutes is not confused with a node having no directed path. If no directed path exists, \(T_i=\infty\), the published minute value is null, and the band is unreachable.

A directionality test on known one-way streets is required. A small sample must also be compared with independently calculated point-to-point routes.

## 7. Accessibility and coverage metrics

### 7.1 Time bands and blind spots

The principal thresholds are \(\tau\in\{5,10,15\}\) minutes. A grid fragment is covered at threshold \(\tau\) when \(T_i\leq\tau\). Bands are mutually exclusive:

- le_5: \(T_i\leq5\);
- 5_10: \(5<T_i\leq10\);
- 10_15: \(10<T_i\leq15\);
- gt_15: \(T_i>15\); and
- unreachable: \(T_i=\infty\).

A blind spot is any fragment in gt_15 or unreachable. Adjacent blind fragments are dissolved within district and scenario to form components for blind_spots.geojson.

### 7.2 Land-area coverage

For a set \(R\) of grid fragments, the land-area coverage percentage is

\[
C_A(R,\tau)=100\,
\frac{\sum_{i\in R}a_i\,\mathbf{1}(T_i\leq\tau)}
{\sum_{i\in R}a_i}.
\]

For city statistics, \(R\) contains all land fragments. For district statistics, \(R=\{i:d(i)=d\}\). This is an areal approximation to network accessibility and must be labelled land-area coverage. An unweighted percentage of grid records is not an official coverage statistic.

To summarize the full travel-time distribution without allowing disconnected or extremely remote fragments to dominate, define

\[
\widetilde T_i^{30}=\min(T_i,30),
\]

with \(\min(\infty,30)=30\). The area-weighted capped mean is

\[
\overline T_A^{30}=
\frac{\sum_i a_i\widetilde T_i^{30}}{\sum_i a_i}.
\]

The reported p90_min_capped30 is the area-weighted 90th percentile of \(\widetilde T_i^{30}\), not the unweighted percentile of grid records.

### 7.3 Identifying the least-accessible districts

The primary district ranking is ascending 15-minute WorldPop-weighted coverage: rank 1 is the least accessible district. Ties are resolved by lower 15-minute land coverage and then canonical district name. Results at all three thresholds, land coverage, blind-area share, unreachable-area share, and capped p90 are shown so the ranking is not interpreted from a single value alone.

### 7.4 Population coverage

WorldPop 2026 constrained 100 m people-per-pixel values are aggregated into each grid fragment using rasterization in the source raster CRS. Population coverage is

\[
C_P(R,\tau)=100\,
\frac{\sum_{i\in R}p_i\,\mathbf{1}(T_i\leq\tau)}
{\sum_{i\in R}p_i}.
\]

\(C_P\) and \(C_A\) answer different questions and must never be substituted for one another. WorldPop R2025A is an alpha modelled surface, not observed addresses, EV ownership, employment, or trip generation. The run manifest records DOI `10.5258/SOTON/WP00839` and `population_status=calculated`.

## 8. Candidate sites

Candidate locations are derived from a versioned OSM POI allowlist. The default conceptual classes are off-street parking, fuel stations, supermarkets, malls, and marketplaces. The exact tag expressions used in a run are stored in the manifest. Polygonal POIs use a point on surface.

A POI is **screening feasible** only when it:

1. lies inside the Baku land mask;
2. is not explicitly private or no-access;
3. belongs to an allowed POI class;
4. is within the configured maximum distance of an eligible driving node;
5. is not an existing charger or a duplicate representation of another candidate;
6. passes the configured minimum separation rule from existing sites; and
7. is not tagged with a manifest exclusion such as military, construction, or another incompatible use.

Pairwise spacing between proposed sites is enforced as binary incompatibility constraints in the location model. Distances and screened records are retained in `candidate_sites.geojson`. Screening feasibility does **not** establish land ownership, electrical capacity, distribution-grid connection, parking control, permitting, safety, cost, or engineering feasibility. Recommended sites therefore require field and utility validation.

## 9. Maximum-coverage site selection

### 9.1 Objective

Let \(K\) be the site budget. The production objective is lexicographic: maximize newly covered WorldPop population at 15 minutes, then at 10 minutes, then at 5 minutes. Land-area coverage remains a separately reported evaluation metric.

For candidate \(c\), a reverse single-source Dijkstra search on \(G^R\), cut off at 15 minutes, identifies coverage at each threshold \(\tau\):

\[
S_{c\tau}=\{i:d_G(v_i,q_c)\leq\tau\},
\]

where \(q_c\) is the candidate's snapped graph node. Let \(b_{i\tau}=1\) when existing chargers cover cell \(i\), \(x_c\) indicate candidate selection, and \(z_{i\tau}\) indicate after-coverage. The model uses

\[
z_{i\tau}\leq b_{i\tau}+\sum_c \mathbf{1}(i\in S_{c\tau})x_c,
\qquad \sum_c x_c\leq K,
\]

with binary \(x_c,z_{i\tau}\). The objective weights use nested factors large enough to make 15-minute population coverage primary, then 10, then 5. Candidate pairs closer than the configured minimum spacing satisfy \(x_c+x_{c'}\leq1\).

### 9.2 Solver and deployment order

SciPy `optimize.milp` with HiGHS solves independent budgets from 1 through 10. Solutions for different budgets need not be nested. If the solver returns no incumbent, a deterministic marginal-gain greedy fallback is used and recorded in the manifest.

\[
c_j=\arg\max_{c\in F_j}
\left(\Delta^{P}_{15}(c),\Delta^{P}_{10}(c),\Delta^{P}_{5}(c)\right),
\]

Within the final 10-site optimum, this marginal population rule supplies a practical deployment order. Marginal land and population gains are both stored for each step. The after-scenario is always recomputed from the minimum time to any existing or selected site, so overlapping service areas are not double-counted.

\[
A_j=A_{j-1}\cup\{c_j\}.
\]

Solver status, selected IDs, budget, spacing, objective basis, and package versions are stored in the run manifest.

### 9.3 After-addition scenario

After each selected site, and for the final proposed set, the pipeline reruns reverse multi-source Dijkstra using existing charger nodes plus all selected candidate nodes. For each threshold,

\[
\Delta C_A(\tau)=
C_A^{\mathrm{after}}(\tau)-C_A^{\mathrm{baseline}}(\tau),
\]

reported in percentage points, while absolute newly covered land is also reported in square kilometres. The expected monotonicity conditions are

\[
C_A(5)\leq C_A(10)\leq C_A(15)
\]

within a scenario and

\[
C_A^{\mathrm{after}}(\tau)\geq C_A^{\mathrm{baseline}}(\tau).
\]

Any violation is a failed quality-control check.

## 10. Outputs

The reproducible data products are defined in data_dictionary.md:

- districts.geojson;
- existing_chargers.geojson;
- grid_accessibility.geojson;
- blind_spots.geojson;
- candidate_sites.geojson;
- recommended_sites.geojson;
- city_coverage.csv;
- district_statistics.csv;
- blind_spot_statistics.csv; and
- scenario_summary.csv.

The interactive map contains switchable district, existing-charger, proposed-site, baseline travel-time heatmap, after-addition heatmap, and blind-spot layers. The “heatmap” is a grid choropleth of network travel time, not a kernel-density surface. Popups expose source identity, scenario times, band, area, district, and selection information.

The 6–10 page technical report should contain the study question, source snapshot and assumptions, method, baseline city and district results, blind spots, recommended sites, before/after changes, validation, limitations, and an OSM attribution statement. Every percentage in maps, tables, and report text must say whether its denominator is land area or population.

## 11. Quality assurance

The pipeline must automatically test:

- exactly 12 unique district relation IDs and names are present;
- all boundary, land, grid, and output geometries are valid and non-empty;
- district land areas sum to city land area within tolerance;
- grid fragment IDs are unique and every fragment belongs to exactly one district;
- all area calculations occur in EPSG:32639;
- edge travel times are positive and finite;
- station, grid, and candidate snap distances are recorded and within their configured limits when used;
- one-way directionality behaves correctly in hand-checked cases;
- coverage is monotone across 5, 10, and 15 minutes;
- after-addition coverage never falls below baseline;
- selected candidates are unique and satisfy all spacing rules;
- blind-area plus 15-minute covered area equals total land area within rounding tolerance; and
- CSV values reproduce aggregations from grid_accessibility.geojson.

Manual review should inspect all recommended sites against current imagery or authoritative local data before any planning conclusion.

## 12. Sensitivity analysis

At minimum, the report should show how conclusions respond to:

- a finer grid resolution;
- alternative fallback speed assumptions;
- inclusion versus exclusion of unknown-access and customer-only chargers;
- alternative optimization thresholds, especially 5 and 15 minutes;
- different site budgets \(K\);
- alternative candidate POI class allowlists; and
- candidate snap and minimum-spacing parameters.

Stable recommendations across these cases are stronger than recommendations that depend on one parameter choice.

## 13. Limitations

- OSM completeness and positional accuracy vary. Chargers, access status, road restrictions, and POIs may be missing or outdated; official district geometry and its OSM crosswalk can also disagree.
- The road model represents assumed static travel time, not traffic by time of day, queueing, charger occupancy, or routing-provider turn penalties.
- The 750 m sample assigns one network time to an entire clipped fragment and can smooth small local barriers.
- Nearest-node snapping can connect a point to the wrong carriageway or access road. Large snap distances are flagged but do not replace site inspection.
- Land-area coverage measures spatial reach; WorldPop coverage measures modelled residents, not EV ownership, employment, trip generation, or utilization.
- A charger site is treated as available regardless of connector compatibility, charging power, number of plugs, reliability, opening hours, or price unless a separate scenario models these.
- OSM POI screening does not establish legal, electrical, financial, or engineering feasibility.
- Maximum coverage is budget and candidate-pool dependent. A WorldPop objective can still underrepresent employment, tourism, freight, road-trip, and low-population strategic needs.
- Official boundaries, Natural Earth coastline geometry, WorldPop, and OSM are dated inputs. Results are tied to the recorded snapshots and should not be read as a cadastral or construction determination.

The recommendations are therefore planning-screening results, not construction approvals.
