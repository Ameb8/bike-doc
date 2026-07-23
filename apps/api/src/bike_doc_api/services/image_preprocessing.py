"""Safe, deterministic normalization for diagnostic-image model input."""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass
from typing import Final, Literal

from PIL import Image, ImageCms, ImageOps

SUPPORTED_IMAGE_MIME_TYPES: Final = frozenset(
    {"image/jpeg", "image/png", "image/webp"},
)
NORMALIZED_IMAGE_MIME_TYPE: Final = "image/jpeg"
PREPROCESSING_VERSION: Final = "diagnostic-image-jpeg-v1"
MAX_DECODED_PIXELS: Final = 40_000_000
MAX_NORMALIZED_LONG_EDGE: Final = 2048
JPEG_QUALITY: Final = 85
JPEG_SUBSAMPLING: Final = "4:2:0"

_PIL_FORMAT_MIME_TYPES: Final = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class NormalizedDiagnosticImage:
    """Ephemeral model-input bytes and their deterministic provenance."""

    artifact_id: str
    content: bytes
    mime_type: Literal["image/jpeg"]
    original_width: int
    original_height: int
    normalized_width: int
    normalized_height: int
    content_sha256: str
    preprocessing_version: str


class ImagePreprocessingError(ValueError):
    """A bounded, app-owned failure for an untrusted image artifact."""

    def __init__(
        self,
        code: Literal["image_decode_failed", "image_normalization_failed"],
    ) -> None:
        self.code = code
        self.message = (
            "Image could not be decoded."
            if code == "image_decode_failed"
            else "Image could not be normalized."
        )
        super().__init__(self.message)


def normalize_diagnostic_image(
    *,
    artifact_id: str,
    declared_mime_type: str | None,
    effective_mime_type: str,
    content: bytes,
) -> NormalizedDiagnosticImage:
    """Return safe model-input bytes or a bounded preprocessing failure.

    This synchronous interface is for worker threads. Async callers must use
    :func:`normalize_diagnostic_image_async` so decoding and resizing cannot
    block the event loop.
    """

    del declared_mime_type  # Retained for provenance at the app-owned seam.
    if effective_mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise ImagePreprocessingError("image_decode_failed")

    try:
        image = _decode_and_validate(content, effective_mime_type)
    except ImagePreprocessingError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImagePreprocessingError("image_decode_failed") from exc

    normalized: Image.Image | None = None
    try:
        original_width, original_height = image.size
        normalized = _normalize_pixels(image)
        normalized_width, normalized_height = normalized.size
        output = io.BytesIO()
        normalized.save(
            output,
            format="JPEG",
            quality=JPEG_QUALITY,
            subsampling=JPEG_SUBSAMPLING,
            optimize=False,
            progressive=False,
        )
        normalized_bytes = output.getvalue()
    except (OSError, ValueError, ImageCms.PyCMSError) as exc:
        raise ImagePreprocessingError("image_normalization_failed") from exc
    finally:
        image.close()
        if normalized is not None:
            normalized.close()

    return NormalizedDiagnosticImage(
        artifact_id=artifact_id,
        content=normalized_bytes,
        mime_type=NORMALIZED_IMAGE_MIME_TYPE,
        original_width=original_width,
        original_height=original_height,
        normalized_width=normalized_width,
        normalized_height=normalized_height,
        content_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        preprocessing_version=PREPROCESSING_VERSION,
    )


async def normalize_diagnostic_image_async(
    *,
    artifact_id: str,
    declared_mime_type: str | None,
    effective_mime_type: str,
    content: bytes,
) -> NormalizedDiagnosticImage:
    """Normalize an image in a worker thread for async application callers."""

    return await asyncio.to_thread(
        normalize_diagnostic_image,
        artifact_id=artifact_id,
        declared_mime_type=declared_mime_type,
        effective_mime_type=effective_mime_type,
        content=content,
    )


def _decode_and_validate(content: bytes, effective_mime_type: str) -> Image.Image:
    """Fully decode one supported image after inexpensive safety checks."""

    with Image.open(io.BytesIO(content)) as opened:
        decoded_mime_type = _PIL_FORMAT_MIME_TYPES.get(opened.format or "")
        if decoded_mime_type != effective_mime_type:
            raise ImagePreprocessingError("image_decode_failed")
        width, height = opened.size
        if width <= 0 or height <= 0 or width * height > MAX_DECODED_PIXELS:
            raise ImagePreprocessingError("image_decode_failed")
        opened.verify()

    decoded = Image.open(io.BytesIO(content))
    if _PIL_FORMAT_MIME_TYPES.get(decoded.format or "") != effective_mime_type:
        decoded.close()
        raise ImagePreprocessingError("image_decode_failed")
    decoded.load()
    oriented = ImageOps.exif_transpose(decoded)
    if oriented is not decoded:
        decoded.close()
    return oriented


def _normalize_pixels(image: Image.Image) -> Image.Image:
    """Convert oriented pixels to bounded 8-bit sRGB on a white background."""

    rgb = _convert_to_srgb(image)
    alpha = _transparency_channel(image)
    if alpha is not None:
        background = Image.new("RGB", image.size, "white")
        try:
            background.paste(rgb, mask=alpha)
        finally:
            alpha.close()
            rgb.close()
        rgb = background

    if max(rgb.size) > MAX_NORMALIZED_LONG_EDGE:
        rgb.thumbnail(
            (MAX_NORMALIZED_LONG_EDGE, MAX_NORMALIZED_LONG_EDGE),
            Image.Resampling.LANCZOS,
        )
    return rgb


def _transparency_channel(image: Image.Image) -> Image.Image | None:
    """Return alpha for direct or palette transparency without retaining metadata."""

    if "A" not in image.getbands() and "transparency" not in image.info:
        return None
    rgba = image.convert("RGBA")
    try:
        return rgba.getchannel("A")
    finally:
        rgba.close()


def _convert_to_srgb(image: Image.Image) -> Image.Image:
    """Convert image pixels to 8-bit sRGB, honoring a source ICC profile."""

    rgb = image.convert("RGB")
    icc_profile = image.info.get("icc_profile")
    if not isinstance(icc_profile, bytes):
        return rgb

    source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
    srgb_profile = ImageCms.createProfile("sRGB")
    converted = ImageCms.profileToProfile(
        rgb,
        source_profile,
        srgb_profile,
        outputMode="RGB",
    )
    rgb.close()
    if converted is None:
        raise ValueError("ICC conversion did not produce an image.")
    return converted
