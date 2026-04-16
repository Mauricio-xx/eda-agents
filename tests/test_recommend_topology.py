"""Unit tests for the S11 Fase 3 analog topology recommender.

Covers the ``analog.idea_to_topology`` skill and the
``recommend_topology`` MCP tool. OpenRouter is mocked out — live
coverage happens when bench / manual scripts set
``OPENROUTER_API_KEY`` and point at the real endpoint.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from eda_agents.skills.registry import get_skill

try:
    import fastmcp  # noqa: F401

    HAS_FASTMCP = True
except ImportError:  # pragma: no cover
    HAS_FASTMCP = False


class TestIdeaToTopologySkill:
    def test_skill_registered(self):
        skill = get_skill("analog.idea_to_topology")
        assert skill.prompt_fn is not None

    def test_skill_render_lists_all_topologies(self):
        skill = get_skill("analog.idea_to_topology")
        body = skill.render()
        # Every registered topology must appear in the prompt so the
        # classifier LLM actually sees the choice.
        for name in (
            "miller_ota",
            "aa_ota",
            "gf180_ota",
            "strongarm_comp",
            "sar_adc_7bit",
            "sar_adc_7bit_behavioral",
            "sar_adc_11bit",
        ):
            assert name in body, f"topology {name!r} not listed in skill body"

    def test_skill_output_contract_documented(self):
        body = get_skill("analog.idea_to_topology").render()
        for keyword in ("topology", "rationale", "starter_specs", "confidence"):
            assert keyword in body, f"skill must document {keyword!r} in output contract"
        assert "JSON object" in body


@pytest.mark.mcp
@pytest.mark.skipif(not HAS_FASTMCP, reason="fastmcp not installed")
class TestRecommendTopologyTool:
    def _mock_openrouter(self, monkeypatch, payload: dict[str, Any] | str):
        """Patch ``_call_openrouter`` so the tool returns a known payload."""
        from eda_agents.mcp import server as mcp_server

        raw = payload if isinstance(payload, str) else json.dumps(payload)

        def fake_call(**_kwargs):
            return raw, 42

        monkeypatch.setattr(mcp_server, "_call_openrouter", fake_call)

    async def test_dry_run_lists_topologies(self):
        from eda_agents.mcp.server import mcp

        result = await mcp.call_tool(
            "recommend_topology",
            {"description": "low-noise OTA for EEG", "dry_run": True},
        )
        data = result.structured_content
        assert data["success"] is True
        assert data["dry_run"] is True
        assert "miller_ota" in data["known_topologies"]
        assert data["prompt_length"] > 1000

    async def test_live_returns_structured_miller(self, monkeypatch):
        from eda_agents.mcp.server import mcp

        self._mock_openrouter(
            monkeypatch,
            {
                "topology": "miller_ota",
                "rationale": "Two-stage Miller OTA matches the 60 dB gain spec.",
                "starter_specs": {"Adc_dB_min": 60, "GBW_Hz_min": 10000},
                "confidence": "high",
                "notes": "",
            },
        )
        result = await mcp.call_tool(
            "recommend_topology",
            {
                "description": "biomedical AFE, 60 dB gain, 1 kHz bandwidth",
                "constraints": {"Adc_dB_min": 60, "PM_deg_min": 60},
            },
        )
        data = result.structured_content
        assert data["success"] is True
        assert data["topology"] == "miller_ota"
        assert data["confidence"] == "high"
        assert data["valid_topology"] is True
        assert data["starter_specs"]["Adc_dB_min"] == 60
        assert data["total_tokens"] == 42

    async def test_custom_topology_marks_valid_but_flagged(self, monkeypatch):
        from eda_agents.mcp.server import mcp

        self._mock_openrouter(
            monkeypatch,
            {
                "topology": "custom",
                "rationale": "24-bit delta-sigma not in registry.",
                "starter_specs": {"resolution_bits": 24},
                "confidence": "low",
                "notes": "no_match_reason",
            },
        )
        result = await mcp.call_tool(
            "recommend_topology",
            {"description": "24-bit delta-sigma modulator"},
        )
        data = result.structured_content
        assert data["success"] is True
        assert data["topology"] == "custom"
        assert data["confidence"] == "low"
        assert data["valid_topology"] is True  # "custom" is valid
        assert data["notes"] == "no_match_reason"

    async def test_unknown_topology_flagged_invalid(self, monkeypatch):
        from eda_agents.mcp.server import mcp

        self._mock_openrouter(
            monkeypatch,
            {
                "topology": "folded_cascode",  # not in registry
                "rationale": "Hallucinated topology.",
                "starter_specs": {},
                "confidence": "medium",
            },
        )
        result = await mcp.call_tool(
            "recommend_topology",
            {"description": "whatever"},
        )
        data = result.structured_content
        assert data["success"] is True
        assert data["topology"] == "folded_cascode"
        assert data["valid_topology"] is False

    async def test_malformed_json_reports_error(self, monkeypatch):
        from eda_agents.mcp.server import mcp

        self._mock_openrouter(monkeypatch, "sorry, I don't speak JSON")
        result = await mcp.call_tool(
            "recommend_topology",
            {"description": "whatever"},
        )
        data = result.structured_content
        assert data["success"] is False
        assert "did not return a JSON object" in data["error"]

    async def test_api_key_missing_reports_error(self, monkeypatch):
        from eda_agents.mcp import server as mcp_server

        def _raise(**_kwargs):
            raise RuntimeError("OPENROUTER_API_KEY not set")

        monkeypatch.setattr(mcp_server, "_call_openrouter", _raise)
        result = await mcp_server.mcp.call_tool(
            "recommend_topology",
            {"description": "whatever"},
        )
        data = result.structured_content
        assert data["success"] is False
        assert "OPENROUTER_API_KEY" in data["error"]


@pytest.mark.mcp
@pytest.mark.skipif(not HAS_FASTMCP, reason="fastmcp not installed")
class TestMCPToolCatalog:
    async def test_recommend_topology_registered(self):
        from eda_agents.mcp.server import mcp

        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "recommend_topology" in names
