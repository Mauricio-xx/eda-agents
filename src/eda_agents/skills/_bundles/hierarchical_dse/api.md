# Hierarchical DSE API -- eda_agents.topologies.sstadex

Imports

```python
from sympy import Symbol
from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.topologies.sstadex import (
    Library, Macromodel, Testbench, dfs,
    VoltageSource, CurrentSource, Resistor, Capacitor,
)
from eda_agents.agents.hierarchical_dse_runner import HierarchicalDseRunner
```

End-to-end sequence

The canonical pattern below sizes a 1-stage OTA on IHP SG13G2 with
`I_amp = 20 uA`, sweeps the diff-pair tail voltage and the L of every
device, and persists a Pareto frontier to disk.

```python
lut = GmIdLookup(pdk="ihp_sg13g2")
lib = Library(name="ihp_sg13g2", lut=lut)

# 1. Get + bias primitives. Port voltages can be scalar OR arrays --
#    arrays are interpreted as sweep axes (outer product against any
#    other arrays you set).
diffpair = lib.get("simplediffpair", il=20e-6)
diffpair.set_port_voltages({
    "VINP": 0.9, "VINN": 0.9, "VOUTP": 1.0, "VOUTN": 1.0,
    "VTAIL": np.linspace(0.2, 0.9, 10),
})
df_dp = diffpair.build(lut)
diffpair.outputs = {
    Symbol("W_diff"): df_dp["width"].values,
    Symbol("L_diff"): df_dp["length"].values,
}
diffpair.interface_variables = {"vs_diff": np.tile(vs, len(lengths))}

# (repeat for currentmirror, currentsource ...)

# 2. Assemble the macromodel.
ota = Macromodel(
    name="OTA_1stage_macro",
    ports=["VINP", "VINN", "VOUT", "VDD", "IBIAS", "Vbias", "VSS"],
    outputs=[Symbol("W_diff"), Symbol("L_diff"),
             Symbol("W_al"), Symbol("L_al")],
    interface_variables=["vs_diff"],
    shared_nodes={"IBIAS_node": ["vs_diff", "vs_cs"]},
)
ota.add_instance("xdp", diffpair, {
    "VINP": "VINP", "VINN": "VINN",
    "VOUTP": "VOUT", "VOUTN": "N1", "VTAIL": "IBIAS",
})
# ... add the other instances (xcm, xcs_macro) ...

# 3. Bind testbench(es).
tb_gain = Testbench(
    name="ota_1stage_gain", dut=ota,
    elements=[
        VoltageSource("Vdd", "VDD", "VSS", 0),
        VoltageSource("V_n", "VINN", "VSS", 0),
        VoltageSource("V_p", "VINP", "VSS", Symbol("V_p")),
    ],
    tf=("VOUT", "VINP"),
    parameter_map={
        Symbol("V_p"): 1,
        Symbol("g_gm_xdp_m2"): Symbol("g_gm_xdp_m1"),
        Symbol("R_gds_xdp_m2"): Symbol("R_gds_xdp_m1"),
        # ... matched-pair identities ...
        Symbol("s"): 0,  # DC analysis
    },
)
ota.specifications = [tb_gain.make_test(
    name="gain_1stage", opt_goal="max",
    conditions={"min": [1e-5]},
)]
ota.opt_specifications = ota.specifications

# 4. Filter pre-Pareto (widths in a sensible range).
ota.propagated_conditions = {"direct": [
    {"kind": "range", "column": Symbol("W_diff"),
     "condition": {"min": 1e-6, "max": 1000e-6}},
    {"kind": "range", "column": Symbol("W_al"),
     "condition": {"min": 1e-6, "max": 1000e-6}},
], "derived": []}
ota.run_pareto = True

# 5. Either call dfs() directly...
result = dfs(ota, lut)
print(len(result.masked_df.index), "Pareto points")

# 5b. ...or wrap with HierarchicalDseRunner for persistence + the
#     optional LLM-in-the-loop knob proposal mode.
runner = HierarchicalDseRunner(
    macromodel_builder=lambda lut, **knobs: ota,  # closure or factory
    lut=lut,
    knob_defaults={"I_amp": 20e-6},
)
res = runner.run(work_dir=Path("./run_1stage_ota"))
```

Outputs after `runner.run(...)`

  * `program.md`   -- machine-written description of the macromodel,
    specs, and Pareto stats. Same persistence layer
    AutoresearchRunner uses.
  * `results.tsv`  -- tab-separated. One row per Pareto point. Columns
    include `configuration_id`, `row_id`, primitive small-signal
    parameters (`g_gm_xdp_m1`, `R_gds_xdp_m1`, ...), sizing outputs
    (`W_diff`, `L_diff`, ...), spec values (`gain_1stage`, ...) and
    `area`.
  * `pareto.csv`  -- comma-separated mirror of the same Pareto for
    tools that do not consume TSV.

`HierarchicalDseRunner` constructor knobs

  * `macromodel_builder(lut, **knobs) -> Macromodel`. The runner calls
    this fresh on every iteration; it must produce a fully wired-up
    macromodel ready for `dfs(macromodel, lut)`. Closures over outer
    state are fine.
  * `knob_defaults`. Values for `run()` single-shot mode. Forwarded
    to the builder as kwargs.
  * `knob_ranges`. Required for `run_greedy(work_dir, budget)`. Maps
    knob name to `(low, high)` interval. Used to clamp LLM proposals
    and as the prompt's knob spec.
  * `fom_fn(pareto_df, macromodel) -> float`. Optional. Default picks
    the maximum of the first `opt_specifications` spec.
  * `backend`. `"litellm"` or `"cc_cli"`. Only consulted by
    `run_greedy`.

Reading the Pareto

Common Pareto inspections from the result TSV:

```python
import pandas as pd
df = pd.read_csv(res.results_tsv, sep="\t")

# Best gain on the Pareto front.
best_gain = df.sort_values("gain_1stage", ascending=False).iloc[0]
print(best_gain[["gain_1stage", "area", "W_diff", "L_diff",
                 "W_al", "L_al"]])

# Smallest-area sized configuration.
smallest = df.sort_values("area").iloc[0]
print(smallest[["gain_1stage", "area"]])

# Configurations within 1 dB of the gain ceiling.
gain_db = 20 * np.log10(df["gain_1stage"])
top_band = df[gain_db >= gain_db.max() - 1.0]
```

Cross-validation with ngspice

`examples/17_sstadex_pareto_ihp.py` shows the full loop. The Pareto
DataFrame's `(W, L, ng)` columns map directly to ngspice deck
parameters; the example takes three Pareto corners (smallest area,
middle, highest gain) and re-simulates them through `SpiceRunner` to
confirm symbolic agreement within 5 %. Use this every time you port
to a new PDK or update the LUT.
