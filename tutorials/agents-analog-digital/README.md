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
