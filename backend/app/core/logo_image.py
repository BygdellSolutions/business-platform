"""Validate and normalize an uploaded logo: only a decoded PNG or JPEG is accepted, and what is stored is a fresh PNG.

Re-encoding from the decoded pixels drops every piece of metadata (EXIF, GPS, embedded profiles, trailing bytes) and anything
that is not image data. SVG, GIF and the like are refused rather than converted.
"""

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 700_000  # base64 of this still fits the BFF's 1 MB JSON body
MAX_SOURCE_SIDE = 4000  # decoded pixels per side; also bounds memory (4000 x 4000 x 4 bytes)
MAX_STORED_SIDE = 1000  # a logo is printed a few centimetres wide; larger is scaled down
ALLOWED_FORMATS = {"PNG", "JPEG"}


class InvalidLogo(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class NormalizedLogo:
    content: bytes
    width: int
    height: int


def normalize(raw: bytes) -> NormalizedLogo:
    if not raw:
        raise InvalidLogo("The file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidLogo(f"The file is larger than {MAX_UPLOAD_BYTES // 1000} KB.")
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_SIDE * MAX_SOURCE_SIDE  # Pillow refuses (DecompressionBombError) at twice this
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ALLOWED_FORMATS:
                raise InvalidLogo("The logo must be a PNG or JPEG image.")
            if max(image.size) > MAX_SOURCE_SIDE:
                raise InvalidLogo(f"The image is larger than {MAX_SOURCE_SIDE} x {MAX_SOURCE_SIDE} pixels.")
            image.load()
            has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
            picture = image.convert("RGBA" if has_alpha else "RGB")
    except InvalidLogo:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise InvalidLogo("The file is not a readable PNG or JPEG image.") from None
    picture.thumbnail((MAX_STORED_SIDE, MAX_STORED_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return NormalizedLogo(out.getvalue(), picture.width, picture.height)
