"""RF / mm-wave topology family for eda-agents.

Hosts gdsfactory-backed RF primitives ported from external upstream
projects. Session 5 ships the SPARX (#10 CAC2026, IHP SG13G2) wrapper;
mixer / balun / transmission-line standalone primitives can land here
in follow-ups once the upstream surfaces them as separate top cells.

All concrete topologies in this package run inside ``.venv-gdsfactory``
through :class:`eda_agents.core.gdsfactory_runner.GdsfactoryRunner`.
None of the modules under ``rf/`` are valid ``CircuitTopology``
subclasses; they are layout-only builders. A future RF SPICE wrapper
can promote them when behavioural / S-parameter models are wired in.
"""

from __future__ import annotations
