"""Async-safe markers for the small set of LLM calls that disable reasoning."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

LLMCallScenario = Literal["extract", "rerank"]

_llm_call_scenario: ContextVar[LLMCallScenario | None] = ContextVar(
    "llm_call_scenario",
    default=None,
)


def current_llm_call_scenario() -> LLMCallScenario | None:
    """Return the marker for this task only, never a process-global value."""
    return _llm_call_scenario.get()


@contextmanager
def llm_call_scope(scenario: LLMCallScenario) -> Iterator[None]:
    """Mark one LLM await boundary and restore any enclosing context afterwards."""
    token = _llm_call_scenario.set(scenario)
    try:
        yield
    finally:
        _llm_call_scenario.reset(token)
