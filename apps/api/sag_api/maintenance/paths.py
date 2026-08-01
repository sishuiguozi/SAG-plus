"""维护/运维脚本的路径默认值解析：去除硬编码绝对路径。

数据根目录优先读 ``SAG_DATA_ROOT`` 环境变量；未设置时回退到
``~/.sag/.data``（平台无关）。各维护脚本的 --data-dir / --uri /
--metadata-db 等默认值统一由本模块派生，命令行显式参数可覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path


def default_data_root() -> Path:
    """返回数据根目录：SAG_DATA_ROOT 优先，否则 ~/.sag/.data。"""
    env = os.environ.get("SAG_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sag" / ".data"
