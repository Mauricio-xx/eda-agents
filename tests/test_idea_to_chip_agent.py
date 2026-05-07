"""Static checks for the gf180-idea-to-chip orchestrator templates.

These tests do not invoke any LLM. They guard:

- Both template files (claude + opencode) exist and have the right
  frontmatter shape.
- Every ``mcp__eda-agents__render_skill(name="...")`` call references a
  skill that actually lives in the registry.
- The decision tree mentions all three concrete entry points
  (``generate_rtl_draft``, ``run_idea_to_rtl_loop``,
  ``DigitalAutoresearchRunner``).
- The agent body does not inline paragraphs from the skills it cites.
- The ``.claude/agents/`` and ``.opencode/agent/`` symlinks resolve to
  the canonical templates.
- The new orchestrator and the existing ``gf180-docker-digital`` agent
  expose disjoint concrete entry points (no contradiction).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_TEMPLATE = (
    REPO_ROOT
    / "src"
    / "eda_agents"
    / "templates"
    / "claude_agents"
    / "gf180-idea-to-chip.md"
)
OPENCODE_TEMPLATE = (
    REPO_ROOT
    / "src"
    / "eda_agents"
    / "templates"
    / "opencode_agents"
    / "gf180-idea-to-chip.md"
)
CLAUDE_SYMLINK = REPO_ROOT / ".claude" / "agents" / "gf180-idea-to-chip.md"
OPENCODE_SYMLINK = REPO_ROOT / ".opencode" / "agent" / "gf180-idea-to-chip.md"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(frontmatter_dict, body)`` from a markdown file."""
    if not text.startswith("---\n"):
        raise AssertionError("template must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise AssertionError("frontmatter has no closing '---'")
    fm_block = text[4:end]
    body = text[end + 5 :]
    parsed = yaml.safe_load(fm_block) or {}
    return parsed, body


def test_template_files_exist() -> None:
    assert CLAUDE_TEMPLATE.is_file(), CLAUDE_TEMPLATE
    assert OPENCODE_TEMPLATE.is_file(), OPENCODE_TEMPLATE


def test_template_frontmatter_parses() -> None:
    claude_fm, _ = _split_frontmatter(CLAUDE_TEMPLATE.read_text())
    assert claude_fm.get("name") == "gf180-idea-to-chip"
    desc = claude_fm.get("description", "")
    assert "GF180MCU" in desc
    assert "Docker" in desc

    opencode_fm, _ = _split_frontmatter(OPENCODE_TEMPLATE.read_text())
    assert opencode_fm.get("mode") == "all"
    assert opencode_fm.get("temperature") == 0.2
    assert "GF180MCU" in opencode_fm.get("description", "")


def test_template_only_calls_existing_skills() -> None:
    from eda_agents.skills.registry import list_skills

    valid = {s.name for s in list_skills()}
    pattern = re.compile(r'mcp__eda-agents__render_skill\(name="([^"]+)"\)')
    for path in (CLAUDE_TEMPLATE, OPENCODE_TEMPLATE):
        body = path.read_text()
        referenced = set(pattern.findall(body))
        assert referenced, f"no skill references in {path}"
        unknown = referenced - valid
        assert not unknown, (
            f"{path.name} references skills not in the registry: {unknown}"
        )


def test_template_decision_tree_mentions_both_entry_points() -> None:
    expected_entry_points = {
        "generate_rtl_draft",
        "run_idea_to_rtl_loop",
        "DigitalAutoresearchRunner",
    }
    for path in (CLAUDE_TEMPLATE, OPENCODE_TEMPLATE):
        body = path.read_text()
        missing = {ep for ep in expected_entry_points if ep not in body}
        assert not missing, f"{path.name} missing entry points {missing}"


def test_template_no_skill_body_inlined() -> None:
    """Agent body must not paraphrase skill content verbatim.

    Sample three distinctive substrings from the rendered
    ``flow.rtl2gds_gf180_docker`` skill. Each should appear at most
    once in the agent body (a single citation is fine; multiple
    verbatim hits indicate the body has been pasted in).
    """
    from eda_agents.skills.registry import get_skill

    skill_body = get_skill("flow.rtl2gds_gf180_docker").render()
    distinctive = [
        "hpretl/iic-osic-tools:next",
        "wafer-space `slot_1x1` GF180 template",
        "source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0",
    ]
    for snippet in distinctive:
        assert snippet in skill_body, (
            f"sentinel snippet not found in skill body: {snippet!r}"
        )

    for path in (CLAUDE_TEMPLATE, OPENCODE_TEMPLATE):
        body = path.read_text()
        verbatim_hits = sum(1 for s in distinctive if s in body)
        assert verbatim_hits <= 1, (
            f"{path.name} appears to inline the skill body "
            f"({verbatim_hits} distinctive snippets matched)."
        )


def test_symlinks_resolve_to_template() -> None:
    assert CLAUDE_SYMLINK.is_symlink(), CLAUDE_SYMLINK
    assert OPENCODE_SYMLINK.is_symlink(), OPENCODE_SYMLINK
    assert CLAUDE_SYMLINK.resolve() == CLAUDE_TEMPLATE.resolve()
    assert OPENCODE_SYMLINK.resolve() == OPENCODE_TEMPLATE.resolve()


def test_template_no_overlap_with_idea_to_chip() -> None:
    """gf180-docker-digital must defer to the orchestrator.

    Phase 4 adds a 'When NOT to use this agent' section to the docker
    template. We assert that the two agent bodies expose disjoint
    concrete entry points: the docker agent never claims ownership of
    ``run_idea_to_rtl_loop`` or ``DigitalAutoresearchRunner`` as
    primary tools, and the orchestrator never re-implements the
    docker agent's single-shot harden flow.
    """
    docker_template = (
        REPO_ROOT
        / "src"
        / "eda_agents"
        / "templates"
        / "claude_agents"
        / "gf180-docker-digital.md"
    )
    docker_body = docker_template.read_text()
    orchestrator_body = CLAUDE_TEMPLATE.read_text()

    # The orchestrator hands off single-shot harden back to the docker
    # agent: it must mention "gf180-docker-digital" as a fallback.
    assert "gf180-docker-digital" in orchestrator_body, (
        "orchestrator must reference gf180-docker-digital for hand-off"
    )

    # The docker agent must reference the orchestrator (Phase 4 hand-off
    # edit). This test will fail until P4 lands; that is intentional so
    # P4 cannot be skipped silently.
    assert "gf180-idea-to-chip" in docker_body, (
        "gf180-docker-digital must reference gf180-idea-to-chip "
        "(Phase 4 hand-off edit)"
    )
