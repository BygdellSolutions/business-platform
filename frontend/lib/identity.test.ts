// @vitest-environment node
import { describe, expect, it } from "vitest";

import { parseEmail } from "@/lib/identity";

describe("parseEmail", () => {
  it("normalizes case and surrounding whitespace, including a trailing line break", () => {
    expect(parseEmail("  Fredrik@Dev.Test ")).toBe("fredrik@dev.test");
    expect(parseEmail("a@c.test\r\n")).toBe("a@c.test"); // trimmed clean; an EMBEDDED newline is rejected below
  });

  it.each(["", "   ", "plain", "a@b", "a b@c.test", "@c.test", "a@@c.test", "a@c.test\nX-Org: 1", `${"x".repeat(70)}@c.test`, `a@${"x".repeat(300)}.test`, undefined, null])("rejects %j", (value) => {
    expect(parseEmail(value as string)).toBeNull();
  });
});
