"""Shared helpers for autoresearch exploration loops.

Extracted from ``AutoresearchRunner`` to enable reuse by both the
analog (SPICE-based) and digital (LibreLane-based) greedy runners
without forcing them into a common base class.

Two utility classes:

- ``ProgramStore``: manages the ``program.md`` persistent brain file
  (init, read, update best/learning/strategy sections).
- ``TsvLogger``: manages ``results.tsv`` with parametrized measurement
  columns (header, append, load history for resume).

Both classes are stateless helpers — they operate on files, not on
runner state. Each runner composes them and passes its own column
names and formatters.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# program.md template (domain-agnostic)
# ---------------------------------------------------------------------------

_PROGRAM_TEMPLATE = """\
# Circuit Design Exploration Program

## Goal
{goal}

## Metrics
{metrics}

## Design Space
{design_space}

## Specs
{specs}

## Current Best
No valid design found yet.

## Strategy
Starting exploration. No data yet -- begin with the reference design
point and systematically explore around it.

Reference: {reference}

## Learned So Far
(empty -- will be populated as exploration progresses)

## Rules
- Propose parameters as a JSON object. Keys must match the design space variables.
- Each evaluation costs 1 evaluation from the budget.
- A design is "valid" only if ALL specs are met simultaneously.
- FoM is only meaningful for valid designs.
- Crashes: If a run crashes (OOM, or a bug, or etc.), use your judgment:
  If it's something dumb and easy to fix (e.g. a typo, a missing import),
  fix it and re-run. If the idea itself is fundamentally broken, just skip
  it, log "crash" as the status in the tsv, and move on.
- NEVER STOP: Once the experiment loop has begun, do NOT pause to ask the
  human if you should continue. The human might be asleep, or gone from a
  computer and expects you to continue working indefinitely until you are
  manually stopped. You are autonomous. If you run out of ideas, think
  harder -- re-read the design space, try combining previous near-misses,
  try more radical parameter changes. The loop runs until the budget is
  exhausted or the human interrupts you, period.
"""


# ---------------------------------------------------------------------------
# ProgramStore
# ---------------------------------------------------------------------------


class ProgramStore:
    """Manages the ``program.md`` persistent state file.

    Parameters
    ----------
    work_dir : Path
        Directory where ``program.md`` lives.
    generate_fn : callable
        Zero-arg function that returns the initial program.md content
        when creating a fresh file.  Typically built from topology or
        design metadata by the owning runner.
    """

    def __init__(self, work_dir: Path, generate_fn: Callable[[], str]):
        self._path = work_dir / "program.md"
        self._generate_fn = generate_fn

    @property
    def path(self) -> Path:
        return self._path

    def init(self) -> Path:
        """Create or load program.md.  Returns the path."""
        if self._path.is_file():
            logger.info("Resuming: found existing program.md")
        else:
            self._path.write_text(self._generate_fn())
            logger.info("Created program.md")
        return self._path

    def read(self) -> str:
        """Read the current program.md content."""
        return self._path.read_text()

    def update_best(self, entry: dict, formatter: Callable[[dict], str]) -> None:
        """Replace the 'Current Best' section.

        Parameters
        ----------
        entry : dict
            The kept entry (must have at least ``eval``, ``fom``, ``params``).
        formatter : callable
            ``formatter(entry) -> str`` producing the replacement text
            for the Current Best section body (everything after the
            ``## Current Best`` heading).
        """
        content = self._path.read_text()
        new_best = f"## Current Best\n{formatter(entry)}"

        content = re.sub(
            r"## Current Best\n.*?(?=\n## )",
            new_best + "\n",
            content,
            flags=re.DOTALL,
        )
        self._path.write_text(content)

    def update_learning(self, insight: str) -> None:
        """Append a learning to the 'Learned So Far' section."""
        content = self._path.read_text()

        marker = "## Learned So Far\n"
        idx = content.find(marker)
        if idx == -1:
            return

        insert_at = idx + len(marker)
        next_section = content.find("\n## ", insert_at)
        if next_section == -1:
            next_section = len(content)

        current = content[insert_at:next_section].strip()
        if current == "(empty -- will be populated as exploration progresses)":
            current = ""

        updated = current + f"\n- {insight}" if current else f"- {insight}"
        content = content[:insert_at] + updated + "\n" + content[next_section:]
        self._path.write_text(content)

    def update_strategy(self, strategy: str) -> None:
        """Replace the 'Strategy' section with updated strategy."""
        content = self._path.read_text()
        new_strategy = f"## Strategy\n{strategy}"
        content = re.sub(
            r"## Strategy\n.*?(?=\n## )",
            new_strategy + "\n",
            content,
            flags=re.DOTALL,
        )
        self._path.write_text(content)


# ---------------------------------------------------------------------------
# TsvLogger
# ---------------------------------------------------------------------------


class TsvLogger:
    """Manages ``results.tsv`` with parametrized columns.

    Parameters
    ----------
    tsv_path : Path
        File path for the TSV log.
    param_cols : list[str]
        Design-space parameter column names (in order).
    measurement_cols : list[str]
        Measurement column names (e.g. ``["Adc_dB", "GBW_Hz", "PM_deg"]``
        for analog, ``["wns_worst_ns", "cell_count", ...]`` for digital).
    """

    def __init__(
        self,
        tsv_path: Path,
        param_cols: list[str],
        measurement_cols: list[str],
    ):
        self.tsv_path = tsv_path
        self.param_cols = param_cols
        self.measurement_cols = measurement_cols

    def write_header(self) -> None:
        """Write TSV header line."""
        param_part = "\t".join(self.param_cols)
        meas_part = "\t".join(self.measurement_cols)
        self.tsv_path.write_text(
            f"eval\t{param_part}\t{meas_part}\tfom\tvalid\tstatus\n"
        )

    def append_row(self, entry: dict) -> None:
        """Append one data row to the TSV log.

        ``entry`` must contain ``eval``, ``params`` (dict), ``fom``,
        ``valid``.  Measurement values are read from ``entry`` by the
        keys in ``measurement_cols`` (missing values become empty).
        """
        param_vals = "\t".join(
            f"{entry['params'].get(k, 0):.4f}" for k in self.param_cols
        )
        meas_vals = "\t".join(
            f"{entry.get(k, '')}" if entry.get(k, "") == "" else f"{entry[k]}"
            for k in self.measurement_cols
        )
        status = entry.get("status", "kept" if entry.get("kept") else "discarded")

        with open(self.tsv_path, "a") as f:
            f.write(
                f"{entry['eval']}\t{param_vals}\t{meas_vals}\t"
                f"{entry['fom']:.6e}\t{entry['valid']}\t{status}\n"
            )

    def load_history(self) -> tuple[list[dict], dict | None, int]:
        """Load history from an existing results.tsv for resume.

        Returns ``(history, best, start_eval)``.

        Each history entry has: ``eval``, ``params``, ``fom``, ``valid``,
        ``status``, ``kept``, ``success``, ``violations``, plus one key
        per measurement column (value or None).
        """
        if not self.tsv_path.is_file():
            return [], None, 1

        lines = self.tsv_path.read_text().strip().splitlines()
        if len(lines) <= 1:
            return [], None, 1

        header = lines[0].split("\t")

        history: list[dict] = []
        best: dict | None = None

        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) < len(header):
                continue

            eval_num = int(fields[0])

            # Parse param columns
            params: dict[str, float] = {}
            for i, key in enumerate(self.param_cols):
                try:
                    params[key] = float(fields[1 + i])
                except (ValueError, IndexError):
                    params[key] = 0.0

            # Parse measurement columns
            offset = 1 + len(self.param_cols)
            measurements: dict[str, float | None] = {}
            for i, col in enumerate(self.measurement_cols):
                raw = fields[offset + i] if (offset + i) < len(fields) else ""
                try:
                    measurements[col] = float(raw) if raw else None
                except ValueError:
                    measurements[col] = None

            # Parse fom, valid, status
            fom_offset = offset + len(self.measurement_cols)
            fom = float(fields[fom_offset]) if fields[fom_offset] else 0.0
            valid = fields[fom_offset + 1].strip().lower() == "true"
            status = (
                fields[fom_offset + 2].strip()
                if len(fields) > fom_offset + 2
                else "discarded"
            )

            entry: dict = {
                "eval": eval_num,
                "params": params,
                "success": status != "crash",
                "fom": fom,
                "valid": valid,
                "violations": [],
                "status": status,
                "kept": status == "kept",
                **measurements,
            }
            history.append(entry)

            if valid and entry["success"] and (best is None or fom > best["fom"]):
                best = entry.copy()

        start_eval = history[-1]["eval"] + 1 if history else 1
        logger.info(
            "Resumed from eval %d (%d prior evals, best FoM=%s)",
            start_eval,
            len(history),
            f"{best['fom']:.2e}" if best else "none",
        )
        return history, best, start_eval


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def generate_program_content(
    *,
    domain_name: str,
    pdk_display_name: str,
    fom_description: str,
    specs_description: str,
    design_vars_description: str,
    design_space_lines: str,
    reference_description: str,
) -> str:
    """Generate initial program.md content from metadata.

    This is the shared template; callers provide domain-specific text
    for each section.
    """
    return _PROGRAM_TEMPLATE.format(
        goal=(
            f"Maximize FoM for {domain_name} on {pdk_display_name}.\n"
            f"FoM definition: {fom_description}"
        ),
        metrics=(
            f"Primary: FoM (higher is better)\n"
            f"Constraints (all must be met for a valid design):\n"
            f"  {specs_description}"
        ),
        design_space=(
            f"{design_vars_description}\n\nRanges:\n{design_space_lines}"
        ),
        specs=specs_description,
        reference=reference_description,
    )


def extract_json_from_response(content: str) -> str:
    """Extract a JSON object from LLM response text.

    Handles markdown code fences and free-form text wrapping.
    Returns the extracted JSON string (still needs ``json.loads``).
    """
    if "```" in content:
        json_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL
        )
        if json_match:
            content = json_match.group(1)

    if not content.strip().startswith("{"):
        json_match = re.search(r"\{[^{}]*\}", content)
        if json_match:
            content = json_match.group(0)

    return content
