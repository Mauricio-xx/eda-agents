"""PDK configuration abstraction layer.

Decouples eda-agents from any specific PDK by centralizing all
process-dependent constants (voltages, device names, model paths,
LUT files) in a frozen dataclass.

Two built-in configurations:
    IHP_SG13G2   -- IHP 130nm BiCMOS (PSP103/OSDI models)
    GF180MCU_D   -- GlobalFoundries 180nm CMOS (BSIM4 models, wafer-space fork)

Users can register additional PDKs via register_pdk() and select
the active PDK via the EDA_AGENTS_PDK environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PdkConfig:
    """Immutable PDK configuration.

    All paths are relative to pdk_root (resolved at runtime from
    PDK_ROOT env var or explicit argument).
    """

    # Identity
    name: str                           # "ihp_sg13g2", "gf180mcu"
    display_name: str                   # "IHP SG13G2 130nm BiCMOS"
    technology_nm: int                  # 130, 180

    # Electrical
    VDD: float                          # 1.2, 1.8
    Lmin_m: float                       # 130e-9, 180e-9
    Wmin_m: float                       # 150e-9, 220e-9
    z1_m: float                         # junction depth: 340e-9, ~400e-9

    # SPICE model paths (relative to pdk_root)
    model_lib_rel: str                  # "ihp-sg13g2/.../cornerMOSlv.lib"
    model_corner: str                   # "mos_tt"
    cap_lib_rel: str | None = None      # for cornerCAP.lib
    cap_corner: str | None = None

    # Device names (subcircuit or model names used in netlists)
    nmos_symbol: str = ""               # "sg13_lv_nmos", "nfet_01v8"
    pmos_symbol: str = ""               # "sg13_lv_pmos", "pfet_01v8"

    # Instance prefix: "X" for subcircuit-based PDKs, "M" for inline models
    instance_prefix: str = "X"

    # OSDI shared libraries (empty tuple for BSIM4 PDKs like GF180)
    osdi_dir_rel: str | None = None
    osdi_files: tuple[str, ...] = ()

    # Capacitor model
    mim_cap_model: str | None = None    # "cap_cmim", "cap_mim_1f5fF"
    mim_cap_density_fF_um2: float = 1.5

    # Pelgrom mismatch coefficients (V*um)
    AVT_nmos_Vum: float = 0.0
    AVT_pmos_Vum: float = 0.0

    # gm/ID LUT configuration
    lut_dir_default: str = ""           # absolute fallback path for LUT data
    lut_nmos_file: str = ""
    lut_pmos_file: str = ""
    lut_model_key_nmos: str = ""
    lut_model_key_pmos: str = ""

    # mosplot sweep config for LUT generation
    vgs_max: float = 1.5
    vds_max: float = 1.5
    vbs_max: float = 1.2

    # Default PDK_ROOT fallback (absolute path)
    default_pdk_root: str = ""

    def has_osdi(self) -> bool:
        """Whether this PDK requires OSDI shared-library loading."""
        return bool(self.osdi_dir_rel and self.osdi_files)

    def model_lib_path(self, pdk_root: str) -> str:
        """Full model library path for a given pdk_root."""
        return f"{pdk_root}/{self.model_lib_rel}"

    def osdi_dir_path(self, pdk_root: str) -> str | None:
        """Full OSDI directory path, or None if not applicable."""
        if self.osdi_dir_rel:
            return f"{pdk_root}/{self.osdi_dir_rel}"
        return None

    def cap_lib_path(self, pdk_root: str) -> str | None:
        """Full capacitor library path, or None."""
        if self.cap_lib_rel:
            return f"{pdk_root}/{self.cap_lib_rel}"
        return None


# ---------------------------------------------------------------------------
# Built-in PDK configurations
# ---------------------------------------------------------------------------

IHP_SG13G2 = PdkConfig(
    name="ihp_sg13g2",
    display_name="IHP SG13G2 130nm BiCMOS",
    technology_nm=130,
    VDD=1.2,
    Lmin_m=130e-9,
    Wmin_m=150e-9,
    z1_m=340e-9,

    model_lib_rel="ihp-sg13g2/libs.tech/ngspice/models/cornerMOSlv.lib",
    model_corner="mos_tt",
    cap_lib_rel="ihp-sg13g2/libs.tech/ngspice/models/cornerCAP.lib",
    cap_corner="cap_typ",

    nmos_symbol="sg13_lv_nmos",
    pmos_symbol="sg13_lv_pmos",
    instance_prefix="X",

    osdi_dir_rel="ihp-sg13g2/libs.tech/ngspice/osdi",
    osdi_files=("psp103_nqs.osdi", "r3_cmc.osdi", "mosvar.osdi"),

    mim_cap_model="cap_cmim",
    mim_cap_density_fF_um2=1.5,

    AVT_nmos_Vum=3.9e-3,
    AVT_pmos_Vum=2.2e-3,

    lut_dir_default="/home/montanares/personal_exp/ihp-gmid-kit/data",
    lut_nmos_file="sg13_lv_nmos.npz",
    lut_pmos_file="sg13_lv_pmos.npz",
    lut_model_key_nmos="sg13_lv_nmos",
    lut_model_key_pmos="sg13_lv_pmos",

    vgs_max=1.5,
    vds_max=1.5,
    vbs_max=1.2,

    default_pdk_root="/home/montanares/git/IHP-Open-PDK",
)

GF180MCU_D = PdkConfig(
    name="gf180mcu",
    display_name="GF180MCU 180nm CMOS",
    technology_nm=180,
    VDD=1.8,
    Lmin_m=180e-9,
    Wmin_m=220e-9,
    z1_m=400e-9,

    model_lib_rel="gf180mcuD/libs.tech/ngspice/design.ngspice",
    model_corner="",  # GF180 uses .include, not .lib corner
    cap_lib_rel=None,  # caps included in main model file
    cap_corner=None,

    nmos_symbol="nfet_01v8",
    pmos_symbol="pfet_01v8",
    instance_prefix="X",  # GF180 also uses subcircuit-based models

    osdi_dir_rel=None,
    osdi_files=(),

    mim_cap_model="cap_mim_1f5fF",
    mim_cap_density_fF_um2=1.5,

    AVT_nmos_Vum=5.0e-3,   # placeholder -- extract from PDK mismatch data
    AVT_pmos_Vum=5.0e-3,

    lut_dir_default="",  # set after LUT generation
    lut_nmos_file="gf180_nfet_01v8.npz",
    lut_pmos_file="gf180_pfet_01v8.npz",
    lut_model_key_nmos="nfet_01v8",
    lut_model_key_pmos="pfet_01v8",

    vgs_max=1.8,
    vds_max=1.8,
    vbs_max=1.8,

    default_pdk_root="",  # no default -- must be set by user
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PDK_REGISTRY: dict[str, PdkConfig] = {
    "ihp_sg13g2": IHP_SG13G2,
    "gf180mcu": GF180MCU_D,
}


def register_pdk(config: PdkConfig) -> None:
    """Register a custom PDK configuration."""
    _PDK_REGISTRY[config.name] = config


def get_pdk(name: str) -> PdkConfig:
    """Retrieve a registered PDK by name.

    Raises KeyError if name is not registered.
    """
    if name not in _PDK_REGISTRY:
        available = ", ".join(sorted(_PDK_REGISTRY))
        raise KeyError(f"Unknown PDK '{name}'. Available: {available}")
    return _PDK_REGISTRY[name]


def list_pdks() -> list[str]:
    """Return names of all registered PDKs."""
    return sorted(_PDK_REGISTRY)


def resolve_pdk(pdk: PdkConfig | str | None = None) -> PdkConfig:
    """Resolve a PDK config from argument, env var, or default.

    Resolution order:
        1. Explicit PdkConfig instance (pass-through)
        2. String name -> registry lookup
        3. EDA_AGENTS_PDK env var -> registry lookup
        4. Default: IHP_SG13G2
    """
    if isinstance(pdk, PdkConfig):
        return pdk
    if isinstance(pdk, str):
        return get_pdk(pdk)
    env_name = os.environ.get("EDA_AGENTS_PDK")
    if env_name:
        return get_pdk(env_name)
    return IHP_SG13G2


def resolve_pdk_root(pdk: PdkConfig, explicit_root: str | None = None) -> str:
    """Resolve the PDK root directory.

    Resolution order:
        1. Explicit argument
        2. PDK_ROOT env var
        3. pdk.default_pdk_root
        4. Raise ValueError
    """
    if explicit_root:
        return explicit_root
    env_root = os.environ.get("PDK_ROOT")
    if env_root:
        return env_root
    if pdk.default_pdk_root:
        return pdk.default_pdk_root
    raise ValueError(
        f"No PDK_ROOT found for '{pdk.name}'. "
        "Set PDK_ROOT environment variable or pass pdk_root explicitly."
    )
