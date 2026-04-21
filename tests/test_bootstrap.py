"""Tests for the ``eda-init`` project bootstrapper."""

from __future__ import annotations

from pathlib import Path

from eda_agents.bootstrap import init_project


EXPECTED_AGENTS = {
    "analog-topology-recommender",
    "analog-sizing-advisor",
    "digital-testbench-author",
    "gf180-docker-digital",
    "gf180-docker-analog",
}


class TestInitProjectFreshTree:
    def test_writes_opencode_and_mcp_configs(self, tmp_path: Path):
        init_project(target=tmp_path)
        assert (tmp_path / "opencode.json").exists()
        assert (tmp_path / ".mcp.json").exists()

    def test_opencode_config_uses_eda_mcp_console_script(self, tmp_path: Path):
        init_project(target=tmp_path)
        content = (tmp_path / "opencode.json").read_text(encoding="utf-8")
        assert "eda-mcp" in content
        # Must not leak the in-repo relative path we use for dev.
        assert ".venv/bin/python" not in content

    def test_claude_mcp_config_declares_stdio(self, tmp_path: Path):
        init_project(target=tmp_path)
        content = (tmp_path / ".mcp.json").read_text(encoding="utf-8")
        assert '"type": "stdio"' in content
        assert '"command": "eda-mcp"' in content

    def test_all_opencode_agents_materialize(self, tmp_path: Path):
        init_project(target=tmp_path)
        agents_dir = tmp_path / ".opencode" / "agent"
        names = {p.stem for p in agents_dir.glob("*.md")}
        assert EXPECTED_AGENTS.issubset(names)

    def test_all_claude_agents_materialize(self, tmp_path: Path):
        init_project(target=tmp_path)
        agents_dir = tmp_path / ".claude" / "agents"
        names = {p.stem for p in agents_dir.glob("*.md")}
        assert EXPECTED_AGENTS.issubset(names)

    def test_agent_bodies_are_non_empty(self, tmp_path: Path):
        init_project(target=tmp_path)
        for md in (tmp_path / ".opencode" / "agent").glob("*.md"):
            assert len(md.read_text(encoding="utf-8")) > 100


class TestInitProjectIdempotence:
    def test_default_does_not_overwrite_config(self, tmp_path: Path):
        (tmp_path / "opencode.json").write_text('{"custom": true}', encoding="utf-8")
        init_project(target=tmp_path, force=False)
        assert (tmp_path / "opencode.json").read_text(encoding="utf-8") == '{"custom": true}'

    def test_default_does_not_overwrite_agent(self, tmp_path: Path):
        tgt = tmp_path / ".opencode" / "agent"
        tgt.mkdir(parents=True)
        (tgt / "gf180-docker-digital.md").write_text("custom body", encoding="utf-8")
        init_project(target=tmp_path, force=False)
        assert (tgt / "gf180-docker-digital.md").read_text(encoding="utf-8") == "custom body"

    def test_force_overwrites_everything(self, tmp_path: Path):
        (tmp_path / "opencode.json").write_text('{"custom": true}', encoding="utf-8")
        init_project(target=tmp_path, force=True)
        content = (tmp_path / "opencode.json").read_text(encoding="utf-8")
        assert '"custom": true' not in content
        assert "eda-mcp" in content


class TestInitProjectTargetCreation:
    def test_missing_target_is_created(self, tmp_path: Path):
        new_target = tmp_path / "fresh_project"
        assert not new_target.exists()
        init_project(target=new_target)
        assert new_target.exists()
        assert (new_target / "opencode.json").exists()
        assert (new_target / ".opencode" / "agent" / "gf180-docker-digital.md").exists()
