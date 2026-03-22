"""Tests for Magic DRC parser."""
from eda_agents.parsers.drc import MagicDrcParser


class TestMagicDrcParser:
    def test_can_parse_rpt_file(self, tmp_path):
        # Magic DRC reports: design name on first line, then --- separator
        rpt = tmp_path / "drc_results.rpt"
        rpt.write_text("my_design\n---\nSome rule\n1.0um 2.0um 3.0um 4.0um\n")
        parser = MagicDrcParser()
        assert parser.can_parse(rpt)

    def test_cannot_parse_non_rpt(self, tmp_path):
        txt = tmp_path / "results.txt"
        txt.write_text("not a DRC report\n")
        parser = MagicDrcParser()
        assert not parser.can_parse(txt)
