import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = fileURLToPath(new URL("./", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      // `server-only` throws outside a React Server environment; in unit tests it is a no-op.
      { find: "server-only", replacement: fileURLToPath(new URL("./test-support/server-only.ts", import.meta.url)) },
      { find: /^@\//, replacement: root },
    ],
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "e2e/**"],
  },
});
