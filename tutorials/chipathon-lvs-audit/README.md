# Chipathon LVS audit — KLayout vs Magic+Netgen (GF180MCU)

Reproduces a reported LVS discrepancy between KLayout LVS and Magic+Netgen LVS
on the `core_biasgen` library of
[`AutoMOS-project/AutoMOS-chipathon2025`](https://github.com/AutoMOS-project/AutoMOS-chipathon2025),
using the `hpretl/iic-osic-tools:next` Docker image.

See [`report/chipathon_lvs_audit.md`](report/chipathon_lvs_audit.md) for the
full report (meeting-ready bullets at the top, per-cell verdict matrix, deep
dive into the root cause, recommendations).

## Prerequisites

- Running container named `gf180-chip-test` built from `hpretl/iic-osic-tools:next`
  with `/tmp/gf180-chip-test` bind-mounted at `/foss/designs`. The sibling
  tutorial `tutorials/rtl2gds-gf180-docker/` already sets this up.
- The AutoMOS repo cloned at
  `/tmp/gf180-chip-test/chipathon-lvs-audit/AutoMOS-chipathon2025/`:

```bash
mkdir -p /tmp/gf180-chip-test/chipathon-lvs-audit
cd /tmp/gf180-chip-test/chipathon-lvs-audit
git clone --branch integration --depth 1 \
    https://github.com/AutoMOS-project/AutoMOS-chipathon2025.git
```

## Run

```bash
cd /home/montanares/personal_exp/eda-agents
bash tutorials/chipathon-lvs-audit/run_audit.sh
python3 tutorials/chipathon-lvs-audit/build_report.py
```

Open `tutorials/chipathon-lvs-audit/report/chipathon_lvs_audit.md`.

## Layout

| File | Purpose |
|---|---|
| `lvs_one_cell.sh`   | In-container runner. Runs KLayout LVS + Magic+Netgen (project setup) + Magic+Netgen (PDK setup) on one cell; emits `summary.json`. |
| `run_audit.sh`      | Host driver. Stages the script into the bind-mount and loops every biasgen cell through `docker exec`. |
| `build_report.py`   | Aggregates per-cell `summary.json` into a Markdown report. |
| `report/`           | Generated report lives here. |
| `logs/`             | Reserved for CI logs (unused today). |

## Caveats

- Audit is pinned to **only the PDK shipped in the Docker image** (ciel-installed
  `/foss/pdks/gf180mcuD/`, open_pdks commit `7b70722`). It does **not** test the
  Mabrains fork that the AutoMOS project may use upstream. If the discrepancy
  shifts with a different PDK version, re-run with `PDK_ROOT` pointing at the
  alternate install.
- Only 2 of 9 biasgen cells (`biasgen_mirror_2_to_10`, `biasgen_v2`) currently
  ship both a `.gds` and a `.spice` in the integration branch. The other 7 are
  schematic-only and report `not LVS-ready`.
- `biasgen_v2` matches "with property errors" (W delta 5e-7 vs 6e-7,
  18.2%) on the `biasgen_inverter` subcell — Netgen treats this as a match
  under its default tolerance, but it is a real layout/schematic divergence
  worth fixing before tapeout.
