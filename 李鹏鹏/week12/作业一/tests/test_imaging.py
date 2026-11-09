from io import BytesIO

import pytest
from PIL import Image

from app.imaging import ImageValidationError, prepare_image


def make_image(size=(3000, 2000), mode="RGBA") -> bytes:
    image = Image.new(mode, size, (180, 80, 40, 128) if mode == "RGBA" else (40, 80, 120))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_image_downscales_and_reencodes():
    output = prepare_image(make_image(), max_side=1600)
    with Image.open(BytesIO(output)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert max(image.size) == 1600


def test_prepare_image_keeps_small_images():
    output = prepare_image(make_image((320, 240), mode="RGB"), max_side=1600)
    with Image.open(BytesIO(output)) as image:
        assert image.size == (320, 240)


def test_prepare_image_rejects_invalid_bytes():
    with pytest.raises(ImageValidationError):
        prepare_image(b"this is not an image")


def test_prepare_image_rejects_empty_payload():
    with pytest.raises(ImageValidationError):
        prepare_image(b"")
