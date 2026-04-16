# Miller OTA on GF180MCU — analytical designer uses IHP process params

## Status

**Resolved in Session S9-gap-closure (gap #1)**. Left open on `main`
through S9/S10 as a documented regression detector; the bench's
`spec_miller_ota_gf180_easy` flipped from `FAIL_SIM` to `PASS` in the
`gap_closure_baseline -> gap1_full_smoke` run of this branch.

## Symptom (historical)

```
$ examples/14_bridge_e2e.py --pdk gf180mcu
Error on line 1573 or its substitute:
  m.x1a.m0 n1 inp ns ns nfet_03v3 w=1.7059e-7 l=1e-6 ...
could not find a valid modelname
    Simulation interrupted due to error!
```

The bench surfaced the same failure as `FAIL_SIM` for
`spec_miller_ota_gf180_easy`
(`bench/tasks/spec-to-topology/miller_ota_gf180_easy.yaml`).

## Root cause

`MillerOTADesigner` took a `PdkConfig` argument but only used it for
transistor *symbol names* (`nfet_03v3` / `pfet_03v3`) and library
include paths. The analytical sizing math ran against the frozen
`ProcessParams` dataclass whose defaults were hardcoded for IHP
SG13G2 (Wmin=150 nm, Lmin=130 nm, IHP-extracted sEKV constants).

When the GF180 PDK was selected, the designer still computed against
the IHP numbers, producing `W = 170 nm` for the input pair. GF180MCU's
`nfet_03v3` subcircuit binning required `W >= 220 nm`, so ngspice's
BSIM4 binner could not pick a model and aborted on the inner `m0` of
the `nfet_03v3` subckt.

## Fix

`ProcessParams` was extracted to
[`src/eda_agents/topologies/process_params.py`](../../src/eda_agents/topologies/process_params.py)
with a registry mapping each PDK name to its own parameter set.
`GF180MCU_PARAMS` uses:

| Parameter | Value | Source |
|---|---|---|
| `Lmin` | 280 nm | GF180 `nfet_03v3.0-7` binner floor |
| `Wmin` | 220 nm | GF180 `nfet_03v3.0-7` binner floor |
| `VDD` | 3.3 V | PDK spec |
| `tox` | 7.95 nm | BSIM4 `TOXE`, `sm141064.ngspice` model card |
| `n0n / n0p` | 1.30 / 1.35 | 180 nm literature |
| `Ispecsqn / Ispecsqp` | 339 / 75.7 nA | 2·n·µCox·UT² with 180 nm mobility |
| `AVTn / AVTp` | 8 / 12 nV·m | Pelgrom 180 nm literature |
| `lambdan / lambdap` | 2.0 / 3.0 × 10⁶ | 180 nm literature |

These are **literature-anchored approximations** — enough for the
analytical sizing path to stay inside the BSIM4 binner envelope for
every transistor it emits. They are not a silicon-traceable sEKV
extraction. A full extraction (LUT-anchored via
`data/gmid_luts/gf180_{nfet,pfet}_03v3.npz` and validated against a
measured gm/ID curve) is a future task; the registry contract stays
the same when those numbers land.

`MillerOTADesigner.__init__` now resolves the active PDK's
`ProcessParams` automatically; when the caller omits `specs=`, the
designer also derives the testbench `VDD` from the same table, so a
GF180 designer no longer builds a 1.2 V testbench for a 3.3 V PDK.

## Coverage

`tests/test_miller_ota_gf180.py` carries six tests:

- IHP process params are the exact pre-fix values (bit-identical
  regression guard for the S9 baseline).
- GF180 sizing produces `W >= 220 nm` and `L >= 280 nm` for every
  transistor on the representative spec.
- Explicit `process=` override beats the PDK default.

The bench task `spec_miller_ota_gf180_easy` keeps its YAML verbatim; it
is now PASS end-to-end on real ngspice 45 + GF180MCU_D.

## What this is NOT

* Not a silicon-traceable sEKV extraction (deferred — see above).
* Not a ngspice/PDK installation bug. The fix lives entirely in the
  analytical designer.
* Not a `.spiceinit` problem. Confirmed reproducible with
  `HOME=/tmp/clean`, no global `~/.spiceinit`, fresh PDK_ROOT.
