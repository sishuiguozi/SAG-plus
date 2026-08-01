import asyncio

from sag_api.core.config import Settings
from sag_api.core.litellm_policy import apply_litellm_completion_policy


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
