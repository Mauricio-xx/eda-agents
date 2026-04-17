"""Thin OpenRouter chat-completion helper shared across adapters and MCP tools.

Wraps the OpenAI-compatible API at ``https://openrouter.ai/api/v1`` so
call sites stay simple::

    from eda_agents.agents.openrouter_client import call_openrouter

    content, total_tokens = call_openrouter(
        model="google/gemini-2.5-flash",
        system_prompt="You are a ...",
        user_prompt="Describe a ...",
        max_tokens=1024,
        temperature=0.0,
    )

Every failure mode (missing API key, network error, bad response) is
funnelled into ``RuntimeError`` so callers have one branch to handle.
Uses the ``OPENROUTER_API_KEY`` env var; no logging of the key.
"""

from __future__ import annotations

import os


def call_openrouter(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> tuple[str, int]:
    """Single chat-completion call through OpenRouter.

    Returns ``(content, total_tokens)`` where ``total_tokens`` is 0 when
    the upstream response omits ``usage``.

    Raises :class:`RuntimeError` on ANY failure (missing API key,
    ``openai`` not installed, HTTP / network error, empty response).
    Callers that want to skip gracefully should catch ``RuntimeError``
    and translate to their local "skip / infra-failure" signal.

    Parameters
    ----------
    model:
        OpenRouter model id. The leading ``openrouter/`` prefix is
        stripped if present (OpenRouter accepts both, but the direct
        API call wants the bare id).
    system_prompt:
        Rendered system message (e.g. a registered skill's ``render()``
        output).
    user_prompt:
        The user message body.
    max_tokens:
        Cap on response tokens (defaults to 1024).
    temperature:
        Sampling temperature (defaults to 0.0 for deterministic
        classification-style tasks; callers doing creative generation
        should raise it).
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover — dep lives in base install
        raise RuntimeError(f"openai not available: {exc}") from exc

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    model_id = (
        model.removeprefix("openrouter/")
        if model.startswith("openrouter/")
        else model
    )

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        total_tokens = 0
        usage = getattr(resp, "usage", None)
        if usage is not None:
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        content = resp.choices[0].message.content or ""
        if not content:
            raise RuntimeError(
                f"OpenRouter returned empty content for model {model_id!r}"
            )
        return content, total_tokens
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 — funnel to RuntimeError
        raise RuntimeError(
            f"OpenRouter call failed (model={model_id!r}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc


__all__ = ["call_openrouter"]
