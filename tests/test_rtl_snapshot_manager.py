"""Tests for RtlSnapshotManager."""

from eda_agents.agents.rtl_snapshot_manager import RtlSnapshotManager


RTL_CONTENT_V1 = """\
module counter(
    input wire clk, rst_n, en,
    output reg [3:0] count
);
    always @(posedge clk or negedge rst_n)
        if (!rst_n) count <= 0;
        else if (en) count <= count + 1;
endmodule
"""

RTL_CONTENT_V2 = """\
module counter(
    input wire clk, rst_n, en,
    output reg [3:0] count
);
    // Optimized: use shift-add
    always @(posedge clk or negedge rst_n)
        if (!rst_n) count <= 0;
        else if (en) count <= count + 4'd1;
endmodule
"""


def _setup(tmp_path):
    """Create a project dir with one RTL file."""
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    rtl = src_dir / "counter.v"
    rtl.write_text(RTL_CONTENT_V1)
    work = tmp_path / "work"
    return project, work, [rtl]


class TestInit:
    def test_init_creates_snapshots(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources)

        snap = work / "rtl_snapshots" / "best" / "src" / "counter.v"
        assert snap.is_file()
        assert snap.read_text() == RTL_CONTENT_V1

        orig = work / "rtl_snapshots" / "original" / "src" / "counter.v"
        assert orig.is_file()

    def test_init_is_noop_on_resume(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources)

        # Modify the best snapshot manually
        snap = work / "rtl_snapshots" / "best" / "src" / "counter.v"
        snap.write_text("modified")

        # Re-init should NOT overwrite
        mgr.init_from_originals(sources)
        assert snap.read_text() == "modified"

    def test_init_with_config(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        config = project / "config.yaml"
        config.write_text("DESIGN_NAME: counter\n")

        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources, config_path=config)

        config_snap = work / "rtl_snapshots" / "config_best" / "config.yaml"
        assert config_snap.is_file()
        assert "counter" in config_snap.read_text()


class TestRestoreAndUpdate:
    def test_restore_best_overwrites_current(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources)

        # Modify RTL in project
        sources[0].write_text(RTL_CONTENT_V2)
        assert "shift-add" in sources[0].read_text()

        # Restore should bring back V1
        mgr.restore_best(sources)
        assert sources[0].read_text() == RTL_CONTENT_V1

    def test_update_best_captures_current(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources)

        # Modify RTL and update best
        sources[0].write_text(RTL_CONTENT_V2)
        mgr.update_best(sources)

        # Now restore should give V2
        sources[0].write_text("garbage")
        mgr.restore_best(sources)
        assert sources[0].read_text() == RTL_CONTENT_V2

    def test_config_restore(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        config = project / "config.yaml"
        config.write_text("CLOCK_PERIOD: 50\n")

        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources, config_path=config)

        # Modify config
        config.write_text("CLOCK_PERIOD: 100\n")

        # Restore
        mgr.restore_best(sources, config_path=config)
        assert "50" in config.read_text()


class TestApplyChanges:
    def test_apply_writes_content(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)

        written = mgr.apply_rtl_changes({"src/counter.v": RTL_CONTENT_V2})
        assert len(written) == 1
        assert written[0].read_text() == RTL_CONTENT_V2

    def test_apply_creates_new_file(self, tmp_path):
        project, work, _ = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)

        written = mgr.apply_rtl_changes({"src/new_module.v": "module new(); endmodule\n"})
        assert (project / "src" / "new_module.v").is_file()
        assert len(written) == 1


class TestDiffAndHash:
    def test_diff_summary_shows_changes(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources)

        sources[0].write_text(RTL_CONTENT_V2)
        diff = mgr.diff_summary(sources)
        assert "shift-add" in diff
        assert "---" in diff  # unified diff marker

    def test_diff_summary_no_changes(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)
        mgr.init_from_originals(sources)

        diff = mgr.diff_summary(sources)
        assert diff == "(no changes)"

    def test_content_hash_changes(self, tmp_path):
        project, work, sources = _setup(tmp_path)
        mgr = RtlSnapshotManager(work, project)

        h1 = mgr.content_hash(sources)
        sources[0].write_text(RTL_CONTENT_V2)
        h2 = mgr.content_hash(sources)
        assert h1 != h2
        assert len(h1) == 12  # truncated md5
