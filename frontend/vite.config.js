import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend calls the backend directly at http://localhost:8000 (see
// src/api/client.js); the backend's CORSMiddleware (app/main.py) allows the
// Vite dev origin, so no dev-server proxy is needed.
export default defineConfig({
  plugins: [react()],
});
