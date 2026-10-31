import { readdir, readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";

const target = process.argv.includes("--static") ? 28562 + 15 * 1024 : 120000;
let total = 0;
for (const file of await readdir(new URL("../dist/assets/", import.meta.url))) {
  if (file.endsWith(".js")) {
    const bytes = gzipSync(
      await readFile(new URL(`../dist/assets/${file}`, import.meta.url)),
    ).length;
    total += bytes;
    console.log(`${file}: ${bytes} bytes gzip`);
  }
}
console.log(
  `Total JavaScript: ${total} bytes gzip (${(total / 1024).toFixed(2)} KiB); target < ${target} bytes`,
);
if (total >= target) process.exitCode = 1;
