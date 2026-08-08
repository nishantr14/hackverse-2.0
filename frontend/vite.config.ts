import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Owner: shared infra / Livana (frontend lane). Phase: Tier 0.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
});
