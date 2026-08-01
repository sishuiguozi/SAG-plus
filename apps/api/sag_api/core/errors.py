"""sag 领域异常 —— 与框架无关，路由层统一映射为 HTTP 响应。

领域服务只抛这些异常；`sag/` 适配层负责把 `zleap-sag` 的 `SagError` 家族翻译到这里。
"""

from __future__ import annotations


class ApiError(Exception):
    """所有 sag 领域异常的基类。"""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str | None = None, *, code: str | None = None):
        self.message = message or self.__class__.__doc__ or "Internal error"
        if code:
            self.code = code
        super().__init__(self.message)


class NotFoundError(ApiError):
    """请求的资源不存在。"""

    status_code = 404
    code = "not_found"


class ConflictError(ApiError):
    """资源冲突（如重复创建）。"""

    status_code = 409
    code = "conflict"


class ValidationError(ApiError):
    """输入校验失败。"""

    status_code = 422
    code = "validation_error"


class AuthError(ApiError):
    """未认证或凭证无效。"""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(ApiError):
    """无权访问该资源。"""

    status_code = 403
    code = "forbidden"


class ConfigurationError(ApiError):
    """缺少必要配置（如未配置 LLM）。"""

    status_code = 400
    code = "configuration_error"


class UpstreamError(ApiError):
    """上游（LLM / 引擎）返回错误。"""

    status_code = 502
    code = "upstream_error"


class ServiceUnavailableError(ApiError):
    """暂时不可用（可重试，如限流 / 超时）。"""

    status_code = 503
    code = "service_unavailable"
