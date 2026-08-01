"""选中文本翻译：把文档预览中选中的片段交给 LLM 翻译。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    target_lang: Literal["zh", "en"] = "zh"

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("翻译内容不能为空")
        return value


class TranslateResponse(BaseModel):
    translated: str
