"""Shared test fixtures for eda-agents."""

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


ihp_available = _pdk_available(IHP_SG13G2)
gf180_available = _pdk_available(GF180MCU_D)


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
