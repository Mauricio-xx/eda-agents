# AI agents for analog and digital IC design (short deck)

A compact Beamer presentation introducing the eda-agents stack:

- Five registered Claude Code agents (analog + digital).
- A shared autoresearch loop that evaluates proposals in ngspice or
  LibreLane.
- The MCP server that exposes the stack to external tools.

~7 content frames across 6 sections. Every slide has a `\note{...}`
with extended context so an expert designer can import slides + notes
into a polished deck with animations later.

Sibling deck: `../rtl2gds-gf180-docker/` -- hands-on LibreLane
RTL-to-GDS tutorial using the same Docker toolchain.

## Building the PDF

Same workflow as the sibling deck:

```bash
make          # main.pdf            (audience view, notes hidden)
make notes    # main-with-notes.pdf (slide + notes right-hand)
make docker   # build via texlive container
make clean
```

Requires a modern texlive with `beamer`, `metropolis`, `tikz`,
`listings`, `hyperref`, `fancyvrb`, `pgfpages`. If `metropolis` is
unavailable, swap `\usetheme{metropolis}` in `preamble.tex` for
`\usetheme{default}\usecolortheme{beaver}`.

## File layout

```
main.tex                      -- audience deck
main-with-notes.tex           -- speaker deck
preamble.tex                  -- packages, colors, TikZ styles
sections/
  01-landscape.tex            -- analog vs digital loops + agent roster
  02-analog.tex               -- topology recommender -> sizing -> SPICE
  03-digital.tex              -- project_manager + 4 specialists
  04-autoresearch.tex         -- the shared greedy loop
  05-mcp.tex                  -- MCP server + skills registry
  06-resources.tex            -- starting points + closer
Makefile
README.md
```

## Companion materials

Beyond the Beamer decks above, two parallel delivery channels ship in
the same folder:

### `claude_design_slides/` — three HTML presentation decks

All three are zero-build static HTML; open any of them in Firefox or
Chrome. Arrow keys navigate, `s` toggles speaker notes. Each has a
`-print.html` sibling that auto-triggers the browser print dialog for
PDF export.

| Deck                                                      | Slides | Audience                                            |
| --------------------------------------------------------- | ------ | --------------------------------------------------- |
| `AI Agents for Analog and Digital IC Design.html`         | 22     | 15-minute overview talk; pick-a-loop intro          |
| `analog.html`                                             | 42     | Deep dive on the analog loop (gm/ID + SPICE)        |
| `digital.html`                                            | 42     | Deep dive on the digital loop (RTL-to-GDS + OpenCode) |

```bash
# Overview / pick-a-loop
xdg-open "claude_design_slides/AI Agents for Analog and Digital IC Design.html"

# Analog deep dive — three agents, five topologies, greedy SPICE
xdg-open "claude_design_slides/analog.html"

# Digital deep dive — 5-agent ADK tree, 4 backends (incl. OpenCode), GenericDesign
xdg-open "claude_design_slides/digital.html"

# Print-to-PDF variants
xdg-open "claude_design_slides/analog-print.html"
xdg-open "claude_design_slides/digital-print.html"
```

Shared infrastructure: `deck-stage.js` (custom Web Component),
`styles/tokens.css` + `styles/deck.css` (PDK-semantic palette),
`assets/logo-*.svg` and `assets/icons/pdk/` (pulled from the
OpenSilicon Labs Design System).

### `demo/` — four hands-on notebooks

Two notebooks per loop (short + deep), all depending on the installed
`eda_agents` package (first cell runs `pip install -e .` from the repo
root in an activated venv):

- `demo/agents_miller_ota.ipynb` — short analog loop; `AutoresearchRunner`
  on the Miller OTA (ngspice + gm/ID LUTs; 2–3 min).
- `demo/agents_analog_topology_to_sizing.ipynb` — **deep** analog chain;
  recommender → sizing advisor → autoresearch → corner validator.
- `demo/agents_rtl2gds_counter.ipynb` — short digital loop;
  `GenericDesign` + `ProjectManager` on a 4-bit counter (GF180MCU
  inside Docker; 10–15 min real run).
- `demo/agents_digital_autoresearch.ipynb` — **deep** digital loop;
  `DigitalAutoresearchRunner` with `backend="opencode"` and
  multi-provider model selection (Gemini Flash, Z.AI GLM, etc.).

Each notebook has a plain-Python twin (`*.py`) with the same steps and
`input()` pauses. See `demo/README.md` for prerequisites and the full
step list.

## Source of authority

Agent names, sub-agent rosters, and skill namespaces are drawn from
the current repo tip:

- Claude Code agent registry: `.claude/agents/*.md`.
- Digital ADK hierarchy: `src/eda_agents/agents/digital_adk_agents.py`.
- Skill registry: `src/eda_agents/skills/{analog,digital}.py`.
- MCP template: `src/eda_agents/templates/mcp.json`.
- Autoresearch core: `src/eda_agents/agents/autoresearch_runner.py`.

Update the slides when any of these files change; do not invent
capabilities the code does not ship.
