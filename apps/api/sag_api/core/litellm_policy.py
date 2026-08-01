"""Muse-wide LiteLLM request policy.

Generation calls can apply this policy directly.  zleap-sag calls LiteLLM
inside the dependency, so the application lifespan also installs the same
policy as a LiteLLM pre-call hook.  This keeps provider quirks in Muse without
patching ``site-packages``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sag_api.core.config import Settings
from sag_api.core.llm_call_context import current_llm_call_scenario

_COMPLETION_CALL_TYPES = {"completion", "acompletion"}


def _thinking_override(extra_body: object) -> bool | None:
    if not isinstance(extra_body, Mapping):
        return None
    direct = extra_body.get("enable_thinking")
    if isinstance(direct, bool):
        return direct
    template_kwargs = extra_body.get("chat_template_kwargs")
    if isinstance(template_kwargs, Mapping):
        nested = template_kwargs.get("enable_thinking")
        if isinstance(nested, bool):
            return nested
    return None


def _is_forced_tool_choice(value: object) -> bool:
    if value == "required":
        return True
    if not isinstance(value, Mapping) or value.get("type") != "function":
        return False
    function = value.get("function")
    return (
        isinstance(function, Mapping)
        and isinstance(function.get("name"), str)
        and bool(function["name"].strip())
    )


def _is_openai_route(model: str, settings: Settings) -> bool:
    if "/" in model:
        return model.split("/", 1)[0].casefold() == "openai"
    return settings.llm_provider == "openai"


def _routing_text(model: str, settings: Settings) -> str:
    return " ".join((model, settings.llm_provider, settings.llm_base_url or "")).casefold()


def _is_opencode_style_route(model: str, settings: Settings) -> bool:
    routing = _routing_text(model, settings)
    return any(marker in routing for marker in ("deepseek", "opencode", "/zen/"))


def _is_qwen_template_route(model: str, settings: Settings) -> bool:
    routing = _routing_text(model, settings)
    return any(marker in routing for marker in ("qwen", "vllm", "sglang"))


def _merge_extra_body(request: dict[str, Any], patch: Mapping[str, Any]) -> None:
    existing = request.get("extra_body")
    merged = dict(existing) if isinstance(existing, Mapping) else {}
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    request["extra_body"] = merged


def _apply_scoped_reasoning_disable(
    normalized: dict[str, Any],
    model: str,
    settings: Settings,
) -> None:
    """Apply only the provider fields accepted by the active endpoint family."""
    normalized["reasoning_effort"] = "none"
    if _is_opencode_style_route(model, settings):
        _merge_extra_body(normalized, {"thinking": {"type": "disabled"}})
    elif _is_qwen_template_route(model, settings):
        existing = normalized.get("extra_body")
        if isinstance(existing, Mapping):
            clean = dict(existing)
            clean.pop("enable_thinking", None)
            normalized["extra_body"] = clean
        _merge_extra_body(normalized, {"chat_template_kwargs": {"enable_thinking": False}})


def _with_allowed_openai_param(request: dict[str, Any], name: str) -> None:
    configured = request.get("allowed_openai_params")
    if configured is None:
        allowed: list[str] = []
    elif isinstance(configured, str):
        allowed = [configured]
    else:
        allowed = list(configured)
    if name not in allowed:
        allowed.append(name)
    request["allowed_openai_params"] = allowed


def apply_litellm_completion_policy(
    settings: Settings,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one normalized LiteLLM completion request.

    User-configured request fields are preserved unless the configured tool
    strategy applies to this call. Entity/event extraction and legacy LLM
    reranking always keep their scoped reasoning disable override;
    ``allowed_openai_params`` supports custom compatible endpoint model names
    whose capabilities LiteLLM cannot infer.
    """

    normalized = dict(request)
    if "extra_body" not in normalized and settings.llm_extra_body:
        normalized["extra_body"] = dict(settings.llm_extra_body)

    model = str(normalized.get("model") or settings.routed_llm_model)
    thinking = _thinking_override(normalized.get("extra_body"))
    tool_choice = normalized.get("tool_choice")
    forced_tool_choice = _is_forced_tool_choice(tool_choice)
    strategy = settings.llm_tool_choice_strategy

    if strategy == "auto" and forced_tool_choice:
        normalized["tool_choice"] = "auto"

    disable_reasoning = (
        current_llm_call_scenario() is not None
        or strategy == "all_no_thinking"
        or (strategy == "forced_no_thinking" and forced_tool_choice)
    )
    if disable_reasoning:
        _apply_scoped_reasoning_disable(normalized, model, settings)
    elif "reasoning_effort" not in normalized and thinking is False:
        normalized["reasoning_effort"] = "none"

    if "reasoning_effort" in normalized and _is_openai_route(model, settings):
        _with_allowed_openai_param(normalized, "reasoning_effort")
    return normalized


def install_litellm_policy(settings: Settings) -> Any:
    """Install the Muse policy for dependency-owned LiteLLM calls."""

    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    class MuseLiteLLMPolicy(CustomLogger):
        async def async_pre_call_deployment_hook(
            self,
            kwargs: dict[str, Any],
            call_type: Any,
        ) -> dict[str, Any]:
            kind = getattr(call_type, "value", call_type)
            if kind is not None and kind not in _COMPLETION_CALL_TYPES:
                return kwargs
            return apply_litellm_completion_policy(settings, kwargs)

    callback = MuseLiteLLMPolicy()
    litellm.callbacks.append(callback)
    return callback


def uninstall_litellm_policy(callback: Any) -> None:
    """Remove a policy installed by :func:`install_litellm_policy`."""

    import litellm

    if callback in litellm.callbacks:
        litellm.callbacks.remove(callback)
