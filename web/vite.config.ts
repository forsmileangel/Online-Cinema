import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:6970",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // React and the router change only on upgrades: their own file stays cached across releases.
    rollupOptions: { output: { manualChunks: { vendor: ["react", "react-dom", "react-router-dom"] } } },
    chunkSizeWarningLimit: 600,
  },
});
