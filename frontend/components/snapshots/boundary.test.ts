// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { sourceFiles, violationsOf } from "../../test-support/genericity";

/**
 * The read-only snapshot renderer is GENERIC, like the editable custom-field layer, and in
 * addition it must be INERT: it may not fetch anything, so it can only show what it was handed.
 */
const ROOT = path.resolve(__dirname, "..", "..");
const OWN_LAYER = [/^@\/components\/snapshots\//];
const files = sourceFiles(path.join(ROOT, "components/snapshots"));

describe("the generic snapshot renderer", () => {
  it("exists", () => {
    expect(files.map((file) => path.basename(file))).toContain("FieldSnapshots.tsx");
  });

  it.each(files.map((file) => [path.basename(file), file]))("%s imports only generic layers, compares no metadata to literals and names no domain", (_name, file) => {
    expect(violationsOf(readFileSync(file, "utf8"), OWN_LAYER)).toEqual([]);
  });

  it.each(files.map((file) => [path.basename(file), file]))("%s cannot fetch: no client, no router, no custom-field library, no effects", (_name, file) => {
    const source = readFileSync(file, "utf8");
    expect(source).not.toMatch(/apiFetch|fetch\(|backendFetch|serverRead|useRouter|useEffect|useOrgId|@\/lib\/custom-fields|@\/components\/custom-fields/);
  });

  it("does not use the editable renderer or its metadata types", () => {
    for (const file of files) expect(readFileSync(file, "utf8")).not.toMatch(/Definition|CustomFieldsPanel|CustomFieldControl|ValueRead/);
  });
});

describe("the check catches a bad renderer (so it cannot pass silently)", () => {
  it.each([
    'import { apiFetch } from "@/lib/api/client";\nconst x = 1; // customer',
    'if (field.key === "owner") {}',
    'import { CustomFieldsPanel } from "@/components/custom-fields/CustomFieldsPanel";',
  ])("flags %#", (source) => {
    expect(violationsOf(source, OWN_LAYER).length).toBeGreaterThan(0);
  });
});
