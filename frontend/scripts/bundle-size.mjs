import { readdir, readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";

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
  `Total JavaScript: ${total} bytes gzip (${(total / 1024).toFixed(2)} KiB); target < 120 KB`,
);
if (total >= 120000) process.exitCode = 1;
