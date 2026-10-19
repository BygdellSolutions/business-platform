/**
 * Getting a chosen picture ready to be a logo, in the browser, before it is sent.
 *
 * The server takes a PNG or JPEG of at most 700 KB and scales it to fit 1000 pixels. A picture straight from a camera, a screenshot or a
 * design tool is often several megabytes, and sending the person away to shrink it themselves is a chore, so a bigger one is made smaller
 * HERE: scaled down to fit 1000 pixels (a logo is printed a few centimetres wide), and re-saved as a PNG (which keeps transparency) at
 * smaller and smaller sizes until it fits; only if even that does not fit is it saved as a JPEG (a white background replaces any
 * transparency, and the person is told). A picture that already fits is sent exactly as it is. The server still checks everything.
 */

export const LOGO_MAX_BYTES = 700_000;
const MAX_SIDE = 1000;
const MAX_SOURCE_BYTES = 30 * 1024 * 1024; // a bigger file is not decoded at all: it would take a lot of the browser's memory
const SCALES = [1, 0.75, 0.5, 0.35];
const JPEG_QUALITIES = [0.9, 0.8, 0.7, 0.6, 0.5];

export interface Picture {
  width: number;
  height: number;
}

/** What the browser does, kept apart so the plan below can be tested without a canvas. */
export interface PictureTools {
  /** The picture's size, or null when the browser cannot read it as a picture. */
  decode(file: File): Promise<Picture | null>;
  /** The picture drawn at `size` and saved as `type` (a JPEG gets a white background), or null when that fails. */
  encode(file: File, size: Picture, type: "image/png" | "image/jpeg", quality?: number): Promise<Blob | null>;
}

export type PreparedLogo = { file: File; note: string | null } | { problem: string };

/** `picture` scaled down, never up, so that neither side is longer than `max`. */
export function fitInside(picture: Picture, max: number): Picture {
  const scale = Math.min(1, max / Math.max(picture.width, picture.height));
  return { width: Math.max(1, Math.round(picture.width * scale)), height: Math.max(1, Math.round(picture.height * scale)) };
}

function sizeText(bytes: number): string {
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

function renamed(name: string, extension: "png" | "jpg"): string {
  const base = name.replace(/\.[^./\\]+$/, "") || "logo";
  return `${base}.${extension}`;
}

export const browserTools: PictureTools = {
  async decode(file) {
    try {
      const bitmap = await createImageBitmap(file);
      const picture = { width: bitmap.width, height: bitmap.height };
      bitmap.close();
      return picture;
    } catch {
      return null;
    }
  },
  async encode(file, size, type, quality) {
    try {
      const bitmap = await createImageBitmap(file);
      const canvas = document.createElement("canvas");
      canvas.width = size.width;
      canvas.height = size.height;
      const context = canvas.getContext("2d");
      if (context === null) return null;
      if (type === "image/jpeg") {
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, size.width, size.height);
      }
      context.drawImage(bitmap, 0, 0, size.width, size.height);
      bitmap.close();
      return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));
    } catch {
      return null;
    }
  },
};

export async function prepareLogo(file: File, tools: PictureTools = browserTools): Promise<PreparedLogo> {
  const isAllowed = file.type === "image/png" || file.type === "image/jpeg";
  if (isAllowed && file.size <= LOGO_MAX_BYTES) return { file, note: null }; // fits already: sent as it is
  if (!file.type.startsWith("image/") || file.type === "image/svg+xml") return { problem: "Choose a picture file, such as a PNG or JPEG." };
  if (file.size > MAX_SOURCE_BYTES) return { problem: "The file is larger than 30 MB. Choose a smaller picture." };

  const picture = await tools.decode(file);
  if (picture === null) return { problem: "This file could not be read as a picture. Choose a PNG or JPEG." };
  const base = fitInside(picture, MAX_SIDE);
  const scaled = (scale: number): Picture => ({ width: Math.max(1, Math.round(base.width * scale)), height: Math.max(1, Math.round(base.height * scale)) });
  const done = (blob: Blob, type: "image/png" | "image/jpeg", extra = ""): PreparedLogo => ({
    file: new File([blob], renamed(file.name, type === "image/png" ? "png" : "jpg"), { type }),
    note: `Your picture was made smaller to fit (${sizeText(file.size)} to ${sizeText(blob.size)}).${extra}`,
  });

  // A PNG keeps transparency, so it comes first, unless the picture is a photo-like JPEG to begin with (then JPEG is the better format).
  const pngFirst = file.type !== "image/jpeg";
  if (pngFirst) {
    for (const scale of SCALES) {
      const blob = await tools.encode(file, scaled(scale), "image/png");
      if (blob !== null && blob.size <= LOGO_MAX_BYTES) return done(blob, "image/png");
    }
  }
  for (const scale of SCALES) {
    for (const quality of JPEG_QUALITIES) {
      const blob = await tools.encode(file, scaled(scale), "image/jpeg", quality);
      if (blob !== null && blob.size <= LOGO_MAX_BYTES) return done(blob, "image/jpeg", pngFirst ? " It was saved as a JPEG, so any transparent background is now white." : "");
    }
  }
  return { problem: "This picture could not be made small enough. Try a simpler or smaller picture." };
}
