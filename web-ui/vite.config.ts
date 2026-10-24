import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const API_TARGET = "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  base: "/static/library/",
  build: {
    outDir: fileURLToPath(new URL("../src/refcollector/web/static/library", import.meta.url)),
    emptyOutDir: true,
    // Strict CSP (script-src 'self'): never inline assets or emit inline scripts.
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true, // the server checks Host exactly
        configure: (proxy) => {
          // ...and Origin on mutations, so present the target's own origin.
          proxy.on("proxyReq", (req) => {
            if (req.getHeader("origin")) req.setHeader("origin", API_TARGET);
          });
        },
      },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
