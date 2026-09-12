import json

from toolbox import cleanup, models


def test_archive_downloads_moves_confirmed_and_lists_remaining(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must archive confirmed files and report destination
    failures.
    """
    ctx.dry_run = False
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    good = new_dir / "123" / "Brother 160 Cambridge.jpg"
    thumb = new_dir / "thumb" / "123" / "Brother 160 Cambridge.jpg"
    missing = new_dir / "456" / "missing.jpg"
    for path, data in ((good, b"good"), (thumb, b"thumb"), (missing, b"missing")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    checked = []

    def url_ok(url):
        checked.append(url)
        return not url.endswith("456/missing.jpg")

    ctx.url_ok = url_ok
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
    assert "2 archived; 1 remaining" in out
    assert "456/missing.jpg" in out
    assert "https://new.example.com/456/missing.jpg" in out


def test_archive_downloads_handles_existing_uploaded_files(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must consume identical archived duplicates and preserve
    conflicts.
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

    ctx.url_ok = lambda _url: True
    cleanup.archive_downloads(ctx)

    assert not same_new.exists()
    assert same_uploaded.read_bytes() == b"same"
    assert conflict_new.read_bytes() == b"new"
    assert conflict_uploaded.read_bytes() == b"old"
    out = capsys.readouterr().out
    assert "456/conflict.jpg" in out
    assert "Conflicts with existing files in _uploaded_" in out


def test_archive_downloads_dry_run_does_not_change_local_files(ctx, monkeypatch, capsys):
    """The `archive_downloads` function must not modify local files or metadata during a dry run."""
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
    new_dir = ctx.path.download_dir / "_new_"
    image = new_dir / "123" / "a.jpg"
    metadata = new_dir / "123" / ".DS_Store"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    metadata.write_bytes(b"finder")

    checked = []
    ctx.url_ok = lambda url: checked.append(url) or True

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
    ctx.url_ok = lambda url: checked.append(url) or True

    cleanup.archive_downloads(ctx)

    uploaded = ctx.path.download_dir / "_uploaded_" / "123" / "nested" / "a.jpg"
    assert uploaded.read_bytes() == b"image"
    assert not (new_dir / "123").exists()
    assert not (new_dir / "empty").exists()
    assert new_dir.exists()
    assert checked == ["https://new.example.com/123/nested/a.jpg"]

    out = capsys.readouterr().out
    assert "1 archived; 0 remaining" in out


def test_check_new_urls_skips_skipped_files_and_uses_local_file_url(
    ctx, tmp_path, monkeypatch, write_csv
):
    """The `check_new_urls` function must ignore skipped files and check local destinations via
    file:// URLs.
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
        [
            ["1", "0", "['https://old.example.com/1.jpg', 'https://old.example.com/2.jpg']", "x"],
        ],
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


def test_check_new_urls_trusts_uploaded_archive_and_falls_back_to_remote(
    ctx, monkeypatch, write_csv
):
    """The `check_new_urls` function must trust archived files and check unarchived destination
    URLs.
    """
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_args, **_kwargs: None)
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
    ctx.url_ok = lambda url: checked.append(url) or True

    assert cleanup.check_new_urls(ctx, files) is True
    assert checked == ["https://new.example.com/456/b.jpg"]


def test_check_new_urls_uses_uploaded_thumbnail_archive_path(ctx, monkeypatch, write_csv):
    """The `check_new_urls` function must recognize thumbnail confirmations under the uploaded
    archive.
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


def test_grep_urls_in_file_returns_matching_pids(tmp_path):
    """The `grep_urls_in_file` function must return PIDs whose content contains any non-empty URL
    pattern.
    """
    updates = tmp_path / "updates.csv"
    updates.write_text(
        "pid,result,content\n"
        "1,success,hello https://a.example.com/x.jpg\n"
        "2,success,bye\n"
        "3,success,see https://b.example.com/y.jpg\n",
    )
    out = cleanup.grep_urls_in_file(updates, ["https://b.example.com/y.jpg", ""])
    assert out.split() == ["3"]


def test_check_old_urls_detects_references_in_updated_or_nonupdated_posts(ctx, tmp_path, write_csv):
    """The `check_old_urls` function must fail verification when old references remain in updated
    or non-updated posts.
    """
    # Create updates.csv (updated posts content)
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [
            ["10", "success", "contains https://old.example.com/123/a.jpg"],
            ["11", "success", "ok"],
        ],
    )
    # Create non-updated posts csvs that still contain a fileid pattern
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [
            ["20", "0", "[]", "legacy =123 somewhere"],
        ],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [
            ["21", "0", "[]", "nope"],
        ],
    )
    files_to_check = [
        models.ForumFile(fileid="123", url="https://old.example.com/123/a.jpg"),
    ]

    ok = cleanup.check_old_urls(ctx, files_to_check, legacy=False)
    assert ok is False


def test_check_old_urls_detects_url_file_in_updated_post(ctx, write_csv):
    """The `check_old_urls` function must detect surviving /file?id= references
    in updated content.
    """
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", "contains /file?id=123"]],
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
    files_to_check = [
        models.ForumFile(
            fileid="123",
            url="https://old.example.com/123/a.jpg",
            url_file="/file?id=123",
        ),
    ]

    assert cleanup.check_old_urls(ctx, files_to_check, legacy=False) is False


def test_check_old_urls_matches_fileids_literally(ctx, write_csv):
    """The `check_old_urls` function must treat regex metacharacters in file IDs as literal text."""
    write_csv(
        ctx.path.updates,
        ["pid", "result", "content"],
        [["10", "success", "no legacy reference"]],
    )
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["20", "0", "[]", "similar but different =12x34 reference"]],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [["21", "0", "[]", "no legacy reference"]],
    )
    files_to_check = [
        models.ForumFile(
            fileid="12.34",
            url="https://old.example.com/12.34/a.jpg",
        ),
    ]

    assert cleanup.check_old_urls(ctx, files_to_check, legacy=False) is True


def test_delete_files_batches_candidates_for_admin_client(ctx, monkeypatch):
    """The `delete_files` function must load deletion candidates and submit them to the Admin
    client in batches of 100.
    """
    ctx.path.fileids_to_delete.write_text(json.dumps([str(i) for i in range(1, 205)]))
    monkeypatch.setattr(cleanup.time, "sleep", lambda *_a, **_k: None)

    calls = []

    class FakeAdmin:
        def check_admin_auth(self):
            return True

        def delete_files(self, fileids):
            calls.append(list(fileids))

    ctx.admin_client = FakeAdmin()

    # Run in apply mode so the admin client is invoked.
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="delete_files", apply=True, yes=True)

    cleanup.delete_files(ctx)
    # Should batch at 100
    assert len(calls) == 3
    assert len(calls[0]) == 100
    assert len(calls[1]) == 100
    assert len(calls[2]) == 4
