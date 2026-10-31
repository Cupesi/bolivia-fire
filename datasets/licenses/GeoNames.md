# GeoNames Bolivia place names

The [GeoNames dump documentation](https://download.geonames.org/export/dump/readme.txt)
licenses the data under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
and specifies UTF-8 tab-delimited records. This was verified on 2026-10-31;
the exact documentation bytes and their SHA-256 are retained beside the ZIP
snapshot and recorded in [sources.yaml](../sources.yaml).

Required attribution: **Place names: GeoNames, CC BY 4.0**.

The Bolivia country dump was retrieved from
[BO.zip](https://download.geonames.org/export/dump/BO.zip), with source
Last-Modified `Sat, 31 Oct 2026 02:50:32 GMT`. Its SHA-256 is
`c78195cf34d32f5b5becba9015f7b0d0e34cf8e56efeb291d3f25ce44f311e47`.
Only country `BO`, feature class `P` records are used. Original names are
preserved; coordinates, populations and feature codes describe the pinned
snapshot, without a guarantee of accuracy or completeness.

Derived outputs select the nearest populated place by great-circle distance
and up to three places inside each eligible national cell, ordered by reported
population. The place metadata does not change benchmark cells, labels or
predictions. Retain the attribution and identify these selection operations
when redistributing it.
