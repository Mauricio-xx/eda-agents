# Speaker notes — RTL-to-GDS on GF180MCU (audience deck)

Each entry below matches a slide in `main.pdf`. Slide PNGs live in `render/slide-NN.png`.

- Slide 01 — title page (*RTL-to-GDS on GF180MCU*).
- Slide 02 — *What you will learn* (no note extracted here; see `main.tex`).
- Slide 03 — *Agenda* (table of contents).

The rest of the deck follows below, grouped by section.

## Section: The RTL-to-GDS pipeline
*(PDF page 4 is the section divider.)*

### Slide 05 — RTL and GDS -- two ends of the same pipeline

![slide](render/slide-05.png)

Audience calibration. Most first-timers understand code (RTL) and know
 chips are "physical" (GDS) but have never seen the in-between steps.
 Emphasise the distinction: *behaviour* vs *geometry*. One wrong
 polygon and the mask is unusable; one wrong environment variable and the
 tool cannot even start. The whole deck earns its keep by showing this
 translation is now reproducible in one command.

 Software analogy (spoken, not on the slide): RTL is like C source,
 GDS is like an ELF binary, and LibreLane is a compiler chain of ~80
 passes. Unlike software, each pass can veto manufacturability.


### Slide 06 — The pipeline, at a glance

![slide](render/slide-06.png)

Reading guide for the diagram.

 Synthesis (Yosys) turns RTL into a gate-level netlist of standard-cell
 instances. Floorplan decides die size and where macros / I/O sit. PDN
 draws VDD/VSS straps and rings. Placement legalises every cell to a
 standard-cell row. CTS balances the clock tree. Routing draws every
 signal wire. Signoff verifies manufacturability (DRC), schematic fidelity
 (LVS), and timing (STA).

 Emphasise: each stage writes a milestone artifact, and any stage can
 halt the flow. LibreLane is the thing that stitches all 80-ish steps
 together – the rest of this deck is about driving *it*, not the
 individual tools.


### Slide 07 — LibreLane is the conductor, not the orchestra

![slide](render/slide-07.png)

This slide is the thesis of the tutorial: *LibreLane is an
 orchestrator*, not an EDA tool. Students routinely conflate the two
 because "`librelane config.yaml`" is the only command they
 type. Make the distinction loud and early.

 Say it out loud: "Yosys synthesises, OpenROAD places and routes,
 Magic writes GDS, KLayout cross-checks, Netgen does LVS. LibreLane
 decides *which one runs when* and *with what arguments*."

 Two consequences: (1) errors almost always come from the underlying
 tool; the fix is in that tool's log, not in LibreLane's
 configuration. (2) If you already know any of those tools, you can
 override a step and keep the rest of the flow. LibreLane stays out
 of your way.

 Pitch: this also explains why the open-source flow is bigger than it
 looks – five independent EDA projects, each with its own history,
 working together through one YAML file.


### Slide 08 — What you get at the end

![slide](render/slide-08.png)

This frame sets the "you will have something tangible" expectation.
 The final layout render is what people put on posters. Everything else
 (DEF, SPEF, SDF) matters only to the people doing follow-up work:
 gate-level sim, analog post-layout, shuttle submission.

 Name-drop `metrics.csv` – that is the single file you grep to know
 whether the chip is green. Mention: commit a trimmed `metrics.csv`
 to git as a regression baseline; you will thank yourself when the image
 is rebuilt weeks later.


## Section: The stack, the container, the PDK
*(PDF page 9 is the section divider.)*

### Slide 10 — The full stack in one picture

![slide](render/slide-10.png)

This restates the thesis from section~1 before we zoom into the
 container and PDK. The message: there are *six* independent
 open-source projects on this slide, not one. LibreLane, Yosys,
 OpenROAD, Magic, KLayout, Netgen are all maintained separately, each
 with its own release cadence and documentation.

 Name LibreLane as the evolution of OpenLane 2 (same lineage, Python
 rewrite). Audiences will almost never type `yosys ...` or
 `openroad ...` directly – but when a step fails, the log you
 read is the underlying tool's log, not a LibreLane log.

 The upcoming two slides (container + PDK) are about giving these six
 tools a common place to live and a common set of foundry files to
 read.


### Slide 11 — Host, container, and your files

![slide](render/slide-11.png)

Mental model is the whole point here. People comfortable with VMs get
 it in 30 seconds; people who have never used containers need to hear:
 the container is disposable, your host folder is not. Treat
 `~/eda/designs` as the canonical workspace. Everything
 written inside `/foss/designs` survives `docker rm`. Everything
 outside that path inside the container is gone.

 The bind-mount is the only reason we can run a heavy-weight image and
 still keep our editor, git, and artifacts on the host where we already
 have backups.


### Slide 12 — Container bootstrap -- four commands

![slide](render/slide-12.png)

Four commands is all a first-time user needs. The flags matter:
 `-v` binds the workspace, `–user` keeps artifacts on the host
 owned by the right UID, `–skip sleep infinity` is the image's
 built-in "do nothing, stay alive" entrypoint so we can
 `docker exec` into it later.

 The `bash -lc` pattern is the non-interactive workhorse we use for
 every subsequent command; it yields a login shell so the container's
 `PATH`, `PDK_ROOT`, etc. are set up. Use single quotes on
 the host side so the entire payload is handed to the container shell
 untouched.

 Cleanup (not on slide): `docker stop gf180 && docker rm gf180`
 removes the container but leaves your host files untouched.


### Slide 13 — GF180MCU: two PDK gotchas to know upfront

![slide](render/slide-13.png)

This is the single slide that saves every first-timer 2 hours of
 debugging. Without the wafer-space fork, you get an obscure "unknown
 cell" error during synthesis. Without the `sak-pdk-script`
 activation, LibreLane cheerfully looks for `sg13g2_stdcell` files
 inside the GF180 directory and stops on a Tcl glob error.

 Both issues show up in Section 6 "Pitfalls" with their exact error
 messages – mention that now so people recognise the symptoms later.
 Tag 1.8.0 of the wafer-space fork is the version we audited; bump only
 with intent.

 A **PDK** itself is just foundry files (stdcell libs, LEF, Liberty,
 SPICE models, DRC decks). We do not need to explain it in depth for a
 first-timer; the container already has the bits.


## Section: LibreLane config -- the heart of the flow
*(PDF page 14 is the section divider.)*

### Slide 15 — The project template: files you actually touch

![slide](render/slide-15.png)

The template is a *working* full-chip design – a tiny 42-bit
 counter with two SRAM512x8 macros sitting inside a padring on a
 3.93 5.12\,mm die. Clone it into the mounted workspace with
 `git clone –depth 1 https://github.com/wafer-space/gf180mcu-project-template.git template`.

 The five files above are the surface area of the flow. Everything else
 (flake.nix, scripts/, ip/, cocotb/) is either nix-only (we ignore) or
 auxiliary (render helper, cocotb sim). Anchor the rest of this section
 around those five paths.


### Slide 16 — LibreLane v3 YAML -- two levels, one command

![slide](render/slide-16.png)

Why two files? Because one template usually targets many shuttle slots
 (different die sizes). Keep everything die-shape-specific in
 `slot_*.yaml` and the rest in `config.yaml`. LibreLane reads
 the left-most file first and lets the right-most override, so list
 slots first, design second.

 `–manual-pdk` tells LibreLane not to activate ciel automatically;
 we did that ourselves with `sak-pdk-script.sh`. Every
 `meta.version: 3` flag on the slide reminds readers this is v3
 syntax – v2 examples on the internet are outdated.


### Slide 17 — \texttt{config.yaml} -- the header

![slide](render/slide-17.png)

`meta.flow: Chip` selects the full-chip padring flow (vs the
 smaller `Classic` flow used for bare-die blocks). `dir::`
 resolves paths relative to the YAML file – portable across machines.

 `CLOCK_PORT` is the external pad (`clk_PAD`); `CLOCK_NET`
 is the internal net after the pad cell's buffer (`clk_pad/Y`). If
 you only define `CLOCK_PORT`, CTS skews everything from the pad
 input and you get sad timing at the core.

 Clock period drives synthesis effort and routing congestion. The
 reference run uses 40\,ns (25\,MHz) – comfortable slack for a
 180\,nm process. Tighten cautiously.


### Slide 18 — \texttt{config.yaml} -- the power grid

![slide](render/slide-18.png)

Power is where designs die silently. Definitions:

 - **Strap**: a wide VDD/VSS wire crossing the core.

 - **Pitch**: the distance between neighbouring straps;
 larger = less metal, higher IR drop.

 - **Core ring**: the loop of wide power wires around the
 standard-cell area; it delivers current from the pads to the straps.

 The defaults here pass IR-drop for the reference design. If you scale
 up the core area, either shrink pitch or widen straps. LibreLane's
 PDNSim will flag violations in signoff.

 `pdn_cfg.tcl` is OpenROAD-specific Tcl (voltage domains, per-macro
 PDN grids). Trust the template's version for now; revisit only when
 you change macro count or placement.


### Slide 19 — \texttt{config.yaml} -- declaring macros

![slide](render/slide-19.png)

A **macro** is a pre-characterised hard block (SRAM, analog IP,
 PLL). LibreLane needs four views: GDS (layout), LEF (abstract
 placement/routing shape), Verilog blackbox (for synthesis to stub it
 out), and one Liberty timing file per PVT corner.

 `instances:` pins each macro instance at an absolute coordinate
 (microns) with an orientation (`N`, `S`, `E`, `W`,
 flipped variants `FN`, `FE` etc.). Manual placement is deliberate
 – automatic macro placement often collides with the padring.

 The `pdk_dir::` prefix resolves against `PDK_ROOT`, so these
 paths stay portable.


### Slide 20 — \texttt{slot\_1x1.yaml} -- the floorplan

![slide](render/slide-20.png)

The padring is the single hardest thing to get right on a chip flow.
 The pad lists describe the physical order on each side; LibreLane's
 `OpenROAD.Padring` step walks them in sequence. Miss one pad and
 the padring won't close.

 Coordinates are in microns, origin at the die's bottom-left corner.
 The gap between `DIE_AREA` and `CORE_AREA` is where the
 padring sits – roughly 440\,um here, typical for 180\,nm.

 Change the slot, change the die – everything else stays the same. That
 separation is why LibreLane enforces two YAML files.


## Section: Running the flow
*(PDF page 21 is the section divider.)*

### Slide 22 — The golden path -- copy, paste, done

![slide](render/slide-22.png)

This is the entire flow. Three conceptual actions:
 (1) fetch the PDK fork, (2) activate the env, (3) invoke LibreLane
 through the template's Makefile. The `PDK_ROOT` on the command
 line overrides whatever the Makefile default is – deliberately
 redundant with `sak-pdk-script` to catch the trap we discuss in
 Section 6.

 Wall time breakdown on the audit run: Yosys 6\,s, floorplan 15\,s,
 PDN + placement + CTS + routing ~3\,min, Magic DRC 32\,min,
 KLayout antenna 7\,min, LVS + STA + IR 1\,min. If anyone asks
 "why so slow": Magic DRC on 84k standard cells with padring. The
 `make librelane-nodrc` target drops DRC for iteration.


### Slide 23 — What scrolls by while it runs

![slide](render/slide-23.png)

The log is dense but readable. Every step prints a header like
 `== Step 42: OpenROAD.Floorplan` so you can grep. Warnings during
 routing repair iterations are expected; failures manifest as the flow
 stopping abruptly on a step boundary.

 The final banner tells you two things: the timestamped run directory
 (where the full logs and intermediate DEFs live) and the path to
 `metrics.csv`. Bookmark both when presenting live.


### Slide 24 — Where artifacts land

![slide](render/slide-24.png)

The split matters: `runs/` is every flow artifact organised per
 run (useful for bisecting), `final/` is the curated deliverable
 set that LibreLane copies via its `–save-views-to` flag. You almost
 never dig into `runs/` unless something failed.

 Practical tip: `runs/` balloons fast. Add it to `.gitignore`.
 Commit only a trimmed `final/metrics.csv` as a regression baseline.
 Add a CI job that greps the next run's `metrics.csv` for the key
 fields and compares – that is how you catch silent regressions.


### Slide 25 — 79 steps grouped into six phases

![slide](render/slide-25.png)

Recap diagram before the deep-dive section. Audience already saw
 the pipeline in section~1 – the difference now is (a) the colour
 key makes "which tool owns which phase" visible at a glance, and
 (b) they have seen the command that starts it.

 Point at each box and promise: "the next six slides tell you what
 actually happens inside these, and which underlying tool LibreLane
 is calling."

 Why 79 steps and not 6? LibreLane breaks the phases into many
 auxiliary passes (netlist cleanup, antenna repair, resizing, multiple
 DRC runs). The 6-phase model is the mental pipeline; the 79 steps
 are the implementation. Each of the 79 maps to exactly one tool.


## Section: Flow stages up close
*(PDF page 26 is the section divider.)*

### Slide 27 — Phase 1 -- Synthesis (Yosys)

![slide](render/slide-27.png)

Yosys is fast (seconds) and deterministic. The 251\,k instance count
 looks huge – it is dominated by ~1100 padring cells and 81k tap
 cells inserted for well-tap density. The meaningful number is
 `instance_count__stdcell` (84k).

 If synthesis fails, it is almost always a Verilog issue
 (unresolved module, missing port, wrong bit width). LibreLane surfaces
 Yosys errors verbatim; grep the log for `ERROR:` to find the
 offending line.


### Slide 28 — Phase 2 -- Floorplan \& padring

![slide](render/slide-28.png)

This is the phase that turns YAML into geometry. If the pad list is
 wrong, the padring does not close and floorplan fails loudly.
 If `CORE_AREA` is too tight, `CutRows` creates too few rows and
 placement fails later. If an SRAM instance coordinate overlaps the
 padring, `ManualMacroPlacement` fails here.

 Tap cells (well ties) are required by the PDK at a fixed pitch. Forget
 them and downstream LVS complains. LibreLane inserts them automatically.


### Slide 29 — Phase 3 -- Power Distribution Network

![slide](render/slide-29.png)

Power is the one pass where you *want* to touch Tcl. `pdn_cfg.tcl`
 declares voltage domains and per-macro power grids; OpenROAD's PDN
 generator consumes it verbatim. Voltage domain naming must match
 `VDD_NETS`/`GND_NETS` in `config.yaml` or the tool refuses
 to start the phase.

 Signoff will re-check the grid (PDNSim + IR drop). A grid with zero
 *construction* violations can still fail IR; if that happens,
 either widen straps or tighten pitch.


### Slide 30 — Phase 4 -- Placement \& CTS

![slide](render/slide-30.png)

Placement is iterative. Global placement runs on a quadratic wirelength
 model; detailed placement snaps to legal rows. CTS is where a
 well-behaved flow earns its keep: 30 clock buffers, skew below half a
 nanosecond, no PLL needed for 25\,MHz.

 Common failure: "too many flops, not enough rows" – placement
 congestion exceeds a threshold. Fix by enlarging `CORE_AREA` or
 adjusting `PL_TARGET_DENSITY_PCT` (lower = more spread, less
 congestion, bigger die).


### Slide 31 — Phase 5 -- Routing

![slide](render/slide-31.png)

Routing is where designs blow up their wall-time budgets. OpenROAD's
 TritonRoute iterates: global route, detailed route, DRC, repair,
 repeat. Our run converges in 6 iterations; busy designs can take
 20+ on a congested die.

 **Antennas** are a fabrication concern, not a timing one: long
 metal wires accumulate plasma charge during etching, which can punch
 through a gate oxide. Diodes bleed the charge. LibreLane's repair pass
 inserts them automatically.

 If routing fails to converge, the fix is almost always the floorplan
 (more core area, lower density) or the congestion-vs-density knob.


### Slide 32 — Phase 6 -- Signoff (geometry + electrical)

![slide](render/slide-32.png)

Signoff is the "is this really manufacturable?" gate. Two independent
 DRC engines (Magic and KLayout) cross-check each other; an XOR confirms
 their geometries match. Any mismatch is a show-stopper.

 Electrical: RCX extracts RC parasitics from the final GDS. STA then
 re-checks timing with those real parasitics at every corner. IR drop
 checks the power grid under dynamic load. LVS is non-negotiable – it
 is the single check that proves the layout implements the circuit you
 synthesized.

 Every check here writes one metric into `metrics.csv`. The next
 section is about reading that file.


## Section: Results, pitfalls, and next steps
*(PDF page 33 is the section divider.)*

### Slide 34 — Headline numbers -- reference run

![slide](render/slide-34.png)

These numbers anchor every conversation. Low core utilisation (10%)
 is normal for a full-chip padring design: the die is sized for the
 shuttle slot, not the logic. If the goal were area, you would shrink
 `DIE_AREA` and drop the padring.

 Power at 34\,mW at 25\,MHz, 5\,V: typical for 180\,nm with this much
 logic. Scale linearly with frequency for first-order estimates.
 Mention these numbers so the audience has a feel for "what good
 looks like" before they run their own design.


### Slide 35 — Signoff checklist -- the things that must be zero

![slide](render/slide-35.png)

Signoff is a binary gate. Everything above the line must be zero.
 Warnings below the line are shipping-acceptable for a first silicon
 spin but worth investigating before re-tapeout. The distinction
 between "violation" and "warning" is tool-specific – LibreLane
 normalises them into `metrics.csv` so you can script gates in CI.

 If you see non-zero LVS, stop. LVS failure means the GDS does not
 match the netlist – the chip will not behave like your simulation,
 regardless of DRC or timing.


### Slide 36 — Reading \texttt{metrics.csv} in one grep

![slide](render/slide-36.png)

This is the presenter's cheat-sheet slide. Keep it on screen during
 Q&A. The recipe grabs: DRC counts from both Magic and KLayout, LVS
 status, setup/hold violation counts, die area, instance count, total
 power. Enough to know a flow is green without opening a GUI.

 CI pattern: on every push, run LibreLane, grep these fields, compare
 to baseline with a tolerance band on numeric fields and an exact match
 on violation counts. Fails loud the moment someone breaks the flow.


### Slide 37 — Four pitfalls you \emph{will} hit

![slide](render/slide-37.png)

These four cover >80\,% of the first-timer failure modes.

 Pitfalls 1 and 2 look like "the PDK is wrong" but have different
 fixes: 1 is *shell env*, 2 is *Make variable*. Teach people
 to check both.

 Pitfall 3 is the reason the wafer-space PDK fork exists at all –
 upstream open_pdks GF180 does not ship the custom padring cells.

 Pitfall 4 is a philosophy point: iteration speed matters. Use
 `nodrc` for design iteration, then run the full flow before tapeout.
 This mirrors how unit tests vs integration tests work in software.


### Slide 38 — What to try next

![slide](render/slide-38.png)

Call-to-action slide. The cheapest way to learn is to take the
 template, replace `chip_core.sv` with "hello world" logic
 (blinky, counter, UART echo), and re-run. 45 minutes later you have
 a real GDS.

 For presenter: point people to the companion Jupyter notebook
 (`demo/rtl2gds_counter.ipynb`) which walks through a minimal
 counter in 7 steps – a faster iteration target than the full-chip
 template. It is fully self-contained; drop it anywhere and run.

