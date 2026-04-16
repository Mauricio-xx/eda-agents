# PR draft: add eda-agents to `Arcadia-1/awesome-ams-skills`

**Status**: draft only. The PR is NOT opened by Session 10. The user
decides when (and whether) to file it against the upstream repository.

Upstream: https://github.com/Arcadia-1/awesome-ams-skills (MIT
licensed — so both the upstream catalog and our contribution can be
attributed properly without license friction).

Local clone reviewed: `~/git/arcadia-review/awesome-ams-skills/`.

## Confirm before opening

Before turning this draft into an actual PR, confirm with the user:

- **Repo URL** for the entry. Current default is
  `https://github.com/Mauricio-xx/eda-agents` (matches
  `pyproject.toml::[project.urls]` and `docs/license_status.md`).
  If the repo is going to be republished under a different
  organization or name, update the link below before filing.
- Whether the entry should be added to the main `## Skills` list or
  to `## Coming Soon`. Given that the bench is surfaceable and the
  bridge demo runs end-to-end on IHP, the main list is the honest
  placement, but the gaps documented in the README could argue for
  `## Coming Soon` depending on the maintainer's bar.
- Upstream contribution guidelines — `CONTRIBUTING.md` is not in the
  upstream repo, so the conventions are implicit from the existing
  entries. The draft below mirrors the style of the
  `gmoverid-skill`, `veriloga-skills`, and `adctoolbox` entries.

## File to edit upstream

`README.md` — under the top-level `## Skills` section, alphabetized
after `analog-circuit-skills` and before `EVAS`. The Jekyll
`_config.yml` renders the README directly; no other file change is
needed.

## Entry (verbatim markdown for upstream PR)

```markdown
### [eda-agents](https://github.com/Mauricio-xx/eda-agents)

Open-source, open-PDK framework (Apache-2.0) for LLM-assisted analog
and digital design against IHP SG13G2 and GF180MCU. Integrates
skill-driven agents, SPICE-in-the-loop validation, and a benchmark
suite with explicit PASS -> FAIL_AUDIT discipline.

- **skills** — 23 Pydantic-backed `Skill` bundles (analog
  explorer / gm/ID sizing / ADC metrics / SAR ADC design / four
  analog roles; digital PM + verification + synthesis + physical +
  signoff; KLayout / LibreLane / drc / lvs flow skills).
- **bench** — Reproducible task suite (`spec-to-topology`,
  `bugfix`, `tb-generation`, `end-to-end`) with audit downgrade,
  restricted callable dispatch, and per-task scoring. First smoke
  run reports 9/11 PASS (Pass@1 = 90% excluding one deliberate
  `FAIL_SIM` on a documented GF180 sizing blocker).
- **bridge** — Virtuoso-bridge-shaped orchestrator rewritten under
  Apache-2.0 for the open-source stack: Pydantic v2 result models,
  UUID job registry with `ThreadPoolExecutor`, OpenSSH + jump-host
  wrapper, `xschem` headless netlist export, KLayout operation
  facade, and `eda-bridge` CLI.
- **topologies** — IHP Miller OTA, AnalogAcademy PMOS-input OTA,
  GF180 OTA, StrongARM comparator, 8-bit SAR (transistor +
  behavioural), and an 11-bit `DESIGN_REFERENCE` SAR with PVT /
  metastability / reference-settling checks.
- **digital** — LibreLane v3 RTL-to-GDS pipeline with ADK and
  Claude Code CLI backends, plus greedy flow-config exploration.
```

## Justification for the PR (to be pasted as the PR body)

```markdown
Hi, adding `eda-agents` to the skills catalog.

`eda-agents` is an Apache-2.0 framework that integrates the
methodology from several skills already listed here (gmoverid,
veriloga/openvaf, SAR ADC, ADCToolbox) behind a common
`Skill` registry, a skill-aware benchmark suite, and a
Virtuoso-bridge-lite-shaped orchestrator aimed at the open-source
EDA tool chain (ngspice, OpenVAF, KLayout, Magic, LibreLane). Target
PDKs are IHP SG13G2 and GF180MCU.

Scope:

- No upstream code is copied verbatim. For the six Arcadia-1 repos
  that currently ship without a LICENSE file (`gmoverid-skill`,
  `veriloga-skills`, `behavioral-veriloga-eval`, `sar-adc-skills`,
  `analog-agents`, `virtuoso-bridge-lite`), we reimplemented the
  equivalent patterns in-tree under Apache-2.0 and document the
  provenance in `docs/license_status.md`. Repos with a compatible
  permissive license (`ADCToolbox`, MIT) are pulled as runtime
  dependencies, not vendored.
- The project is openly experimental; the README lists every known
  gap by name plus the session in which it will close, rather than
  hiding them in a vague "future work" bullet.
- The first bench smoke (run `s9_initial_smoke`, Session 9) is
  committed to the repo so reviewers can audit the claims without
  re-running the tools.

Happy to adjust the entry wording or placement (main list vs. Coming
Soon) to whatever you prefer for this catalog.
```

## Checklist before opening

- [ ] User confirms repo URL.
- [ ] User confirms placement (main list vs. Coming Soon).
- [ ] `feat/arcadia-integration` merged to `main` — the link needs
      to point to a branch / tag where the README, CHANGELOG, and
      `bench/results/s9_initial_smoke/` are already visible.
- [ ] Session 9 gap-closure has not yet started (the PR is honest
      about the current state — if Tier 1 gaps have been fixed,
      update the "9/11 PASS, 1 FAIL_SIM" number before filing).
- [ ] Fork `Arcadia-1/awesome-ams-skills`, add the entry on a
      branch, open the PR pointing at `main`.
