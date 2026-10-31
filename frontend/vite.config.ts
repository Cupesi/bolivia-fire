import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => ({
  base: mode === "static" ? process.env.PAGES_BASE || "/bolivia-fire/" : "/",
  plugins: [svelte(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
  build: { target: "es2022" },
}));
