"""Subprocess tests for scripts/idea_to_chip_wizard.py.

Covers the two non-interactive entry points (digital + analog) in
dry modes that don't hit external tools. Exercises the argparse
plumbing, prompt generation, and result JSON writing without
needing Claude CLI or OpenRouter.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "idea_to_chip_wizard.py"


def _run_wizard(args: list[str], env: dict[str, str] | None = None):
    """Run the wizard as a subprocess with the worktree's venv python."""
    import os

    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=full_env,
    )


class TestWizardDigital:
    def test_digital_dry_run_succeeds(self, tmp_path):
        out_dir = tmp_path / "work"
        result = _run_wizard([
            "--digital", "--dry-run",
            "--description", "4-bit sync counter with enable",
            "--design-name", "counter4",
            "--pdk", "gf180mcu",
            "--pdk-root", "/tmp/fake_pdk",
            "--work-dir", str(out_dir),
            "--yes",
        ])
        assert result.returncode == 0, result.stderr
        assert "all_passed : True" in result.stdout
        assert "wizard_result.json" in result.stdout
        payload = json.loads((out_dir / "wizard_result.json").read_text())
        assert payload["success"] is True
        assert payload["design_name"] == "counter4"

    def test_digital_dry_run_missing_description(self, tmp_path):
        # Feed an empty description via stdin — wizard should abort cleanly.
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--digital", "--dry-run", "--yes"],
            input="\n",  # empty description
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1


class TestWizardAnalog:
    def test_analog_missing_api_key_reports_error(self, monkeypatch):
        result = subprocess.run(
            [
                sys.executable, str(_SCRIPT), "--analog",
                "--description", "low-noise amp",
            ],
            input="\n",  # empty constraints
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": __import__("os").environ.get("PATH", ""),
                 "HOME": __import__("os").environ.get("HOME", "")},
        )
        assert result.returncode == 2
        assert "OPENROUTER_API_KEY" in result.stdout


class TestWizardCli:
    def test_help_runs(self):
        result = _run_wizard(["--help"])
        assert result.returncode == 0
        assert "idea-to-chip" in result.stdout
        assert "--digital" in result.stdout
        assert "--analog" in result.stdout

    def test_mutually_exclusive_domain(self, tmp_path):
        result = _run_wizard([
            "--digital", "--analog",
            "--description", "x",
            "--design-name", "y",
            "--yes",
        ])
        assert result.returncode != 0
        # argparse emits mutually-exclusive errors on stderr
        assert "not allowed with" in result.stderr or "mutually exclusive" in result.stderr
