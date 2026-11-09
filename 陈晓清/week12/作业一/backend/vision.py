"""图片校验、压缩、转 base64 data URL"""
import base64
import io

from PIL import Image, UnidentifiedImageError

from .config import MAX_IMAGE_MB, MAX_IMAGE_SIDE

# PIL 识别出的真实格式白名单（不信任文件名后缀）
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF"}


class ImageError(Exception):
    """图片来源不合法"""


def to_data_url(raw: bytes) -> str:
    """把原始字节流转成 data:image/jpeg;base64,..."""
    if not raw:
        raise ImageError("图片为空")

    max_bytes = MAX_IMAGE_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise ImageError(f"图片超过 {MAX_IMAGE_MB:g} MB，请压缩后重试")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except UnidentifiedImageError:
        raise ImageError("图片格式不支持，请上传 jpg / png / webp")
    except Exception as exc:  # 损坏文件等
        raise ImageError(f"图片无法解析：{exc}")

    if img.format not in ALLOWED_FORMATS:
        raise ImageError(f"不支持的图片格式：{img.format}")

    # 手机照片可能是 RGBA / 带 EXIF 旋转，统一转 RGB 并摆正
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
