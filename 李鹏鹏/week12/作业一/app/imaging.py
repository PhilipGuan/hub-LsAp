"""Image validation and normalisation before sending a photo to the model."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

# Guard against decompression bombs while still accepting normal phone photos.
Image.MAX_IMAGE_PIXELS = 48_000_000

try:  # Optional: makes iPhone HEIC/HEIF uploads work when installed.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - depends on optional native package
    pass


class ImageValidationError(ValueError):
    """Raised when the uploaded bytes are not a usable image."""


def prepare_image(data: bytes, max_side: int = 1600) -> bytes:
    """Validate, auto-orient, downscale and re-encode an upload as JPEG.

    Re-encoding has two benefits: metadata (including GPS data) is stripped,
    and oversized phone photos stay within a sensible vision-token budget.
    """
    if not data:
        raise ImageValidationError("上传的图片为空。")

    try:
        with Image.open(BytesIO(data)) as source:
            source.seek(0)  # first frame of animated images
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                image = canvas
            else:
                image = image.convert("RGB")

            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            output = BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue()
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
    ) as exc:
        raise ImageValidationError(
            "无法读取该图片，请上传 JPG、PNG、WebP 或 HEIC 格式的照片。"
        ) from exc
