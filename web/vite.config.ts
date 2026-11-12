import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Production serves the built files from nginx, which proxies /api to the API
// service (web/nginx.conf). The dev server proxy below applies the same rule so
// `npm run dev` talks to the API on the host.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
