# Miller OTA on GF180MCU — analytical designer uses IHP process params

## Status

Open. Documented during Sesión 9. **Not** an upstream PDK bug — this
is an in-tree topology limitation. The blocker file lives under
`docs/upstream_issues/` so it sits next to the IHP Magic / KLayout LVS
disclosures and shows up in the same review pass.

## Symptom

```
$ examples/14_bridge_e2e.py --pdk gf180mcu
Error on line 1573 or its substitute:
  m.x1a.m0 n1 inp ns ns nfet_03v3 w=1.7059e-7 l=1e-6 ...
could not find a valid modelname
    Simulation interrupted due to error!
```

The bench surfaces the same failure as `FAIL_SIM` for
`spec_miller_ota_gf180_easy`
(`bench/tasks/spec-to-topology/miller_ota_gf180_easy.yaml`).

## Root cause

`MillerOTADesigner` (`src/eda_agents/topologies/miller_ota.py:371`)
takes a `PdkConfig` argument but only uses it for transistor *symbol
names* (`nfet_03v3` / `pfet_03v3`) and library include paths. The
analytical sizing math runs against the frozen `ProcessParams`
dataclass at `src/eda_agents/topologies/miller_ota.py:104`, whose
defaults are:

| field | default | meaning |
|---|---|---|
| `Lmin` | `130e-9` | IHP SG13G2 130 nm minimum drawn length |
| `Wmin` | `150e-9` | IHP SG13G2 150 nm minimum drawn width |
| `Ispecsqn`, `Ispecsqp` | IHP-extracted | sEKV specific currents |
| `lambdan`, `lambdap` | IHP-extracted | channel-length modulation |
| `AVTn`, `AVTp` | IHP-extracted | Pelgrom mismatch |

When the GF180 PDK is selected the designer still computes against the
IHP numbers, producing `W = 170 nm` for the input pair. GF180MCU's
`nfet_03v3` subcircuit binning requires `W >= 220 nm`, so ngspice's
BSIM4 binner cannot pick a model and aborts with
"could not find a valid modelname" on the inner `m0` of the `nfet_03v3`
subckt.

## Why the symptom is misleading

ngspice prints the failure against the **inner BSIM4 device** of the
GF180 subcircuit (`m.x1a.m0 ... nfet_03v3 ...`), which led S8 to
suspect the `.lib sm141064.ngspice typical` selection or the
`set ngbehavior=hsa` toggle. Both are correct. The deck loads cleanly
on every other GF180 example — the fault is entirely upstream in the
sizing layer.

## Fix path (out of scope for S9)

A proper port of `MillerOTADesigner` to GF180 needs:

1. A GF180 sEKV process-parameter extraction (analogous to IHP's
   `ihp130g2_sekv.py`). At minimum: `Lmin`, `Wmin`, `tox`, `Ispecsqn/p`,
   `lambdan/p`, `AVTn/p`.
2. A `ProcessParams` registry (or a `pdk_to_process_params(pdk)`
   resolver) so `MillerOTADesigner.__init__` picks the correct set
   from the active PDK.
3. Verification against measured curves or against the GF180 gm/ID
   LUTs that already ship under `data/gmid_luts/`.

Until that lands, GF180-targeted Miller OTA tasks must be marked as
`FAIL_SIM` regression detectors (their behavior today). The bench
intentionally keeps `spec_miller_ota_gf180_easy` in the suite so any
future fix flips the verdict to `PASS` without YAML edits.

## What this is NOT

* Not a bridge bug. The bridge propagates the failure correctly
  (S8 audit verdict `FAIL`, structured `BridgeResult` with the ngspice
  stderr captured). Re-opening the bridge demo for this is wrong scope.
* Not an ngspice/PDK installation bug. The same `.lib` sequence works
  for every other GF180 example in the tree.
* Not a `.spiceinit` problem. Reproducible with `HOME=/tmp/clean`,
  no global `~/.spiceinit`, fresh PDK_ROOT.

## Owner / next step

Diagnosed in S9 (`bench/results/<run>/spec_miller_ota_gf180_easy.json`).
A future "GF180 sEKV port" session should pick this up; until then, the
bench's GF180 Miller task is a regression detector, not a design
deliverable.
