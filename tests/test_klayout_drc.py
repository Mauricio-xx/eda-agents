"""Tests for KLayout DRC runner and parser.

Unit tests (no klayout needed):
    pytest tests/test_klayout_drc.py -m "not klayout" -v

Integration tests (needs klayout + GF180 PDK):
    pytest tests/test_klayout_drc.py -m klayout -v
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from eda_agents.core.klayout_drc import KLayoutDrcResult, KLayoutDrcRunner, parse_lyrdb
from eda_agents.parsers.klayout_drc import KLayoutDrcParser


# ---------------------------------------------------------------------------
# Synthetic .lyrdb XML for unit tests
# ---------------------------------------------------------------------------

LYRDB_CLEAN = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <report-database>
     <description>DRC Report</description>
     <original-file>/tmp/test.gds</original-file>
     <generator>test</generator>
     <top-cell>TOP</top-cell>
     <tags/>
     <categories>
      <category>
       <name>MET1.W.1</name>
       <description>Min width of Metal1</description>
       <categories/>
      </category>
     </categories>
     <cells>
      <cell>
       <name>TOP</name>
       <variant/>
       <references/>
      </cell>
     </cells>
     <items/>
    </report-database>
""")

LYRDB_VIOLATIONS = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <report-database>
     <description>DRC Report</description>
     <original-file>/tmp/test.gds</original-file>
     <generator>test</generator>
     <top-cell>TOP</top-cell>
     <tags/>
     <categories>
      <category>
       <name>COMP.1</name>
       <description>Min COMP width</description>
       <categories/>
      </category>
      <category>
       <name>POLY.3</name>
       <description>Min POLY spacing</description>
       <categories/>
      </category>
     </categories>
     <cells>
      <cell>
       <name>TOP</name>
       <variant/>
       <references/>
      </cell>
     </cells>
     <items>
      <item>
       <tags/>
       <category>'COMP.1'</category>
       <cell>TOP</cell>
       <visited>false</visited>
       <multiplicity>1</multiplicity>
       <values>
        <value>polygon: (0,100;100,100;100,0;0,0)</value>
       </values>
      </item>
      <item>
       <tags/>
       <category>'COMP.1'</category>
       <cell>TOP</cell>
       <visited>false</visited>
       <multiplicity>1</multiplicity>
       <values>
        <value>polygon: (200,300;300,300;300,200;200,200)</value>
       </values>
      </item>
      <item>
       <tags/>
       <category>'POLY.3'</category>
       <cell>TOP</cell>
       <visited>false</visited>
       <multiplicity>1</multiplicity>
       <values>
        <value>edge-pair: (50,50;60,50)/(70,50;80,50)</value>
       </values>
      </item>
     </items>
    </report-database>
""")


# ---------------------------------------------------------------------------
# Unit tests: parse_lyrdb
# ---------------------------------------------------------------------------


class TestParseLyrdb:
    def test_clean_report(self, tmp_path):
        lyrdb = tmp_path / "clean.lyrdb"
        lyrdb.write_text(LYRDB_CLEAN)
        rules = parse_lyrdb(lyrdb)
        assert rules == {}

    def test_violations(self, tmp_path):
        lyrdb = tmp_path / "dirty.lyrdb"
        lyrdb.write_text(LYRDB_VIOLATIONS)
        rules = parse_lyrdb(lyrdb)
        assert rules == {"COMP.1": 2, "POLY.3": 1}

    def test_total_count(self, tmp_path):
        lyrdb = tmp_path / "dirty.lyrdb"
        lyrdb.write_text(LYRDB_VIOLATIONS)
        rules = parse_lyrdb(lyrdb)
        assert sum(rules.values()) == 3

    def test_nonexistent_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_lyrdb(tmp_path / "missing.lyrdb")


# ---------------------------------------------------------------------------
# Unit tests: KLayoutDrcResult
# ---------------------------------------------------------------------------


class TestKLayoutDrcResult:
    def test_clean_result(self):
        r = KLayoutDrcResult(success=True, total_violations=0, clean=True)
        assert r.clean
        assert "clean" in r.summary.lower()

    def test_dirty_result(self):
        r = KLayoutDrcResult(
            success=True,
            total_violations=3,
            clean=False,
            violated_rules={"COMP.1": 2, "POLY.3": 1},
        )
        assert not r.clean
        assert "3 violations" in r.summary
        assert "COMP.1" in r.summary

    def test_error_result(self):
        r = KLayoutDrcResult(
            success=False,
            total_violations=0,
            clean=False,
            error="klayout crashed",
        )
        assert "error" in r.summary.lower()


# ---------------------------------------------------------------------------
# Unit tests: KLayoutDrcParser (EdaImporter)
# ---------------------------------------------------------------------------


class TestKLayoutDrcParser:
    def test_can_parse_lyrdb(self, tmp_path):
        parser = KLayoutDrcParser()
        lyrdb = tmp_path / "test.lyrdb"
        lyrdb.write_text(LYRDB_VIOLATIONS)
        assert parser.can_parse(lyrdb)

    def test_cannot_parse_other(self, tmp_path):
        parser = KLayoutDrcParser()
        txt = tmp_path / "test.txt"
        txt.write_text("hello")
        assert not parser.can_parse(txt)

    def test_parse_violations(self, tmp_path):
        parser = KLayoutDrcParser()
        lyrdb = tmp_path / "test_comp.lyrdb"
        lyrdb.write_text(LYRDB_VIOLATIONS)
        items = parser.parse(lyrdb)
        assert len(items) == 1
        item = items[0]
        assert item.type == "knowledge"
        assert "klayout-drc" in item.key
        assert "3" in item.content  # total violations
        assert "COMP.1" in item.content
        assert "POLY.3" in item.content

    def test_parse_clean(self, tmp_path):
        parser = KLayoutDrcParser()
        lyrdb = tmp_path / "clean.lyrdb"
        lyrdb.write_text(LYRDB_CLEAN)
        items = parser.parse(lyrdb)
        assert len(items) == 1
        assert "0" in items[0].content

    def test_describe(self):
        parser = KLayoutDrcParser()
        desc = parser.describe()
        assert "klayout" in desc.lower()
        assert "lyrdb" in desc.lower()


# ---------------------------------------------------------------------------
# Unit tests: KLayoutDrcRunner construction
# ---------------------------------------------------------------------------


class TestKLayoutDrcRunnerInit:
    def test_gds_not_found(self, tmp_path):
        runner = KLayoutDrcRunner(pdk_root="/nonexistent")
        result = runner.run(
            gds_path=tmp_path / "missing.gds",
            run_dir=tmp_path / "run",
        )
        assert not result.success
        assert "not found" in result.error

    def test_script_not_found(self, tmp_path):
        gds = tmp_path / "test.gds"
        gds.write_bytes(b"")
        runner = KLayoutDrcRunner(pdk_root="/nonexistent")
        result = runner.run(gds_path=gds, run_dir=tmp_path / "run")
        assert not result.success
        assert "run_drc.py" in result.error


# ---------------------------------------------------------------------------
# Integration tests (require klayout + GF180 PDK)
# ---------------------------------------------------------------------------

GF180_PDK_ROOT = Path(
    "/home/montanares/git/wafer-space-gf180mcu"
)
COMP_GDS = GF180_PDK_ROOT / (
    "gf180mcuD/libs.tech/klayout/tech/drc/testing/"
    "testcases/unit/comp.gds"
)
ANTENNA_GDS = GF180_PDK_ROOT / (
    "gf180mcuD/libs.tech/klayout/tech/drc/testing/"
    "testcases/unit/antenna-1.gds"
)
DENSITY_PASS_GDS = GF180_PDK_ROOT / (
    "gf180mcuD/libs.tech/klayout/tech/drc/testing/"
    "testcases/unit/density/M1.4/pass/density-M1_4_pass.gds"
)


@pytest.mark.klayout
class TestKLayoutDrcIntegration:
    """Integration tests that run real KLayout DRC.

    These require:
    - klayout in PATH
    - GF180MCU PDK at the expected location
    - python3 with klayout.db and docopt
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        import shutil

        if not shutil.which("klayout"):
            pytest.skip("klayout not in PATH")
        if not GF180_PDK_ROOT.is_dir():
            pytest.skip("GF180MCU PDK not found")
        if not COMP_GDS.is_file():
            pytest.skip(f"Test GDS not found: {COMP_GDS}")

    def test_drc_comp_table(self, tmp_path):
        """Run DRC on comp.gds with --table=comp --variant=C."""
        runner = KLayoutDrcRunner(
            pdk_root=str(GF180_PDK_ROOT),
            variant="C",
            timeout_s=300,
        )
        result = runner.run(
            gds_path=COMP_GDS,
            run_dir=tmp_path,
            table="comp",
        )
        assert result.success, f"DRC failed: {result.error}"
        # comp.gds is a test case, may have violations
        assert result.total_violations >= 0
        assert isinstance(result.violated_rules, dict)
        if result.report_paths:
            assert Path(result.report_paths[0]).is_file()

    def test_validate_setup(self):
        runner = KLayoutDrcRunner(pdk_root=str(GF180_PDK_ROOT))
        problems = runner.validate_setup()
        assert problems == [], f"Setup problems: {problems}"

    def test_drc_antenna_gds(self, tmp_path):
        """Run DRC on PDK antenna test case, verify violations found."""
        if not ANTENNA_GDS.is_file():
            pytest.skip(f"Antenna test GDS not found: {ANTENNA_GDS}")

        runner = KLayoutDrcRunner(
            pdk_root=str(GF180_PDK_ROOT),
            variant="C",
            timeout_s=300,
        )
        result = runner.run(
            gds_path=ANTENNA_GDS,
            run_dir=tmp_path / "drc_antenna",
            table="antenna",
        )
        assert result.success, f"DRC run failed: {result.error}"
        assert result.total_violations > 0, "antenna-1.gds should have violations"
        assert len(result.violated_rules) > 0

    def test_drc_density_pass(self, tmp_path):
        """Run DRC on density pass case, verify clean result."""
        if not DENSITY_PASS_GDS.is_file():
            pytest.skip(f"Density pass GDS not found: {DENSITY_PASS_GDS}")

        runner = KLayoutDrcRunner(
            pdk_root=str(GF180_PDK_ROOT),
            variant="C",
            timeout_s=300,
        )
        result = runner.run(
            gds_path=DENSITY_PASS_GDS,
            run_dir=tmp_path / "drc_density_pass",
        )
        assert result.success, f"DRC run failed: {result.error}"
        # This is a pass case, should be clean (or very few violations)
        assert result.total_violations == 0, (
            f"density pass GDS should be clean, got: {result.violated_rules}"
        )


@pytest.mark.klayout
class TestEdaToolsDrcWrapper:
    """Integration tests for the higher-level run_klayout_drc wrapper."""

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        import shutil

        if not shutil.which("klayout"):
            pytest.skip("klayout not in PATH")
        if not GF180_PDK_ROOT.is_dir():
            pytest.skip("GF180MCU PDK not found")

    def test_wrapper_with_antenna_gds(self):
        """Test eda_tools.run_klayout_drc on antenna test case."""
        if not ANTENNA_GDS.is_file():
            pytest.skip(f"Antenna GDS not found: {ANTENNA_GDS}")

        from eda_agents.tools.eda_tools import run_klayout_drc

        result = run_klayout_drc(
            gds_path=str(ANTENNA_GDS),
            variant="C",
            table="antenna",
            pdk_root=str(GF180_PDK_ROOT),
        )
        assert "error" not in result, f"Wrapper error: {result.get('error')}"
        assert result["success"]
        assert result["total_errors"] > 0
        assert isinstance(result["violated_rules"], dict)

    def test_wrapper_with_comp_gds(self):
        """Test eda_tools.run_klayout_drc on comp test case."""
        if not COMP_GDS.is_file():
            pytest.skip(f"Comp GDS not found: {COMP_GDS}")

        from eda_agents.tools.eda_tools import run_klayout_drc

        result = run_klayout_drc(
            gds_path=str(COMP_GDS),
            variant="C",
            table="comp",
            pdk_root=str(GF180_PDK_ROOT),
        )
        assert "error" not in result, f"Wrapper error: {result.get('error')}"
        assert result["success"]
