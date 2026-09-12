from datetime import datetime

import pytest

from toolbox import context, io, models


def test_batched_yields_consecutive_batches():
    """The `batched` function must preserve order and yield a shorter final batch when needed."""
    assert list(io.batched([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_batched_rejects_non_positive_batch_size():
    """The `batched` function must reject non-positive batch sizes."""
    with pytest.raises(ValueError, match="n must be at least one"):
        list(io.batched([1, 2], 0))


def test_friendly_size_formats_expected_units():
    """The `friendly_size` function must format byte counts using the expected unit thresholds and
    labels.
    """
    assert io.friendly_size(10) == "10 bytes"
    assert io.friendly_size(1024) == "1024 bytes"
    assert io.friendly_size(1025) == "1 kb"
    assert io.friendly_size(1024 * 1024 + 10) == "1 MB"


def test_read_csv_missing_file_yields_no_rows(tmp_path):
    """The `read_csv` function must yield no rows when the input file does not exist."""
    rows = list(io.read_csv(tmp_path / "missing.csv"))
    assert rows == []


def test_read_csv_yields_header_keyed_string_rows(tmp_path):
    """The `read_csv` function must yield rows as dictionaries keyed by CSV headers with string
    values.
    """
    p = tmp_path / "a.csv"
    p.write_text("pid,date,message\n1,2,hi\n")
    rows = list(io.read_csv(p))
    assert rows == [{"pid": "1", "date": "2", "message": "hi"}]


def test_linecount_missing_file_returns_zero(tmp_path):
    """The `linecount` function must return zero when the input file does not exist."""
    assert io.linecount(tmp_path / "nope.txt") == 0


def test_linecount_counts_existing_file_lines(tmp_path):
    """The `linecount` function must return the number of newline-delimited lines in an existing
    file.
    """
    p = tmp_path / "x.txt"
    p.write_text("a\nb\nc\n")
    assert io.linecount(p) == 3


def test_linecount_reflects_file_changes(tmp_path):
    """The `linecount` function must reflect changes made after an earlier count."""
    p = tmp_path / "x.txt"
    p.write_text("a\n")
    assert io.linecount(p) == 1

    p.write_text("a\nb\n")
    assert io.linecount(p) == 2


def test_rotate_output_archive_archives_output_and_prunes_old_archives(
    tmp_path, monkeypatch, config_for
):
    """The `rotate_output_archive` function must archive a non-empty output directory, recreate it,
    and prune older archives beyond the retention count.
    """
    export_dir = tmp_path / "export"
    download_dir = tmp_path / "downloads"
    output_dir = tmp_path / "out"
    for d in (export_dir, download_dir, output_dir):
        d.mkdir()
    cfg = config_for(
        tmp_path,
        export_dir=export_dir,
        download_dir=download_dir,
        output_dir=output_dir,
    )
    ctx = context.Context(
        args=models.CliArgs(mode="download_files"),
        config=cfg,
        path=context.paths(cfg),
        dry_run=True,
    )

    # make output non-empty so it will archive
    (output_dir / "something.txt").write_text("x")
    # create existing archives to trigger pruning
    archive_dir = output_dir.with_name(output_dir.name + ".archive")
    archive_dir.mkdir()
    for i in range(5):
        (archive_dir / f"old{i}").mkdir()

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2020, 1, 2, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr(io, "datetime", FixedDateTime)

    # prune down to 2
    io.rotate_output_archive(ctx, count=2)
    # output should exist again and be empty (aside from new work)
    assert output_dir.exists()
    assert (output_dir / "something.txt").exists() is False

    # archives should be at most 2 + the new one created
    dirs = [p for p in archive_dir.iterdir() if p.is_dir()]
    assert len(dirs) <= 3  # old pruned + new archive (timestamp) + maybe some remain


def test_log_appends_timestamped_message(ctx, monkeypatch):
    """The `log` function must append a timestamped message to the run log."""

    class FixedDateTime:
        @classmethod
        def now(cls):
            return datetime(2020, 1, 2, 3, 4, 5)

    monkeypatch.setattr(io, "datetime", FixedDateTime)
    io.log(ctx, text="hello")
    txt = ctx.path.log.read_text()
    assert "hello" in txt
    assert "2020-01-02" in txt
