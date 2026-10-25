import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Settings are WEB_* env variables (web-framework decision). Vite compiles them
// into the bundle, so none of them may ever hold a secret.
export default defineConfig({
  plugins: [react()],
  envPrefix: "WEB_",
  server: {
    // The fake state source, the fixtures and the allergen list are read from ../contracts.
    fs: { allow: [".."] },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test_setup.ts"],
    env: {
      WEB_ORDERS_URL: "http://orders.test",
      WEB_VISION_URL: "http://vision.test",
      WEB_SUMMARY_URL: "http://summary.test",
      // The entity view, so tests see the entity boxes; the fast view's test turns it on itself.
      WEB_FAST_BOXES: "false",
    },
  },
});
