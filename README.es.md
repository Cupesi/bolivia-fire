# Bolivia Fire

[English](README.md) · **Español**

Bolivia Fire construye un conjunto de datos semanal y un proceso de evaluación reproducibles
para las detecciones satelitales de incendios en Bolivia. El
[explorador en línea](https://bolivia-fire.cupesi.org) presenta una estimación semanal experimental
con datos casi en tiempo real; la API local permite explorar la evaluación histórica. La variable
objetivo indica si hay al menos una detección seleccionada de NASA FIRMS VIIRS S-NPP en la porción
nacional de una celda de 0.05° durante los siete días que comienzan el lunes a las 00:00 UTC.

El LightGBM estricto calibrado alcanza una precisión promedio (AP) de 0.323393 en la prueba temporal
(IC del 95% [0.221655, 0.382973]). H1 recibe respaldo: el IC del 95% de la mejora pareada de AP
frente a la climatología sin calibrar, seleccionada en validación, es [0.099838, 0.217886].
H2 cumple la regla fijada porque el intervalo de AP retrospectiva menos estricta
[-0.004107, 0.001889] contiene cero; esto no constituye una prueba de equivalencia.
La AP en la reserva geográfica es 0.383912 (IC del 95% [0.250128, 0.467940]).
Consulte los [resultados y análisis de sensibilidad](docs/results.md), la
[evaluación de origen](artifacts/evaluation/test_strict.json) y la
[comparación posterior de modelos](docs/model-comparison.md), que conserva el LightGBM publicado
y presenta por separado los resultados exploratorios.

La variable objetivo describe detecciones satelitales, no cantidades de incendios, igniciones,
superficie quemada ni una alerta oficial. Las etiquetas negativas completas indican que no se
observó ninguna detección seleccionada; las semanas objetivo incompletas tienen etiquetas nulas.
Consulte la [ficha del conjunto de datos](datasets/dataset_card.md).

## Capturas de pantalla

![Estimación semanal y serie histórica de una celda](docs/assets/screenshots/live-spanish-desktop.png)

Estimación semanal con fecha de emisión 2026-10-26: cuadrícula nacional, escala de probabilidad
y serie histórica de una celda seleccionada de San José. El detalle identifica municipio,
provincia, departamento y localidad más cercana.

![Mapa ampliado y detalle de localidades de la celda](docs/assets/screenshots/live-cell-detail.png)

Mapa de estimaciones ampliado, con límites administrativos y nombres de localidades, junto al
detalle de la celda seleccionada y su serie histórica semanal. Esta vista está en inglés.

<details>
<summary>Tabla, vista móvil e interfaz en español</summary>

![Tabla de estimaciones con columnas de lugares](docs/assets/screenshots/table-desktop.png)

Tabla de estimaciones filtrada por Campesina el Soto, con municipio, provincia, departamento,
localidad más cercana, superficie nacional y probabilidad, junto a la serie histórica de la celda.
Esta vista está en inglés.

![Estimación semanal en un celular](docs/assets/screenshots/estimate-mobile.png)

La vista móvil de 390 × 844 muestra el mapa nacional de estimaciones, la fecha de emisión,
el modelo y la leyenda de probabilidad. Esta vista está en inglés.

![Estimación semanal en español](docs/assets/screenshots/live-spanish-desktop.png)

Interfaz en español con fecha de emisión 2026-10-26, las mismas estimaciones nacionales y el detalle
de la celda seleccionada. La interfaz está disponible en inglés y español.

</details>

## Cómo funciona

![Proceso de adquisición, evaluación y publicación](docs/assets/diagrams/pipeline.gif)

Las etapas siguen el [protocolo fijado](configs/protocol.yaml): validar observaciones FIRMS de
procesamiento estándar, construir etiquetas y variables, seleccionar y calibrar con los años de
validación, y registrar las predicciones y métricas de prueba. El [proceso de actualización](docs/live.md)
aplica el modelo fijado a datos casi en tiempo real anteriores a la fecha de emisión y separa
las reconstrucciones históricas de las métricas prospectivas.

Los mapas históricos combinan estimaciones temporales `strict` con estimaciones `strict_geo` para
las celdas de la reserva geográfica. Un contorno violeta fino marca estos bloques; el detalle de
cada celda identifica el modelo que produjo el valor. Las estimaciones geográficas no disponibles
tienen una entrada propia en la leyenda.

## Instalación

Se requiere Python 3.12 o posterior y [uv](https://docs.astral.sh/uv/). Desde la raíz del repositorio:

```powershell
uv sync --all-extras
uv run --no-sync bolivia-fire --help
```

La interfaz web requiere Node 22.12 o posterior y npm:

```powershell
cd frontend
npm ci
npm run check
npm run test
npm run build
cd ..
```

## Comandos y reproducción

Estas consultas de interfaz y verificaciones sin conexión no descargan datos de origen
ni modifican modelos ajustados:

```powershell
uv run --no-sync bolivia-fire build --help
uv run --no-sync bolivia-fire train --help
uv run --no-sync bolivia-fire select --help
uv run --no-sync bolivia-fire evaluate --help
uv run --no-sync bolivia-fire predict --help
uv run --no-sync bolivia-fire compare --help
uv run --no-sync bolivia-fire live --help
uv run --no-sync python -m backend.app.prepare --help
uv run --no-sync pytest tests backend/tests -m "not live and not local_data" -q -p no:cacheprovider
```

La [guía de reproducción](docs/reproduction.md) presenta, en orden, la adquisición, construcción
del conjunto de datos, extracción de precipitación, entrenamiento, selección con validación,
acceso a la prueba final, exportación de predicciones y arranque de la API y la interfaz.
Los cuatro cuadernos cubren [exploración](notebooks/01_exploration.ipynb),
[variables](notebooks/02_features.ipynb), [modelado](notebooks/03_modeling.ipynb) y
[evaluación](notebooks/04_evaluation.ipynb). El [índice de documentación](docs/index.md) enlaza
los contratos, el [alojamiento estático](docs/frontend.md#static-hosting) y las mediciones de recursos.

## Acceso a los datos

Obtenga una clave de mapas de NASA FIRMS, copie [.env.example](.env.example) a `.env` y configure
`FIRMS_MAP_KEY` localmente; una variable de entorno tiene prioridad. Mantenga la clave y las URL
con credenciales fuera de los registros y del control de versiones. Las versiones fijadas y la
atribución de FIRMS, geoBoundaries, GeoNames y CHIRPS figuran en
[datasets/sources.yaml](datasets/sources.yaml). MapBiomas se omitió tras fallar solicitudes
anónimas verificadas de sus rásteres; el modelo retrospectivo ajustado utiliza precipitación de CHIRPS.

Git contiene manifiestos, versiones fijadas de las fuentes, esquemas, notas de licencia y
resúmenes descriptivos. Los datos originales, paquetes intermedios, conjuntos procesados,
binarios de modelos y archivos Parquet de predicciones son artefactos generados localmente.
Los manifiestos registran la identidad de las descargas y sus hashes, sin las observaciones
originales. Consulte [almacenamiento y reconstrucción](datasets/README.md).

## Limitaciones

- Las nubes, el humo, la hora de paso del satélite, los incendios pequeños y las interrupciones
  del sensor afectan la detección; un cero no demuestra ausencia de fuego. La regla de
  interrupciones no demuestra que la observación haya estado libre de nubes.
- Las celdas no tienen la misma superficie. Los presupuestos de área utilizan intersecciones
  geodésicas con el territorio nacional; el ADM0 fijado excluye grandes salares y lagos interiores.
- La equivalencia entre SP y NRT no se ha validado. NRT no incluye el tipo de fuente; el filtro
  en línea utiliza una máscara de fuentes estáticas que aproxima el filtro de fuentes de vegetación de SP.
- La climatología de entrenamiento utiliza otros años de entrenamiento, incluidos años posteriores,
  según la regla fijada de ajuste cruzado. Las etiquetas de la reserva nunca intervienen en el ajuste
  ni en la selección. Las versiones históricas de CHIRPS v3 son retrospectivas.
- Las reconstrucciones históricas en línea se presentan por separado del desempeño prospectivo.
  Un registro breve no demuestra fiabilidad; las semanas objetivo incompletas no se evalúan.

## Licencia y cita

El código tiene [licencia MIT](LICENSE). Los datos conservan los términos de sus fuentes:
[FIRMS](datasets/licenses/FIRMS.md), [geoBoundaries](datasets/licenses/geoBoundaries.md),
[GeoNames](datasets/licenses/GeoNames.md), [CHIRPS](datasets/licenses/CHIRPS.md),
[MapBiomas](datasets/licenses/MapBiomas.md) y la [ficha del conjunto de datos](datasets/dataset_card.md).
Las versiones fijadas de geoBoundaries gbOpen BOL ADM0/ADM1 en la revisión `9469f09` indican
`boundaryLicense: Public Domain`. Conserve la atribución a GeoBolivia, geoBoundaries y
Runfola et al. (2020). Los nombres de lugares de GeoNames tienen licencia CC BY 4.0.
CHIRPS v3.0 final daily `rnl` utiliza CC BY 4.0 con la renuncia declarada por el proveedor;
cite Climate Hazards Center y CHIRPS3 Data Repository (2025), la desagregación de ERA5,
la asignación a centros de celda y la agregación de precipitación antecedente.
Use [CITATION.cff](CITATION.cff) para citar el software y conserve las atribuciones de las fuentes
al redistribuir datos derivados.

Bolivia Fire es mantenido por Victor Hugo Cuellar Flores y alojado por la organización de código abierto Cupesí Labs.
