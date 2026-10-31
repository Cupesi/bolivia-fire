import {
  copyFile,
  mkdir,
  readFile,
  readdir,
  stat,
  writeFile,
} from "node:fs/promises";
import { resolve, join } from "node:path";
import { build } from "vite";

if (!process.env.LIVE_DATA_DIR)
  throw new Error("Set LIVE_DATA_DIR to a live-data checkout.");
const source = resolve(process.env.LIVE_DATA_DIR);
const index = JSON.parse(await readFile(join(source, "index.json"), "utf8"));
if (!Array.isArray(index.issues))
  throw new Error("live-data index.json must contain issues.");
index.issues = [...index.issues]
  .sort((a, b) => a.issue_date.localeCompare(b.issue_date))
  .slice(-52);
if (index.issues.some((issue) => !/^\d{4}-\d{2}-\d{2}$/.test(issue.issue_date)))
  throw new Error("live-data index contains an invalid issue date.");
const forecasts = index.issues.filter((issue) => issue.forecast);
index.latest_issue = forecasts.some(
  (issue) => issue.issue_date === index.latest_issue,
)
  ? index.latest_issue
  : (forecasts.at(-1)?.issue_date ?? null);
const files = [
  "static/grid.json",
  "static/boundaries_adm0.json",
  "static/boundaries_adm1.json",
  "static/boundaries_adm2.json",
  "static/boundaries_adm3.json",
  "static/cell_places.json",
  "static/meta.json",
];
for (const issue of index.issues) {
  if (issue.forecast) files.push(`forecasts/${issue.issue_date}.json`);
  if (issue.observation) files.push(`observations/${issue.issue_date}.json`);
}
const verification = "verification/track_record.csv";
try {
  await stat(join(source, verification));
  files.push(verification);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
for (const file of files) await stat(join(source, file));
if ((await stat(join(source, "static/cell_places.json"))).size >= 5_000_000)
  throw new Error("Static cell places must be below 5 MB.");
const size = (
  await Promise.all(files.map((file) => stat(join(source, file))))
).reduce((sum, item) => sum + item.size, 0);
if (size > 200_000_000) throw new Error("Static data exceeds 200 MB.");
await build({
  mode: "static",
  publicDir: "public",
  build: { copyPublicDir: true },
});
const out = resolve("dist/data");
await mkdir(out, { recursive: true });
await writeFile(join(out, "index.json"), JSON.stringify(index));
for (const file of files) {
  const target = join(out, file);
  await mkdir(resolve(target, ".."), { recursive: true });
  await copyFile(join(source, file), target);
}
try {
  const card = JSON.parse(
    await readFile(join(source, "model/model_card.json"), "utf8"),
  );
  const metaPath = join(out, "static/meta.json");
  const meta = JSON.parse(await readFile(metaPath, "utf8"));
  if (card.model_id !== meta.model_id || typeof card.model_name !== "string")
    throw new Error("Model card does not match static metadata.");
  await writeFile(
    metaPath,
    JSON.stringify({ ...meta, model_name: card.model_name }),
  );
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
async function treeSize(path) {
  let bytes = 0;
  for (const item of await readdir(path, { withFileTypes: true }))
    bytes += item.isDirectory()
      ? await treeSize(join(path, item.name))
      : (await stat(join(path, item.name))).size;
  return bytes;
}
console.log(
  `Static data: ${files.length + 1} files; ${index.issues.length} issues; ${await treeSize(out)} bytes`,
);
console.log(`Total dist: ${await treeSize(resolve("dist"))} bytes`);
