import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A date that means "when it happened" is shown as a DAY: the time of day is noise on a service, a visit, a payment, a document
 * (reported more than once). Only the audit trail and the security records (sign-ins, invitations and access changes) keep the time. This lists every file that formats a time of
 * day, so a new screen cannot start showing one by accident: add it here only if its time really matters.
 */
const SHOW_THE_TIME = new Set([
  "components/history/RecordHistory.tsx", // the audit trail
  "features/account/SessionsPanel.tsx", // sign-ins: security
  "features/members/InvitationHistory.tsx", // who was invited, who accepted, who revoked: access control, security
]);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" || name === ".next" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path.replaceAll("\\", "/")] : [];
  });
}

describe("times of day on screen", () => {
  it("are shown only where the time matters", () => {
    const files = ["components", "features", "app"].flatMap(sources).filter((file) => /\bformatTimestamp\b/.test(readFileSync(file, "utf8")));
    expect(files.filter((file) => !SHOW_THE_TIME.has(file))).toEqual([]);
  });

  it("does not use a date-and-time input where a day is meant", () => {
    const files = ["components", "features", "app"].flatMap(sources).filter((file) => /type="datetime-local"/.test(readFileSync(file, "utf8")));
    expect(files).toEqual([]);
  });
});
