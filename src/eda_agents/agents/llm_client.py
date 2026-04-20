"""Provider-agnostic chat-completion helper via LiteLLM.

Wraps :func:`litellm.completion` so callers can target any LiteLLM-routed
backend (OpenRouter, Anthropic, Gemini, Z.AI, Ollama, …) by prefixing the
model id with the provider name::

    from eda_agents.agents.llm_client import call_llm

    content, total_tokens = call_llm(
        model="openrouter/google/gemini-3-flash-preview",
        system_prompt="You are a ...",
        user_prompt="Describe a ...",
        max_tokens=1024,
        temperature=0.0,
    )

Same ``(content, total_tokens)`` contract as
:func:`eda_agents.agents.openrouter_client.call_openrouter`; every failure
funnels into :class:`RuntimeError` so call sites keep one branch.

The env var required by each backend follows LiteLLM's convention
(``OPENROUTER_API_KEY`` for ``openrouter/*``, ``ZAI_API_KEY`` for
``zai/*``, ``ANTHROPIC_API_KEY`` for ``anthropic/*``, …). Use
:func:`validate_model_env` to probe up front without burning a call.
"""

from __future__ import annotations


def validate_model_env(model: str) -> dict:
    """Return ``{"env_ok": bool, "missing_keys": list[str]}`` for ``model``.

    Thin wrapper over :func:`litellm.validate_environment` that normalises
    the output to a stable shape so callers do not depend on LiteLLM's
    internal dict keys.
    """
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover — litellm is in [adk] extra
        raise RuntimeError(f"litellm not available: {exc}") from exc

    info = litellm.validate_environment(model=model)
    missing = list(info.get("missing_keys", []) or [])
    return {
        "env_ok": bool(info.get("keys_in_environment", False)) and not missing,
        "missing_keys": missing,
    }


def call_llm(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> tuple[str, int]:
    """Single chat-completion call via LiteLLM.

    Parameters
    ----------
    model:
        LiteLLM-routed model id (e.g. ``openrouter/google/gemini-3-flash-
        preview``, ``zai/glm-4.6``, ``anthropic/claude-haiku-4-5``,
        ``gemini/gemini-2.5-flash``). LiteLLM chooses the provider and
        required env var from the prefix.
    system_prompt:
        Rendered system message (e.g. a registered skill's ``render()``
        output).
    user_prompt:
        The user message body.
    max_tokens:
        Cap on response tokens (defaults to 1024).
    temperature:
        Sampling temperature (defaults to 0.0 for deterministic
        classification-style tasks).

    Returns
    -------
    tuple[str, int]
        ``(content, total_tokens)``. ``total_tokens`` is 0 when the
        upstream response omits ``usage``.

    Raises
    ------
    RuntimeError
        On ANY failure — missing env var, import failure, upstream HTTP
        error, empty response. Callers that want to skip gracefully
        should catch ``RuntimeError``.
    """
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover — litellm is in [adk] extra
        raise RuntimeError(f"litellm not available: {exc}") from exc

    env = validate_model_env(model)
    if not env["env_ok"]:
        missing = ", ".join(env["missing_keys"]) or "?"
        raise RuntimeError(
            f"missing env var(s) for model {model!r}: {missing}"
        )

    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:  # noqa: BLE001 — funnel to RuntimeError
        raise RuntimeError(
            f"LiteLLM call failed (model={model!r}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"LiteLLM response shape unexpected (model={model!r}): {exc}"
        ) from exc
    if not content:
        raise RuntimeError(
            f"LiteLLM returned empty content for model {model!r}"
        )

    total_tokens = 0
    usage = getattr(resp, "usage", None)
    if usage is not None:
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    return content, total_tokens


__all__ = ["call_llm", "validate_model_env"]
