"""把 zleap-sag 的 `SagError` 家族翻译为 sag 领域异常。"""

from __future__ import annotations

from contextlib import contextmanager

from zleap.sag.exceptions import (
    ConfigError,
    InvalidInputError,
    NonRetryableError,
    ResourceNotFoundError,
    RetryableError,
    SagError,
)

from sag_api.core.errors import (
    ConfigurationError,
    NotFoundError,
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)


@contextmanager
def map_sag_errors():
    """在此上下文内发生的 SagError 会被翻译成对应的 ApiError。"""
    try:
        yield
    except ConfigError as e:
        raise ConfigurationError(str(e)) from e
    except ResourceNotFoundError as e:
        raise NotFoundError(str(e)) from e
    except InvalidInputError as e:
        raise ValidationError(str(e)) from e
    except RetryableError as e:
        # 限流 / 超时 / 上游暂不可用 —— 可重试
        raise ServiceUnavailableError(str(e)) from e
    except NonRetryableError as e:
        raise ValidationError(str(e)) from e
    except SagError as e:
        raise UpstreamError(str(e)) from e
