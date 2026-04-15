# License status — Arcadia-1 ecosystem integration

This file tracks the license status of the eight Arcadia-1 repositories
reviewed during the Sesión 0 deep-dive (cloned locally under
`~/git/arcadia-review/`), and the operational rule each one imposes on the
`feat/arcadia-integration` work in eda-agents.

## Summary

| Repo | Upstream | Local path | License | Operational rule |
|------|----------|-----------|---------|------------------|
| ADCToolbox | https://github.com/Arcadia-1/ADCToolbox | `~/git/arcadia-review/ADCToolbox` | MIT | Depend via PyPI (`adctoolbox`); fork allowed |
| awesome-ams-skills | https://github.com/Arcadia-1/awesome-ams-skills | `~/git/arcadia-review/awesome-ams-skills` | MIT | Publish our entry; fork allowed |
| gmoverid-skill | https://github.com/Arcadia-1/gmoverid-skill | `~/git/arcadia-review/gmoverid-skill` | No LICENSE file | Study patterns only; reimplement under Apache-2.0 |
| veriloga-skills | https://github.com/Arcadia-1/veriloga-skills | `~/git/arcadia-review/veriloga-skills` | No LICENSE file | Study patterns only; reimplement |
| behavioral-veriloga-eval | https://github.com/Arcadia-1/behavioral-veriloga-eval | `~/git/arcadia-review/behavioral-veriloga-eval` | No LICENSE file | Study schemas only; reimplement |
| sar-adc-skills | https://github.com/Arcadia-1/sar-adc-skills | `~/git/arcadia-review/sar-adc-skills` | No LICENSE file | Study knowledge only; rewrite docs |
| analog-agents | https://github.com/Arcadia-1/analog-agents | `~/git/arcadia-review/analog-agents` | No LICENSE file | Study patterns only; reimplement |
| virtuoso-bridge-lite | https://github.com/Arcadia-1/virtuoso-bridge-lite | `~/git/arcadia-review/virtuoso-bridge-lite` | No LICENSE file | Study patterns only; reimplement |

## Operational rule (until upstream clarifies)

For every repo without a LICENSE file:

- Do **not** copy source code verbatim into eda-agents (Python, Verilog-A,
  SPICE, YAML, JSON, Markdown).
- Do study the architecture, data shapes, prompts, and file layout as
  inspiration.
- Do reimplement equivalent functionality under the eda-agents license
  (Apache-2.0, per `pyproject.toml`).
- Do cite the inspiration in code comments or docs where relevant.

This rule stays in force until each upstream repo publishes a LICENSE
file or responds to a licensing-clarification issue.

## Issues to open (pending user approval)

Before creating issues on these external repos, the wording needs user
sign-off. Draft text is stored at the bottom of this document. Once
issues are filed, replace these placeholders with real URLs.

- [ ] https://github.com/Arcadia-1/gmoverid-skill/issues/??? — license clarification
- [ ] https://github.com/Arcadia-1/veriloga-skills/issues/??? — license clarification
- [ ] https://github.com/Arcadia-1/behavioral-veriloga-eval/issues/??? — license clarification
- [ ] https://github.com/Arcadia-1/sar-adc-skills/issues/??? — license clarification
- [ ] https://github.com/Arcadia-1/analog-agents/issues/??? — license clarification
- [ ] https://github.com/Arcadia-1/virtuoso-bridge-lite/issues/??? — license clarification

## Issue draft (English, to be sent to each repo)

> Hi, thanks for publishing this work openly.
>
> I'm evaluating integration of architectural patterns from this repository
> into an open-source, open-PDK analog+digital EDA agent framework
> (`github.com/Mauricio-xx/eda-agents`, Apache-2.0). At the moment this
> repository does not include a LICENSE file, which makes it ambiguous
> whether code/docs/schemas can be reused.
>
> Could you confirm the intended license? A permissive choice (MIT,
> Apache-2.0, BSD-3-Clause) would make it easy to credit this project and
> reuse patterns directly. If you prefer to keep the code
> all-rights-reserved, that is also fine — I'd then stick to reading the
> repo for inspiration and reimplementing equivalents from scratch.
>
> Happy to answer any questions about how we plan to use it. Thanks!

## Review schedule

Re-check this file at the start of each new session and update status
entries when upstream licenses change.
