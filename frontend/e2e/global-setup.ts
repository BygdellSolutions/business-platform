import { execFileSync } from "node:child_process";

import { BACKEND_DIR, PYTHON, assertTestDatabase } from "./env";

/** Rebuild and seed the TEST database before the run (the backend guard refuses anything else). */
export default function globalSetup(): void {
  const url = assertTestDatabase();
  execFileSync(PYTHON, ["-m", "app.scripts.reset_test_db", "--seed"], {
    cwd: BACKEND_DIR,
    env: { ...process.env, TEST_DATABASE_URL: url },
    stdio: "inherit",
  });
}
