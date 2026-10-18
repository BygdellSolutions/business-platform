import { describe, expect, it, vi } from "vitest";

import { LOGO_MAX_BYTES, fitInside, prepareLogo, type PictureTools } from "@/lib/logo-picture";

const file = (size: number, type: string, name = "logo.png") => new File([new Uint8Array(size)], name, { type });
const blob = (size: number) => new Blob([new Uint8Array(size)]);

/** Fake browser: `bytes` says how many bytes a picture encodes to for each type, width and quality. */
function tools(opts: { picture?: { width: number; height: number } | null; bytes: (type: string, width: number, quality?: number) => number | null }): PictureTools & { calls: [string, number, number | undefined][] } {
  const calls: [string, number, number | undefined][] = [];
  return {
    calls,
    decode: vi.fn(async () => (opts.picture === undefined ? { width: 4000, height: 2000 } : opts.picture)),
    encode: vi.fn(async (_file, size, type, quality) => {
      calls.push([type, size.width, quality]);
      const bytes = opts.bytes(type, size.width, quality);
      return bytes === null ? null : blob(bytes);
    }),
  };
}

describe("fitInside", () => {
  it("scales down to the longest side and never up", () => {
    expect(fitInside({ width: 4000, height: 2000 }, 1000)).toEqual({ width: 1000, height: 500 });
    expect(fitInside({ width: 800, height: 600 }, 1000)).toEqual({ width: 800, height: 600 });
    expect(fitInside({ width: 3000, height: 1 }, 1000)).toEqual({ width: 1000, height: 1 }); // never a zero side
  });
});

describe("prepareLogo", () => {
  it("sends a PNG or JPEG that already fits exactly as it is, without decoding it", async () => {
    const t = tools({ bytes: () => 1 });
    const original = file(LOGO_MAX_BYTES, "image/png");
    expect(await prepareLogo(original, t)).toEqual({ file: original, note: null });
    expect(t.decode).not.toHaveBeenCalled();
  });

  it("makes a big PNG smaller: 1000 pixels wide, still a PNG, and says so", async () => {
    const t = tools({ bytes: (type, width) => (type === "image/png" && width === 1000 ? 400_000 : 9_999_999) });
    const prepared = await prepareLogo(file(5_000_000, "image/png", "photo.PNG"), t);
    if ("problem" in prepared) throw new Error(prepared.problem);
    expect(prepared.file.type).toBe("image/png");
    expect(prepared.file.name).toBe("photo.png");
    expect(prepared.file.size).toBe(400_000);
    expect(prepared.note).toContain("5.0 MB to 400 KB");
    expect(t.calls).toEqual([["image/png", 1000, undefined]]); // the first try fitted
  });

  it("tries smaller sizes of the PNG before giving up its transparency", async () => {
    const t = tools({ bytes: (type, width) => (type === "image/png" && width <= 500 ? 600_000 : 9_999_999) });
    const prepared = await prepareLogo(file(5_000_000, "image/png"), t);
    if ("problem" in prepared) throw new Error(prepared.problem);
    expect(prepared.file.type).toBe("image/png");
    expect(t.calls.map(([type, width]) => `${type}:${width}`)).toEqual(["image/png:1000", "image/png:750", "image/png:500"]);
  });

  it("falls back to a JPEG when no PNG fits, and says the transparency is gone", async () => {
    const t = tools({ bytes: (type, _width, quality) => (type === "image/jpeg" && quality !== undefined && quality <= 0.8 ? 300_000 : 9_999_999) });
    const prepared = await prepareLogo(file(5_000_000, "image/png", "logo.png"), t);
    if ("problem" in prepared) throw new Error(prepared.problem);
    expect(prepared.file.type).toBe("image/jpeg");
    expect(prepared.file.name).toBe("logo.jpg");
    expect(prepared.note).toContain("transparent background is now white");
  });

  it("starts a big JPEG with a JPEG (a photo is no PNG) and does not mention transparency", async () => {
    const t = tools({ bytes: (type, _width, quality) => (type === "image/jpeg" && quality === 0.9 ? 500_000 : 9_999_999) });
    const prepared = await prepareLogo(file(3_000_000, "image/jpeg", "big.jpg"), t);
    if ("problem" in prepared) throw new Error(prepared.problem);
    expect(prepared.file.type).toBe("image/jpeg");
    expect(prepared.note).not.toContain("transparent");
    expect(t.calls[0][0]).toBe("image/jpeg");
  });

  it("converts a picture that is not a PNG or JPEG, even when it is small, and never scales it up", async () => {
    const t = tools({ picture: { width: 300, height: 100 }, bytes: () => 20_000 });
    const prepared = await prepareLogo(file(5_000, "image/webp", "logo.webp"), t);
    if ("problem" in prepared) throw new Error(prepared.problem);
    expect([prepared.file.type, prepared.file.name]).toEqual(["image/png", "logo.png"]);
    expect(t.calls[0]).toEqual(["image/png", 300, undefined]);
  });

  it.each([
    ["something that is not a picture", file(10, "text/plain", "a.txt"), "Choose a picture file"],
    ["an SVG (it is not a raster picture)", file(10, "image/svg+xml", "a.svg"), "Choose a picture file"],
    ["a file over 30 MB", file(31 * 1024 * 1024, "image/png"), "larger than 30 MB"],
  ])("refuses %s without decoding it", async (_name, input, text) => {
    const t = tools({ bytes: () => 1 });
    const prepared = await prepareLogo(input, t);
    expect("problem" in prepared && prepared.problem).toContain(text);
    expect(t.decode).not.toHaveBeenCalled();
  });

  it("says so when the browser cannot read the file as a picture, or nothing makes it small enough", async () => {
    expect(await prepareLogo(file(5_000_000, "image/png"), tools({ picture: null, bytes: () => 1 }))).toEqual({ problem: "This file could not be read as a picture. Choose a PNG or JPEG." });
    const none = await prepareLogo(file(5_000_000, "image/png"), tools({ bytes: () => 9_999_999 }));
    expect("problem" in none && none.problem).toContain("could not be made small enough");
    const broken = await prepareLogo(file(5_000_000, "image/png"), tools({ bytes: () => null }));
    expect("problem" in broken).toBe(true);
  });
});
