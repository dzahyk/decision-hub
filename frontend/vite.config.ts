import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // GitHub Pages serves the static product page from /decision-hub/ (`vite build --mode pages`).
  base: mode === "pages" ? "/decision-hub/" : "/",
  server: {
    port: 5173,
    proxy: { "/v1": "http://127.0.0.1:8010" },
  },
}));
