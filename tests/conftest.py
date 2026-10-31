"""测试夹具：离线导入插件与 SDK，不依赖宿主进程。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# 允许通过 MAIBOT_SDK_PATH 指定 SDK 解压目录（双版本验证时切换）
_sdk_path = os.environ.get("MAIBOT_SDK_PATH")
if _sdk_path:
    sys.path.insert(0, _sdk_path)

sys.path.insert(0, str(REPO_ROOT.parent))
sys.path.insert(0, str(REPO_ROOT))

from tests.support import StubCtx, StubLogger, make_config  # noqa: E402

__all__ = ["StubCtx", "StubLogger", "make_config"]


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def ctx(tmp_path: Path) -> StubCtx:
    return StubCtx(tmp_path)
