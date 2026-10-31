# geoBoundaries Bolivia gbOpen

The pinned Bolivia ADM0 and ADM1 source boundaries are **Public Domain** under
their [ADM0 metadata](https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM0/)
and [ADM1 metadata](https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM1/).
Verification on 2026-10-31 read the exact `boundaryLicense` field from both
official API responses: **`Public Domain`**. Their `gjDownloadURL` values point
to revision `9469f09`, matching the pinned releases in
[sources.yaml](../sources.yaml):

| Level | `boundaryID` | `boundaryLicense` |
|---|---|---|
| ADM0 | `BOL-ADM0-4181665` | `Public Domain` |
| ADM1 | `BOL-ADM1-26024454` | `Public Domain` |

Both responses give `boundarySource: GeoBolivia, Derived from ADM3 layer` and
`licenseDetail: Public Domain; free use and access to information`. These are
the country-specific source terms; a general geoBoundaries licence label must
not replace them.

Attribution: GeoBolivia; geoBoundaries gbOpen Bolivia ADM0 and ADM1, version
9469f09, accessed 2026-10-31. Cite Runfola et al. (2020),
[geoBoundaries: A global database of political administrative boundaries](https://doi.org/10.1371/journal.pone.0231866).

Derived cell intersections, area weights and regional assignments may be
redistributed. Retain the source pins and attribution so the boundary snapshot
and processing can be reconstructed. The application credits are registered in
[the API catalog](../../backend/app/catalog.py); metadata and CSV exports use
that registry, and the frontend renders its product, provider, licence,
processing and citation fields.

## Province and municipality snapshots

The [ADM2 metadata](https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM2/)
and [ADM3 metadata](https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM3/)
were verified on 2026-10-31. Both report `boundaryLicense: Public Domain` and
`licenseDetail: Public Domain; free use and access to information`, for
revision `9469f09`, representing 2015 boundaries.

| Level | Boundary ID | Units | Source SHA-256 |
|---|---|---:|---|
| ADM2 | BOL-ADM2-80513517 | 110 | `33a11ff32d935ad24f2e412a09ff383cbab2c63daff6b21de905dd3953ccac8c` |
| ADM3 | BOL-ADM3-33444609 | 339 | `35bf9e07686f94dddb7d9c592aed36c8d981bafa91df12ddd7295142cbdc6aa2` |

The recorded ADM2 source is `GeoBolivia, Derived from ADM3 layer`; ADM3 records
`GeoBolivia`. Their licence source is the GeoBolivia catalogue record
`aeeb85a9-23df-48d4-a4e5-dd19e8b206db`, as pinned in sources.yaml.

Attribution: GeoBolivia; geoBoundaries gbOpen Bolivia ADM2 and ADM3, revision
9469f09, accessed 2026-10-31; Runfola et al. (2020),
doi:10.1371/journal.pone.0231866. Derived display geometries use
topology-preserving simplification; each export records its tolerance in
degrees and source hash. Cell assignments use the unsimplified snapshots.
