"""Tests for LiteLLMAgentHarness and OpenCodeHarness."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eda_agents.agents.claude_code_harness import HarnessResult
from eda_agents.agents.litellm_harness import LiteLLMAgentHarness, _safe_resolve
from eda_agents.agents.opencode_harness import OpenCodeHarness


# ---------------------------------------------------------------------------
# _safe_resolve
# ---------------------------------------------------------------------------


class TestSafeResolve:
    def test_valid_relative(self, tmp_path):
        resolved = _safe_resolve(tmp_path, "foo/bar.v")
        assert resolved == tmp_path / "foo" / "bar.v"

    def test_work_dir_itself(self, tmp_path):
        resolved = _safe_resolve(tmp_path, ".")
        assert resolved == tmp_path.resolve()

    def test_traversal_raises(self, tmp_path):
        with pytest.raises(ValueError, match="escapes work_dir"):
            _safe_resolve(tmp_path, "../outside.txt")

    def test_absolute_outside_raises(self, tmp_path):
        with pytest.raises(ValueError, match="escapes work_dir"):
            _safe_resolve(tmp_path, "/etc/passwd")

    def test_symlink_escape_raises(self, tmp_path):
        # Construct a path that looks safe but resolves outside
        evil = "foo/../../outside"
        with pytest.raises(ValueError, match="escapes work_dir"):
            _safe_resolve(tmp_path, evil)


# ---------------------------------------------------------------------------
# Filesystem tool unit tests
# ---------------------------------------------------------------------------


class TestFilesystemTools:
    def setup_method(self, method):
        pass

    def _harness(self, tmp_path) -> LiteLLMAgentHarness:
        return LiteLLMAgentHarness(
            prompt="test",
            work_dir=tmp_path,
            model="openrouter/test/model",
        )

    def test_read_existing_file(self, tmp_path):
        (tmp_path / "hello.v").write_text("module hello(); endmodule")
        h = self._harness(tmp_path)
        assert h._read_file("hello.v") == "module hello(); endmodule"

    def test_read_missing_file(self, tmp_path):
        h = self._harness(tmp_path)
        assert h._read_file("missing.v").startswith("ERROR:")

    def test_read_escape_blocked(self, tmp_path):
        h = self._harness(tmp_path)
        result = h._read_file("../../etc/passwd")
        assert result.startswith("ERROR:")

    def test_write_creates_file(self, tmp_path):
        h = self._harness(tmp_path)
        out = h._write_file("new.v", "module new(); endmodule")
        assert out["ok"] is True
        assert (tmp_path / "new.v").read_text() == "module new(); endmodule"

    def test_write_escape_blocked(self, tmp_path):
        h = self._harness(tmp_path)
        out = h._write_file("../outside.v", "bad")
        assert out["ok"] is False

    def test_list_dir(self, tmp_path):
        (tmp_path / "a.v").write_text("")
        (tmp_path / "b.v").write_text("")
        h = self._harness(tmp_path)
        entries = h._list_dir(".")
        assert "a.v" in entries
        assert "b.v" in entries

    def test_run_bash_gated(self, tmp_path):
        # Gate is enforced in _dispatch, not _run_bash directly
        h = self._harness(tmp_path)  # allow_bash=False by default
        result = h._dispatch("run_bash", {"cmd": "echo hello"})
        assert "not permitted" in result

    def test_run_bash_allowed(self, tmp_path):
        h = LiteLLMAgentHarness(
            prompt="test",
            work_dir=tmp_path,
            model="openrouter/test/model",
            allow_bash=True,
        )
        result = h._run_bash("echo hello_world")
        assert "hello_world" in result

    def test_dispatch_unknown_tool(self, tmp_path):
        h = self._harness(tmp_path)
        result = h._dispatch("nonexistent_tool", {})
        assert "unknown tool" in result


# ---------------------------------------------------------------------------
# Agent loop with mocked LiteLLM
# ---------------------------------------------------------------------------


def _make_completion_response(content: str, tool_calls=None):
    """Build a minimal mock completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []

    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    resp.usage = MagicMock()
    resp.usage.model_dump.return_value = {"prompt_tokens": 10, "completion_tokens": 5}
    return resp


def _make_tool_call(name: str, args: dict, call_id: str = "call_001"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_success(self, tmp_path):
        final_resp = _make_completion_response("All done.")

        with (
            patch("litellm.acompletion", new=AsyncMock(return_value=final_resp)),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            harness = LiteLLMAgentHarness(
                prompt="Do something", work_dir=tmp_path, model="openrouter/test/m"
            )
            result = await harness.run()

        assert result.success is True
        assert result.result_text == "All done."
        assert result.num_turns == 1

    @pytest.mark.asyncio
    async def test_tool_call_read_file(self, tmp_path):
        (tmp_path / "design.v").write_text("module top(); endmodule")

        tc = _make_tool_call("read_file", {"path": "design.v"})
        tool_resp = _make_completion_response(None, tool_calls=[tc])
        final_resp = _make_completion_response("Read successfully.")

        completions = [tool_resp, final_resp]
        with (
            patch(
                "litellm.acompletion",
                new=AsyncMock(side_effect=completions),
            ),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            harness = LiteLLMAgentHarness(
                prompt="Read the file", work_dir=tmp_path, model="openrouter/test/m"
            )
            result = await harness.run()

        assert result.success is True
        assert result.num_turns == 2

    @pytest.mark.asyncio
    async def test_budget_exceeded(self, tmp_path):
        resp = _make_completion_response(None, tool_calls=[_make_tool_call("list_dir", {})])

        with (
            patch("litellm.acompletion", new=AsyncMock(return_value=resp)),
            patch("litellm.completion_cost", return_value=5.0),
        ):
            harness = LiteLLMAgentHarness(
                prompt="loop",
                work_dir=tmp_path,
                model="openrouter/test/m",
                max_budget_usd=1.0,
            )
            result = await harness.run()

        assert result.success is False
        assert "Budget exceeded" in (result.error or "")

    @pytest.mark.asyncio
    async def test_litellm_exception_returns_failure(self, tmp_path):
        with patch(
            "litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("api error")),
        ):
            harness = LiteLLMAgentHarness(
                prompt="test", work_dir=tmp_path, model="openrouter/test/m"
            )
            result = await harness.run()

        assert result.success is False
        assert "api error" in (result.error or "")

    @pytest.mark.asyncio
    async def test_tool_specs_excludes_bash_by_default(self, tmp_path):
        harness = LiteLLMAgentHarness(
            prompt="test", work_dir=tmp_path, model="openrouter/test/m"
        )
        names = [t["function"]["name"] for t in harness._tool_specs()]
        assert "run_bash" not in names

    @pytest.mark.asyncio
    async def test_tool_specs_includes_bash_when_allowed(self, tmp_path):
        harness = LiteLLMAgentHarness(
            prompt="test",
            work_dir=tmp_path,
            model="openrouter/test/m",
            allow_bash=True,
        )
        names = [t["function"]["name"] for t in harness._tool_specs()]
        assert "run_bash" in names


# ---------------------------------------------------------------------------
# OpenCodeHarness
# ---------------------------------------------------------------------------


class TestOpenCodeHarness:
    def test_build_argv_no_model(self, tmp_path):
        h = OpenCodeHarness(prompt="do it", work_dir=tmp_path, cli_path="opencode")
        with patch("shutil.which", return_value="/usr/bin/opencode"):
            argv = h.build_argv(include_prompt=False)
        assert argv == ["/usr/bin/opencode", "run", "--format", "json", "--dir", str(tmp_path)]

    def test_build_argv_with_model(self, tmp_path):
        h = OpenCodeHarness(
            prompt="do it",
            work_dir=tmp_path,
            model="openrouter/google/gemini-flash-1.5",
            cli_path="opencode",
        )
        with patch("shutil.which", return_value="/usr/bin/opencode"):
            argv = h.build_argv(include_prompt=False)
        assert "-m" in argv
        assert "openrouter/google/gemini-flash-1.5" in argv

    def test_build_argv_missing_cli(self, tmp_path):
        h = OpenCodeHarness(prompt="do it", work_dir=tmp_path, cli_path="opencode")
        with patch("shutil.which", return_value=None):
            argv = h.build_argv()
        assert argv == []

    def test_parse_event_stream_assistant_event(self, tmp_path):
        # Real opencode --format json format: text in part.text, turns via step_start
        events = [
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": "Hello!"}},
            {"type": "step_finish", "part": {"type": "step-finish", "cost": 0, "tokens": {"total": 10}}},
        ]
        stdout = "\n".join(json.dumps(e) for e in events)
        result = OpenCodeHarness._parse_event_stream(stdout, "1.2.15")
        assert result.success is True
        assert result.result_text == "Hello!"
        assert result.num_turns == 1

    def test_parse_event_stream_no_json(self, tmp_path):
        result = OpenCodeHarness._parse_event_stream("plain text output", "1.2.15")
        assert result.result_text == "plain text output"

    def test_parse_event_stream_empty(self, tmp_path):
        result = OpenCodeHarness._parse_event_stream("", "1.2.15")
        assert result.success is False

    def test_parse_event_stream_with_cost(self, tmp_path):
        events = [
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": "Done."}},
            {"type": "step_finish", "part": {"type": "step-finish", "cost": 0.025, "tokens": {}}},
        ]
        stdout = "\n".join(json.dumps(e) for e in events)
        result = OpenCodeHarness._parse_event_stream(stdout, "1.2.15")
        assert result.total_cost_usd == pytest.approx(0.025)

    @pytest.mark.asyncio
    async def test_run_cli_not_found(self, tmp_path):
        h = OpenCodeHarness(prompt="test", work_dir=tmp_path, cli_path="opencode")
        with patch("shutil.which", return_value=None):
            result = await h.run()
        assert result.success is False
        assert "not found" in (result.error or "")


# ---------------------------------------------------------------------------
# HarnessResult interface compatibility
# ---------------------------------------------------------------------------


class TestHarnessResultCompat:
    """Both harnesses must return the same HarnessResult type."""

    @pytest.mark.asyncio
    async def test_litellm_harness_result_is_harness_result(self, tmp_path):
        with (
            patch(
                "litellm.acompletion",
                new=AsyncMock(return_value=_make_completion_response("hi")),
            ),
            patch("litellm.completion_cost", return_value=0.0),
        ):
            harness = LiteLLMAgentHarness(
                prompt="hi", work_dir=tmp_path, model="openrouter/test/m"
            )
            result = await harness.run()
        assert isinstance(result, HarnessResult)

    @pytest.mark.asyncio
    async def test_opencode_harness_result_is_harness_result(self, tmp_path):
        h = OpenCodeHarness(prompt="test", work_dir=tmp_path, cli_path="opencode")
        with patch("shutil.which", return_value=None):
            result = await h.run()
        assert isinstance(result, HarnessResult)
