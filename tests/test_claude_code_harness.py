"""Tests for Claude Code CLI harness (Phase 5).

Tests the ClaudeCodeHarness and HarnessResult without invoking
the real Claude CLI (mocked subprocess).  Integration test with
the real CLI is gated behind ``-m cc_cli``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eda_agents.agents.claude_code_harness import (
    ClaudeCodeHarness,
    HarnessResult,
)

# ---------------------------------------------------------------------------
# Recorded JSON output from a real ``claude --print --output-format json``
# ---------------------------------------------------------------------------

RECORDED_JSON = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 2110,
    "duration_api_ms": 2045,
    "num_turns": 1,
    "result": "Hello! How can I help you today?",
    "stop_reason": "end_turn",
    "session_id": "e3be4230-16c4-49a7-898a-808fc0770847",
    "total_cost_usd": 0.128,
    "usage": {"input_tokens": 2, "output_tokens": 12},
    "modelUsage": {
        "claude-opus-4-6[1m]": {
            "inputTokens": 2,
            "outputTokens": 12,
            "costUSD": 0.128,
        }
    },
    "permission_denials": [],
    "terminal_reason": "completed",
}

RECORDED_JSON_STR = json.dumps(RECORDED_JSON)


# ---------------------------------------------------------------------------
# HarnessResult tests
# ---------------------------------------------------------------------------


class TestHarnessResult:
    def test_default_construction(self):
        r = HarnessResult(success=True)
        assert r.success
        assert r.result_text == ""
        assert r.duration_ms == 0.0
        assert r.error is None
        assert r.cli_version == ""
        assert r.model_usage == {}
        assert r.raw_json == {}

    def test_error_construction(self):
        r = HarnessResult(success=False, error="timeout")
        assert not r.success
        assert r.error == "timeout"

    def test_full_construction(self):
        r = HarnessResult(
            success=True,
            result_text="hello",
            duration_ms=1234.5,
            num_turns=3,
            total_cost_usd=0.5,
            session_id="abc-123",
            model_usage={"model": {"tokens": 100}},
            raw_json={"type": "result"},
            cli_version="2.1.104",
        )
        assert r.result_text == "hello"
        assert r.duration_ms == 1234.5
        assert r.num_turns == 3
        assert r.total_cost_usd == 0.5
        assert r.session_id == "abc-123"
        assert r.cli_version == "2.1.104"


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------


class TestJsonParsing:
    def test_parse_recorded_json(self):
        result = ClaudeCodeHarness._parse_json_output(
            RECORDED_JSON_STR, "2.1.104"
        )
        assert result.success
        assert "Hello" in result.result_text
        assert result.duration_ms == 2110
        assert result.num_turns == 1
        assert result.total_cost_usd == 0.128
        assert result.session_id == "e3be4230-16c4-49a7-898a-808fc0770847"
        assert "claude-opus-4-6[1m]" in result.model_usage
        assert result.cli_version == "2.1.104"
        assert result.error is None

    def test_parse_error_json(self):
        error_json = json.dumps({
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Rate limit exceeded",
        })
        result = ClaudeCodeHarness._parse_json_output(error_json, "2.1.0")
        assert not result.success
        assert result.error == "Rate limit exceeded"

    def test_parse_invalid_json(self):
        result = ClaudeCodeHarness._parse_json_output("not json", "2.1.0")
        assert not result.success
        assert "Failed to parse" in result.error
        assert "not json" in result.result_text

    def test_parse_empty_string(self):
        result = ClaudeCodeHarness._parse_json_output("", "2.1.0")
        assert not result.success
        assert "Failed to parse" in result.error


# ---------------------------------------------------------------------------
# build_argv tests
# ---------------------------------------------------------------------------


class TestBuildArgv:
    def setup_method(self):
        self.harness = ClaudeCodeHarness(
            prompt="test prompt",
            work_dir=Path("/tmp/test"),
        )

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_basic_argv(self, mock_which):
        argv = self.harness.build_argv()
        assert argv[0] == "/usr/bin/claude"
        assert "--print" in argv
        assert "--output-format" in argv
        assert "json" in argv
        assert "--no-session-persistence" in argv
        # bare=False by default (--bare skips OAuth keychain)
        assert "--bare" not in argv

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_bare_true(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), bare=True,
        )
        argv = h.build_argv()
        assert "--bare" in argv

    @patch("shutil.which", return_value=None)
    def test_missing_cli_returns_empty(self, mock_which):
        argv = self.harness.build_argv()
        assert argv == []

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_model_in_argv(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), model="sonnet"
        )
        argv = h.build_argv()
        assert "--model" in argv
        idx = argv.index("--model")
        assert argv[idx + 1] == "sonnet"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_max_budget_in_argv(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), max_budget_usd=5.0
        )
        argv = h.build_argv()
        assert "--max-budget-usd" in argv
        idx = argv.index("--max-budget-usd")
        assert argv[idx + 1] == "5.0"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_append_system_prompt_in_argv(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"),
            append_system_prompt="extra instructions",
        )
        argv = h.build_argv()
        assert "--append-system-prompt" in argv
        idx = argv.index("--append-system-prompt")
        assert argv[idx + 1] == "extra instructions"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_bare_false(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), bare=False,
        )
        argv = h.build_argv()
        assert "--bare" not in argv


# ---------------------------------------------------------------------------
# Double gate tests
# ---------------------------------------------------------------------------


class TestDoubleGate:
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_both_gates_set(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), allow_dangerous=True,
        )
        with patch.dict(os.environ, {"EDA_AGENTS_ALLOW_DANGEROUS": "1"}):
            argv = h.build_argv()
        assert "--dangerously-skip-permissions" in argv

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_only_constructor_gate(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), allow_dangerous=True,
        )
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the env var is NOT set
            os.environ.pop("EDA_AGENTS_ALLOW_DANGEROUS", None)
            argv = h.build_argv()
        assert "--dangerously-skip-permissions" not in argv

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_only_env_gate(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), allow_dangerous=False,
        )
        with patch.dict(os.environ, {"EDA_AGENTS_ALLOW_DANGEROUS": "1"}):
            argv = h.build_argv()
        assert "--dangerously-skip-permissions" not in argv

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_neither_gate(self, mock_which):
        h = ClaudeCodeHarness(
            prompt="test", work_dir=Path("/tmp"), allow_dangerous=False,
        )
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EDA_AGENTS_ALLOW_DANGEROUS", None)
            argv = h.build_argv()
        assert "--dangerously-skip-permissions" not in argv


# ---------------------------------------------------------------------------
# MCP config tests
# ---------------------------------------------------------------------------


class TestMcpConfig:
    def test_no_mcp_config(self, tmp_path):
        h = ClaudeCodeHarness(prompt="test", work_dir=tmp_path)
        assert h._write_mcp_config() is None

    def test_mcp_config_writes_file(self, tmp_path):
        config = {"mcpServers": {"test": {"command": "python"}}}
        h = ClaudeCodeHarness(
            prompt="test", work_dir=tmp_path, mcp_config=config,
        )
        path = h._write_mcp_config()
        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == config

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_mcp_config_in_argv(self, mock_which, tmp_path):
        config = {"mcpServers": {"test": {"command": "python"}}}
        h = ClaudeCodeHarness(
            prompt="test", work_dir=tmp_path, mcp_config=config,
        )
        argv = h.build_argv()
        assert "--mcp-config" in argv


# ---------------------------------------------------------------------------
# CLI version tests
# ---------------------------------------------------------------------------


class TestCliVersion:
    def test_version_cached(self):
        h = ClaudeCodeHarness(prompt="test", work_dir=Path("/tmp"))
        h._cli_version = "2.1.104"
        assert asyncio.run(h.get_cli_version()) == "2.1.104"

    @patch("shutil.which", return_value=None)
    def test_version_missing_cli(self, mock_which):
        h = ClaudeCodeHarness(prompt="test", work_dir=Path("/tmp"))
        version = asyncio.run(h.get_cli_version())
        assert version == ""


# ---------------------------------------------------------------------------
# Async run tests (mocked subprocess)
# ---------------------------------------------------------------------------


def _make_mock_process(stdout: bytes, returncode: int = 0, stderr: bytes = b""):
    """Create a mock async subprocess."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


class TestRun:
    @patch("shutil.which", return_value=None)
    def test_run_missing_cli(self, mock_which, tmp_path):
        h = ClaudeCodeHarness(prompt="test", work_dir=tmp_path)
        result = asyncio.run(h.run())
        assert not result.success
        assert "not found" in result.error

    @patch("asyncio.create_subprocess_exec")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_run_success(self, mock_which, mock_exec, tmp_path):
        mock_exec.return_value = _make_mock_process(
            stdout=RECORDED_JSON_STR.encode()
        )
        h = ClaudeCodeHarness(prompt="hello", work_dir=tmp_path)
        result = asyncio.run(h.run())
        assert result.success
        assert "Hello" in result.result_text
        assert result.duration_ms == 2110

    @patch("asyncio.create_subprocess_exec")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_run_nonzero_exit(self, mock_which, mock_exec, tmp_path):
        mock_exec.return_value = _make_mock_process(
            stdout=b"", returncode=1, stderr=b"Error occurred"
        )
        h = ClaudeCodeHarness(prompt="hello", work_dir=tmp_path)
        result = asyncio.run(h.run())
        assert not result.success
        assert "code 1" in result.error

    @patch("asyncio.wait_for", side_effect=asyncio.TimeoutError)
    @patch("asyncio.create_subprocess_exec")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_run_timeout(self, mock_which, mock_exec, mock_wait, tmp_path):
        mock_proc = _make_mock_process(stdout=b"")
        mock_exec.return_value = mock_proc
        h = ClaudeCodeHarness(prompt="hello", work_dir=tmp_path, timeout_s=1)
        result = asyncio.run(h.run())
        assert not result.success
        assert "Timeout" in result.error

    @patch("asyncio.create_subprocess_exec")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_run_pipes_prompt_via_stdin(self, mock_which, mock_exec, tmp_path):
        mock_proc = _make_mock_process(stdout=RECORDED_JSON_STR.encode())
        # Version check also calls create_subprocess_exec, so return mock for both
        version_proc = _make_mock_process(stdout=b"2.1.104")
        mock_exec.side_effect = [version_proc, mock_proc]
        h = ClaudeCodeHarness(prompt="my test prompt", work_dir=tmp_path)
        asyncio.run(h.run())
        # The main run call should pass the prompt as stdin input
        assert mock_proc.communicate.call_count == 1
        call_args = mock_proc.communicate.call_args
        # input=b"my test prompt" passed as keyword
        assert call_args[1]["input"] == b"my test prompt"

    @patch("asyncio.create_subprocess_exec")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_run_creates_work_dir(self, mock_which, mock_exec, tmp_path):
        work = tmp_path / "subdir" / "deep"
        mock_exec.return_value = _make_mock_process(
            stdout=RECORDED_JSON_STR.encode()
        )
        h = ClaudeCodeHarness(prompt="hello", work_dir=work)
        asyncio.run(h.run())
        assert work.exists()


# ---------------------------------------------------------------------------
# Integration test (real CLI, gated behind marker)
# ---------------------------------------------------------------------------


@pytest.mark.cc_cli
class TestIntegration:
    """Real Claude CLI invocation.  Run with ``pytest -m cc_cli``."""

    def test_real_hello(self, tmp_path):
        h = ClaudeCodeHarness(
            prompt="Respond with exactly the word 'pong' and nothing else.",
            work_dir=tmp_path,
            timeout_s=30,
        )
        result = asyncio.run(h.run())
        assert result.success, f"CLI failed: {result.error}"
        assert result.cli_version
        assert result.duration_ms > 0
        assert result.total_cost_usd > 0
        assert len(result.result_text) > 0
