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
