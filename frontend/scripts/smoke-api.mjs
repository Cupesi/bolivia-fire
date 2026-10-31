import assert from "node:assert/strict";

const base = process.env.API_BASE_URL ?? "http://127.0.0.1:8765";
async function json(path) {
  const response = await fetch(new URL(`/api/v1${path}`, base));
  assert.equal(response.status, 200, `${path}: HTTP ${response.status}`);
  return response.json();
}

const [meta, grid, catalog] = await Promise.all([
  json("/meta"),
  json("/grid"),
  json("/weeks?limit=700&offset=0"),
]);
assert.match(meta.protocol.sha256, /^[a-f0-9]{64}$/);
assert.ok(meta.available_layers.includes("observed"));
assert.ok(grid.cell_id.length > 0);
assert.equal(catalog.weeks.length, catalog.total);
assert.ok(catalog.total > 0 && catalog.total <= 700);
const latest = catalog.weeks.at(-1).issue_date;
const layer = await json(`/layer?issue_date=${latest}&kind=observed`);
assert.equal(layer.cell_index.length, layer.value.length);
assert.equal(layer.value.length, layer.n_primary.length);
assert.ok(layer.value.length > 0);
assert.ok(
  layer.cell_index.every(
    (index) =>
      Number.isInteger(index) && index >= 0 && index < grid.cell_id.length,
  ),
);

const incomplete = catalog.weeks.find((week) => week.label_complete_share < 1);
let missingLabels = 0;
if (incomplete) {
  const missingLayer = await json(
    `/layer?issue_date=${incomplete.issue_date}&kind=observed`,
  );
  missingLabels = missingLayer.value.filter((value) => value === null).length;
  assert.ok(missingLabels > 0, "Incomplete labels must remain null");
}

const cell = grid.cell_id[Math.floor(grid.cell_id.length / 2)];
const start = catalog.weeks[Math.max(0, catalog.weeks.length - 52)].issue_date;
const series = await json(`/cells/${cell}/series?start=${start}&end=${latest}`);
assert.ok(series.issue_date.length > 0 && series.issue_date.length <= 700);
assert.equal(series.issue_date.length, series.y.length);
assert.equal(series.issue_date.length, series.p.length);
assert.ok(series.issue_date.every((date) => date >= start && date <= latest));

const csvResponse = await fetch(
  new URL(`/api/v1/export?issue_date=${latest}&kind=observed&format=csv`, base),
);
assert.equal(csvResponse.status, 200);
const csv = await csvResponse.text();
assert.ok(csv.startsWith("# project="));
const lines = csv
  .split(/\r?\n/)
  .filter((line) => line && !line.startsWith("#"));
assert.equal(lines[0], "cell_id,issue_date,kind,variant,model,value,n_primary");
assert.equal(lines.length - 1, layer.value.length);
assert.ok(lines.slice(1).every((line) => line.split(",")[1] === latest));
console.log(
  JSON.stringify(
    {
      protocol: meta.protocol.version,
      cells: grid.cell_id.length,
      weeks: catalog.total,
      latest,
      weeklyValues: layer.value.length,
      incompleteWeek: incomplete?.issue_date ?? null,
      missingLabels,
      selectedCell: cell,
      seriesWeeks: series.issue_date.length,
      csvRows: lines.length - 1,
      csvBytes: Buffer.byteLength(csv),
    },
    null,
    2,
  ),
);
