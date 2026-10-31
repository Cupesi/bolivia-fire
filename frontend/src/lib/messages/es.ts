import type { Messages } from "./en";
export const es = {
  geographicHoldout: "Reserva geográfica (modelo geográfico)",
  geographicUnavailable:
    "Reserva geográfica (sin estimación geográfica esta semana)",
  valueModel: "Modelo del valor",
  dateRange: "{start} – {end} (UTC)",
  enCode: "EN",
  esCode: "ES",
  variantStrict: "Estricto",
  variantAllConfidence: "Estricto · todos los niveles de confianza",
  variantDropClimatology: "Estricto · sin climatología",
  variantDropNational: "Estricto · sin variables nacionales",
  variantDropNeighborhood: "Estricto · sin variables de celdas vecinas",
  variantExcludeLowCoverage: "Estricto · sin semanas de baja cobertura",
  backfillNote:
    "Estimación retrospectiva: generada después de la fecha de emisión con datos adquiridos antes de ella",
  departmentFallback:
    "Las celdas sin climatología propia calculada con datos de entrenamiento usan la climatología departamental de entrenamiento",
  reasonSingleClass: "una sola clase",
  reasonEmpty: "subconjunto vacío",
  reasonArea: "área no disponible",
  reasonNoPositives: "sin positivos",
  reasonBaseline: "no disponible o una sola clase",
  metricAp: "AP",
  metricBrier: "Brier",
  metricLogLoss: "Pérdida logarítmica",
  metricRocAuc: "ROC AUC",
  metricEce: "Error esperado de calibración",
  metricRecall5: "Sensibilidad al 5 % del área",
  metricRecall10: "Sensibilidad al 10 % del área",
  metricClimatology: "AP de climatología",
  noAvailableIssueWeeks: "No hay semanas de emisión disponibles",
  skipToDataExplorer: "Ir al explorador de datos",
  boliviaFire: "Bolivia Fire",
  aboutLimitations: "Acerca del proyecto y limitaciones",
  eyebrow: "Observaciones satelitales · Semanales · 0,05°",
  weeklyFireDetections: "Detecciones satelitales semanales",
  liveIntro: "Estimación semanal experimental (datos casi en tiempo real)",
  apiIntro:
    "Explore las detecciones satelitales observadas y las estimaciones retrospectivas disponibles para Bolivia.",
  loadingTheDataExplorer: "Cargando el explorador de datos",
  bootHint: "Leyendo la cuadrícula, las semanas disponibles y los metadatos.",
  dataFilesOffline: "Archivos de datos no disponibles",
  apiOffline: "Sin conexión con la API",
  dataServiceUnavailable: "Servicio de datos no disponible",
  retryConnection: "Reintentar conexión",
  dataExplorer: "Explorador de datos",
  issueWeek: "Semana de emisión",
  mondayUtc: "Lunes · UTC",
  previousWeek: "Semana anterior",
  nextWeek: "Semana siguiente",
  layer: "Capa",
  observedDetections: "Detecciones observadas",
  weeklyEstimate: "Estimación semanal",
  retrospectiveEstimate: "Estimación retrospectiva",
  model: "Modelo",
  noneAvailable: "Sin opciones disponibles",
  variant: "Variante",
  weekHint:
    "Alt + ← / → cambia la semana de emisión. La semana objetivo abarca los siete días siguientes.",
  noEstimatesHint: "No hay estimaciones preparadas disponibles.",
  retrospectiveNotice:
    "Estimación retrospectiva del modelo; no es un pronóstico operativo.",
  forecastMetadata: "Metadatos de la estimación",
  prospectiveEstimate: "Estimación prospectiva",
  backfillEstimate:
    "Estimación retrospectiva · generada después de la fecha de emisión",
  issue: "Fecha de emisión:",
  text0000Utc: "00:00 UTC",
  target: "· Semana objetivo:",
  published: "Publicación:",
  hAfterIssueModel: "después de la emisión · Modelo:",
  inputWindow: "Período de datos de entrada:",
  through: "a",
  text2359UtcBeforeTheIssue: "23:59 UTC, antes de la fecha de emisión.",
  inputDaysInThe365Day:
    "{count} días no observados en el período de entrada de 365 días (cortes del sensor)",
  inputDaysInThe365DayOne:
    "{count} día no observado en el período de entrada de 365 días (cortes del sensor)",
  unobservedInputDays: "Días no observados en los datos de entrada",
  forecastProvenance: "Procedencia de la estimación",
  exactTargetInterval: "Intervalo exacto de la semana objetivo",
  utc: ") UTC",
  publicationTimestamp: "Fecha y hora de publicación",
  modelSha256Identifier: "SHA-256 / identificador del modelo",
  observationFileUnavailable: "Archivo de observaciones no disponible:",
  observationsPending:
    "Las observaciones de esta semana aún no están disponibles. Podrá seleccionar las detecciones observadas cuando se hayan observado los siete días de la semana objetivo.",
  targetProgress:
    "Semana objetivo en curso: observaciones disponibles hasta el",
  targetPending:
    "Semana objetivo en curso: aún no hay observaciones disponibles.",
  outageNotice:
    "Esta semana incluye días no observados. Las etiquetas incompletas se marcan como datos faltantes, nunca como cero.",
  targetOutages:
    "Días no observados de la semana objetivo (cortes del sensor):",
  lowCoverageNotice:
    "Indicador orientativo de baja cobertura: al menos un día de la semana objetivo no tiene detecciones en el área de adquisición. Este indicador no determina si las etiquetas están completas.",
  weeklyGrid: "Cuadrícula semanal",
  labelCompleteness: " de etiquetas completas",
  model2: "Modelo:",
  variant2: "· Variante:",
  mapView: "Vista de mapa",
  map: "Mapa",
  tableView: "Vista de tabla",
  table: "Tabla",
  loadingWeeklyValues: "Cargando valores semanales…",
  missingSelection: "No hay estimaciones para esta semana, modelo y variante.",
  emptySnapshot:
    "Esta versión de los datos no contiene celdas ni semanas de emisión disponibles.",
  missingPredictions: "Sin estimaciones",
  selectionUnavailable: "Selección no disponible",
  retryWeeklyData: "Reintentar carga semanal",
  noWeeklyData: "Sin datos semanales",
  mapLegend: "Leyenda del mapa",
  probability: "Probabilidad",
  detected: "Con detecciones",
  notDetected: "Sin detecciones",
  missingObservation: "Observación faltante",
  estimate: "/ estimación",
  missingHint:
    "El gris indica datos no disponibles. Una etiqueta faltante no demuestra ausencia de detecciones.",
  downloadWeekCsv: "Descargar CSV de la semana",
  cellDetail: "Detalle de la celda",
  nationalIntersection: "Intersección con el territorio nacional",
  nationalArea: "Área dentro del país",
  km: "km²",
  loading: "Cargando…",
  unavailable: "No disponible",
  weeklyHistory: "Historial semanal",
  seriesKey: "p = probabilidad estimada · y = indicador observado",
  from: "Desde",
  to: "Hasta",
  loadingCellHistory: "Cargando historial de la celda…",
  observedYIs1WhenAt:
    "El indicador observado y vale 1 si hubo al menos una detección seleccionada, y 0 si no hubo ninguna en un intervalo completo. Los períodos sin datos se mantienen como faltantes.",
  selectAGridCell: "Seleccione una celda",
  clickTheMapOrChooseA:
    "Haga clic en el mapa o elija una celda de la tabla para consultar su área e historial semanal.",
  keyboardHint:
    "Teclado: sitúe el foco en el mapa, desplácese con las flechas y pulse Enter.",
  aboutAndLimitations: "Acerca del proyecto y sus limitaciones",
  whatIsADetection: "¿Qué es una detección?",
  theTargetIsAtLeastOne: "El objetivo es al menos una detección VIIRS S-NPP",
  nearRealTime: "casi en tiempo real",
  standardProcessing: "de procesamiento estándar",
  targetDefinition:
    "de un presunto incendio de vegetación, con confianza nominal o alta, dentro de la porción de una celda situada en Bolivia durante la semana objetivo.",
  detectionLimitations:
    "Las detecciones no equivalen a incendios, puntos de inicio, superficie quemada ni alertas oficiales. Un cero no demuestra ausencia: las nubes, el humo, los horarios de paso del satélite y los incendios pequeños afectan la detección.",
  observationCoverage: "Cobertura de observaciones",
  aDayMustBeAcquiredAnd:
    "Para considerar un día observado, sus datos deben haberse adquirido y los cortes documentados del sensor deben sumar menos de seis horas. Cualquier día no observado deja la etiqueta semanal como faltante. Los conteos observados pueden seguir disponibles en semanas incompletas.",
  experimentalWeeklyEstimates: "Estimaciones semanales experimentales",
  retrospectiveEstimates: "Estimaciones retrospectivas",
  liveLimitations:
    "El modelo se entrenó con detecciones de procesamiento estándar y se aplica a detecciones casi en tiempo real. El historial de verificación permite evaluar su desempeño. Las revisiones de procesamiento y los retrasos pueden modificar la disponibilidad de detecciones. Esta estimación es experimental y no constituye una alerta oficial.",
  retrospectiveLimitations:
    "Estas estimaciones del modelo son retrospectivas y no constituyen pronósticos operativos. La simulación estricta supone que las detecciones de procesamiento estándar se aproximan a las disponibles casi en tiempo real; esa equivalencia no se ha validado. Las variables de precipitación CHIRPS revisadas no reproducen la información disponible en cada momento histórico.",
  datasetSpecificLimitations: "Limitaciones del conjunto de datos",
  sources: "Fuentes",
  placeNames: "Nombres de lugares:",
  geonames: "GeoNames",
  ccBy40: "CC BY 4.0",
  provinceAndMunicipalityBoundaries: "Límites de provincias y municipios:",
  geoboundariesAdm2Adm3: "geoBoundaries ADM2/ADM3",
  sourceCreditsAreUnavailableSeeThe:
    "No están disponibles los créditos de las fuentes. Consulte las",
  datasetSourceTerms: "condiciones de uso de las fuentes de datos",
  dataProvenance: "Procedencia de datos",
  viewIdentifiers: "Ver identificadores",
  areaDefinition:
    "Las celdas usan coordenadas geográficas y tienen áreas distintas. Los valores de área corresponden a la intersección de cada celda con el territorio nacional, calculada sobre WGS84.",
  boliviaWeeklyDetectionBenchmarkResearchData:
    "Evaluación de detecciones semanales en Bolivia · Explorador de datos de investigación",
  boliviaCellMap: "Mapa de celdas de Bolivia",
  mapControls: "Controles del mapa",
  zoomIn: "Acercar",
  zoomOut: "Alejar",
  resetMapExtent: "Restablecer extensión del mapa",
  reset: "Restablecer",
  n: "N ↑",
  bolivia: "Bolivia ·",
  grid: "° por celda",
  mapHelp:
    "Use la rueda o +/− para acercar o alejar; arrastre para desplazar. Las flechas cambian de celda; Enter selecciona. Ctrl + flechas desplaza; Inicio restablece.",
  pressEnterToSelectACentral: "Pulse Enter para seleccionar una celda central.",
  filterPlaces: "Filtrar lugares",
  municipalityProvinceDepartmentOrPlace:
    "Municipio, provincia, departamento o lugar",
  topCells: "Celdas con mayor valor",
  top50ByValue: "Las 50 con mayor valor",
  top100ByValue: "Las 100 con mayor valor",
  top500ByValue: "Las 500 con mayor valor",
  allCells: "Todas las celdas",
  sortCellsBy: "Ordenar celdas por",
  reverseCellSort: "Invertir orden de las celdas",
  descending: "Descendente ↓",
  ascending: "Ascendente ↑",
  filterBeforeRankingMissingValuesSort:
    "El filtro se aplica antes de ordenar por valor. Los datos faltantes van al final. Las flechas cambian de fila; Enter selecciona.",
  currentWeekCells: "Celdas de la semana seleccionada",
  tableCaption:
    "Valores de la semana seleccionada, filtrados por lugar y clasificados por valor antes de ordenar y paginar. El indicador vale 1 si hubo al menos una detección y 0 si no hubo ninguna en una semana completamente observada. En semanas incompletas, el indicador se muestra como faltante. El conteo es el número de detecciones y puede estar disponible aunque la semana esté incompleta.",
  cell: "Celda",
  cellId: "identificador de celda",
  municipality: "Municipio",
  municipality2: "municipio",
  province: "Provincia",
  province2: "provincia",
  department: "Departamento",
  department2: "departamento",
  nearestPlace: "Lugar más cercano",
  nearestPlace2: "lugar más cercano",
  areaKm: "Área (km²)",
  nationalArea2: "área dentro del país",
  value: "valor",
  count: "Conteo",
  count2: "conteo",
  unassigned: "Sin asignar",
  missing: "Faltante",
  noCellsMatchThisPlaceFilter:
    "Ninguna celda coincide con el filtro de lugares.",
  noCellsAreAvailableForThis: "No hay celdas disponibles para esta semana.",
  previousTablePage: "Página anterior de la tabla",
  previous: "Anterior",
  nextTablePage: "Página siguiente de la tabla",
  next: "Siguiente",
  tablePage: "Página {page} de {pages} · {count} celdas",
  tablePageOne: "Página {page} de {pages} · {count} celda",
  municipality3: "Municipio:",
  province3: "Provincia:",
  department3: "Departamento:",
  placesInCell: "Lugares dentro de la celda:",
  seriesChartLabel:
    "Indicador semanal de detección observada (y) y probabilidad estimada (p), de cero a uno",
  observedPoint: "{date}: y observado = {y}; detecciones = {n}",
  missingPoint: "{date}: observación faltante",
  probabilityPoint: "{date}: p = {p}",
  estimateP: "Estimación p",
  observedY2: "Observado y",
  missingY: "y faltante",
  noPredictionsInThisRangeObservations:
    "No hay estimaciones en este período. Las observaciones siguen disponibles.",
  readWeeklyValues: "Consultar valores semanales",
  selectedCellWeeklySeries: "Serie semanal de la celda seleccionada",
  detections2: "Detecciones",
  noWeeklyRecordsInThisRange: "No hay registros semanales en este período.",
  trackRecord: "Historial de verificación",
  verificationHint:
    "Solo se incluyen semanas objetivo completas. La climatología usa la misma semana y las mismas celdas. Un historial de pocas semanas aporta evidencia limitada sobre el desempeño.",
  loadingVerificationScores: "Cargando resultados de verificación…",
  retryTrackRecord: "Reintentar carga del historial",
  noVerifiedWeeksYet: "Aún no hay semanas verificadas.",
  prospectiveHint:
    "El gráfico muestra solo semanas prospectivas. Las estimaciones generadas después de la fecha de emisión no miden el desempeño prospectivo.",
  verificationChartLabel:
    "Precisión promedio semanal comparada con la climatología de entrenamiento, solo para semanas prospectivas, de cero a uno",
  ap: ": AP",
  climatologyAp: ": AP de climatología",
  estimateAp: "AP de la estimación",
  climatologyAp2: "AP de climatología",
  noProspectiveVerifiedWeeksYet: "Aún no hay semanas prospectivas verificadas.",
  verificationScroll:
    "Resultados de verificación; desplácese horizontalmente para ver todas las métricas",
  publishedUtc: "Publicación UTC",
  lagHours: "Demora (horas)",
  cells2: "Celdas",
  positives: "Positivos",
  ap2: "AP",
  brier: "Brier",
  recallAt5Area: "Sensibilidad al 5 % del área",
  recallAt10Area: "Sensibilidad al 10 % del área",
  rocAuc: "ROC AUC",
  ece: "ECE",
  notes: "Notas",
  metricHint:
    "AP es la precisión promedio; un valor mayor indica mejor desempeño. En Brier y en el error esperado de calibración (ECE), los valores menores son mejores. Cuando una métrica no puede calcularse, se muestra «Indefinido», nunca cero.",
  prospectiveWeeks: "Semanas prospectivas",
  weeklyVerificationScores: "Resultados semanales de verificación",
  backfillWeeksGeneratedAfterIssueTime:
    "Semanas retrospectivas · generadas después de la fecha de emisión",
  backfillVerificationScores: "Resultados de verificación retrospectiva",
  undefined: "Indefinido",
  trackRecordUnavailable: "Historial de verificación no disponible.",
  theRequestCouldNotBeCompleted: "No se pudo completar la solicitud.",
  invalidWeek:
    "Filtros incompatibles. Elija una de las semanas de emisión disponibles.",
  invalidRange:
    "Período incompatible. La fecha inicial debe ser anterior a la final y el período no debe superar 700 semanas.",
  apiOfflineTheLocalDataService:
    "Sin conexión con la API. No se pudo acceder al servicio local de datos.",
  noPreparedDataAreAvailableFor: "No hay datos preparados para esta selección.",
  incompatibleFiltersChooseAnAvailableWeek:
    "Filtros incompatibles. Elija una semana, un modelo y una variante disponibles.",
  theDataServiceCouldNotComplete:
    "El servicio de datos no pudo completar la solicitud.",
  theDataServiceReturnedAnUnreadable:
    "El servicio de datos devolvió una respuesta ilegible.",
  invalidGridColumns: "Las columnas de la cuadrícula no son coherentes.",
  theGridResponseIsIncomplete: "La respuesta de la cuadrícula está incompleta.",
  unreadableTrackCsv: "El CSV del historial de verificación es ilegible.",
  theTrackRecordCsvIsMissing:
    "Faltan columnas obligatorias en el CSV del historial de verificación.",
  invalidTrackColumns:
    "Las columnas del CSV del historial de verificación no son coherentes.",
  theTrackRecordHasAnInvalid:
    "El historial de verificación contiene una fecha de emisión inválida.",
  missingTrackTiming:
    "Falta la información que documenta las fechas y horas de emisión y publicación en el historial de verificación.",
  invalidTrackMetric:
    "El historial de verificación contiene una métrica inválida.",
  missingTrackPopulation:
    "Faltan conteos de celdas o de casos positivos en el historial de verificación.",
  missingTrackLag:
    "Falta la demora de publicación en el historial de verificación.",
  dataFilesUnavailableCheckYourConnection:
    "Archivos de datos no disponibles. Revise su conexión y vuelva a intentarlo.",
  theIssueCatalogIsIncomplete:
    "El catálogo de semanas de emisión está incompleto.",
  thePlaceDataDoNotAlign:
    "Los datos de lugares no coinciden con las celdas de la cuadrícula.",
  chooseAnAvailableIssueWeek: "Elija una semana de emisión disponible.",
  theWeeklyDataFileIsIncomplete:
    "El archivo de datos semanales está incompleto.",
  invalidWeeklyValues:
    "El archivo de datos semanales tiene columnas o valores incoherentes.",
  missingForecastHistory:
    "Falta el historial de datos de entrada o la información que documenta las fechas y horas de emisión y publicación en el archivo de estimaciones.",
  invalidObservation:
    "Los conteos o el estado de completitud del archivo de observaciones no son coherentes.",
  protocolVersion: "Versión del protocolo",
  protocolSha256: "SHA-256 del protocolo",
  experimentsSha256: "SHA-256 de los experimentos",
  sourcesSha256: "SHA-256 de las fuentes",
  cellsSha256: "SHA-256 de las celdas",
  cellPlacesSha256: "SHA-256 de los lugares por celda",
  evaluationSourcesSha256: "SHA-256 de las fuentes de evaluación",
  firmsManifestSha256: "SHA-256 del manifiesto FIRMS",
  outageSnapshotSha256:
    "SHA-256 de la versión del registro de cortes del sensor",
  detectionsSha256: "SHA-256 de las detecciones",
  acquisitionDaysSha256: "SHA-256 del registro de días de adquisición",
  frozenSpBundleSha256: "SHA-256 del conjunto SP congelado",
  panelSnapshot: "Versión del panel",
  modelIdentifier: "Identificador del modelo",
  modelSha256: "SHA-256 del modelo",
  calibratorSha256: "SHA-256 del calibrador",
  finalTestOpeningUtc: "Apertura de la prueba final (UTC)",
  language: "Idioma",
  english: "Inglés",
  spanish: "Español",
  languageLoadError: "No se pudo cargar el idioma. Vuelva a intentarlo.",
  pageTitle: "Bolivia Fire · Detecciones satelitales semanales",
  layerSummary: "{count} celdas · {complete} con datos · {missing} sin datos",
  layerSummaryOne: "{count} celda · {complete} con datos · {missing} sin datos",
  sortByColumn: "Ordenar por {column}",
  selectCellId: "Seleccionar celda {id}",
  nearestPlaceNameDistance: "Lugar más cercano: {name}{distance}",
  currentProbability: "Probabilidad de la semana seleccionada",
  currentObservation: "Observación de la semana seleccionada",
  dataFileUnavailablePath: "Archivo de datos no disponible: {path}.",
  dataFileIsUnreadablePath: "El archivo de datos es ilegible: {path}.",
  satelliteDetectionsAreNotCountsOf:
    "Las detecciones satelitales no son conteos de incendios, puntos de inicio ni superficie quemada.",
  zerosAreNotProofOfAbsence:
    "Los ceros no demuestran ausencia: las nubes, el humo, los horarios de paso del satélite y los incendios pequeños reducen la detección.",
  retrospectiveEstimatesAreNotOperationalForecasts:
    "Las estimaciones retrospectivas no constituyen pronósticos operativos.",
  weeksWithUnobservedTargetDaysHave:
    "Las semanas objetivo con días no observados tienen etiquetas faltantes; nunca se rellenan con ceros.",
  strictEstimatesAssumeStandardProcessingDetections:
    "Las estimaciones estrictas suponen que las detecciones de procesamiento estándar se aproximan a las disponibles casi en tiempo real; esta equivalencia no se ha validado.",
  chirpsV3HistoricalVintagesWereUnavailable:
    "Las versiones históricas de CHIRPS v3 no estaban disponibles durante gran parte del período de estudio.",
  cloudsSmokeOverpassTimingSmallFires:
    "Las nubes, el humo, los horarios de paso del satélite, los incendios pequeños y los cortes del sensor pueden impedir la detección.",
  theModelWasTrainedOnStandard2:
    "El modelo se entrenó con detecciones de procesamiento estándar y se aplica a detecciones casi en tiempo real; el historial de verificación permite evaluar su desempeño.",
  nrtHasNoTypeFieldNominal:
    "NRT no incluye un campo de tipo: solo se conservan detecciones de confianza nominal o alta fuera de un radio geodésico de 375 m alrededor de las ubicaciones SP de tipo 1/2 observadas entre el 20 de enero de 2012 y el 31 de julio de 2026. Esta máscara de fuentes fijas reproduce de forma aproximada la selección SP tipo=0; pueden persistir fuentes fijas no identificadas y detecciones ajenas a la vegetación.",
  scheduledForecastsPublishedWithin24Hours:
    "Las estimaciones programadas publicadas dentro de las 24 horas posteriores a la emisión se marcan como prospectivas; normalmente se publican unas seis horas después del inicio de la semana objetivo. Los datos de entrada excluyen esas seis horas y la evaluación abarca los siete días completos. Las publicaciones manuales o realizadas después de ese plazo se consideran retrospectivas. Los resúmenes del desempeño prospectivo deben usar solo las filas con prospective=true.",
  incompleteTargetWeeksHaveNullLabels:
    "Las semanas objetivo incompletas tienen etiquetas nulas y no se evalúan. Las estimaciones y sus resultados pueden variar mucho entre semanas; un historial breve no demuestra fiabilidad.",
  theStrictModelIsAlsoApplied:
    "El modelo estricto también se aplica a celdas reservadas para la evaluación geográfica; allí falta la climatología por celda y la referencia de comparación usa la climatología departamental de entrenamiento.",
  viirsSNppStandardProcessing: "VIIRS S-NPP de procesamiento estándar",
  viirsSNppStandardProcessingAnd:
    "VIIRS S-NPP de procesamiento estándar y casi en tiempo real",
  nasaOpenDataReuseWithSource:
    "Reutilización de datos abiertos de NASA con atribución de la fuente",
  nasaDataPolicy: "Política de datos de NASA",
  publicDomain: "Dominio público",
  geoboliviaDerivedFromAdm3Layer: "GeoBolivia (derivado de la capa ADM3)",
  geoboliviaDerivedFromAdm3Layer2: "GeoBolivia, derivado de la capa ADM3",
  simplifiedDisplayBoundariesDerivedGridIntersections:
    "Límites simplificados para visualización; intersecciones de cuadrícula y ponderaciones por área derivadas.",
  topologyPreservingDisplaySimplificationNationalCell:
    "Simplificación visual que conserva la topología; proporciones del área nacional por celda.",
  boliviaPopulatedPlacesFeatureClassP:
    "Centros poblados de Bolivia, clase de entidad P",
  nearestPlaceByGreatCircleDistance:
    "Lugar más cercano según distancia geodésica; hasta tres lugares por celda.",
  ccBy40WithProvider:
    "CC BY 4.0, con declaración del proveedor de renuncia a derechos de autor",
  era5TemporalDisaggregationNativePixelsMapped:
    "Desagregación temporal ERA5; píxeles originales asignados a los centros de celda y agregados como variables de precipitación antecedente.",
} satisfies Messages;
