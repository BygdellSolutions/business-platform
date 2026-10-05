// @vitest-environment node
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import nextConfig from "../next.config";

/**
 * Static guards for the "one image, runtime configuration" invariant of the production container: nothing that
 * depends on the authentication mode or the environment may be baked into the build, no font service is contacted,
 * and the browser bundle carries no backend address. (The image tests prove the same from the built output.)
 */
const ROOT = path.resolve(__dirname, "..");
const read = (file: string) => readFileSync(path.join(ROOT, file), "utf8");

function files(directory: string, pattern: RegExp): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(path.join(ROOT, directory))) {
    const relative = `${directory}/${entry}`;
    if (statSync(path.join(ROOT, relative)).isDirectory()) out.push(...files(relative, pattern));
    else if (pattern.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) out.push(relative);
  }
  return out;
}

describe("pages that depend on runtime configuration are never prerendered at build time", () => {
  const CONFIG = /\b(authMode|authConfig|appEnv|devIdentityEnabled|cookiePolicy)\(|process\.env/;
  const REQUEST_BOUND = /force-dynamic|requireCredential|getCredential|serverRead/;

  it.each(files("app", /^page\.tsx$/).map((file) => [file]))("%s", (file) => {
    const source = read(file);
    if (CONFIG.test(source)) expect(source, `${file} reads configuration but is not request-bound`).toMatch(REQUEST_BOUND);
  });

  it("the pages whose behaviour IS the authentication mode declare force-dynamic explicitly", () => {
    for (const file of ["app/setup/page.tsx", "app/dev-login/page.tsx", "app/login/page.tsx", "app/invite/page.tsx"]) {
      expect(read(file), file).toMatch(/export const dynamic = "force-dynamic"/);
    }
  });

  it("no route declares force-static or a revalidate period", () => {
    for (const file of [...files("app", /\.(ts|tsx)$/)]) expect(read(file), file).not.toMatch(/force-static|export const revalidate/);
  });
});

describe("a production build is self-contained and reproducible", () => {
  it("emits a standalone server and no X-Powered-By header", () => {
    expect(nextConfig.output).toBe("standalone");
    expect(nextConfig.poweredByHeader).toBe(false);
  });

  it("contacts no font service: the sans font is bundled and nothing imports next/font/google", () => {
    for (const file of [...files("app", /\.(ts|tsx|css)$/), ...files("components", /\.(ts|tsx)$/), ...files("features", /\.(ts|tsx)$/), ...files("lib", /\.(ts|tsx)$/)]) {
      expect(read(file), file).not.toMatch(/next\/font\/google|fonts\.googleapis|fonts\.gstatic/);
    }
    expect(read("app/layout.tsx")).toMatch(/next\/font\/local/);
  });

  it("the bundled UI fonts are byte-identical to the pinned fonts of the PDF renderer, and carry their license", () => {
    const sums = readFileSync(path.join(ROOT, "..", "backend/app/modules/invoicing/pdf/fonts/SHA256SUMS"), "utf8");
    for (const name of ["NotoSans-Regular.ttf", "NotoSans-Bold.ttf"]) {
      const digest = createHash("sha256").update(readFileSync(path.join(ROOT, "app/fonts", name))).digest("hex");
      expect(sums).toContain(`${digest} *${name}`);
    }
    expect(read("app/fonts/OFL-notosans.txt")).toMatch(/SIL OPEN FONT LICENSE/i);
  });

  it("no public (browser-visible) environment variable exists, and only lib/backend.ts reads BACKEND_URL", () => {
    const all = [...files("app", /\.(ts|tsx)$/), ...files("components", /\.(ts|tsx)$/), ...files("features", /\.(ts|tsx)$/), ...files("lib", /\.(ts|tsx)$/)];
    for (const file of all) {
      const source = read(file);
      expect(source, file).not.toMatch(/NEXT_PUBLIC_/);
      if (file !== "lib/backend.ts") expect(source, file).not.toMatch(/BACKEND_URL|localhost:8000/);
    }
  });
});
