"""Generic SSH / SCP runner for the EDA bridge.

Reimplemented from scratch under Apache-2.0 — inspired by the
``virtuoso-bridge-lite`` SSH transport (jump-host, ControlMaster, scp
helpers) but no code is copied. The upstream repo has no LICENSE file;
see ``docs/license_status.md``.

Scope of this module:

  - Drive a remote ``ssh user@host`` (optionally via ``-J jump@host``).
  - Run shell commands and capture (rc, stdout, stderr) as
    ``CommandResult``.
  - Upload / download files via ``scp``.
  - Optionally enable OpenSSH ControlMaster connection multiplexing so
    repeated commands reuse one TCP session.
  - Append every invocation to ``~/.cache/eda_agents/commands.log`` for
    audit / replay.

Out of scope (deliberately):

  - Persistent interactive shells. The bridge runs commands one-shot.
  - Port forwarding / tunnels. Add when an agent role actually needs it.
  - Windows Path / no-window flags. Linux-only by design (the eda-agents
    stack is Linux-only — see CLAUDE.md).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path.home() / ".cache" / "eda_agents" / "commands.log"


class RemoteSshEnv(NamedTuple):
    """SSH target read from ``EDA_BRIDGE_*`` environment variables.

    Mirrors the ``virtuoso-bridge-lite`` shape (host / user / jump_host /
    jump_user) but uses our own variable namespace so the two stacks can
    coexist without colliding.
    """

    remote_host: str | None
    remote_user: str | None
    jump_host: str | None
    jump_user: str | None


def remote_ssh_env_from_os(profile: str | None = None) -> RemoteSshEnv:
    """Read SSH target from environment.

    With no profile: ``EDA_BRIDGE_REMOTE_HOST``, ``EDA_BRIDGE_REMOTE_USER``,
    ``EDA_BRIDGE_JUMP_HOST``, ``EDA_BRIDGE_JUMP_USER``.

    With profile ``"gpu1"``: ``EDA_BRIDGE_REMOTE_HOST_GPU1`` etc.
    """
    suffix = f"_{profile.upper()}" if profile else ""

    def _strip(name: str) -> str | None:
        raw = os.environ.get(f"{name}{suffix}")
        if raw is None:
            return None
        s = raw.strip()
        return s or None

    return RemoteSshEnv(
        remote_host=_strip("EDA_BRIDGE_REMOTE_HOST"),
        remote_user=_strip("EDA_BRIDGE_REMOTE_USER"),
        jump_host=_strip("EDA_BRIDGE_JUMP_HOST"),
        jump_user=_strip("EDA_BRIDGE_JUMP_USER"),
    )


class CommandResult(NamedTuple):
    """Outcome of a single ``ssh ... <command>`` invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _append_command_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{ts} {message}\n")


class SSHRunner:
    """Linux OpenSSH wrapper for the EDA bridge.

    Parameters
    ----------
    host : str
        Remote host (DNS name or IP).
    user : str, optional
        Remote user.
    jump_host : str, optional
        Jump host for ``ssh -J``.
    jump_user : str, optional
        Jump host user. Defaults to ``user``.
    ssh_key_path : Path, optional
        Identity file (``-i``).
    ssh_config_path : Path, optional
        Custom ``~/.ssh/config`` file (``-F``).
    timeout_s : int
        Default per-command timeout.
    connect_timeout_s : int
        OpenSSH ``ConnectTimeout``.
    use_control_master : bool
        Enable OpenSSH connection multiplexing. Default True.
    log_path : Path, optional
        Where to append a per-invocation audit line. Defaults to
        ``~/.cache/eda_agents/commands.log``. Pass a tmp_path in tests.
    ssh_cmd / scp_cmd : str, optional
        Override the binary names (used in tests to inject mocks).
    """

    def __init__(
        self,
        host: str,
        user: str | None = None,
        jump_host: str | None = None,
        jump_user: str | None = None,
        ssh_key_path: Path | None = None,
        ssh_config_path: Path | None = None,
        timeout_s: int = 600,
        connect_timeout_s: int = 30,
        use_control_master: bool = True,
        log_path: Path | None = None,
        ssh_cmd: str | None = None,
        scp_cmd: str | None = None,
    ) -> None:
        self.host = host
        self.user = user
        self.jump_host = jump_host
        self.jump_user = jump_user or user
        self.ssh_key_path = ssh_key_path
        self.ssh_config_path = ssh_config_path
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.use_control_master = use_control_master
        self.log_path = log_path or DEFAULT_LOG_PATH
        self.ssh_cmd = ssh_cmd or shutil.which("ssh") or "ssh"
        self.scp_cmd = scp_cmd or shutil.which("scp") or "scp"

        # ControlMaster socket path. One socket per (user@host, jump).
        user_part = user or "default"
        tmp = tempfile.gettempdir()
        self._control_path = (
            f"{tmp}/eda_bridge_ssh_{user_part}@{host}:{jump_host or 'direct'}"
        )

    # -- command construction --------------------------------------------------

    def _common_options(self) -> list[str]:
        opts: list[str] = [
            "-o",
            f"ConnectTimeout={self.connect_timeout_s}",
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=30",
        ]
        if self.use_control_master:
            opts += [
                "-o",
                "ControlMaster=auto",
                "-o",
                f"ControlPath={self._control_path}",
                "-o",
                "ControlPersist=60",
            ]
        if self.ssh_config_path:
            opts += ["-F", str(self.ssh_config_path)]
        if self.ssh_key_path:
            opts += ["-i", str(self.ssh_key_path)]
        if self.jump_host:
            jump_target = (
                f"{self.jump_user}@{self.jump_host}" if self.jump_user else self.jump_host
            )
            opts += ["-J", jump_target]
        return opts

    def _ssh_target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def build_ssh_command(self, remote_command: str | None = None) -> list[str]:
        """Return the full ``ssh ...`` argv. Public for tests."""
        cmd = [self.ssh_cmd, *self._common_options(), self._ssh_target()]
        if remote_command is not None:
            cmd += ["sh", "-c", remote_command]
        return cmd

    def build_scp_command(
        self, source: str, dest: str, *, recursive: bool = False
    ) -> list[str]:
        """Return the full ``scp ...`` argv. Public for tests."""
        cmd = [self.scp_cmd, *self._common_options()]
        if recursive:
            cmd.append("-r")
        cmd += [source, dest]
        return cmd

    # -- public methods --------------------------------------------------------

    def run_command(self, command: str, timeout: int | None = None) -> CommandResult:
        """Run ``command`` on the remote host through ``sh -c``."""
        argv = self.build_ssh_command(command)
        _append_command_log(
            self.log_path, f"[ssh] host={self.host} cmd={command!r}"
        )
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else (
                f"timeout after {timeout or self.timeout_s}s"
            )
            return CommandResult(returncode=124, stdout=stdout, stderr=stderr)
        except FileNotFoundError as exc:
            return CommandResult(returncode=127, stdout="", stderr=str(exc))
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def test_connection(self, timeout: int | None = None) -> bool:
        """Cheap reachability probe. Returns True iff ``exit 0`` works."""
        result = self.run_command("exit 0", timeout=timeout or self.connect_timeout_s)
        return result.ok

    def scp_to(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        recursive: bool = False,
        timeout: int | None = None,
    ) -> CommandResult:
        """Copy a local file or directory to the remote host."""
        local = Path(local_path)
        if not local.exists():
            return CommandResult(
                returncode=2,
                stdout="",
                stderr=f"local path not found: {local}",
            )
        target = (
            f"{self._ssh_target()}:{remote_path}"
        )
        argv = self.build_scp_command(str(local), target, recursive=recursive)
        _append_command_log(
            self.log_path,
            f"[scp_to] host={self.host} src={local} dst={remote_path}",
        )
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=124,
                stdout="",
                stderr=str(exc),
            )
        except FileNotFoundError as exc:
            return CommandResult(returncode=127, stdout="", stderr=str(exc))
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def scp_from(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        recursive: bool = False,
        timeout: int | None = None,
    ) -> CommandResult:
        """Copy a remote file or directory back to the local host."""
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        source = f"{self._ssh_target()}:{remote_path}"
        argv = self.build_scp_command(source, str(local), recursive=recursive)
        _append_command_log(
            self.log_path,
            f"[scp_from] host={self.host} src={remote_path} dst={local}",
        )
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=124,
                stdout="",
                stderr=str(exc),
            )
        except FileNotFoundError as exc:
            return CommandResult(returncode=127, stdout="", stderr=str(exc))
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def close_control_master(self) -> None:
        """Close the OpenSSH ControlMaster socket if one exists.

        Safe to call when no master is open — ``ssh -O exit`` returns
        non-zero and we ignore that.
        """
        if not self.use_control_master:
            return
        if not Path(self._control_path).exists():
            return
        cmd = [
            self.ssh_cmd,
            "-o",
            f"ControlPath={self._control_path}",
            "-O",
            "exit",
            self._ssh_target(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass


__all__ = [
    "CommandResult",
    "DEFAULT_LOG_PATH",
    "RemoteSshEnv",
    "SSHRunner",
    "remote_ssh_env_from_os",
]
