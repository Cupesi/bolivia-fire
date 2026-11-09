# MapBiomas Bolivia

Licence: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/), as stated in the [official portal](https://bolivia.mapbiomas.org/) and [download page](https://bolivia.mapbiomas.org/downloads/mapas-para-descargar-geotiff/).

English attribution: MapBiomas Bolivia Project, Collection 4 annual land-cover and land-use maps of Bolivia, accessed 2026-11-01 through https://bolivia.mapbiomas.org/. The [provider citation guidance](https://bolivia.mapbiomas.org/preguntas-frecuentes/?tema=datos) requests the project, collection, access date and platform link.

Derived class fractions may be redistributed with attribution, a licence link, and an explanation of the aggregation and other modifications. The authenticated polygon pilots use native-grid pixel-area sums over each cell's national part, grouped under the pinned Collection 4 legend. The raster alternative applies the national mask to binary class indicators, aggregates means to the exact 0.05-degree project grid and weights areas locally with the pinned national cell areas. The two methods can differ at borders and in degree-versus-geodesic weighting; see [covariate notes](../../docs/covariates.md).

Anonymous raster access returned HTTP 403. Authenticated Earth Engine access was verified on 2026-11-01 for the official public Collection 4 asset. National maps for 2012-2025 were downloaded directly to local files, with project-level Cloud Monitoring as the primary usage ledger. Request profiles remain secondary evidence. The full 2012 raster passed the predeclared 0.01 absolute class-fraction and coverage tolerance against 1,000 polygon cells. Per-year hashes, measured usage, collection metadata and the proposed shared source-registry update are in `datasets/manifests/mapbiomas_source.jsonl`. Partial files remain historical evidence and do not establish feature availability.

Collection 4 was released on 2026-10-01 and uses temporal rules involving following years and filtering across the series. Derived issue-level features are retrospective: a Y-1 map reference does not establish historical availability.
