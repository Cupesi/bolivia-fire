import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";

const require = createRequire(resolve(process.cwd(), "package.json"));
const { chromium } = require("@playwright/test");
const mermaid = resolve(dirname(require.resolve("mermaid/package.json")), "dist/mermaid.min.js");
let input = "";
for await (const chunk of process.stdin) input += chunk;
const spec = JSON.parse(input);
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: spec.width, height: spec.height } });
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
    * { box-sizing: border-box; }
    body { margin: 0; padding: 24px; background: white; color: #172c35;
           font: 16px Arial, sans-serif; }
    h1 { margin: 0 0 10px; font-size: 22px; font-weight: 600; text-align: center; }
    main { display: flex; height: ${spec.height - 116}px; align-items: center; gap: 22px; }
    #diagram { flex: 1; min-width: 0; height: 100%; }
    #diagram > svg { width: 100% !important; height: 100% !important; max-width: none !important; }
    footer { font-size: 14px; padding-top: 8px; text-align: center; }
    aside { flex: 0 0 250px; align-self: stretch; display: flex; flex-direction: column; }
    aside h2 { font-size: 17px; margin: 0; }
    aside p { font-size: 14px; line-height: 1.4; margin: 8px 0; }
    aside svg { width: 100%; min-height: 0; flex: 1; }
    .held-out { fill: #eeeeee; stroke: #777; stroke-width: .025; }
    .complete .held-out { fill: #e69f00; }
    #diagram .tick text { font-size: 16px !important; }
  </style></head><body><h1></h1><main><div id="diagram"></div>${spec.extra}</main>
  <footer></footer></body></html>`);
  await page.evaluate((title) => { document.querySelector("h1").textContent = title; }, spec.title);
  await page.addScriptTag({ content: await readFile(mermaid, "utf8") });
  await page.evaluate(() => window.mermaid.initialize({
    startOnLoad: false, securityLevel: "strict", theme: "base",
    themeVariables: { fontFamily: "Arial, sans-serif", fontSize: "18px",
      primaryColor: "#F3F6F8", primaryTextColor: "#172C35", primaryBorderColor: "#59717E",
      lineColor: "#59717E", clusterBkg: "#FAFBFC", clusterBorder: "#9BAEB8" },
    flowchart: { curve: "linear", padding: 16, nodeSpacing: 24, rankSpacing: 44,
      htmlLabels: true },
    gantt: { barHeight: 26, barGap: 14, topPadding: 35, leftPadding: 90,
      rightPadding: 20, fontSize: 16, sectionFontSize: 16, useMaxWidth: false },
  }));
  for (let step = 0; step <= spec.stages.length; step++) {
    await page.evaluate(async ({ spec, step }) => {
      const source = spec.source;
      if (step === 0) {
        const { svg } = await window.mermaid.render("stage0", source);
        document.querySelector("#diagram").innerHTML = svg;
        document.querySelector("#diagram > svg").setAttribute("preserveAspectRatio", "xMidYMid meet");
      }
      if (!source.startsWith("gantt")) {
        spec.stages.forEach((id, index) => {
          const node = document.querySelector(`#diagram [id*="flowchart-${id}-"]`);
          const shape = node.querySelector("rect");
          shape.style.setProperty("fill", index < step ? "#79BCD9" : "#F3F6F8", "important");
          shape.style.setProperty("stroke", "#0072B2", "important");
        });
      }
      if (source.startsWith("gantt")) {
        const colors = ["#0072B2", "#E69F00", "#009E73", "#CC79A7"];
        spec.stages.slice(0, 4).forEach((id, index) => {
          const rectangle = document.querySelector(`#diagram #stage0-${id}`);
          rectangle.style.fill = index < step ? colors[index] : "#E9EFF2";
        });
        document.querySelectorAll('[id*="-purge"]').forEach((item) => {
          if (item.tagName.toLowerCase() === "rect") item.style.fill = step === 5 ? "#333" : "#EEE";
        });
      }
      document.body.classList.toggle("complete", step === spec.stages.length);
      document.querySelector("footer").textContent = source.startsWith("gantt")
        ? "Seven-day targets crossing partition boundaries are purged; geographic blocks remain excluded."
        : spec.stages.length === 9
          ? "Selection and calibration use distinct partitions; later final-test openings are logged."
          : "Information availability distinguishes retrospective analysis, simulation and operational use.";
      await document.fonts.ready;
    }, { spec, step });
    await page.screenshot({ path: resolve(spec.output, `frame-${String(step).padStart(2, "0")}.png`) });
  }
} finally {
  await browser.close();
}
