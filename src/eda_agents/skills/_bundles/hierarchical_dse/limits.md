# Hierarchical DSE -- limits, pitfalls, when to bail

The methodology buys you full-Pareto visibility cheaply, but the
shortcuts that make it cheap also bound where its numerical agreement
holds. Understand the limits before quoting a Pareto point as final.

LUT corner

Only TT-corner LUTs ship in `ihp-gmid-kit` and the GF180MCU cache
today. SSTADEX's symbolic Pareto is implicitly TT-typical. Validate
your kept point in SS / FF if the spec has any margin to tighten
under PVT:

  * The diffpair Vth at FF can drop ~50 mV vs TT on SG13G2, shifting
    the (vds, vgs) sweep grid and the per-W small-signal params.
    Critical regions of the Pareto frontier shift accordingly.
  * Mirror mismatch (Pelgrom area) is not in the model. The 1-stage
    OTA Pareto's `gain_1stage` does not include random offset at the
    output. If offset matters for your design, gate the Pareto by an
    analytical Pelgrom estimate or run a sigma-Vos test in ngspice.
  * Cgg / cap-related dynamics are derived from the LUT only at the
    operating point. Sweeping `s` for bandwidth (`out_def={"frec":
    ...}`) is supported but the parasitic capacitance to non-modelled
    routing (Cdb, Csb, interconnect) is missed. The notebook's
    bandwidth column should be read as "upper bound" rather than
    "expected silicon".

Small-signal assumption

Every spec evaluation assumes the operating point is fully in
saturation. The `propagated_conditions` width filter eliminates most
subthreshold sliver points, but at low `gm/ID` (high I_density) the
LUT can land in the linear region for short L; the symbolic gain is
still computed but does not reflect the device. If `gain_1stage` is
mysteriously low at the area-minimum corner of the Pareto, check the
operating-point `vds_V` value -- if it is below ~150 mV, you are in
the linear region.

Non-zero VBS

Both primitive subclasses fix `vbs=0`. The 1-stage OTA Pareto in
SSTADEX bottoms-out at `vs_diff ~ 0.2 V` for low-Vds operating
points; the model ignores the bulk modulation of Vth at that bias.
Real silicon would see a ~20 mV Vth uplift at `vs_diff = 0.5 V` on
SG13G2 NFETs. Build LUTs with non-zero Vbs slices (the LUT format
supports it; current ihp-gmid-kit only ships Vbs = 0 because every
shipped design biases the bulk at ground) if your design depends on
the body effect.

Composed-spec gotcha

`gm_1stage = gain_1stage / rout_1stage` is a composed spec with
`composed=1` and `out_def={"divide": [gain_test, rout_test]}`. It
relies on the underlying tests already being evaluated. If you
re-order `Macromodel.specifications`, the composed spec must come
after its constituents or `_evaluate_spec` will find missing columns
and silently return ones. Keep the composed specs at the tail of the
list.

When to bail and use SPICE

You should drop the hierarchical-DSE path entirely and just run
ngspice-in-the-loop (via `AutoresearchRunner`) when:

  * Spec has non-linear components: large-signal slew, settling, or
    swing-limited stability targets.
  * The block is dominated by parasitics outside the LUT's small-signal
    model (e.g. heavy interconnect on a Class-AB OTA tail).
  * The Pareto frontier shrinks to a single point after applying spec
    floors. That signals either an unsatisfiable spec or a missing
    degree of freedom in the macromodel; iterating in SPICE is faster
    than expanding the symbolic exploration in this regime.

For the canonical case where this skill is the right answer (1-stage
OTA, 2-stage OTA, current-mirror chains, simple LDOs on IHP SG13G2 or
GF180MCU), the validation gate is `examples/17_sstadex_pareto_ihp.py`:
the Pareto rows match ngspice gain measurements within 5 % across
multiple corner points on the front. Reproduce that gate before
trusting the Pareto numbers for a new design.
