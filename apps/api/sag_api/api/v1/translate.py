"""文档选中文本翻译端点：走现有 LLMClient，不持久化、不落库。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from sag_api.core.deps import get_current_user
from sag_api.core.errors import ApiError, ConfigurationError, UpstreamError
from sag_api.core.logging import get_logger
from sag_api.db.models import User
from sag_api.schemas.translate import TranslateRequest, TranslateResponse

router = APIRouter(prefix="/translate", tags=["translate"])
log = get_logger("translate")

_TARGET_LABEL = {"zh": "中文", "en": "English"}
_SYSTEM_PROMPT = (
    "你是一名专业翻译。把用户提供的内容翻译成{target}。"
    "只输出译文本身，不要任何解释、标注、引号或前后缀；"
    "代码、专有名词（如 AFSIM、MinerU、API 名）保持原文；"
    "保留原文的 Markdown/换行结构。"
)


@router.post("", response_model=TranslateResponse)
async def translate(
    body: TranslateRequest,
    request: Request,
    _user: User = Depends(get_current_user),
) -> TranslateResponse:
    """把选中的一段文本翻译为目标语言（界面语言）。"""
    llm = request.app.state.llm
    if not llm.configured:
        raise ConfigurationError("尚未配置 LLM，无法使用翻译")

    target = _TARGET_LABEL.get(body.target_lang, "中文")
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT.format(target=target)},
        {"role": "user", "content": body.text},
    ]
    try:
        translated = (await llm.complete(messages)).strip()
    except UpstreamError as error:
        raise ApiError(error.message) from error
    except Exception as error:  # noqa: BLE001
        log.warning("翻译失败：%s", error)
        raise ApiError("翻译失败，请稍后重试") from error
    if not translated:
        raise UpstreamError("模型未返回译文，请重试")

    # 去掉模型偶尔带回的多余引号包裹
    if len(translated) >= 2 and translated[0] in "\"'“”" and translated[-1] in "\"'“”":
        translated = translated[1:-1].strip()
    return TranslateResponse(translated=translated)
