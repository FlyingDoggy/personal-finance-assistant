import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { stylexPlugins } from "./vendor/stylex-build.mjs";

export default defineConfig({
  plugins: [
    react({
      babel: {
        plugins: stylexPlugins(process.env.NODE_ENV),
      },
    }),
  ],
  optimizeDeps: { exclude: ["@tuja/ui"] },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist" },
});
