import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { expect, test } from "./fixtures";

import { ROOT_DIR } from "./env";
import { FREDRIK, ORG_A, createCustomer, psql, signIn } from "./support";

/**
 * Proof that end-to-end execution cannot touch the development database: data written through
 * the real stack lands in the test database and never appears in the development one.
 * (The backend started by Playwright is given ONLY the test database URL, and the reset
 * script refuses any database that is not clearly a test database.)
 */

test("data written through the running stack goes to the test database, never the dev database", async ({ context }) => {
  const marker = `E2E isolation canary ${randomUUID()}`;
  await signIn(context, FREDRIK);

  await createCustomer(context, ORG_A.id, marker);

  expect(psql("postgres-test", `select count(*) from customers where name = '${marker}'`)).toBe("1");
  expect(psql("postgres-test", "select current_database()")).toMatch(/_test$/);
  if (process.env.CI) {
    // A CI runner has no development database at all (the guard in .github/scripts refuses any other): there is nothing to compare
    // with, so assert that none is running, which is the strongest form of "the run cannot touch it".
    expect(execFileSync("docker", ["compose", "ps", "-q", "postgres"], { cwd: ROOT_DIR, encoding: "utf8" }).trim()).toBe("");
    return;
  }
  expect(psql("postgres", `select count(*) from customers where name = '${marker}'`)).toBe("0");
  // and the two are different servers, not two databases of one
  expect(psql("postgres", "select current_database()")).not.toMatch(/_test$/);
});

test("the test database was rebuilt and seeded for this run", async () => {
  expect(psql("postgres-test", "select count(*) from organizations where id in ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b2')")).toBe("2");
  expect(psql("postgres-test", "select version_num from alembic_version")).toMatch(/^[0-9a-f]{12}$/);
});
