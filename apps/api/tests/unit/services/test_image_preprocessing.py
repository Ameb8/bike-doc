"""Tests for safe, deterministic diagnostic-image normalization."""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Callable

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from bike_doc_api.services.image_preprocessing import (
    NORMALIZED_IMAGE_MIME_TYPE,
    PREPROCESSING_VERSION,
    ImagePreprocessingError,
    normalize_diagnostic_image,
    normalize_diagnostic_image_async,
)


def _image_bytes(
    image: Image.Image,
    image_format: str,
    **save_kwargs: object,
) -> bytes:
    output = io.BytesIO()
    image.save(output, format=image_format, **save_kwargs)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [
        ("JPEG", "image/jpeg"),
        ("PNG", "image/png"),
        ("WEBP", "image/webp"),
    ],
)
def test_normalizes_each_supported_format_to_deterministic_jpeg(
    image_format: str,
    mime_type: str,
) -> None:
    source = _image_bytes(Image.new("RGB", (80, 40), "steelblue"), image_format)

    first = normalize_diagnostic_image(
        artifact_id="art_diagnostic",
        declared_mime_type=mime_type,
        effective_mime_type=mime_type,
        content=source,
    )
    second = normalize_diagnostic_image(
        artifact_id="art_diagnostic",
        declared_mime_type=mime_type,
        effective_mime_type=mime_type,
        content=source,
    )

    assert first.artifact_id == "art_diagnostic"
    assert first.mime_type == NORMALIZED_IMAGE_MIME_TYPE == "image/jpeg"
    assert first.original_width == 80
    assert first.original_height == 40
    assert first.normalized_width == 80
    assert first.normalized_height == 40
    assert first.preprocessing_version == PREPROCESSING_VERSION
    assert first.content == second.content
    assert first.content_sha256 == hashlib.sha256(first.content).hexdigest()
    with Image.open(io.BytesIO(first.content)) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.mode == "RGB"
        assert normalized.size == (80, 40)
        assert normalized.info.get("progressive") is None


def test_normalizes_orientation_transparency_resize_and_removes_metadata() -> None:
    image = Image.new("RGBA", (5000, 1000), (255, 0, 0, 0))
    image.putpixel((0, 0), (0, 0, 255, 255))
    source = _image_bytes(
        image,
        "PNG",
        pnginfo=_png_info("sensitive source comment"),
    )

    normalized = normalize_diagnostic_image(
        artifact_id="art_transparent",
        declared_mime_type="image/png",
        effective_mime_type="image/png",
        content=source,
    )

    assert (normalized.normalized_width, normalized.normalized_height) == (2048, 410)
    with Image.open(io.BytesIO(normalized.content)) as result:
        assert result.getexif() == {}
        assert result.info.get("comment") is None
        assert result.info.get("icc_profile") is None
        assert result.getpixel((2047, 409))[0] > 240
        assert result.getpixel((2047, 409))[1] > 240
        assert result.getpixel((2047, 409))[2] > 240


def test_composites_paletted_transparency_onto_white() -> None:
    image = Image.new("P", (1, 1), 0)
    image.putpalette([0, 0, 255] + [0, 0, 0] * 255)
    source = _image_bytes(image, "PNG", transparency=0)

    normalized = normalize_diagnostic_image(
        artifact_id="art_paletted_transparent",
        declared_mime_type="image/png",
        effective_mime_type="image/png",
        content=source,
    )

    with Image.open(io.BytesIO(normalized.content)) as result:
        red, green, blue = result.getpixel((0, 0))
        assert red > 240
        assert green > 240
        assert blue > 240


def test_applies_effective_exif_orientation_before_recording_dimensions() -> None:
    image = Image.new("RGB", (40, 20), "white")
    image.putpixel((0, 0), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    source = _image_bytes(image, "JPEG", exif=exif)

    normalized = normalize_diagnostic_image(
        artifact_id="art_oriented",
        declared_mime_type="image/jpeg",
        effective_mime_type="image/jpeg",
        content=source,
    )

    assert (normalized.original_width, normalized.original_height) == (20, 40)
    assert (normalized.normalized_width, normalized.normalized_height) == (20, 40)
    with Image.open(io.BytesIO(normalized.content)) as result:
        assert result.size == (20, 40)
        assert result.getpixel((19, 0))[0] > 80


@pytest.mark.parametrize(
    ("content", "effective_mime_type"),
    [
        (b"not an image", "image/jpeg"),
        (b"\xff\xd8\xff\xe0JFIF", "image/jpeg"),
        (_image_bytes(Image.new("RGB", (1, 1)), "PNG"), "image/jpeg"),
        (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x00\x00\x00\x00\x01\x08\x02\x00\x00\x00",
            "image/png",
        ),
    ],
)
def test_rejects_malformed_truncated_mismatched_and_zero_dimension_images(
    content: bytes,
    effective_mime_type: str,
) -> None:
    with pytest.raises(ImagePreprocessingError) as exc_info:
        normalize_diagnostic_image(
            artifact_id="art_invalid",
            declared_mime_type=effective_mime_type,
            effective_mime_type=effective_mime_type,
            content=content,
        )

    assert exc_info.value.code == "image_decode_failed"
    assert exc_info.value.message == "Image could not be decoded."


def test_rejects_images_above_the_decoded_pixel_limit() -> None:
    source = _image_bytes(Image.new("1", (8000, 5001)), "PNG")

    with pytest.raises(ImagePreprocessingError) as exc_info:
        normalize_diagnostic_image(
            artifact_id="art_oversized",
            declared_mime_type="image/png",
            effective_mime_type="image/png",
            content=source,
        )

    assert exc_info.value.code == "image_decode_failed"


async def test_async_normalization_offloads_work_from_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _image_bytes(Image.new("RGB", (80, 40), "steelblue"), "JPEG")
    original_to_thread = asyncio.to_thread
    calls: list[object] = []

    async def recording_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append(function)
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)

    normalized = await normalize_diagnostic_image_async(
        artifact_id="art_async",
        declared_mime_type="image/jpeg",
        effective_mime_type="image/jpeg",
        content=source,
    )

    assert normalized.artifact_id == "art_async"
    assert calls == [normalize_diagnostic_image]


def _png_info(comment: str) -> PngInfo:
    info = PngInfo()
    info.add_text("Comment", comment)
    return info
