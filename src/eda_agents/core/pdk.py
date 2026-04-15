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
from dataclasses import dataclass


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
    global_include_rel: str | None = None  # global params (e.g. design.ngspice)
    cap_lib_rel: str | None = None      # for cornerCAP.lib
    cap_corner: str | None = None
    mimcap_corner: str | None = None    # e.g. "mimcap_typical" in main model lib

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

    # ---- LibreLane digital flow ----------------------------------------
    librelane_pdk_name: str = ""            # "gf180mcuD" | "ihp-sg13g2"
    librelane_flow: str = "Classic"         # "Classic" | "Chip"
    stdcell_library: str = ""               # "gf180mcu_fd_sc_mcu7t5v0" | "sg13g2_stdcell"
    librelane_extra_flags: tuple[str, ...] = ()  # e.g. ("--manual-pdk",)

    # From-spec template defaults (PDK-specific)
    default_clock_period_ns: float = 50.0
    default_die_um: tuple[float, float] = (300.0, 300.0)
    default_density_pct: int = 65
    rt_max_layer: str = "Metal4"            # GF180 default; IHP = TopMetal2
    librelane_config_template: str = "gf180"  # key consumed by get_config_template()

    # ---- Gate-level simulation (GlSimRunner) ---------------------------
    # Glob (pdk_root-relative) matching the stdcell Verilog models the
    # runner hands to iverilog for post-synth and post-PnR GL sim. Both
    # built-in PDKs ship models with ``specify`` blocks, so SDF
    # annotation can anchor on top.
    stdcell_verilog_models_glob: str = ""
    # Reserved for PDKs that split behavioural and timing-aware models
    # across different directories. Unused today; kept so future PDKs
    # can override without a schema change.
    stdcell_verilog_timing_glob: str | None = None
    # STA corner name used to pick the default SDF file under
    # ``<run>/final/sdf/<corner>/`` for post-PnR GL sim.
    default_sta_corner: str = ""

    def has_osdi(self) -> bool:
        """Whether this PDK requires OSDI shared-library loading."""
        return bool(self.osdi_dir_rel and self.osdi_files)

    def global_include_path(self, pdk_root: str) -> str | None:
        """Full global include path, or None if not needed."""
        if self.global_include_rel:
            return f"{pdk_root}/{self.global_include_rel}"
        return None

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

    # LibreLane digital flow (PDK config at
    # ihp-sg13g2/libs.tech/librelane/config.tcl supplies RT layers + PDN)
    librelane_pdk_name="ihp-sg13g2",
    librelane_flow="Classic",
    stdcell_library="sg13g2_stdcell",
    # IHP-Open-PDK is manually managed (not shipped via Volare/Ciel),
    # so LibreLane needs --manual-pdk to skip PDK auto-management.
    librelane_extra_flags=("--manual-pdk",),
    default_clock_period_ns=10.0,   # 130nm comfortably handles 100 MHz
    default_die_um=(300.0, 300.0),
    default_density_pct=50,         # start conservative; tuneable per design
    rt_max_layer="TopMetal2",
    librelane_config_template="ihp_sg13g2",

    # Gate-level simulation: stdcell + UDP models live here. The glob
    # matches both sg13g2_stdcell.v and sg13g2_udp.v (the UDP file
    # defines flip-flop primitives the stdcells reference).
    stdcell_verilog_models_glob=(
        "ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/*.v"
    ),
    # LibreLane STAPostPNR writes SDFs under final/sdf/<corner>/ where
    # <corner> is nom_{process}_{voltage}V_{temperature}C. Typical
    # corner is `nom_typ_1p20V_25C` for 1.2 V / 25 C.
    default_sta_corner="nom_typ_1p20V_25C",
)

GF180MCU_D = PdkConfig(
    name="gf180mcu",
    display_name="GF180MCU 180nm CMOS",
    technology_nm=180,
    VDD=3.3,
    Lmin_m=280e-9,
    Wmin_m=220e-9,
    z1_m=380e-9,

    # wafer-space fork: design.ngspice defines global params, then .lib corners
    global_include_rel="gf180mcuD/libs.tech/ngspice/design.ngspice",
    model_lib_rel="gf180mcuD/libs.tech/ngspice/sm141064.ngspice",
    model_corner="typical",
    mimcap_corner="mimcap_typical",
    cap_lib_rel="gf180mcuD/libs.tech/ngspice/sm141064_mim.ngspice",
    cap_corner="cap_mim_new",

    nmos_symbol="nfet_03v3",
    pmos_symbol="pfet_03v3",
    instance_prefix="X",  # subcircuit-based models

    osdi_dir_rel=None,
    osdi_files=(),

    mim_cap_model="cap_mim_1f5_m2m3_noshield",
    mim_cap_density_fF_um2=1.5,

    AVT_nmos_Vum=5.0e-3,   # placeholder -- extract from PDK mismatch data
    AVT_pmos_Vum=5.0e-3,

    lut_dir_default="data/gmid_luts",  # relative to eda-agents root
    lut_nmos_file="gf180_nfet_03v3.npz",
    lut_pmos_file="gf180_pfet_03v3.npz",
    lut_model_key_nmos="nfet_03v3",
    lut_model_key_pmos="pfet_03v3",

    vgs_max=3.3,
    vds_max=3.3,
    vbs_max=3.3,

    default_pdk_root="/home/montanares/git/wafer-space-gf180mcu",

    # LibreLane digital flow
    librelane_pdk_name="gf180mcuD",
    librelane_flow="Classic",
    stdcell_library="gf180mcu_fd_sc_mcu7t5v0",
    librelane_extra_flags=(),
    default_clock_period_ns=50.0,   # 180nm, 20 MHz conservative default
    default_die_um=(300.0, 300.0),
    default_density_pct=65,
    rt_max_layer="Metal4",
    librelane_config_template="gf180",

    # Gate-level simulation: stdcell Verilog + UDP primitives. Glob
    # matches both gf180mcu_fd_sc_mcu7t5v0.v and primitives.v.
    stdcell_verilog_models_glob=(
        "gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/*.v"
    ),
    # Typical corner for 3v3 flow. Confirmed against the stdcell lib
    # directory (tt_025C_3v30.lib). Actual SDF directory naming is
    # confirmed when a real GF180 LibreLane run lands; the runner
    # falls back to the first SDF found if this exact name is missing.
    default_sta_corner="nom_tt_025C_3v30",
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


def netlist_lib_lines(pdk: PdkConfig) -> list[str]:
    """Build SPICE library include lines for a PDK.

    Returns a list of SPICE directives (.include / .lib) that should
    appear at the top of generated netlists. Uses $PDK_ROOT for
    portability.
    """
    lines: list[str] = []

    # Global include (e.g., GF180 design.ngspice with global params)
    if pdk.global_include_rel:
        lines.append(f".include $PDK_ROOT/{pdk.global_include_rel}")

    # Main model library
    if pdk.model_corner:
        lines.append(f".lib $PDK_ROOT/{pdk.model_lib_rel} {pdk.model_corner}")
    else:
        lines.append(f".include $PDK_ROOT/{pdk.model_lib_rel}")

    # MIM capacitor corner parameters (separate section in main model lib)
    # When mimcap_corner is set, it internally loads the cap model lib,
    # so we skip the separate cap_lib_rel include to avoid double-loading.
    if pdk.mimcap_corner:
        lines.append(f".lib $PDK_ROOT/{pdk.model_lib_rel} {pdk.mimcap_corner}")
    elif pdk.cap_lib_rel:
        if pdk.cap_corner:
            lines.append(f".lib $PDK_ROOT/{pdk.cap_lib_rel} {pdk.cap_corner}")
        else:
            lines.append(f".include $PDK_ROOT/{pdk.cap_lib_rel}")

    return lines


def netlist_osdi_lines(pdk: PdkConfig) -> list[str]:
    """Build OSDI load directives for a PDK.

    Returns lines for the .control block. Empty list for BSIM4 PDKs.
    """
    if not pdk.has_osdi():
        return []
    osdi_base = f"$PDK_ROOT/{pdk.osdi_dir_rel}"
    return [f"  osdi '{osdi_base}/{f}'" for f in pdk.osdi_files]


def resolve_pdk_root(pdk: PdkConfig, explicit_root: str | None = None) -> str:
    """Resolve the PDK root directory.

    Resolution order:
        1. Explicit argument
        2. PDK_ROOT env var (only if it contains this PDK's model files)
        3. pdk.default_pdk_root
        4. Raise ValueError
    """
    if explicit_root:
        return explicit_root
    env_root = os.environ.get("PDK_ROOT")
    if env_root:
        model_path = os.path.join(env_root, pdk.model_lib_rel)
        if os.path.isfile(model_path):
            return env_root
    if pdk.default_pdk_root:
        return pdk.default_pdk_root
    raise ValueError(
        f"No PDK_ROOT found for '{pdk.name}'. "
        "Set PDK_ROOT environment variable or pass pdk_root explicitly."
    )
