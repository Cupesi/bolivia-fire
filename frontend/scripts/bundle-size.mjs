import { readdir, readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";

const target = (process.argv.includes("--static") ? 36266 : 34589) + 3 * 1024;
let total = 0;
let main = 0;
const html = await readFile(
  new URL("../dist/index.html", import.meta.url),
  "utf8",
);
const initial = new Set(
  Array.from(
    html.matchAll(/(?:src|href)="[^"]*\/assets\/([^"]+\.js)"/g),
    (match) => match[1],
  ),
);
for (const file of await readdir(new URL("../dist/assets/", import.meta.url))) {
  if (file.endsWith(".js")) {
    const bytes = gzipSync(
      await readFile(new URL(`../dist/assets/${file}`, import.meta.url)),
    ).length;
    total += bytes;
    if (initial.has(file)) main += bytes;
    console.log(`${file}: ${bytes} bytes gzip`);
  }
}
console.log(
  `Total JavaScript: ${total} bytes gzip (${(total / 1024).toFixed(2)} KiB); initial JavaScript: ${main} bytes; target < ${target} bytes`,
);
if (!main || main >= target) process.exitCode = 1;
