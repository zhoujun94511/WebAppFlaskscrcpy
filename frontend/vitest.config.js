// Vitest config — kept separate from ``vite.config.js`` so the
// production build doesn't pull in test deps. Vitest picks this up
// automatically because it shares Vite's config resolution.
//
// Test discovery:
//   tests/**/*.test.{js,mjs,ts}         — co-located unit tests
//   src/**/__tests__/*.test.{js,mjs,ts} — component-scoped tests
//
// Default env is ``node`` — keep deps minimal (no jsdom unless needed).
// For tests that need DOM APIs (window / localStorage / RTC*), use the
// ``*.dom.test.js`` suffix and install jsdom: ``npm i -D jsdom`` then
// remove the ``passWithNoTests`` workaround for that subset.

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    environmentMatchGlobs: [
      // Reserved suffix for any future test that needs a real DOM. The
      // matcher applies only when jsdom is also installed; tests using
      // this suffix should declare ``jsdom`` as a devDep next to them.
      ["**/*.dom.test.{js,mjs,ts}", "jsdom"],
    ],
    include: [
      "tests/**/*.test.{js,mjs,ts}",
      "src/**/__tests__/**/*.test.{js,mjs,ts}",
    ],
    // Keep CI green even when there are zero test files (the migration
    // ports tests in batches). Without this Vitest exits 1 on an empty
    // suite which would break ``npm test`` in CI.
    passWithNoTests: true,
  },
});
