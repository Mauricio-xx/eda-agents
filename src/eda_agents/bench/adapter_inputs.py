"""Typed ``BenchTask.inputs`` schemas per adapter.

Closes gap #11. The wire format on disk
(``bench/tasks/**/*.yaml::inputs``) is still free-form YAML — this
module layers Pydantic v2 validation on top so each adapter fails
loudly on typos (e.g. ``design_paramms`` or ``N_sample``) instead of
silently using defaults.

Each adapter parses its sub-section of ``task.inputs`` through the
matching model at the top of its helper. On validation error, the
adapter returns :class:`BenchStatus.FAIL_INFRA` with the Pydantic
message so the YAML author sees exactly which key misbehaved.

Design notes:

* ``extra="forbid"`` everywhere — typos are the whole point.
* Callable-routed models include ``callable: str`` as a pass-through so
  the schema is symmetrical with the on-disk document.
* Optional fields use ``None`` defaults; adapters apply their own
  business defaults after parsing (keeps the schema a pure data shape).
* Kept deliberately small — one model per adapter, no inheritance
  hierarchy. If two adapters grow a common field, extract then.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Common ConfigDict for every inputs model. Frozen so adapters cannot
# accidentally mutate the parsed record; extra forbidden to catch typos.
_COMMON_CFG = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------


class DryRunInputs(BaseModel):
    """Inputs for ``dry_run_adapter``. Both fields optional."""

    model_config = _COMMON_CFG

    fake_metrics: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# analog_roles
# ---------------------------------------------------------------------------


class AnalogRolesInputs(BaseModel):
    """Inputs for ``analog_roles_adapter``."""

    model_config = _COMMON_CFG

    spec_yaml: str = Field(
        ...,
        description="Inline SpecYaml document consumed by load_spec_from_string.",
    )
    max_iterations: int = Field(default=3, ge=1, le=10)


# ---------------------------------------------------------------------------
# callable-routed helpers
# ---------------------------------------------------------------------------


class MillerDesignParams(BaseModel):
    """Inner ``design_params`` block for the analytical Miller designer."""

    model_config = _COMMON_CFG

    gmid_input: float = Field(..., gt=0.0, le=40.0)
    gmid_load: float = Field(..., gt=0.0, le=40.0)
    L_input: float = Field(..., gt=0.0, le=1e-5)
    L_load: float = Field(..., gt=0.0, le=1e-5)
    Cc: float = Field(..., gt=0.0, le=1e-9)
    Ibias: float | None = Field(default=None, gt=0.0, le=1e-3)


class AnalyticalMillerInputs(BaseModel):
    """Inputs for ``analytical_miller_design``."""

    model_config = _COMMON_CFG

    callable: str  # namespace-checked by resolve_callable upstream
    design_params: MillerDesignParams


class PreSimGateInputs(BaseModel):
    """Inputs for ``run_pre_sim_gate_on_inline_netlist``."""

    model_config = _COMMON_CFG

    callable: str
    gate: str = Field(
        ...,
        description=(
            "Name of the gate in check functions: floating_nodes, "
            "bulk_connections, mirror_ratio, bias_source, vds_polarity."
        ),
    )
    subckt: str
    expect_violation: bool = False
    netlist: str = Field(..., min_length=1)

    @field_validator("gate")
    @classmethod
    def _known_gate(cls, v: str) -> str:
        allowed = {
            "floating_nodes",
            "bulk_connections",
            "mirror_ratio",
            "bias_source",
            "vds_polarity",
        }
        if v not in allowed:
            raise ValueError(
                f"unknown pre-sim gate {v!r}; allowed: {sorted(allowed)}"
            )
        return v


class GlSimPostSynthInputs(BaseModel):
    """Inputs for ``run_gl_sim_post_synth``.

    ``run_dir`` may be absent; the adapter falls back to
    ``EDA_AGENTS_GL_SIM_RUN_DIR`` or, when gap #5 lands, the counter
    cache under ``bench/cache/librelane_runs/counter``.
    """

    model_config = _COMMON_CFG

    callable: str
    run_dir: str | None = None


class Sar11bEnobInputs(BaseModel):
    """Inputs for ``run_sar11_enob_measurement`` (gap #6)."""

    model_config = _COMMON_CFG

    callable: str
    N_samples: int = Field(default=128, ge=32, le=8192)
    Fs_Hz: float = Field(default=1.0e6, gt=0.0)
    # Coherent Fin is derived from N_samples/Fs ratio if None.
    Fin_Hz: float | None = Field(default=None, gt=0.0)
    topology_params: dict[str, Any] = Field(default_factory=dict)


class DigitalFlowInputs(BaseModel):
    """Inputs for ``run_librelane_flow_task`` (gap #5)."""

    model_config = _COMMON_CFG

    callable: str
    design_dir: str = Field(
        ...,
        description="Path to a LibreLane project directory holding config.yaml + rtl/.",
    )
    stop_after: str = Field(
        default="Checker.KLayoutDRC",
        description=(
            "LibreLane step ID where the flow stops. Defaults to "
            "Checker.KLayoutDRC so the run produces signoff .lyrdb "
            "files for the DRC audit. Full Classic steps list: "
            "https://librelane.readthedocs.io/"
        ),
    )
    cache_run_dir: bool = True


class DigitalAutoresearchInputs(BaseModel):
    """Inputs for the real ``digital_autoresearch_adapter`` (gap #4)."""

    model_config = _COMMON_CFG

    design_dir: str | None = None
    budget: int = Field(default=2, ge=1, le=10)
    mock_metrics_path: str | None = None
    # S10h: select a specific DigitalDesign subclass instead of the
    # default GenericDesign wrapper. Needed for designs that carry a
    # nix-shell wrapper or custom prompt metadata (e.g.
    # ``fazyrv_hachure`` on GF180MCU). When unset the adapter falls
    # back to ``GenericDesign(config_path=...)``.
    design_class: str | None = Field(
        default=None,
        description=(
            "Optional DigitalDesign subclass name. Supported: "
            "'fazyrv_hachure'. When provided, the adapter instantiates "
            "that class with design-specific defaults instead of "
            "GenericDesign."
        ),
    )


class LlmSpecToSizingInputs(BaseModel):
    """Inputs for ``llm_spec_to_sizing_adapter`` (gap #8)."""

    model_config = _COMMON_CFG

    callable: str
    spec_yaml: str = Field(..., min_length=1)
    model: str = Field(
        default="google/gemini-2.5-flash",
        description=(
            "OpenRouter model id. Gemini Flash is the default per "
            "persistent memory (feedback_openrouter_model.md); the "
            "`openrouter/` prefix is stripped before the API call."
        ),
    )
    max_tokens: int = Field(default=1024, ge=64, le=16384)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    pdk: str | None = None  # resolves via EDA_AGENTS_PDK when None


__all__ = [
    "AnalogRolesInputs",
    "AnalyticalMillerInputs",
    "DigitalAutoresearchInputs",
    "DigitalFlowInputs",
    "DryRunInputs",
    "GlSimPostSynthInputs",
    "LlmSpecToSizingInputs",
    "MillerDesignParams",
    "PreSimGateInputs",
    "Sar11bEnobInputs",
]
