"""Shared test fixtures for eda-agents."""

import importlib.util
import os

import pytest

from eda_agents.core.pdk import IHP_SG13G2, GF180MCU_D, PdkConfig


def _pdk_available(pdk: PdkConfig) -> bool:
    """Check if a PDK is installed and accessible."""
    root = os.environ.get("PDK_ROOT", pdk.default_pdk_root)
    if not root or not os.path.isdir(root):
        return False
    model_path = os.path.join(root, pdk.model_lib_rel)
    return os.path.isfile(model_path)


def _has_mcp() -> bool:
    """Detect the optional ``fastmcp`` dependency used by the MCP server."""
    return importlib.util.find_spec("fastmcp") is not None


def _has_adk() -> bool:
    """Detect the optional ``google-adk`` dependency used by the ADK agents."""
    try:
        return importlib.util.find_spec("google.adk") is not None
    except ModuleNotFoundError:
        return False


def _has_litellm() -> bool:
    """Detect the optional ``litellm`` dependency used by the LLM clients."""
    return importlib.util.find_spec("litellm") is not None


ihp_available = _pdk_available(IHP_SG13G2)
gf180_available = _pdk_available(GF180MCU_D)
HAS_MCP = _has_mcp()
HAS_ADK = _has_adk()
HAS_LITELLM = _has_litellm()

requires_litellm = pytest.mark.skipif(
    not HAS_LITELLM, reason="litellm not installed (pip install -e '.[mcp]')"
)
requires_adk = pytest.mark.skipif(
    not HAS_ADK, reason="google-adk not installed (pip install -e '.[adk]')"
)


@pytest.fixture
def pdk_root_env(tmp_path_factory, monkeypatch):
    """Guarantee a resolvable ``PDK_ROOT`` for tests that only build runners.

    When both PDKs resolve the environment is left alone. Otherwise
    ``PDK_ROOT`` points at a skeleton holding just the model-lib files
    ``resolve_pdk_root()`` looks for, so constructors work on hosts
    without a PDK (CI). Nothing in the skeleton can be simulated.
    """
    if ihp_available and gf180_available:
        return os.environ.get("PDK_ROOT")
    root = tmp_path_factory.mktemp("fake_pdk")
    for pdk in (IHP_SG13G2, GF180MCU_D):
        lib = root / pdk.model_lib_rel
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_text("* placeholder model lib for tests\n")
    monkeypatch.setenv("PDK_ROOT", str(root))
    return str(root)


@pytest.fixture(params=["ihp_sg13g2", "gf180mcu"])
def pdk_config(request):
    """Parametrized PDK fixture for multi-PDK tests.

    Skips automatically if the PDK is not installed.
    """
    name = request.param
    if name == "ihp_sg13g2":
        if not ihp_available:
            pytest.skip("IHP SG13G2 PDK not available")
        return IHP_SG13G2
    elif name == "gf180mcu":
        if not gf180_available:
            pytest.skip("GF180MCU PDK not available")
        return GF180MCU_D
    else:
        pytest.skip(f"Unknown PDK: {name}")
