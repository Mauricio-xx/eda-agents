"""LibreLane config templates for from-spec design generation.

One YAML template per supported PDK. Only four design-level fields need
filling in by the spec-to-RTL prompt: DESIGN_NAME, VERILOG_FILES,
CLOCK_PORT, CLOCK_PERIOD (plus die dimensions). PDK-level defaults
(RT layers, PDN straps, stdcell lib paths) come from the PDK's own
`libs.tech/librelane/config.tcl` and are not restated here.

Templates here are **infrastructure**, not design knobs: they are
informed by upstream LibreLane project templates and are not subject to
autoresearch tuning. Designs plug *into* the template.

Storage: each template lives as a ``{name}.yaml.tmpl`` file inside
the ``eda_agents.agents.templates`` package and is loaded at import
time via ``importlib.resources``. The upstream project templates that
inspired them are vendored as git submodules under ``external/`` and
a parity-check script (``scripts/check_librelane_template_upstream.py``)
flags drift on curated fields. See ``docs/librelane_templates.md`` for
the full workflow.
"""

from __future__ import annotations

from importlib.resources import files as _files


def _load_template(name: str) -> str:
    """Read a .yaml.tmpl file from the templates package as a string."""
    return (
        _files("eda_agents.agents.templates") / f"{name}.yaml.tmpl"
    ).read_text(encoding="utf-8")


GF180_CONFIG_TEMPLATE = _load_template("gf180")

GF180_DEFAULTS = {
    "clock_period": 50,       # ns (conservative, 20 MHz)
    "die_width": 300.0,       # um (small design default)
    "die_height": 300.0,      # um
    "clock_port": "clk",
}


# IHP SG13G2 Classic-flow template. PDK-level defaults (RT_MIN_LAYER,
# RT_MAX_LAYER, PDN_{V,H}{WIDTH,SPACING,PITCH,OFFSET}, LAYERS_RC, VIAS_R,
# FP_IO_{H,V}LAYER, STA_CORNERS, DEFAULT_CORNER) are set by
# /ihp-sg13g2/libs.tech/librelane/config.tcl and do not need to be
# repeated here. Upstream project template (reference only, vendored as
# a submodule at external/ihp-sg13g2-librelane-template):
#   https://github.com/IHP-GmbH/ihp-sg13g2-librelane-template
IHP_SG13G2_CONFIG_TEMPLATE = _load_template("ihp_sg13g2")

IHP_SG13G2_DEFAULTS = {
    "clock_period": 10,       # ns (130nm, 100 MHz default)
    "die_width": 300.0,       # um
    "die_height": 300.0,      # um
    "clock_port": "clk",
}


_TEMPLATES = {
    "gf180": (GF180_CONFIG_TEMPLATE, GF180_DEFAULTS),
    "ihp_sg13g2": (IHP_SG13G2_CONFIG_TEMPLATE, IHP_SG13G2_DEFAULTS),
}


def get_config_template(pdk_config) -> tuple[str, dict]:
    """Return (template_string, defaults_dict) for the given PdkConfig.

    Selector key comes from pdk_config.librelane_config_template.
    Raises KeyError if the key is unknown.
    """
    key = pdk_config.librelane_config_template
    if key not in _TEMPLATES:
        available = ", ".join(sorted(_TEMPLATES))
        raise KeyError(
            f"No LibreLane config template for '{key}'. Available: {available}"
        )
    return _TEMPLATES[key]
