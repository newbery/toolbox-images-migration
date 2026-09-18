import json

import pytest
import requests

from tests.helpers import write_csv
from toolbox import cleanup, clients, models


def test_archive_downloads_moves_confirmed_and_lists_remaining(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must archive confirmed files and
    report destination failures.
    """
    ctx.dry_run = False
    ctx.config.new_url_sleep = 0.4
    events = []
    monkeypatch.setattr(cleanup.time, "sleep", lambda delay: events.append(("sleep", delay)))
    progress = []

    class RecordingAliveBar:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            def bar(_n=1):
                progress.append(bar.text)

            bar.text = ""
            return bar

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(cleanup, "alive_bar", RecordingAliveBar)
    new_dir = ctx.path.download_dir / "_new_"
    good = new_dir / "123" / "Brother 160 Cambridge.jpg"
    thumb = new_dir / "thumb" / "123" / "Brother 160 Cambridge.jpg"
    missing = new_dir / "456" / "missing.jpg"
    for path, data in ((good, b"good"), (thumb, b"thumb"), (missing, b"missing")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    checked = []

    def url_status(url):
        checked.append(url)
        events.append(("check", url))
        return 404 if url.endswith("456/missing.jpg") else 200

    ctx.url_status = url_status
    cleanup.archive_downloads(ctx)

    uploaded_dir = ctx.path.download_dir / "_uploaded_"
    assert (uploaded_dir / "123" / good.name).read_bytes() == b"good"
    assert (uploaded_dir / "thumb" / "123" / thumb.name).read_bytes() == b"thumb"
    assert missing.exists()
    assert not good.exists()
    assert not thumb.exists()
    assert checked == [
        "https://new.example.com/123/Brother%20160%20Cambridge.jpg",
        "https://new.example.com/456/missing.jpg",
        "https://new.example.com/thumb/123/Brother%20160%20Cambridge.jpg",
    ]

    out = capsys.readouterr().out
    assert "Checking 3 files" in out
    assert "2 archived; 1 remaining" in out
    assert "456/missing.jpg" in out
    assert "HTTP 404" in out
    assert "https://new.example.com/456/missing.jpg" in out
    assert progress == [
        "1/3 checked; 1 found; 0 failed",
        "2/3 checked; 1 found; 1 failed",
        "3/3 checked; 2 found; 1 failed",
    ]
    assert events == [
        ("sleep", 0.4),
        ("check", checked[0]),
        ("sleep", 0.4),
        ("check", checked[1]),
        ("sleep", 0.4),
        ("check", checked[2]),
    ]
    assert "3 checked; 2 found; 1 failed; 0 unchecked" in out
    assert out.rstrip().splitlines()[-1].startswith("Archive downloads: 2 archived; 1 remaining")


def test_archive_downloads_reports_request_exceptions_and_continues(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must report request failures and
    continue checking later files.
    """
    ctx.dry_run = False
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    timed_out = new_dir / "123" / "timeout.jpg"
    good = new_dir / "456" / "good.jpg"
    for path in (timed_out, good):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

    def url_status(url):
        if url.endswith("123/timeout.jpg"):
            raise requests.ReadTimeout("timed out")
        return 200

    ctx.url_status = url_status
    cleanup.archive_downloads(ctx)

    assert timed_out.exists()
    assert not good.exists()
    out = capsys.readouterr().out
    assert "Destination checks failed:" in out
    assert "123/timeout.jpg: ReadTimeout: timed out" in out
    assert "2 checked; 1 found; 1 failed; 0 unchecked" in out


def test_archive_downloads_ctrl_c_reports_partial_results_and_exits_130(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must report partial results, exit 130,
    and suppress a traceback when interrupted.
    """
    ctx.dry_run = False
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    first = new_dir / "123" / "missing.jpg"
    second = new_dir / "456" / "unchecked.jpg"
    third = new_dir / "789" / "also-unchecked.jpg"
    for path in (first, second, third):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

    calls = 0

    def url_status(_url):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 404
        raise KeyboardInterrupt

    ctx.url_status = url_status

    with pytest.raises(SystemExit) as exc_info:
        cleanup.archive_downloads(ctx)

    assert exc_info.value.code == 130
    assert first.exists()
    assert second.exists()
    assert third.exists()
    out = capsys.readouterr().out
    assert "Interrupted after checking 1/3 files" in out
    assert "Destination checks failed:" in out
    assert "123/missing.jpg: HTTP 404" in out
    assert "1 checked; 0 found; 1 failed; 2 unchecked" in out
    assert "0 archived; 3 remaining" in out
    assert "456/unchecked.jpg" not in out
    assert "789/also-unchecked.jpg" not in out
    assert capsys.readouterr().err == ""


def test_archive_downloads_handles_existing_uploaded_files(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must consume identical archived duplicates
    and preserve conflicts.
    """
    ctx.dry_run = False
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    uploaded_dir = ctx.path.download_dir / "_uploaded_"

    same_new = new_dir / "123" / "same.jpg"
    same_uploaded = uploaded_dir / "123" / "same.jpg"
    conflict_new = new_dir / "456" / "conflict.jpg"
    conflict_uploaded = uploaded_dir / "456" / "conflict.jpg"
    for path, data in (
        (same_new, b"same"),
        (same_uploaded, b"same"),
        (conflict_new, b"new"),
        (conflict_uploaded, b"old"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    ctx.url_status = lambda _url: 200
    cleanup.archive_downloads(ctx)

    assert not same_new.exists()
    assert same_uploaded.read_bytes() == b"same"
    assert conflict_new.read_bytes() == b"new"
    assert conflict_uploaded.read_bytes() == b"old"
    out = capsys.readouterr().out
    assert "456/conflict.jpg" in out
    assert "Conflicts with existing files in _uploaded_" in out


def test_archive_downloads_dry_run_does_not_change_local_files(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must not modify local files or metadata
    during a dry run.
    """
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    image = new_dir / "123" / "a.jpg"
    metadata = new_dir / "123" / ".DS_Store"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    metadata.write_bytes(b"finder")

    checked = []
    ctx.url_status = lambda url: checked.append(url) or 200

    cleanup.archive_downloads(ctx)

    assert image.read_bytes() == b"image"
    assert metadata.read_bytes() == b"finder"

    assert not (ctx.path.download_dir / "_uploaded_" / "123" / "a.jpg").exists()
    assert checked == ["https://new.example.com/123/a.jpg"]

    out = capsys.readouterr().out
    assert "Dry run; no local files will be moved or deleted" in out
    assert "1 would archive; 0 would remain" in out


def test_archive_downloads_removes_ds_store_and_empty_directories(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must remove Finder metadata and
    prune emptied directories.
    """
    ctx.dry_run = False
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    image = new_dir / "123" / "nested" / "a.jpg"
    metadata = image.parent / ".DS_Store"
    metadata_only = new_dir / "empty" / "nested" / ".DS_Store"
    image.parent.mkdir(parents=True)
    metadata_only.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    metadata.write_bytes(b"finder")
    metadata_only.write_bytes(b"finder")

    checked = []
    ctx.url_status = lambda url: checked.append(url) or 200

    cleanup.archive_downloads(ctx)

    uploaded = ctx.path.download_dir / "_uploaded_" / "123" / "nested" / "a.jpg"
    assert uploaded.read_bytes() == b"image"
    assert not (new_dir / "123").exists()
    assert not (new_dir / "empty").exists()
    assert new_dir.exists()
    assert checked == ["https://new.example.com/123/nested/a.jpg"]

    out = capsys.readouterr().out
    assert "1 archived; 0 remaining" in out


def test_check_new_urls_skips_skipped_files_and_uses_local_file_url(ctx, tmp_path, monkeypatch):
    """The `check_new_urls` function must ignore skipped files and check
    local destinations via file:// URLs.
    """
    # Use local directory as "new_url" and dry-run=True to enable file:// prefix
    new_root = tmp_path / "new"
    new_root.mkdir()
    ctx.config.new_url = str(new_root)  # not http(s)
    ctx.dry_run = True

    # posts.csv with two urls, one skipped, one checked
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", "['https://old.example.com/1.jpg', 'https://old.example.com/2.jpg']", "x"]],
    )
    files = {
        "https://old.example.com/1.jpg": models.ForumFile(
            fileid="1",
            url="https://old.example.com/1.jpg",
            result=models.FileResult.skipped,
        ),
        "https://old.example.com/2.jpg": models.ForumFile(
            fileid="2",
            url="https://old.example.com/2.jpg",
            result=models.FileResult.downloaded,
        ),
    }

    def url_ok(url: str) -> bool:
        # It should be a file:// url pointing into new_root
        assert url.startswith("file://")
        return True

    ctx.url_ok = url_ok

    assert cleanup.check_new_urls(ctx, files) is True


def test_check_new_urls_trusts_uploaded_archive_and_falls_back_to_remote(ctx, monkeypatch):
    """The `check_new_urls` function must trust archived files and check
    unarchived destination URLs.
    """
    ctx.config.new_url_sleep = 0.6
    events = []
    monkeypatch.setattr(cleanup.time, "sleep", lambda delay: events.append(("sleep", delay)))
    uploaded = ctx.path.download_dir / "_uploaded_" / "123" / "a.jpg"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"confirmed")

    urls = [
        "https://old.example.com/123/a.jpg",
        "https://old.example.com/456/b.jpg",
    ]
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", repr(urls), "x"]],
    )
    downloaded = models.FileResult.downloaded
    files = {
        urls[0]: models.ForumFile(fileid="123", url=urls[0], path="123/a.jpg", result=downloaded),
        urls[1]: models.ForumFile(fileid="456", url=urls[1], path="456/b.jpg", result=downloaded),
    }
    checked = []

    def url_ok(url):
        checked.append(url)
        events.append(("check", url))
        return True

    ctx.url_ok = url_ok

    assert cleanup.check_new_urls(ctx, files) is True
    assert checked == ["https://new.example.com/456/b.jpg"]
    assert events == [("sleep", 0.6), ("check", checked[0])]


def test_check_new_urls_uses_uploaded_thumbnail_archive_path(ctx, monkeypatch):
    """The `check_new_urls` function must recognize thumbnail confirmations
    under the uploaded archive.
    """
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    url = "https://old.example.com/123/a.jpg"
    url_thumb = "https://old.example.com/thumb/123/a.jpg"
    uploaded = ctx.path.download_dir / "_uploaded_" / "thumb" / "123" / "a.jpg"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"confirmed")

    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", repr([url_thumb]), "x"]],
    )

    downloaded = models.FileResult.downloaded
    file = models.ForumFile(
        fileid="123", url=url, url_thumb=url_thumb, path="123/a.jpg", result=downloaded
    )
    ctx.url_ok = lambda _url: (_ for _ in ()).throw(
        AssertionError("archived thumbnail should not be checked remotely")
    )

    assert cleanup.check_new_urls(ctx, {url_thumb: file}) is True


def test_check_new_urls_falls_back_to_full_image_when_thumbnail_failed(ctx):
    """The `check_new_urls` function must verify the full-image destination when thumbnail
    migration fails.
    """
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_thumb=thumb,
        path="123/a.jpg",
        result=models.FileResult.downloaded,
        thumb_result=models.FileResult.error,
    )
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", repr([thumb]), f"<a href='{full}'><img src='{thumb}'></a>"]],
    )
    seen = []
    ctx.url_ok = lambda url: seen.append(url) or True

    assert cleanup.check_new_urls(ctx, {full: file, thumb: file}) is True
    assert seen == ["https://new.example.com/123/a.jpg"]


def test_check_new_urls_uses_thumbnail_when_full_image_failed(ctx, monkeypatch):
    """The `check_new_urls` function must verify the thumbnail destination when full-image
    migration fails.
    """
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", repr([thumb]), f"<a href='{full}'><img src='{thumb}'></a>"]],
    )
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_thumb=thumb,
        path="123/a.jpg",
        result=models.FileResult.error,
        thumb_result=models.FileResult.downloaded,
    )
    files = {full: file, thumb: file}
    checked = []
    ctx.url_ok = lambda url: checked.append(url) or True

    assert cleanup.check_new_urls(ctx, files) is True
    assert checked == ["https://new.example.com/thumb/123/a.jpg"]


def test_check_new_urls_skips_unrecoverable_source_pair(ctx, monkeypatch):
    """The `check_new_urls` function must skip destination checks when both
    source variants are unrecoverable.
    """
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", repr([thumb]), f"<img src='{thumb}'>"]],
    )
    error = models.FileResult.error
    file = models.ForumFile(
        fileid="123", url=full, url_thumb=thumb, result=error, thumb_result=error
    )
    files = {full: file, thumb: file}
    checked = []
    ctx.url_ok = lambda url: checked.append(url) or True

    assert cleanup.check_new_urls(ctx, files) is True
    assert checked == []


def test_references_in_content_matches_entity_encoded_html_reference():
    """The `_references_in_content` function must decode HTML entities when
    matching references.
    """
    full = "https://old.example.com/123/seller's.jpg"
    files = {"123": models.ForumFile(fileid="123", url=full)}
    content = '<a href="https://old.example.com/123/seller&#39;s.jpg">image</a>'

    matches = cleanup._references_in_content(content, files)

    assert matches == [("123", "url", full)]


def test_check_old_urls_detects_url_file_in_updated_post(ctx):
    """The `check_old_urls` function must detect surviving /file?id= references
    in updated content.
    """
    url = "https://old.example.com/123/a.jpg"
    url_file = "/file?id=123"
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", f"contains {url_file}"]],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["20", "0", "[]", "no legacy reference"]],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [["21", "0", "[]", "no legacy reference"]],
    )
    files_to_check = [models.ForumFile(fileid="123", url=url, url_file=url_file)]

    assert cleanup.check_old_urls(ctx, files_to_check, legacy=False) is False


def test_check_old_urls_excludes_successfully_updated_pids_from_source_snapshots(ctx):
    """The `check_old_urls` function must ignore old source references for
    successfully updated posts.
    """
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", "rewritten content"]],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["10", "0", "[]", "original reference /file?id=123"]],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [],
    )

    files_to_check = [
        models.ForumFile(fileid="123", url="https://old.example.com/123/a.jpg"),
    ]

    assert cleanup.check_old_urls(ctx, files_to_check) is True


def test_check_old_urls_does_not_exclude_failed_update_pids(ctx, capsys):
    """The `check_old_urls` function must treat failed updates as non-updated
    source posts.
    """
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "fail", "rewritten content"]],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["10", "0", "[]", "original reference /file?id=123"]],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [],
    )

    files_to_check = [
        models.ForumFile(fileid="123", url="https://old.example.com/123/a.jpg"),
    ]

    assert cleanup.check_old_urls(ctx, files_to_check) is False
    assert "Old fileids found in these non-updated posts" in capsys.readouterr().out


def test_check_old_urls_does_not_treat_destination_fileid_path_as_old_reference(ctx):
    """The `check_old_urls` function must not treat destination file-ID paths
    as old references.
    """
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", "https://new.example.com/123/a.jpg"]],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["20", "0", "[]", "no legacy reference"]],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [],
    )

    files_to_check = [
        models.ForumFile(fileid="123", url="https://old.example.com/123/a.jpg"),
    ]

    assert cleanup.check_old_urls(ctx, files_to_check) is True


def test_check_old_urls_writes_exact_diagnostic_report(ctx, capsys):
    """The `check_old_urls` function must write exact diagnostics for surviving
    old references.
    """
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", '<img src="https://old.example.com/123/a.jpg">']],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["20", "0", "[]", '<a href="/file?id=456">download</a>']],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [],
    )
    files_to_check = [
        models.ForumFile(
            fileid="123",
            url="https://old.example.com/123/a.jpg",
            url_file="/file?id=123",
        ),
        models.ForumFile(
            fileid="456",
            url="https://old.example.com/456/b.jpg",
            url_file="/file?id=456",
        ),
    ]

    assert cleanup.check_old_urls(ctx, files_to_check) is False

    rows = list(cleanup.read_csv(ctx.path.old_reference_failures))
    assert rows == [
        {
            "state": "non-updated",
            "source": "posts_from_export.csv",
            "pid": "20",
            "fileid": "456",
            "kind": "file",
            "reference": "/file?id=456",
        },
        {
            "state": "updated",
            "source": "updates.csv",
            "pid": "10",
            "fileid": "123",
            "kind": "url",
            "reference": "https://old.example.com/123/a.jpg",
        },
    ]
    assert "diagnostic report written to:" in capsys.readouterr().out


def test_check_old_urls_removes_stale_diagnostic_report_on_success(ctx):
    """The `check_old_urls` function must remove a stale diagnostic report
    after successful verification.
    """
    ctx.path.old_reference_failures.write_text("stale")
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", "https://new.example.com/123/a.jpg"]],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [],
    )
    files = [models.ForumFile(fileid="123", url="https://old.example.com/123/a.jpg")]

    assert cleanup.check_old_urls(ctx, files) is True
    assert not ctx.path.old_reference_failures.exists()


class _FakeDeleteAdmin:
    def __init__(self):
        self.defaults = {"action": "deleteFiles"}

    def get_delete_defaults(self):
        return self.defaults


def _http_error(status: int, *, retry_after: str | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(f"HTTP {status}", response=response)


def test_delete_files_checkpoints_each_successful_batch(ctx, monkeypatch, capsys):
    """The `delete_files` function must checkpoint confirmed batches while
    preserving unconfirmed IDs.
    """
    original = [str(i) for i in range(1, 206)]
    ctx.path.fileids_to_delete.write_text(json.dumps(original))
    calls = []
    sleeps = []

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            assert defaults is self.defaults
            calls.append(list(fileids))
            if len(calls) == 2:
                raise _http_error(500)
            return clients.DeleteConfirmation(
                message=f"{len(fileids)} files have been deleted.", count=len(fileids)
            )

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.config.admin_url_sleep = 2.5
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)
    monkeypatch.setattr(cleanup.time, "sleep", sleeps.append)

    with pytest.raises(SystemExit) as error:
        cleanup.delete_files(ctx)

    assert error.value.code == 1
    assert calls[0] == original[:100]
    assert calls[1] == original[100:200]
    assert sleeps == [2.5, 2.5]
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == original[100:]
    assert "105 remaining; checkpointed" in capsys.readouterr().out


def test_delete_files_retries_429_and_honors_retry_after(ctx, monkeypatch, capsys):
    """The `delete_files` function must retry HTTP 429 responses and honor
    Retry-After
    ."""
    ctx.path.fileids_to_delete.write_text(json.dumps(["1", "2", "3"]))
    calls = []
    sleeps = []

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            calls.append(list(fileids))
            if len(calls) == 1:
                raise _http_error(429, retry_after="7")
            return clients.DeleteConfirmation(
                message=f"{len(fileids)} files have been deleted.", count=len(fileids)
            )

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.config.admin_url_sleep = 2.5
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)
    monkeypatch.setattr(cleanup.time, "sleep", sleeps.append)

    cleanup.delete_files(ctx)

    assert calls == [["1", "2", "3"], ["1", "2", "3"]]
    assert sleeps == [2.5, 7.0]
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == []
    out = capsys.readouterr().out
    assert "HTTP 429 Too Many Requests" in out
    assert "3 submitted; 3 confirmed deleted; 0 unresolved; 0 remaining" in out


def test_delete_files_interruption_preserves_checkpoint(ctx):
    """The `delete_files` function must preserve the current and later batches
    when interrupted.
    """
    original = [str(i) for i in range(1, 206)]
    ctx.path.fileids_to_delete.write_text(json.dumps(original))
    calls = []

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            calls.append(list(fileids))
            if len(calls) == 2:
                raise KeyboardInterrupt
            return clients.DeleteConfirmation(
                message=f"{len(fileids)} files have been deleted.", count=len(fileids)
            )

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)

    with pytest.raises(SystemExit) as error:
        cleanup.delete_files(ctx)

    assert error.value.code == 130
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == original[100:]


def test_delete_files_dry_run_reports_count(ctx, capsys):
    """The `delete_files` function must report candidate and submission counts
    during a dry run.
    """
    ctx.path.fileids_to_delete.write_text(json.dumps(["1", "2", "3"]))
    ctx.dry_run = True

    cleanup.delete_files(ctx)

    out = capsys.readouterr().out
    assert "Delete files: 3 candidates" in out
    assert "Delete files: would submit 3 files" in out


def test_delete_files_limit_checkpoints_only_requested_ids(ctx, capsys):
    """The `delete_files` function must honor --delete-limit and preserve the
    remaining IDs.
    """
    original = ["1", "2", "3", "4", "5"]
    ctx.path.fileids_to_delete.write_text(json.dumps(original))
    calls = []

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            calls.append(list(fileids))
            return clients.DeleteConfirmation(
                message=f"{len(fileids)} files have been deleted.", count=len(fileids)
            )

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.args = models.CliArgs(
        mode="delete_files",
        apply=True,
        yes=True,
        delete_limit=3,
    )

    cleanup.delete_files(ctx)

    assert calls == [["1", "2", "3"]]
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == ["4", "5"]
    out = capsys.readouterr().out
    assert "limiting this run to 3 files" in out
    assert "Website Toolbox confirmation: 3 files have been deleted." in out
    assert "3 submitted; 3 confirmed deleted; 0 unresolved; 2 remaining" in out


def test_delete_files_unconfirmed_response_does_not_checkpoint(ctx, capsys):
    """The `delete_files` function must keep the current batch pending when
    deletion is unconfirmed.
    """
    original = ["1", "2", "3"]
    ctx.path.fileids_to_delete.write_text(json.dumps(original))

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            raise RuntimeError("Website Toolbox did not confirm deletion")

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)

    with pytest.raises(SystemExit) as error:
        cleanup.delete_files(ctx)

    assert error.value.code == 1
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == original
    assert "0 submitted, 0 confirmed deleted; 3 remaining" in capsys.readouterr().out


def test_delete_files_partial_confirmation_preserves_ambiguous_batch(ctx, capsys):
    """The `delete_files` function must preserve and report batches with
    ambiguous partial confirmation.
    """
    original = [str(i) for i in range(1, 106)]
    ctx.path.fileids_to_delete.write_text(json.dumps(original))

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            return clients.DeleteConfirmation(message="93 files have been deleted.", count=93)

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)

    with pytest.raises(SystemExit) as error:
        cleanup.delete_files(ctx)

    assert error.value.code == 1
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == original
    report = json.loads(ctx.path.delete_unresolved.read_text())
    assert report == {
        "submitted": 100,
        "confirmed_deleted": 93,
        "unresolved": 7,
        "confirmation": "93 files have been deleted.",
        "fileids": original[:100],
    }
    out = capsys.readouterr().out
    assert "100 submitted, 93 confirmed deleted, 7 unresolved" in out
    assert "entire batch remains checkpointed" in out
    assert "100 submitted; 93 confirmed deleted; 7 unresolved; 105 checkpointed" in out


def test_delete_files_success_removes_stale_unresolved_report(ctx):
    """The `delete_files` function must remove stale unresolved-delete reports
    after a clean run.
    """
    ctx.path.fileids_to_delete.write_text(json.dumps(["1", "2"]))
    ctx.path.delete_unresolved.write_text("stale")

    class FakeAdmin(_FakeDeleteAdmin):
        def delete_files(self, fileids, defaults):
            return clients.DeleteConfirmation(message="2 files have been deleted.", count=2)

    ctx.admin_client = FakeAdmin()
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)

    cleanup.delete_files(ctx)

    assert not ctx.path.delete_unresolved.exists()
