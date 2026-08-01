"""文件上传连接器 —— MVP 内置的静态连接器。

文档由用户经 API 直接上传，不走 discover/fetch；此连接器主要提供元数据与配置校验，
并作为「采集层抽象」的第一个落地实现，为后续动态连接器立好接口范式。
"""

from __future__ import annotations

from sag_api.connectors.base import Connector, ConnectorMeta
from sag_api.enums import ConnectorKind


class FileUploadConnector(Connector):
    meta = ConnectorMeta(
        kind=ConnectorKind.FILE_UPLOAD,
        title="文件上传",
        description="上传本地文档（Markdown / 文本 / PDF 等），由引擎解析、分块、向量化并抽取事件与实体。",
        supports_sync=False,
        config_fields=[],
    )
