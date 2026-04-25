# Designer review — RTL-to-GDS on GF180MCU (Docker + LibreLane)

**Subject PDF:** `RTL-to-GDS on GF180MCU — LibreLane + Docker.pdf` (38 pages).
**Baseline:** a snapshot of our Beamer source deck before the
last two edits (see "What is new since your snapshot" below).
**Audience:** students and first-timers with no prior EDA exposure.

The goal of this document is to give you a prioritised, actionable
delta against the pedagogical contract of the source deck. Visual
craft does not need feedback — it is already above the source deck's
ceiling. This review is strictly about content and teaching thread.

---

## TL;DR

- **Visually**: beyond our bar. Typography, colour restraint,
  light/dark alternation, breadcrumbs are production-grade.
- **Thesis (LibreLane = orchestrator)**: lands on p.7 and p.10, but
  is dropped through the middle and the end of the deck. Needs to
  come back as a per-stage banner and in the section-6 closing.
- **Section 6 (closing) was replaced** with a shuttle tutorial. The
  headline numbers / signoff table / `metrics.csv` recipe / four
  pitfalls / "what to try next" that we need are all gone. This is
  the biggest structural regression.
- **"Slot"** is currently presented as a LibreLane requirement. It
  is a wafer-space MPW convention. A new frame is required (text
  provided below) and several sentences need fixing.
- **Zero `eda-agents` / `EDA-Agents Project` / audit / sibling-deck
  references anywhere.** Thank you — this was the single hardest
  constraint and you held it.

---

## What's working (keep as-is)

| Page | Slide | Why it works |
|------|-------|--------------|
| 1    | Cover | Author line correct ("Mauricio Montanares / Scientist, IHP"), no eda-agents reference. |
| 5    | RTL and GDS — two ends of the same pipeline | Behaviour/geometry pair of cards, perfect first-timer framing. |
| 7    | LibreLane is the conductor, not the orchestra | Thesis, tool roles, hub-and-spoke diagram — this is the pedagogical spine. |
| 10   | The full stack in one picture | Restates the thesis operationally: "Everything below the top box does the actual EDA work". |
| 12   | GF180MCU — two gotchas to know upfront | Teaches the *why* behind each fix, not just the fix. |
| 28   | LVS — does the layout actually match the circuit? | Cleanest teaching diagram in the deck; tool-colour legend best adherence. |
| 36   | Further reading | No audit file, no sibling deck, upstream links correct. |
| 38   | Questions? | Clean close, no leftover `eda-agents` references. |

One open question: the **"OpenSilicon Labs" / "/dev/silicon"** brand
on the cover and the footer of the closing slide is a designer
addition not present in the source deck. If the author does not run
that brand, drop or replace; if intentional, keep but confirm with the
author before print.

---

## Priority A — high pedagogical impact

### A1. Section 6 has been replaced by a shuttle tutorial. Restore the original closing.

**What changed.** Pages 31–35 are now titled "From one chip to a
shuttle / A shuttle — one reticle, many users / What changes, what
does not / Shuttle CI — one workflow, many slots / Six things to walk
away with". Page 36 is the "Further reading" slide (correct) and
page 38 is "Questions?" (correct).

**What is missing.** The source deck closes with:

1. **Headline numbers** — die 3.93×5.12 mm, core 3.05×4.24 mm,
   10.45 % core utilisation, 84 417 standard-cell instances, 251 615
   total, 4 macros, 58 pads, 153.5 mm wirelength, 34.3 mW power,
   40 ns / 25 MHz clock. Sourced from the reference run's
   `final/metrics.csv`.
2. **Signoff checklist** table with every zero-count the student must
   hit: Magic DRC, KLayout DRC, KLayout XOR (Magic vs KLayout),
   KLayout antenna/density, Magic illegal overlap, Netgen LVS,
   setup/hold across all 9 STA corners, routing DRC, IR drop. Plus
   two warnings disclosed honestly: max slew (3 warnings, ss corner)
   and max cap (100–103 warnings, all corners). The row order and
   "chip is manufacturable" framing is load-bearing.
3. **Reading metrics.csv in one grep** — the `grep -E '^(magic__drc|klayout__drc|lvs|timing__(setup|hold)_vio__count$|design__(violations|die__area|instance__count)|power__total)'` recipe,
   plus the "commit a trimmed copy as regression baseline, CI diff on
   every push" pattern.
4. **Four pitfalls** — the four first-timer failure modes with the
   exact error message and the exact one-line fix:
   (1) Wrong PDK active — error mentions `sg13g2_stdcell` — fix:
   `source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0`.
   (2) Missing SRAM / I/O files — error cites the IHP path — fix:
   pass `PDK_ROOT=/foss/designs/template/gf180mcu` explicitly.
   (3) Unknown I/O cell `gf180mcu_ws_io__...` — fix: clone the
   wafer-space PDK fork (`make clone-pdk`).
   (4) Flow is absurdly slow — Magic DRC ~32 min + KLayout antenna
   ~7 min dominate runtime — fix for iteration: `make librelane-nodrc`;
   never hand in a chip without one full DRC run.
5. **What to try next** — swap `chip_core.sv` for your own logic,
   change `CLOCK_PERIOD` and watch slack move, swap the slot to
   `1x0p5`/`0p5x0p5`, submit to Tiny Tapeout / wafer-space MPW.
6. **Companion notebook** pointer — `demo/rtl2gds_counter.ipynb`
   ships a 4-bit counter through the Classic flow in 7 steps. Good
   hands-on follow-up.

**Fix.** Restore the original six-frame closing. The Shuttle CI slide
(your p.34) is genuinely nice — keep it as an **appendix** or as a
later section in a future v2, but it must not displace the signoff /
pitfalls / next-steps story. Two slides of your shuttle content can
also merge into Priority A2's new "Slots" frame.

### A2. "Slot" is currently presented as a LibreLane requirement. It is a wafer-space MPW convention. Insert a clarification frame and correct three specific sentences.

**What changed.** The source deck has been updated with a new frame
(inserted between the "Two YAML files" frame and the `slot_1x1.yaml`
frame). Your snapshot predates this. In addition to inserting the
new frame, three existing sentences bake the misconception:

- **p.19 closing italic**: *"Change the slot, change the die —
  everything else stays the same. That separation is why LibreLane
  enforces two YAML files."* This is factually wrong. LibreLane
  accepts one YAML or many; the two-file split is a wafer-space
  template convenience, not a LibreLane requirement.
- **p.14 divider subtitle**: *"Two YAML files, one command. This is
  where most of the thinking happens."* True for the wafer-space
  path; misleading for Classic flow (which uses one YAML).
- **p.38 tagline**: *"The rest is iteration — one slot, one command,
  one metrics.csv at a time."* Implies slots are mandatory.

**Fix — new frame to insert between your p.14 (Section 3 opener) and
p.15 (project-template file table), OR between the "Two YAML files"
frame and the `slot_1x1.yaml` frame**. Title:
*"Slots: a wafer-space convention, not a LibreLane one"*.

> A **slot** is a pre-reserved rectangle on wafer-space's MPW
> shuttle. Three are standard: `1x1` (~3.9×5.1 mm), `1x0p5`,
> `0p5x0p5`. Each `slot_*.yaml` sets `DIE_AREA`, `CORE_AREA`, and
> the pad lists; everything else stays in `config.yaml`.
>
> **Not taping out with wafer-space? Three paths:**
>
> 1. **Other shuttle** (IHP MPW, TinyTapeout, …) — use that
>    programme's template; its own die sizes replace slots.
> 2. **Custom tape-out with padring** — drop `slots/`. Move
>    `FP_SIZING`, `DIE_AREA`, `CORE_AREA` and the `PAD_*` lists
>    into `config.yaml`. Pick any die size that fits your logic.
> 3. **Bare macro (no padring)** — switch to `meta.flow: Classic`.
>    No pads, no slots, no sealring, no wafer-space PDK fork. That
>    is what the counter notebook does.

**Fix — ready-to-paste corrections for the three offending sentences:**

- **p.19 closing** → *"Change the slot, change the die — everything
  else stays the same. The wafer-space template enforces two YAML
  files for this reason; bare-macro Classic flows use just one."*
- **p.14 divider subtitle** → *"Two YAML files for the wafer-space
  template, one for Classic. This is where most of the thinking
  happens."*
- **p.38 tagline** → *"The rest is iteration — one design, one
  command, one metrics.csv at a time."*

### A3. Restore the per-stage "LibreLane driving: <Tool>" callout.

**What changed.** The source deck places a small right-aligned chip
on every phase slide — literal text *"LibreLane driving: Yosys"*,
*"LibreLane driving: OpenROAD"*, *"LibreLane driving: Magic + KLayout
+ Netgen + OpenSTA"*. Purpose: the thesis on p.7 says LibreLane
orchestrates and never does EDA itself, but by slide 25 the student
is deep in PDN / placement / DRC and easily forgets who is driving.
In your version the callout survives only as prose ("LibreLane runs
both…", "LibreLane fixes these…"), never banner-styled.

**Fix.** Apply a small colour chip at the top-right of every phase
slide — style can be yours (we suggest a filled rounded-corner
rectangle with the same accent blue / orange family already in the
deck). Mapping:

| Slide                | Banner text                                         |
|----------------------|-----------------------------------------------------|
| Synthesis            | LibreLane driving: **Yosys**                        |
| Floorplan            | LibreLane driving: **OpenROAD**                     |
| PDN                  | LibreLane driving: **OpenROAD**                     |
| Placement + CTS      | LibreLane driving: **OpenROAD**                     |
| Routing              | LibreLane driving: **OpenROAD**                     |
| DRC                  | LibreLane driving: **Magic + KLayout**              |
| LVS                  | LibreLane driving: **Netgen**                       |
| STA                  | LibreLane driving: **OpenSTA (inside OpenROAD)**    |
| IR drop              | LibreLane driving: **PDNSim (inside OpenROAD)**     |
| Antenna              | LibreLane driving: **OpenROAD.RepairAntennas**      |

---

## Priority B — medium impact

### B1. Tool-colour legend is inconsistent across slides.

The source deck's visual contract is:
- **Yosys** = green
- **OpenROAD** = blue (all of its sub-utilities: pdngen, RePlAce,
  TritonCTS, TritonRoute, OpenRCX, OpenSTA, PDNSim, RepairAntennas)
- **Magic + KLayout + Netgen** = orange

Where this is broken in your version:

- **p.6 pipeline diagram**: every OpenROAD stage has a different
  colour (floorplan cyan, PDN blue, placement purple, CTS pink,
  routing purple). Breaks the "colour = tool" reading.
- **p.22 step tree** (79 steps): perfect place to tint each step's
  tool prefix (`Yosys.*` green, `OpenROAD.*` blue, `Magic.*` orange,
  etc.) — currently all in the same grey.
- **p.26 "Four checks"** tablero colours by **check** (DRC cyan, LVS
  blue, STA lime, Antenna orange). Please colour by **tool** instead,
  so OpenSTA = blue not lime.

**Fix.** Unify to the three-colour scheme. Consider adding a small
legend strip at the bottom of p.6 and p.22 ("Yosys · OpenROAD · Magic
+ KLayout + Netgen" with the matching swatches).

### B2. Phase 6 is under-specified — add KLayout XOR, PDNSim IR drop, OpenRCX, and expand STA to 9 corners.

The source deck's signoff phase covers:

- Geometry: Magic DRC + KLayout DRC cross-check + **KLayout XOR**
  (Magic GDS vs KLayout GDS, zero diff) + antenna + density +
  sealring.
- Electrical: **OpenRCX** parasitic extraction (3 corners) +
  **OpenSTA** across **9** PVT + extraction corners + **PDNSim**
  IR drop + Netgen LVS.

Your version:

- **p.25 "Signoff & verification"** divider: subtitle "Four checks
  between routed and taped out" — which drops XOR, IR, parasitics.
- **p.26 "Four checks — and what each guards against"**: same trim.
- **p.29 "STA — five corners, two slack numbers"**: shows 5, should
  be 9 (or state explicitly "we show the 5 that break first;
  LibreLane runs 9").

**Fix.** Either rename to "Four big checks, plus IR drop, XOR, and
parasitics" and add a companion slide, or extend the "Four checks"
table to seven rows.

### B3. "Six open-source projects" / "six tools" contradicts the five-tool thesis.

The source deck counts **five** EDA tools (Yosys, OpenROAD, Magic,
KLayout, Netgen) — LibreLane is the **orchestrator**, not a sixth
tool. Your version:

- **p.9 divider subtitle**: *"Six open-source projects, one container,
  one set of foundry files."*
- **p.20 divider subtitle**: *"One command, six tools, eight to
  twelve minutes, one GDS."*

**Fix.** Unify to *"Five open-source tools + LibreLane as orchestrator"*
or simply *"five tools"*.

Suggested text:

- **p.9 subtitle** → *"Five open-source tools, one container, one set
  of foundry files."*
- **p.20 subtitle** → *"One command, five tools, eight to twelve
  minutes, one GDS."*

### B4. p.10 visual hierarchy flattens LibreLane into the engines.

Inside the container box, the green bar
*"LibreLane + Yosys + OpenROAD + KLayout + Magic + Netgen"* puts
LibreLane at the same typographic level as the five engines it drives.
A student sees six coequal tools.

**Fix.** Redraw as two rows: LibreLane on top in its own colour as a
thin orchestrator bar, an arrow downwards into a row of five engines
in their tool-colour tints. One-line sub-caption: *"LibreLane drives;
the others execute."*

---

## Priority C — minor (style, typos, unrendered placeholders)

| Page | Issue | Proposed fix |
|------|-------|--------------|
| 8    | Image placeholder `slides/figures/final_layout_render.png` unrendered. | Replace with a real KLayout render. We have one of the counter notebook's final GDS available. |
| 11   | Title says *"four commands"* but only 3 are shown in the main block (the 4th is in the "Cleanup" card). | Either retitle *"three commands (plus cleanup)"* or number the cleanup pair as `# 4`. |
| 11   | Verify `--skip sleep infinity` rendering. The real invocation is `hpretl/iic-osic-tools:next --skip sleep infinity` where the `--skip` flag + `sleep infinity` is an image-entrypoint convention, not a `docker run` flag. Current rendering reads ambiguously. | Either use the entrypoint-override form (`--entrypoint sleep` + `infinity` as image command) or annotate that `--skip sleep infinity` is passed to the image entrypoint. |
| 19   | YAML listing contains `..gds` / `..lef` that look like placeholders that did not get filled in (e.g. `[pdk_dir::libs.ref/gf180mcu_fd_ip_sram/gds/..gds]`). | Use explicit ellipsis (`…sram512x8m8wm1.gds`) or the full filename. |
| 22   | Step tree cuts at 42 (`42-Magic.StreamOut/`). Source deck promises 79 steps. | Replace with the full-six-phase partition or add a "…79 steps total, grouped into six phases →" fold line. |
| 23   | Example `design__instance__count = 8421` does not match the later 84 417 / 251 615 that the deck quotes for the reference design. | Replace with 84 417 and label "reference run (chip_top on GF180)" so later numbers don't collide. |
| 24   | Fictional failing log uses wirelength `128 345 µm`; Phase-5 reference is 153 mm. | Either match 153 mm or use a clearly-different small number (e.g. 15 mm) so the two do not cross-contaminate. |
| 27   | `magic_drc_zoom.png` placeholder unrendered. | Ship an actual Magic DRC GUI screenshot. |
| 30   | `antenna_fix.svg` placeholder unrendered. | Ship a before/after SVG or a simple schematic. |
| 32   | `shuttle_floorplan.svg` placeholder unrendered. | Ship a 4×4 reticle grid with the 3 slot sizes called out. |
| 34   | Last line of the GitHub Actions YAML (`actions/upload-artifact@v4`) is clipped at the bottom of the slide. | Shrink the code block or split into two columns. |

---

## What is new since your snapshot

Two edits have landed in the source deck after you made your copy:

1. **New frame in section 3 — "Slots: a wafer-space convention,
   not a LibreLane one"**. The full replacement text is embedded in
   priority **A2** above. Please typeset it in the deck's style and
   insert it between your p.14 and p.15. Alternatively insert between
   the "Two YAML files, one command" divider and the `slot_1x1.yaml`
   frame.
2. **Author line fixed.** The source deck used to say
   *"EDA-Agents Project"* as author; corrected to
   *"Mauricio Montanares / Scientist, IHP"*. You already have this
   right on the cover — thank you. No action needed.

The new frame is the one pedagogical addition we need from you.
Everything else in this document is either a correction of an
existing slide or a restoration of a section-6 frame you already
had in your baseline.

---

## Where the source artifacts live

On the author's machine:

- `tutorials/rtl2gds-gf180-docker/main.pdf` — audience deck (39 pages).
- `tutorials/rtl2gds-gf180-docker/main-with-notes.pdf` — speaker
  deck with notes on second screen (same 39 pages).
- `tutorials/rtl2gds-gf180-docker/render/slide-NN.png` — per-slide
  PNG renders at 150 dpi.
- `tutorials/rtl2gds-gf180-docker/render/speaker_notes.md` — the
  `\note{...}` payload of each frame, extracted to markdown.
- `tutorials/rtl2gds-gf180-docker/sections/03-librelane-config.tex`
  — contains the canonical Latex source of the new "Slots" frame
  (search for *"Slots: a wafer-space convention"*).
- `tutorials/rtl2gds-gf180-docker/demo/rtl2gds_counter.ipynb` —
  the companion hands-on notebook (counter on Classic flow).

Thanks — the visual work is excellent, and the constraint that was
most at risk (zero `eda-agents` references) has been held cleanly.
The deltas above are all content restorations; none of them touch
the visual system.
