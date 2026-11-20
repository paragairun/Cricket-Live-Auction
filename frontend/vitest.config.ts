/**
 * Vitest configuration.
 *
 * New in 2.0.0 (2026-11-20): the frontend had no test framework at all, despite
 * the README documenting `npm test`.
 */
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts", "store/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
