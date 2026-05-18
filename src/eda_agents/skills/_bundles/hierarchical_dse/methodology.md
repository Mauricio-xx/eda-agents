# Hierarchical Design-Space Exploration (SSTADEX methodology)

Hierarchical DSE attacks an analog block by composing a few well-characterised
primitives (differential pair, current mirror, current source, common source)
into a macromodel, deriving the small-signal transfer function symbolically,
and then sweeping the LUT-driven primitive operating points plus a small set
of macromodel-level parameters to harvest a Pareto frontier of valid
configurations. It is the cleanest fit when:

  * The block's small-signal behaviour is well-described by a closed-form
    network of canonical primitives (most OTAs, LDOs, basic comparators,
    bias generators). Blocks with strongly non-linear or event-driven
    behaviour (StrongARM latches, DLLs, bootstrap switches) do not fit.
  * You want to inspect the full design space rather than greedily search
    for a single point. The output is a DataFrame of sized configurations
    along the Pareto front of `(area, gain)` or any user-chosen axes.
  * You need numerical agreement with ngspice without paying the per-iteration
    SPICE cost during exploration. The LUT-driven evaluation typically
    matches transistor-level simulation within a few percent on `Av_dc`,
    `Rout`, and bias current (the SSTADEX paper validates within 0.3 dB
    on gain across a six-point Pareto subset).

When to reach for this skill versus plain `analog.gmid_sizing` or
`analog.miller_ota_design`: gm/ID sizing gives you one operating point
per call; the Miller skill bundles the rules for a known two-stage
topology. Hierarchical DSE is the right answer when the search shape is
"give me the family of `(area, gain)` tradeoffs for this block on this
PDK" and you want to keep all viable points for downstream ranking.

The eda-agents port

The schema lives at `eda_agents.topologies.sstadex` and mirrors the
upstream user-facing surface (Library / Primitive / Macromodel /
Testbench / dfs). The deviations from upstream are deliberate and
limited to:

  * The LUT engine is `GmIdLookup` (PSP103 .npz from `ihp-gmid-kit` for
    IHP SG13G2, downloaded cache for GF180MCU). Upstream uses mosplot's
    `Transistor` class; we skip the dependency because the LUT data
    already lives in the same .npz files.
  * The symbolic transfer function is built by an in-house sympy MNA
    module (`symbolic_mna.py`). Upstream uses XSCHEM to author a
    schematic, exports a SPICE netlist, then feeds it to a separate
    `Symbolic-modified-nodal-analysis` Python package. We compose the
    small-signal network directly from `Macromodel.instances` plus
    `Testbench.elements`, which is far cleaner and works identically on
    IHP and GF180.

The methodology itself is unchanged: characterize primitives off the
LUT at the requested port voltages, propagate node-voltage constraints
through `shared_nodes`, evaluate every spec's symbolic TF on the
Cartesian product of primitive operating points and macromodel
parameter sweeps, apply spec floors / propagated conditions, then
filter to the Pareto front via `paretoset`.

Typical Pareto axes

For a 1-stage OTA the natural axes are
`(min area, max gain_1stage)`. Add `(min I_total)` for a low-power
exploration, or `(max bandwidth)` for a high-speed target. Append
extra spec values via `Macromodel.opt_specifications` and the explorer
will widen the Pareto correctly.

Hard pitfalls

  1. Forgetting `shared_nodes`. If the diff-pair tail and the current
     source share a bias node, the upstream pattern is
     `shared_nodes={"IBIAS_node": ["vs_diff", "vs_cs"]}` so the
     Cartesian product collapses to rows where both primitives are
     biased at the same tail voltage. Without this filter, the
     resulting Pareto contains thousands of incoherent points where
     the diff pair and the current source disagree on the operating
     voltage at the same physical net.
  2. Missing `parameter_map` entries. The Testbench needs every
     "matched" pair (`g_gm_xdp_m2 -> g_gm_xdp_m1`, etc.) plus the
     source values (`V_p = 1, V_n = 0, Vdd = 0`) and `s = 0` for DC
     analysis. Forgetting `s = 0` leaves a frequency-dependent TF that
     `np.abs(...)` will silently coerce to 0 in the magnitude path.
  3. Width / length filters. The Pareto search will happily include
     subthreshold points where `W` blows up to mm-scale. Set the
     `propagated_conditions` to clamp widths to `[1 um, 1000 um]`
     before believing any "huge gain" Pareto endpoint.
