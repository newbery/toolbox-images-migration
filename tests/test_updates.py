import json

import pytest

from toolbox import io, models, updates


def test_select_files_to_delete_blocks_kept_fileids_and_ignores_non_toolbox_files():
    """The `select_files_to_delete` function must block a whole file ID when any reference is kept
    and ignore non-Website Toolbox files.
    """
    toolbox_file = models.ForumFile(
        fileid="123",
        url="https://old.example.com/123/a.jpg",
        url_thumb="https://old.example.com/thumb/123/a.jpg",
        url_file="/file?id=123",
    )
    files = {
        toolbox_file.url: toolbox_file,
        toolbox_file.url_thumb: toolbox_file,
    }

    assert (
        updates.select_files_to_delete(
            files=files,
            urls_to_delete={toolbox_file.url, toolbox_file.url_thumb},
            urls_to_keep={toolbox_file.url_thumb},
        )
        == []
    )
    assert updates.select_files_to_delete(
        files=files,
        urls_to_delete={toolbox_file.url},
        urls_to_keep=set(),
    ) == [toolbox_file]

    external = models.ForumFile(
        fileid="https://legacy.example/a.jpg",
        url="https://legacy.example/a.jpg",
    )
    assert (
        updates.select_files_to_delete(
            files={external.url: external},
            urls_to_delete={external.url},
            urls_to_keep=set(),
        )
        == []
    )


def test_update_posts_dry_run_writes_preview_and_delete_candidates(ctx, monkeypatch, write_csv):
    """The `update_posts` function must write a dry-run preview and deletion candidates without
    calling the API.
    """
    # Avoid sleeping
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)

    # Prepare posts.csv to update
    msg = (
        "<p>"
        "<img src='https://old.example.com/123/a.jpg'/>"
        "<img src='https://old.example.com/thumb/123/a.jpg'/>"
        "<a href='/file?id=123'>file</a>"
        "</p>"
    )
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [
            [
                "1",
                "0",
                "['https://old.example.com/123/a.jpg', 'https://old.example.com/thumb/123/a.jpg']",
                msg,
            ],
            # This one should be unchanged (skipped file)
            [
                "2",
                "0",
                "['https://old.example.com/555/a.jpg']",
                "<img src='https://old.example.com/555/a.jpg'/>",
            ],
        ],
    )
    # Prepare files.csv input
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        [
            [
                "123",
                "{'1'}",
                "https://old.example.com/123/a.jpg",
                "https://old.example.com/thumb/123/a.jpg",
                "/file?id=123",
                "",
                str(models.FileResult.downloaded.value),
            ],
            [
                "555",
                "{'2'}",
                "https://old.example.com/555/a.jpg",
                "",
                "",
                "",
                str(models.FileResult.skipped.value),
            ],
        ],
    )

    # Patch check_new_urls/check_old_urls
    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates, "check_old_urls", lambda *_args, **_kwargs: True)

    # Fake API client: should NOT be called in dry-run mode.
    class FakeClient:
        def update_post(self, _pid, _message):
            raise AssertionError("update_post should not be called in dry-run mode")

    ctx.api_client = FakeClient()
    updates.update_posts(ctx, legacy=False)

    # updates.csv should include a dry-run result with rewritten content for pid 1
    updates_rows = list(io.read_csv(ctx.path.updates))
    row1 = next(r for r in updates_rows if r["pid"] == "1")
    assert row1["result"] == "dry_run"
    assert "https://new.example.com/123/a.jpg" in row1["content"]
    assert "https://new.example.com/thumb/123/a.jpg" in row1["content"]

    # /file?id link replaced with full url
    assert "/file?id=123" not in row1["content"]

    # In dry-run mode, fileids_to_delete.json is intentionally left empty,
    # while the would-delete set is written to fileids_to_delete.dry_run.json.
    to_delete = ctx.path.fileids_to_delete
    fileids = json.loads(to_delete.read_text())
    assert fileids == []

    to_delete_dry_run = ctx.path.fileids_to_delete_dry_run
    dry_fileids = json.loads(to_delete_dry_run.read_text())
    assert dry_fileids == ["123"]


def test_update_posts_clears_stale_delete_handoffs_before_preflight(ctx, monkeypatch, write_csv):
    """The `update_posts` function must clear stale delete handoffs before an early preflight
    return.
    """
    ctx.path.fileids_to_delete.write_text('["stale"]')
    ctx.path.fileids_to_delete_dry_run.write_text('["stale-dry"]')
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        [],
    )
    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: False)

    updates.update_posts(ctx)
    to_delete = ctx.path.fileids_to_delete
    to_delete_dry_run = ctx.path.fileids_to_delete_dry_run

    assert json.loads(to_delete.read_text()) == []
    assert json.loads(to_delete_dry_run.read_text()) == []


def test_update_posts_keeps_delete_handoff_empty_when_final_check_fails(
    ctx, monkeypatch, write_csv
):
    """The `update_posts` function must leave the destructive delete handoff empty when final
    old-reference verification fails.
    """
    ctx.dry_run = False
    ctx.args.dry_run = False
    ctx.args.yes = True
    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [
            [
                "1",
                "0",
                "['https://old.example.com/123/a.jpg']",
                "<img src='https://old.example.com/123/a.jpg'/>",
            ]
        ],
    )
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        [
            [
                "123",
                "{'1'}",
                "https://old.example.com/123/a.jpg",
                "",
                "/file?id=123",
                "",
                str(models.FileResult.downloaded.value),
            ]
        ],
    )
    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates, "check_old_urls", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)

    class FakeClient:
        def update_post(self, _pid, _message):
            return True

    ctx.api_client = FakeClient()

    with pytest.raises(RuntimeError, match="Old Toolbox references remain"):
        updates.update_posts(ctx)

    to_delete = ctx.path.fileids_to_delete
    assert json.loads(to_delete.read_text()) == []
