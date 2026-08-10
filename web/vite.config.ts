import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// Build to a single self-contained index.html (JS + CSS inlined) so the
// output is CSP-safe and can be served by the stdlib API or dropped on
// GitHub Pages with no external requests — the same guarantee the vanilla
// console had, now from a real React + TypeScript source tree.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "dist",
    target: "es2020",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    chunkSizeWarningLimit: 4000,
  },
});
