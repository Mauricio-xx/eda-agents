"""Design-driven prompt templates for digital RTL-to-GDS ADK agents.

This module is a compatibility shim: prompt bodies now live in
``eda_agents.skills.digital``. Every public function here delegates to
the corresponding skill via ``get_skill``.
"""

from __future__ import annotations

from eda_agents.core.digital_design import DigitalDesign
from eda_agents.skills import get_skill


def project_manager_prompt(design: DigitalDesign) -> str:
    """Delegates to ``digital.project_manager``."""
    return get_skill("digital.project_manager").render(design)


def verification_engineer_prompt(design: DigitalDesign) -> str:
    """Delegates to ``digital.verification``."""
    return get_skill("digital.verification").render(design)


def synthesis_engineer_prompt(design: DigitalDesign) -> str:
    """Delegates to ``digital.synthesis``."""
    return get_skill("digital.synthesis").render(design)


def physical_designer_prompt(design: DigitalDesign) -> str:
    """Delegates to ``digital.physical``."""
    return get_skill("digital.physical").render(design)


def signoff_checker_prompt(design: DigitalDesign) -> str:
    """Delegates to ``digital.signoff``."""
    return get_skill("digital.signoff").render(design)
