"""Tests for the provider-agnostic LiteLLM chat-completion helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from eda_agents.agents.llm_client import call_llm, validate_model_env


def _mock_completion_response(content: str, total_tokens: int = 42) -> MagicMock:
    """Minimal OpenAI-compatible response mock."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock(message=msg)

    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.total_tokens = total_tokens
    resp.usage = usage
    return resp


class TestValidateModelEnv:
    def test_returns_normalised_shape(self):
        with patch(
            "litellm.validate_environment",
            return_value={"keys_in_environment": True, "missing_keys": []},
        ):
            info = validate_model_env("openrouter/google/gemini-3-flash-preview")
        assert info == {"env_ok": True, "missing_keys": []}

    def test_missing_keys_sets_env_ok_false(self):
        with patch(
            "litellm.validate_environment",
            return_value={"keys_in_environment": False, "missing_keys": ["ZAI_API_KEY"]},
        ):
            info = validate_model_env("zai/glm-4.6")
        assert info == {"env_ok": False, "missing_keys": ["ZAI_API_KEY"]}

    def test_missing_keys_defeats_env_ok_even_if_flag_true(self):
        # LiteLLM sometimes reports keys_in_environment=True alongside a
        # non-empty missing_keys list (e.g. optional base-url). Treat any
        # missing key as env_ok=False so callers do not trust the flag alone.
        with patch(
            "litellm.validate_environment",
            return_value={"keys_in_environment": True, "missing_keys": ["FOO"]},
        ):
            info = validate_model_env("custom/model")
        assert info["env_ok"] is False
        assert info["missing_keys"] == ["FOO"]


class TestCallLLM:
    def test_success_returns_content_and_tokens(self):
        resp = _mock_completion_response("hello", total_tokens=123)
        with (
            patch(
                "litellm.validate_environment",
                return_value={"keys_in_environment": True, "missing_keys": []},
            ),
            patch("litellm.completion", return_value=resp) as mock_completion,
        ):
            content, tokens = call_llm(
                model="openrouter/google/gemini-3-flash-preview",
                system_prompt="sys",
                user_prompt="usr",
            )
        assert content == "hello"
        assert tokens == 123
        # Confirm messages passed through correctly.
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "openrouter/google/gemini-3-flash-preview"
        assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
        assert kwargs["messages"][1] == {"role": "user", "content": "usr"}
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 1024

    def test_missing_env_raises(self):
        with patch(
            "litellm.validate_environment",
            return_value={"keys_in_environment": False, "missing_keys": ["ZAI_API_KEY"]},
        ):
            with pytest.raises(RuntimeError, match="ZAI_API_KEY"):
                call_llm(
                    model="zai/glm-4.6", system_prompt="s", user_prompt="u"
                )

    def test_completion_exception_funnels_to_runtimeerror(self):
        with (
            patch(
                "litellm.validate_environment",
                return_value={"keys_in_environment": True, "missing_keys": []},
            ),
            patch(
                "litellm.completion",
                side_effect=ValueError("upstream 500"),
            ),
        ):
            with pytest.raises(RuntimeError, match="LiteLLM call failed"):
                call_llm(
                    model="openrouter/x/y",
                    system_prompt="s",
                    user_prompt="u",
                )

    def test_empty_content_raises(self):
        resp = _mock_completion_response("")
        with (
            patch(
                "litellm.validate_environment",
                return_value={"keys_in_environment": True, "missing_keys": []},
            ),
            patch("litellm.completion", return_value=resp),
        ):
            with pytest.raises(RuntimeError, match="empty content"):
                call_llm(model="openrouter/x/y", system_prompt="s", user_prompt="u")

    def test_missing_usage_returns_zero_tokens(self):
        resp = _mock_completion_response("ok", total_tokens=0)
        resp.usage = None
        with (
            patch(
                "litellm.validate_environment",
                return_value={"keys_in_environment": True, "missing_keys": []},
            ),
            patch("litellm.completion", return_value=resp),
        ):
            content, tokens = call_llm(
                model="openrouter/x/y", system_prompt="s", user_prompt="u"
            )
        assert content == "ok"
        assert tokens == 0

    def test_custom_temperature_and_max_tokens_propagate(self):
        resp = _mock_completion_response("ok")
        with (
            patch(
                "litellm.validate_environment",
                return_value={"keys_in_environment": True, "missing_keys": []},
            ),
            patch("litellm.completion", return_value=resp) as mock_completion,
        ):
            call_llm(
                model="anthropic/claude-haiku-4-5",
                system_prompt="s",
                user_prompt="u",
                max_tokens=2048,
                temperature=0.7,
            )
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == 2048
        assert kwargs["temperature"] == 0.7
