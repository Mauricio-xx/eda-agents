"""EDA artifact parsers for DRC, LVS, Liberty, and build system configs."""

from eda_agents.parsers.base import EdaImporter, ImportItem

_IMPORTERS: dict[str, type] = {}


def _register_defaults():
    from eda_agents.parsers.librelane import LibreLaneConfigParser
    from eda_agents.parsers.metrics import LibreLaneMetricsParser
    from eda_agents.parsers.drc import MagicDrcParser
    from eda_agents.parsers.lvs import NetgenLvsParser
    from eda_agents.parsers.orfs import OrfsConfigParser
    from eda_agents.parsers.liberty import LibertyParser

    for cls in (
        LibreLaneConfigParser,
        LibreLaneMetricsParser,
        MagicDrcParser,
        NetgenLvsParser,
        OrfsConfigParser,
        LibertyParser,
    ):
        _IMPORTERS[cls().name] = cls


def get_importer(name: str) -> EdaImporter:
    if not _IMPORTERS:
        _register_defaults()
    cls = _IMPORTERS.get(name)
    if cls is None:
        raise KeyError(f"Unknown importer: {name}. Available: {list(_IMPORTERS)}")
    return cls()


def auto_detect_importer(path) -> EdaImporter | None:
    if not _IMPORTERS:
        _register_defaults()
    from pathlib import Path
    path = Path(path)
    for cls in _IMPORTERS.values():
        imp = cls()
        if imp.can_parse(path):
            return imp
    return None


def list_importers() -> list[str]:
    if not _IMPORTERS:
        _register_defaults()
    return list(_IMPORTERS.keys())


__all__ = [
    "EdaImporter",
    "ImportItem",
    "get_importer",
    "auto_detect_importer",
    "list_importers",
]
