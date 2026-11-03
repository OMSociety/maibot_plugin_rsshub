"""把图片压进上下文注入的预算。

注入的图留在聊天历史里，既占插件与宿主之间的帧体（单帧 16 MiB、base64 膨胀约 1/3），
也持续消耗视觉 token。超过预算的图先整幅缩放再逐档降质量，压不下去就放弃这一张
（调用方计入 ``skipped``，推送本身继续）。
"""

from __future__ import annotations

import io
from typing import Any

# 注入路径的单张图上限：1 MiB 原图约 1.3 MiB base64，三张也远在单帧上限内。
CONTEXT_IMAGE_BYTES = 1024 * 1024
_MAX_SIDE = 1600
_MIN_SIDE = 320
_QUALITY_STEPS = (85, 78, 70, 62)


def compress_image(
    payload: bytes,
    *,
    max_bytes: int = CONTEXT_IMAGE_BYTES,
    max_side: int = _MAX_SIDE,
) -> bytes | None:
    """把图片压到 ``max_bytes`` 以内；原图已在预算内则原样返回，压不下去返回 ``None``。"""
    if not payload:
        return None
    if len(payload) <= max_bytes:
        return payload

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(payload)) as source:
            image = ImageOps.exif_transpose(source) or source
            image = _flatten_alpha(image)
            original_side = min(image.size)
            for side in (max_side, max_side // 2, max_side // 4, _MIN_SIDE):
                if side < _MIN_SIDE:
                    break
                if side >= original_side:
                    candidate = image
                else:
                    candidate = image.copy()
                    candidate.thumbnail((side, side), Image.LANCZOS)
                encoded = _encode_within(candidate, max_bytes)
                if encoded is not None:
                    return encoded
    except (OSError, ValueError):
        # 损坏或非图片数据：交给调用方按跳过处理
        return None
    return None


def can_decode(payload: bytes) -> bool:
    """判断这段字节能不能被 Pillow 解码，用来区分「压不小」与「根本不是图」。"""
    try:
        from PIL import Image
    except ImportError:
        return True
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
    except (OSError, ValueError):
        return False
    return True


def _flatten_alpha(image: Any) -> Any:
    """带透明通道的图铺白底再转 RGB，避免透明区域压成黑色。"""
    from PIL import Image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if image.mode not in ("RGB", "L"):
        return image.convert("RGB")
    return image


def _encode_within(image: Any, max_bytes: int) -> bytes | None:
    for quality in _QUALITY_STEPS:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data
    return None
