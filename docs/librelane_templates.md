# LibreLane config templates

eda-agents ships two LibreLane RTL-to-GDS config templates — one per
supported PDK — to back the spec-to-GDS flow. This doc explains the
architecture, the provenance link to upstream, and the process for
bumping upstream pins.

## Layout

```
src/eda_agents/agents/
├── librelane_config_templates.py        # loader + selector + defaults
├── gf180_config_template.py             # backward-compat shim
└── templates/
    ├── __init__.py
    ├── gf180.yaml.tmpl                  # our GF180 template
    └── ihp_sg13g2.yaml.tmpl             # our IHP template

external/
├── gf180mcu-project-template/           # upstream reference (submodule)
└── ihp-sg13g2-librelane-template/       # upstream reference (submodule)

scripts/
└── check_librelane_template_upstream.py # parity script

tests/
├── test_template_snapshot.py            # byte-level drift guard
├── test_template_upstream_parity.py     # upstream parity guard
└── snapshots/*.filled.yaml              # golden snapshots
```

## Why this shape

**Templates are infrastructure, not design knobs.** They describe how
a generated design plugs into LibreLane's Classic flow — not what the
design *does*. Autoresearch and from-spec prompts change designs, not
templates. Template values must be traceable to a convention or
requirement, not tuned for QoR.

**Our templates are not forks of upstream.** Both upstream project
templates
([IHP-GmbH/ihp-sg13g2-librelane-template](https://github.com/IHP-GmbH/ihp-sg13g2-librelane-template)
and
[wafer-space/gf180mcu-project-template](https://github.com/wafer-space/gf180mcu-project-template))
are full-chip Chip-flow configs with padring, bondpads, SRAM macros,
and 1600×1600 die. Ours are Classic-flow macro-only, ~300×300 default,
with an IHP magic-skip workaround for a regression in the upstream
IHP-Open-PDK dev branch. A patch against upstream would delete 90% of
it — so we don't patch, we **reference**: upstream sits under
`external/` as a pinned submodule and our templates live in-repo as
YAML.

Six `str.format()` placeholders are filled by the spec-to-RTL prompt:
`{design_name}`, `{verilog_file}`, `{clock_port}`, `{clock_period}`,
`{die_width}`, `{die_height}`.

## How templates are loaded

`librelane_config_templates.py` reads both `.yaml.tmpl` files at
import time via `importlib.resources`. Public API (unchanged from
before the YAML migration):

- `GF180_CONFIG_TEMPLATE: str`
- `IHP_SG13G2_CONFIG_TEMPLATE: str`
- `GF180_DEFAULTS: dict`, `IHP_SG13G2_DEFAULTS: dict`
- `get_config_template(pdk_config) -> (str, dict)` — the canonical
  selector; reads `pdk_config.librelane_config_template`.

Callers (`agents/tool_defs.py`, test fixtures) use
`get_config_template()`. Defaults stay as Python dicts because they
are runtime constants, not YAML.

## Parity with upstream

`scripts/check_librelane_template_upstream.py` diffs our templates
against the pinned submodule content. Two field buckets:

### Verbatim fields (must match when both sides define them)

| Field                            | Purpose                                        |
|----------------------------------|------------------------------------------------|
| `meta.version`                   | LibreLane config schema version.               |
| `VDD_NETS`                       | Power net naming convention.                   |
| `GND_NETS`                       | Ground net naming convention.                  |
| `PRIMARY_GDSII_STREAMOUT_TOOL`   | Final GDS streamout tool (magic vs klayout).   |

Rule: if *both* upstream and ours set the field, the values must be
byte-identical. If either side leaves it unset, the check logs the
asymmetry and moves on — our GF180 template, for example, doesn't
set `meta.version` or the streamout tool today and upstream does;
bringing these over is a policy decision, not mechanical parity.

### Informational fields (always logged, never fail)

`meta.flow`, `USE_SLANG`, `PL_TARGET_DENSITY_PCT`, `DIE_AREA`,
`CLOCK_PERIOD`, `CLOCK_PORT`, `RT_MAX_LAYER`, `FP_SIZING`, `MACROS`,
`PAD_NORTH/SOUTH/EAST/WEST`.

Large divergences here are expected (Classic macro-only vs. Chip
full-chip with padring + SRAM). The point of logging them is so a
human reviewing an upstream bump can see what has changed in the
scope we chose not to mirror.

### Running the check locally

```bash
python scripts/check_librelane_template_upstream.py all
# or a single PDK:
python scripts/check_librelane_template_upstream.py ihp_sg13g2
```

`tests/test_template_upstream_parity.py` runs the same check under
pytest and skips gracefully when submodules are not initialised.

## Bumping an upstream pin

1. Fetch candidate upstream commits:
   ```bash
   python scripts/check_librelane_template_upstream.py ihp_sg13g2 --update-pin
   ```
   This prints the pinned SHA, the latest upstream SHA on `origin/HEAD`,
   and `git log` between them. It does **not** move the pin.

2. If the diff shows no change in our verbatim fields, bump:
   ```bash
   cd external/ihp-sg13g2-librelane-template
   git checkout <NEW_SHA>
   cd ../..
   python scripts/check_librelane_template_upstream.py ihp_sg13g2
   git add external/ihp-sg13g2-librelane-template
   git commit -m "bump: ihp-sg13g2 librelane template to <short_sha>"
   ```

3. If a verbatim field *did* change upstream and the change is one we
   want to adopt, edit the corresponding `.yaml.tmpl`, regenerate
   snapshots (`REGEN_SNAPSHOTS=1 pytest tests/test_template_snapshot.py`),
   and include both changes in the same commit. Reference the upstream
   commit in the commit body.

4. If upstream drifted on a verbatim field and we don't want to follow,
   update `VERBATIM_FIELDS` in
   `scripts/check_librelane_template_upstream.py` to demote the field
   to informational (or drop it altogether), and document the reason
   in this file and in the commit body.

Bumps happen at clear boundaries (end of a release cycle, when
starting new work on the digital flow) — not on a schedule and not
mid-release.

## Regenerating snapshots

`tests/test_template_snapshot.py` byte-compares the `.format()`-ed
template against
`tests/snapshots/{gf180,ihp_sg13g2}.filled.yaml`. After an intentional
template edit:

```bash
REGEN_SNAPSHOTS=1 pytest tests/test_template_snapshot.py
git diff tests/snapshots/
```

Review the diff carefully — this is the last line of defense against
accidental drift.

## Known divergences from upstream

### IHP: Classic flow + magic skip

- `meta.flow: Classic` (upstream: `Chip`).
- `meta.substituting_steps` drops `Magic.{StreamOut,WriteLEF,DRC,
  SpiceExtraction}`, `Checker.{MagicDRC,IllegalOverlap,LVS}`,
  `Odb.CheckDesignAntennaProperties`, `Netgen.LVS`.
- `RUN_MAGIC_STREAMOUT/WRITE_LEF/DRC: false`; `RUN_KLAYOUT_XOR: false`;
  `RUN_LVS: false`.
- Root cause: the IHP-Open-PDK dev branch is >685 commits past the
  LibreLane-qualified revision `cb7daaa8`; Magic steps hang on the
  current dev tech files. The KLayout LVS deck (`sg13g2.lvs`) also
  errors on stdcell CDLs today, so LVS is disabled entirely.
- Signoff path: KLayout streamout + KLayout DRC. GDS is clean, timing
  and power metrics are real; LVS comes back when upstream fixes
  the deck.

### GF180: macro-only Classic

- No `meta.flow` (Classic is the LibreLane default).
- No padring/macros. `PL_TARGET_DENSITY_PCT: 65` (upstream: 35 for a
  much larger chip). `DIODE_ON_PORTS: in`.
- Uses magic for streamout today (no `PRIMARY_GDSII_STREAMOUT_TOOL`
  override). Upstream selects klayout — adopting that is a separate
  decision, out of scope for the template-architecture migration.

## Licensing

Both upstream project templates are Apache License 2.0. Vendoring
them as git submodules is redistribution-safe. Our templates are
licensed under the eda-agents project license (see `LICENSE`).
