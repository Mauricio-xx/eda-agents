"""Tests for ``eda_agents.bridge.ssh`` — fully mocked, no real SSH.

The mandatory rule from the S8 handoff: tests must NOT open SSH to any
external host. Every subprocess.run is patched.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from eda_agents.bridge.ssh import (
    CommandResult,
    SSHRunner,
    remote_ssh_env_from_os,
)


@pytest.fixture
def runner(tmp_path):
    return SSHRunner(
        host="example.invalid",
        user="alice",
        timeout_s=5,
        connect_timeout_s=2,
        log_path=tmp_path / "commands.log",
        ssh_cmd="/usr/bin/ssh",
        scp_cmd="/usr/bin/scp",
    )


# -- argv construction (no subprocess) -------------------------------------------------


def test_build_ssh_command_basic(runner):
    argv = runner.build_ssh_command("ls /tmp")
    assert argv[0] == "/usr/bin/ssh"
    assert "alice@example.invalid" in argv
    assert argv[-3:] == ["sh", "-c", "ls /tmp"]
    assert "BatchMode=yes" in argv  # safety: never prompt


def test_build_ssh_command_with_jump():
    r = SSHRunner(
        host="target.invalid",
        user="alice",
        jump_host="bastion.invalid",
        jump_user="bob",
        ssh_cmd="/usr/bin/ssh",
    )
    argv = r.build_ssh_command("uptime")
    assert "-J" in argv
    j_idx = argv.index("-J")
    assert argv[j_idx + 1] == "bob@bastion.invalid"


def test_build_ssh_command_uses_identity_and_config(tmp_path):
    key = tmp_path / "id_rsa"
    cfg = tmp_path / "config"
    key.write_text("")
    cfg.write_text("")
    r = SSHRunner(
        host="h", user="u",
        ssh_key_path=key, ssh_config_path=cfg,
        ssh_cmd="/usr/bin/ssh",
    )
    argv = r.build_ssh_command("true")
    assert "-i" in argv and str(key) in argv
    assert "-F" in argv and str(cfg) in argv


def test_control_master_options_present_by_default(runner):
    argv = runner.build_ssh_command("true")
    assert "ControlMaster=auto" in argv
    cp_idx = [i for i, a in enumerate(argv) if a.startswith("ControlPath=")]
    assert cp_idx, "ControlPath option should be present"


def test_control_master_can_be_disabled():
    r = SSHRunner(host="h", user="u", use_control_master=False, ssh_cmd="ssh")
    argv = r.build_ssh_command("true")
    assert "ControlMaster=auto" not in argv


def test_build_scp_command_recursive(runner):
    argv = runner.build_scp_command("/local/dir", "alice@example.invalid:/remote/dir", recursive=True)
    assert argv[0] == "/usr/bin/scp"
    assert "-r" in argv
    assert argv[-2:] == ["/local/dir", "alice@example.invalid:/remote/dir"]


# -- run_command (mocked subprocess) ---------------------------------------------------


def _fake_completed(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def test_run_command_success(runner):
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.return_value = _fake_completed(rc=0, out="hello\n")
        result = runner.run_command("echo hello")
    assert result.ok
    assert result.returncode == 0
    assert "hello" in result.stdout
    # log line was appended
    log = (runner.log_path).read_text()
    assert "[ssh]" in log and "echo hello" in log


def test_run_command_nonzero_propagates(runner):
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.return_value = _fake_completed(rc=2, err="permission denied")
        result = runner.run_command("rm -rf /")
    assert not result.ok
    assert result.returncode == 2
    assert "permission denied" in result.stderr


def test_run_command_timeout_returns_124(runner):
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=1)
        result = runner.run_command("sleep 100", timeout=1)
    assert result.returncode == 124


def test_run_command_missing_binary_returns_127(runner):
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.side_effect = FileNotFoundError("no ssh on this box")
        result = runner.run_command("true")
    assert result.returncode == 127
    assert "no ssh" in result.stderr


def test_test_connection_uses_run_command(runner):
    with patch.object(runner, "run_command") as m:
        m.return_value = CommandResult(returncode=0, stdout="", stderr="")
        assert runner.test_connection() is True
        m.return_value = CommandResult(returncode=255, stdout="", stderr="boom")
        assert runner.test_connection() is False


# -- scp_to / scp_from -----------------------------------------------------------------


def test_scp_to_missing_local_returns_2(runner, tmp_path):
    nope = tmp_path / "nope.txt"
    res = runner.scp_to(nope, "/remote/nope.txt")
    assert res.returncode == 2
    assert "local path not found" in res.stderr


def test_scp_to_invokes_scp(runner, tmp_path):
    src = tmp_path / "f.txt"
    src.write_text("hi")
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.return_value = _fake_completed(rc=0)
        res = runner.scp_to(src, "/remote/f.txt")
    assert res.ok
    argv = m.call_args.args[0]
    assert argv[0] == "/usr/bin/scp"
    assert str(src) in argv
    assert any(a.endswith(":/remote/f.txt") for a in argv)
    log = runner.log_path.read_text()
    assert "[scp_to]" in log


def test_scp_from_invokes_scp(runner, tmp_path):
    dst = tmp_path / "out" / "f.txt"
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.return_value = _fake_completed(rc=0)
        res = runner.scp_from("/remote/f.txt", dst)
    assert res.ok
    argv = m.call_args.args[0]
    assert any(a.endswith(":/remote/f.txt") for a in argv)
    assert str(dst) in argv
    assert dst.parent.is_dir()  # parent created
    log = runner.log_path.read_text()
    assert "[scp_from]" in log


def test_command_log_format_iso_timestamp(runner, tmp_path):
    with patch("eda_agents.bridge.ssh.subprocess.run") as m:
        m.return_value = _fake_completed(rc=0)
        runner.run_command("date")
    line = (runner.log_path).read_text().strip()
    # ISO 8601: 2026-04-15T...+00:00
    assert "T" in line.split(" ", 1)[0]
    assert "+00:00" in line.split(" ", 1)[0]


# -- env helper ------------------------------------------------------------------------


def test_remote_ssh_env_from_os_default():
    env_vars = {
        "EDA_BRIDGE_REMOTE_HOST": "host.example",
        "EDA_BRIDGE_REMOTE_USER": "alice",
        "EDA_BRIDGE_JUMP_HOST": "bastion.example",
        "EDA_BRIDGE_JUMP_USER": "bob",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        e = remote_ssh_env_from_os()
    assert e.remote_host == "host.example"
    assert e.remote_user == "alice"
    assert e.jump_host == "bastion.example"
    assert e.jump_user == "bob"


def test_remote_ssh_env_from_os_profile():
    env_vars = {
        "EDA_BRIDGE_REMOTE_HOST_GPU1": "gpu1.example",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        e = remote_ssh_env_from_os(profile="gpu1")
    assert e.remote_host == "gpu1.example"


def test_remote_ssh_env_strips_blank():
    with patch.dict(os.environ, {"EDA_BRIDGE_REMOTE_HOST": "   "}, clear=False):
        e = remote_ssh_env_from_os()
    assert e.remote_host is None
