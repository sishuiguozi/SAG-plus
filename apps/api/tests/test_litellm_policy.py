import asyncio

import pytest

from sag_api.core.config import Settings
from sag_api.core.litellm_policy import apply_litellm_completion_policy

NAMED_SEARCH = {
    "type": "function",
    "function": {"name": "search_context"},
}


def test_extract_scope_disables_qwen_reasoning_without_mutating_chat_requests():
    from sag_api.core.llm_call_context import llm_call_scope

    settings = Settings(_env_file=None, llm_api_key="test-key", llm_model="qwen3.6-flash")
    base_request = {"model": settings.routed_llm_model, "messages": []}

    chat_request = apply_litellm_completion_policy(settings, base_request)
    assert "reasoning_effort" not in chat_request
    assert "extra_body" not in chat_request

    with llm_call_scope("extract"):
        extract_request = apply_litellm_completion_policy(settings, base_request)

    assert extract_request["reasoning_effort"] == "none"
    assert extract_request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert extract_request["allowed_openai_params"] == ["reasoning_effort"]


def test_rerank_scope_disables_opencode_style_thinking():
    from sag_api.core.llm_call_context import llm_call_scope

    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_base_url="https://opencode.ai/zen/go",
        llm_model="gpt-5-mini",
    )

    with llm_call_scope("rerank"):
        request = apply_litellm_completion_policy(settings, {"messages": []})

    assert request["reasoning_effort"] == "none"
    assert request["extra_body"]["thinking"] == {"type": "disabled"}


def test_extract_scope_overrides_user_thinking_opt_in():
    from sag_api.core.llm_call_context import llm_call_scope

    settings = Settings(_env_file=None, llm_api_key="test-key", llm_model="qwen3.6-flash")

    with llm_call_scope("extract"):
        request = apply_litellm_completion_policy(
            settings,
            {"messages": [], "extra_body": {"enable_thinking": True}},
        )

    assert request["extra_body"].get("enable_thinking") is None
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


async def _read_scope_after_yield(scope: str | None = None) -> str | None:
    from sag_api.core.llm_call_context import current_llm_call_scenario, llm_call_scope

    if scope is None:
        await asyncio.sleep(0)
        return current_llm_call_scenario()
    with llm_call_scope(scope):
        await asyncio.sleep(0)
        return current_llm_call_scenario()


def test_call_scope_is_restored_and_isolated_between_async_tasks():
    from sag_api.core.llm_call_context import current_llm_call_scenario, llm_call_scope

    async def run() -> tuple[str | None, str | None, str | None]:
        chat_task = asyncio.create_task(_read_scope_after_yield())
        with llm_call_scope("extract"):
            extract = await _read_scope_after_yield("extract")
        chat = await chat_task
        return extract, chat, current_llm_call_scenario()

    assert asyncio.run(run()) == ("extract", None, None)


def test_reranker_configuration_defaults_to_off_and_accepts_api_source():
    settings = Settings(
        _env_file=None,
        search_rerank_mode="api",
        search_rerank_candidates=12,
        search_rerank_api_url="https://dashscope-intl.aliyuncs.com/compatible-api/v1/reranks",
        search_rerank_api_key="secret",
        search_rerank_api_model="qwen3-rerank",
    )

    assert Settings(_env_file=None).search_rerank_mode == "off"
    assert settings.search_rerank_candidates == 12
    assert settings.search_rerank_api_model == "qwen3-rerank"


def test_local_reranker_save_allows_an_empty_unused_api_model() -> None:
    from sag_api.schemas.system import ModelConfigUpdate

    patch = ModelConfigUpdate(
        search_rerank_mode="local",
        search_rerank_api_model="",
    )

    assert patch.search_rerank_api_model == ""


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("tool_choice", [NAMED_SEARCH, "required"])
def test_forced_no_thinking_preserves_forced_choice_and_disables_reasoning(
    stream: bool,
    tool_choice: object,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_base_url="https://opencode.ai/zen/go/v1",
        llm_model="deepseek-v4-flash",
        llm_tool_choice_strategy="forced_no_thinking",
    )
    original = {"messages": [], "tool_choice": tool_choice, "stream": stream}

    request = apply_litellm_completion_policy(settings, original)

    assert request["tool_choice"] == tool_choice
    assert request["reasoning_effort"] == "none"
    assert request["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in original
    assert "extra_body" not in original


@pytest.mark.parametrize("tool_choice", ["none", "auto"])
def test_forced_no_thinking_leaves_non_forced_chat_reasoning_unchanged(tool_choice: str) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_tool_choice_strategy="forced_no_thinking",
    )

    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": tool_choice},
    )

    assert request["tool_choice"] == tool_choice
    assert "reasoning_effort" not in request


def test_forced_with_thinking_preserves_named_choice_and_reasoning() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_tool_choice_strategy="forced_with_thinking",
    )

    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": NAMED_SEARCH},
    )

    assert request["tool_choice"] == NAMED_SEARCH
    assert "reasoning_effort" not in request


@pytest.mark.parametrize("tool_choice", [NAMED_SEARCH, "required"])
def test_auto_strategy_rewrites_only_forced_choices(tool_choice: object) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_tool_choice_strategy="auto",
    )

    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": tool_choice},
    )

    assert request["tool_choice"] == "auto"
    assert "reasoning_effort" not in request


@pytest.mark.parametrize("tool_choice", [NAMED_SEARCH, "required", "auto", "none"])
def test_all_no_thinking_disables_every_chat_request(tool_choice: object) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="qwen3.6-flash",
        llm_tool_choice_strategy="all_no_thinking",
    )

    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": tool_choice},
    )

    assert request["tool_choice"] == tool_choice
    assert request["reasoning_effort"] == "none"
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_extract_scope_still_disables_reasoning_under_auto_strategy() -> None:
    from sag_api.core.llm_call_context import llm_call_scope

    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="qwen3.6-flash",
        llm_tool_choice_strategy="auto",
    )

    with llm_call_scope("extract"):
        request = apply_litellm_completion_policy(
            settings,
            {"messages": [], "tool_choice": "required"},
        )

    assert request["tool_choice"] == "auto"
    assert request["reasoning_effort"] == "none"
