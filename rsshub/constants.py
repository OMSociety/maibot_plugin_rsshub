"""插件级常量。"""

from __future__ import annotations

from typing import Final

PLUGIN_ID: Final = "omsociety.rsshub"

# 推送模式：persona=人格化播报 / direct=模板直推 / silent=注入上下文并请麦麦想一轮
PUSH_MODES: Final = ("persona", "direct", "silent")
DEFAULT_PUSH_MODE: Final = "persona"

# 内容处理器（handler）：ai_filter 判是否推送，ai_transform 改写正文
HANDLERS: Final = ("ai_filter", "ai_transform")
DEFAULT_HANDLERS: Final = ("ai_filter",)

# 文本裁剪默认值
MAX_TITLE_CHARS: Final = 200
MAX_SUMMARY_CHARS: Final = 800
