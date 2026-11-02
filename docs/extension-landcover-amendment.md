# Land-cover amendment, 2026-11-02

Amendment `landcover-2026-11-02`, version 1.0, supplements the immutable
[base contract](../configs/extension_vegetation.yaml) and
[base registration](../artifacts/evaluation/extension_registration.json).
Its machine-readable terms are in
[extension_amendment_landcover.yaml](../configs/extension_amendment_landcover.yaml).
It is prepared for coordinator commitment and registration; these files alone
do not authorize execution.

H3 validation metrics were seen before this amendment. No land-cover (H4) or
combined (H5) labels or metrics have been accessed for this amendment. The
owner has chosen to activate the six H4/H5 runs already defined in the base
manifest after the separate source, feature, access and cost gates pass.
Ordered features, published comparators, search space, seed and budget,
selection, calibration, paired rows, bootstrap, decision rules, exploratory
test handling and confirmation gates remain as registered. The amended
H4 and H5 comparisons each use identical rows against the same published
comparators, with their own intersections; they never change frozen H3 rows.
All H3 runs, scores and decisions remain unchanged and are retained by hash.

The estimator of record for H4/H5 is the implemented binary class-mask
`reduceResolution(mean)` aggregation in EPSG:4326, followed by normalization
over mapped classes and scaling of area totals to pinned geodesic national
cell areas. The native-pixel national mask approximates border intersections.
Degree-area means and this mask do not implement native geodesic
pixel/cell/boundary intersection weights. The observed 1,000-cell polygon
pilot had maximum class-fraction error 0.0006533796633643452 and mapped-coverage
error 0.0002451674139329896, both below the registered acceptance tolerance
0.01. These are pilot measurements, not a national error bound. The frozen
land-cover source manifest and raster manifest must carry that same evidence.
The fraction-sum tolerance remains the separate base requirement of 1e-6.

The registration command verifies committed amendment files and base hashes,
records UTC time and the amendment commit, and freezes hashes of all existing
metric artifacts. Commit its resulting registration record before execution.
The runner refuses an absent, uncommitted or changed chain, freezes the input
plan before labels, and writes new fits and selections under the amendment's
artifact namespace. Prior metric-bearing artifacts are never overwritten or
recomputed. This command performs no feature download, label load or fitting.

Suggested paper disclosure: "After inspecting H3 validation results and before
accessing H4/H5 labels or metrics, we registered a dated amendment activating
the previously specified land-cover and combined comparisons and documenting
the degree-area and national-border aggregation approximation with its
1,000-cell polygon-pilot acceptance evidence; prior H3 results were preserved."
