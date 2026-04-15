"""Backward-compatibility shim.

The GF180 template now lives alongside the IHP template in
``librelane_config_templates``. New code should import from there and
use ``get_config_template(pdk_config)`` to get the correct PDK-specific
template at runtime.
"""

from .librelane_config_templates import (  # noqa: F401
    GF180_CONFIG_TEMPLATE,
    GF180_DEFAULTS,
)
