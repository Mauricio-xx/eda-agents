"""Hierarchical depth-first explorer for SSTADEX-style analog DSE.

Walks a ``Macromodel`` tree, characterizes primitives off LUTs, builds
the Cartesian product of primitive operating points and macromodel
parameter sweeps, evaluates each ``Testbench``'s symbolic TF over the
grid, applies spec conditions + propagated conditions, and optionally
returns only the Pareto-frontier rows.

The shape of the result mirrors upstream: a single ``pd.DataFrame``
whose columns are

  * macromodel_parameters (sympy Symbol keys)
  * primitive parameters per instance (e.g. ``g_gm_xdp_m1``,
    ``R_gds_xdp_m1``, ...)
  * primitive outputs per instance (e.g. ``W_diff``, ``L_diff``)
  * spec values per ``Testbench.name`` (e.g. ``gain_1stage``,
    ``rout_1stage``)
  * ``area`` — sum of all width outputs

The MNA/symbolic-TF derivation is performed once per spec; the
expression is then ``sympy.lambdify``-ed and evaluated on the numeric
grid, which keeps the inner loop fast even on 1e4+ point sweeps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import sympy as sym

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.topologies.sstadex.macromodel import Macromodel
from eda_agents.topologies.sstadex.primitives import Primitive
from eda_agents.topologies.sstadex.testbench import Test, Testbench

logger = logging.getLogger(__name__)


@dataclass
class ExplorationResult:
    """Single exploration node's output, mirroring upstream's tuple
    return (macro_results, exploration_axes, primmods_output, df,
    mask, pareto_mask=None)."""

    macromodel: Macromodel
    df: pd.DataFrame
    masked_df: pd.DataFrame
    pareto_mask: np.ndarray | None = None


# ---------------------------------------------------------------------------
# Cartesian helpers
# ---------------------------------------------------------------------------


def _cartesian_product_dfs(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    """Cartesian-merge several DataFrames into one big DataFrame.

    Equivalent to ``df.merge(..., how='cross')`` chained over the
    inputs. Returns an empty DataFrame if any input is empty.

    Column collisions (a common case: each primitive carries a
    ``length`` column) are resolved by dropping the duplicate from the
    second DataFrame -- callers should ensure that primitive-specific
    columns are named with the instance prefix to avoid silent
    aliasing.
    """
    if not dfs:
        return pd.DataFrame()
    out = dfs[0].copy()
    for other in dfs[1:]:
        if len(other.index) == 0:
            return pd.DataFrame()
        # Drop columns from ``other`` that already exist in ``out`` to
        # avoid pandas' default suffix-rename behaviour, which trips on
        # sympy-Symbol column names (``Symbol.endswith`` is undefined).
        overlap = [c for c in other.columns if c in out.columns]
        right = other.drop(columns=overlap) if overlap else other
        out = out.merge(right, how="cross")
    return out


def _filter_shared_nodes(
    df: pd.DataFrame, shared_nodes: dict[str, list[str]]
) -> pd.DataFrame:
    """Keep only rows where each shared-node variable group is
    consistent. ``shared_nodes`` maps a logical node name to a list of
    column names that must all be equal."""
    if df is None or len(df.index) == 0 or not shared_nodes:
        return df

    filtered = df
    for _node, variables in shared_nodes.items():
        if len(variables) < 2:
            continue
        present = [v for v in variables if v in filtered.columns]
        if len(present) < 2:
            continue
        ref = present[0]
        for var in present[1:]:
            filtered = filtered[filtered[ref] == filtered[var]]
    return filtered


# ---------------------------------------------------------------------------
# Primitive DataFrame assembly
# ---------------------------------------------------------------------------


def _primitive_to_columns(
    inst_name: str, prim: Primitive, df: pd.DataFrame
) -> pd.DataFrame:
    """Map a primitive's build() DataFrame to engine columns.

    Engine columns (string names, normalised so sympy Symbol-keyed
    sources do not collide with their string equivalents):
      * ``g_gm_<inst>_<branch>``  per branch (gm) — from ``df['gm']``
      * ``R_gds_<inst>_<branch>`` per branch (Ro)
      * primitive ``outputs`` keys (W_*, L_*) -- Symbol keys are
        stringified so pandas sees one column per logical name.
      * primitive ``parameters`` keys, if not already covered by the
        per-branch fields above.
      * Interface variables (e.g. ``vs_diff``, ``vs_cs``)
      * ``length_<inst>`` -- primitive-scoped length column.
    """
    out = pd.DataFrame()
    for br in prim.small_signal_branches:
        # Each branch in our schema shares the same row from the LUT
        # sweep: gm/gds are identical across m1/m2 of a primitive.
        # The parameter_map can then enforce "m2 = m1" identity at the
        # testbench level if needed.
        out[f"g_gm_{inst_name}_{br['name']}"] = df["gm"].values
        out[f"R_gds_{inst_name}_{br['name']}"] = df["Ro"].values

    def _key(k):
        return k.name if hasattr(k, "name") else str(k)

    # Primitive outputs (W, L per upstream symbol naming).
    for sym_key, arr in prim.outputs.items():
        key = _key(sym_key)
        if key not in out.columns:
            out[key] = np.asarray(arr)

    # Small-signal parameters from primitive.parameters -- only add if
    # not already covered by the per-branch loop above (the upstream
    # notebook redundantly assigns these; we de-duplicate here).
    for sym_key, arr in prim.parameters.items():
        key = _key(sym_key)
        if key not in out.columns:
            out[key] = np.asarray(arr)

    # Interface variables (per upstream convention).
    for name, arr in prim.interface_variables.items():
        if name not in out.columns:
            out[name] = np.asarray(arr)

    # Always copy length (for area / W constraints).
    if "length" in df.columns and f"length_{inst_name}" not in out.columns:
        out[f"length_{inst_name}"] = df["length"].values

    return out


def _macro_param_grid(macromodel: Macromodel) -> pd.DataFrame:
    """Convert ``macromodel.macromodel_parameters`` into a DataFrame
    with one row per Cartesian combination of the parameter sweeps."""
    items = list(macromodel.macromodel_parameters.items())
    if not items:
        return pd.DataFrame({"_macro_dummy": [0]})
    arrays = [np.atleast_1d(np.asarray(v)) for _k, v in items]
    keys = [k for k, _v in items]

    if len(arrays) == 1:
        return pd.DataFrame({keys[0]: arrays[0]})

    grid = np.array(np.meshgrid(*arrays, indexing="ij"))
    flat = grid.reshape(len(arrays), -1).T
    return pd.DataFrame({k: flat[:, i] for i, k in enumerate(keys)})


# ---------------------------------------------------------------------------
# Spec evaluation
# ---------------------------------------------------------------------------


def _evaluate_spec(
    test: Test,
    df: pd.DataFrame,
    *,
    cache: dict[int, np.ndarray] | None = None,
) -> np.ndarray:
    """Evaluate one spec's transfer function over ``df``.

    Picks up the testbench from ``test.testbench``, calls
    ``Testbench.eval()`` to get the sympy expression, substitutes the
    parameter map, lambdifies, and evaluates on the DataFrame's
    columns by name. Supports the upstream ``out_def`` vocabulary:

      * ``{"eval": _}``    -> direct DC magnitude of TF
      * ``{"divide": [num_test, den_test]}`` -> ratio of two
        previously evaluated tests (caller passes their values via
        ``cache[id(test)]``)
      * ``{"frec": _}``    -> -3 dB bandwidth (computed numerically)
      * ``{"pm": _}``      -> phase margin (degrees) at unity gain

    The post-processing keys ``frec`` and ``pm`` need a swept ``s``;
    the spec testbench's ``parameter_map`` should NOT zero ``s`` for
    those.
    """
    proc = list(test.out_def.keys())[0] if test.out_def else "eval"

    if proc == "divide":
        # Composed spec: caller must have already evaluated the
        # constituent tests and stored them in ``df`` as columns.
        num_test, den_test = test.out_def["divide"]
        num_name = num_test if isinstance(num_test, str) else getattr(num_test, "name", "")
        den_name = den_test if isinstance(den_test, str) else getattr(den_test, "name", "")
        if num_name and num_name in df.columns:
            num_vals = df[num_name].to_numpy()
        else:
            num_vals = np.ones(len(df.index))
        den_vals = df[den_name].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            return num_vals / den_vals

    tb: Testbench = test.testbench
    if tb is None:
        raise ValueError(
            f"Test '{test.name}' has no testbench bound; cannot evaluate."
        )

    expr = tb.eval(simplify=False)
    # Apply parameter_map substitutions (matched-pair identities,
    # source-value substitutions like V_p=1, Vss=0, s=0 for DC).
    if test.parametros:
        expr = expr.xreplace(test.parametros)

    # Lambdify with the DataFrame columns as free symbols.
    free_syms = sorted(expr.free_symbols, key=lambda s: s.name)
    if not free_syms:
        # Constant expression; broadcast.
        val = complex(expr)
        out = np.full(len(df.index), val)
        result = np.abs(out)
    else:
        col_arrays = []
        for s in free_syms:
            col = s.name
            if col in df.columns:
                col_arrays.append(df[col].to_numpy())
            elif sym.Symbol(col) in df.columns:
                col_arrays.append(df[sym.Symbol(col)].to_numpy())
            else:
                raise KeyError(
                    f"Symbol '{col}' in test '{test.name}' has no column "
                    f"in the DataFrame. Known columns: {list(df.columns)[:30]}..."
                )
        lam = sym.lambdify(free_syms, expr, modules=["numpy"])
        raw = lam(*col_arrays)
        result = np.abs(raw)

    # Optional user lambda post-processing (upstream uses this to
    # recover R_out from the "Vr*Rr/(Vr - Vout)" inversion test).
    if test.lamd is not None:
        result = test.lamd(result)

    return result


# ---------------------------------------------------------------------------
# Mask & filter helpers
# ---------------------------------------------------------------------------


def _spec_mask(values: np.ndarray, conditions: dict) -> np.ndarray:
    """Return a bool mask of rows satisfying the spec's conditions."""
    mask = np.ones(len(values), dtype=bool)
    if "min" in conditions:
        for floor in conditions["min"]:
            mask = mask & (np.abs(values) >= floor)
    if "max" in conditions:
        for ceil in conditions["max"]:
            mask = mask & (np.abs(values) <= ceil)
    return mask


def _compute_area(df: pd.DataFrame) -> np.ndarray:
    """Sum of all sized widths (in metres) for the row -- the SSTADEx
    Pareto axis. Looks at any column whose Symbol/name starts with
    ``W``."""
    area = np.zeros(len(df.index))
    for col in df.columns:
        col_name = col.name if hasattr(col, "name") else str(col)
        if col_name.startswith("W"):
            arr = df[col].to_numpy()
            try:
                area = area + arr.astype(float)
            except (TypeError, ValueError):
                continue
    return area


def _run_pareto(macromodel: Macromodel, df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Filter ``df`` to the Pareto front using ``paretoset``.

    Axes: ``area`` (minimise) + each ``opt_specifications`` entry
    (with its ``opt_goal`` direction). Returns ``(pareto_df,
    bool_mask)``.
    """
    try:
        import paretoset
    except ImportError as exc:  # pragma: no cover -- runtime gate
        raise ImportError(
            "paretoset is required for Pareto filtering. Install via "
            "`pip install paretoset` or pin in pyproject.toml."
        ) from exc

    cols = ["area"]
    goals = ["min"]
    for spec in macromodel.opt_specifications:
        cols.append(spec.name)
        goals.append(spec.opt_goal)
    present = [c for c in cols if c in df.columns]
    if len(present) < 2:
        # Need at least one axis besides area; pareto undefined.
        return df, np.ones(len(df.index), dtype=bool)
    goals_present = [goals[cols.index(c)] for c in present]
    mask = paretoset.paretoset(df[present], goals_present)
    return df[mask].reset_index(drop=True), np.asarray(mask)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def dfs(
    macromodel: Macromodel,
    lut: GmIdLookup,
    *,
    debug: bool = False,
) -> ExplorationResult:
    """Run the depth-first exploration on ``macromodel``.

    Recursive when ``macromodel.submacromodels`` is non-empty AND
    ``macromodel.num_level_exp != 1``. The recursion contract: a
    submacromodel runs ``dfs()`` first, materialises its results into
    columns on the parent's DataFrame, and its outputs become Cartesian
    factors of the parent's grid.
    """
    if debug:
        logger.info("[dfs] entering %s", macromodel.name)

    # 1. Submacromodels first (so their outputs are available as
    #    primitive-like sweep columns on the parent's grid).
    sub_dfs: list[pd.DataFrame] = []
    for sub in macromodel.submacromodels:
        sub_res = dfs(sub, lut, debug=debug)
        # Take the post-filter (masked) DataFrame as the sweep grid.
        if len(sub_res.masked_df.index) == 0:
            logger.warning(
                "[dfs] submacro %s produced 0 rows; parent %s will produce 0 rows.",
                sub.name, macromodel.name,
            )
        sub_dfs.append(sub_res.masked_df)

    # 2. Characterize primitives.
    prim_dfs: list[pd.DataFrame] = []
    for inst in macromodel.instances:
        block = inst.block
        if isinstance(block, Primitive):
            built = block.build(lut)
            cols = _primitive_to_columns(inst.name, block, built)
            prim_dfs.append(cols)

    # 3. Macromodel parameter sweep grid.
    macro_grid = _macro_param_grid(macromodel)
    if "_macro_dummy" in macro_grid.columns:
        macro_grid = macro_grid.drop(columns=["_macro_dummy"])

    # 4. Cartesian product all sources of variation.
    parts = [df for df in (prim_dfs + sub_dfs) if df is not None and len(df.index) > 0]
    if not parts and macro_grid.empty:
        df = pd.DataFrame()
    elif not parts:
        df = macro_grid
    else:
        df = _cartesian_product_dfs(parts)
        if not macro_grid.empty:
            df = _cartesian_product_dfs([df, macro_grid])

    if debug:
        logger.info("[dfs] %s post-cartesian rows=%d", macromodel.name, len(df.index))

    # 5. Apply shared-node filter (collapse rows where shared variables disagree).
    df = _filter_shared_nodes(df, macromodel.shared_nodes)
    if debug:
        logger.info(
            "[dfs] %s post-shared-nodes rows=%d", macromodel.name, len(df.index)
        )

    # 6. Evaluate each spec's TF and add a column per spec name.
    leaf_specs: list[Test] = []
    composed_specs: list[Test] = []
    for spec in macromodel.specifications:
        if spec.composed:
            composed_specs.append(spec)
        else:
            leaf_specs.append(spec)

    for spec in leaf_specs:
        df[spec.name] = _evaluate_spec(spec, df)
    for spec in composed_specs:
        df[spec.name] = _evaluate_spec(spec, df)

    # 7. Mask via per-spec conditions.
    spec_mask = np.ones(len(df.index), dtype=bool)
    for spec in macromodel.specifications:
        if spec.name not in df.columns:
            continue
        spec_mask = spec_mask & _spec_mask(df[spec.name].to_numpy(), spec.conditions)
    df = df[spec_mask].reset_index(drop=True)
    if debug:
        logger.info(
            "[dfs] %s post-spec-mask rows=%d", macromodel.name, len(df.index)
        )

    # 8. Area column.
    df["area"] = _compute_area(df)

    # 9. Propagated conditions.
    df = macromodel.apply_propagated_conditions(df)
    if debug:
        logger.info(
            "[dfs] %s post-propagated rows=%d", macromodel.name, len(df.index)
        )

    # 10. Persist child output_results back onto the macromodel object
    #     (lets parent macromodels pull these as Cartesian factors).
    for out_sym in macromodel.outputs:
        if out_sym in df.columns:
            macromodel.output_results[out_sym] = df[out_sym].to_numpy()
    for ivar in macromodel.interface_variables:
        if ivar in df.columns:
            macromodel.interface_results[ivar] = df[ivar].to_numpy()
    macromodel.its_final = True

    masked = df
    pareto_mask = None
    if macromodel.run_pareto and len(masked.index) > 0:
        masked, pareto_mask = _run_pareto(macromodel, masked)
        if debug:
            logger.info(
                "[dfs] %s pareto rows=%d / %d",
                macromodel.name, len(masked.index), len(df.index),
            )

    return ExplorationResult(
        macromodel=macromodel,
        df=df,
        masked_df=masked,
        pareto_mask=pareto_mask,
    )
