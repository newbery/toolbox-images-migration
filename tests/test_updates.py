import json

import pytest

from tests.helpers import write_csv
from toolbox import io, models, updates


def test_rewrite_post_content_preserves_full_and_thumbnail_destinations():
    """The `rewrite_post_content` function must preserve distinct full-image
    and thumbnail destinations when both migrations succeed.
    """
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_thumb=thumb,
        url_file="/file?id=123",
        result=models.FileResult.downloaded,
        thumb_result=models.FileResult.downloaded,
    )
    files = {full: file, thumb: file, file.url_file: file}
    message = f"<a href='{full}'><img src='{thumb}'></a>"

    rewritten, touched = updates.rewrite_post_content(
        message=message,
        image_urls=[thumb],
        files=files,
        legacy=False,
        new_url_func=lambda url: url.replace(
            "https://old.example.com/", "https://new.example.com/"
        ),
    )

    assert "href='https://new.example.com/123/a.jpg'" in rewritten
    assert "src='https://new.example.com/thumb/123/a.jpg'" in rewritten
    assert touched == {full}


def test_rewrite_post_content_rewrites_known_reference_not_in_image_urls():
    """The `rewrite_post_content` function must rewrite known migrated-file
    references even when they are absent from image discovery.
    """
    full = "https://old.example.com/123/a.jpg"
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_file="/file?id=123",
        result=models.FileResult.downloaded,
    )
    files = {full: file, file.url_file: file}
    message = f"<a href='{full}'>full</a> <a href='/file?id=123'>file</a>"

    rewritten, touched = updates.rewrite_post_content(
        message=message,
        image_urls=[],
        files=files,
        legacy=False,
        new_url_func=lambda url: url.replace(
            "https://old.example.com/", "https://new.example.com/"
        ),
    )

    assert full not in rewritten
    assert "/file?id=123" not in rewritten
    assert rewritten.count("https://new.example.com/123/a.jpg") == 2
    assert touched == {full}


def test_rewrite_post_content_preserves_skipped_file():
    """The `rewrite_post_content` function must leave intentionally skipped
    files unchanged.
    """
    full = "https://old.example.com/123/a.jpg"
    skipped = models.FileResult.skipped
    file = models.ForumFile(fileid="123", url=full, result=skipped)

    message = f"<img src='{full}'>"
    rewritten, touched = updates.rewrite_post_content(
        message=message,
        image_urls=[full],
        files={full: file},
        legacy=False,
        new_url_func=lambda url: url.replace("old.example.com", "new.example.com"),
    )

    assert rewritten == message
    assert touched == set()


def test_rewrite_post_content_uses_full_image_when_thumbnail_fails():
    """The `rewrite_post_content` function must fall back to the migrated
    full image when thumbnail migration fails.
    """
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_thumb=thumb,
        result=models.FileResult.downloaded,
        thumb_result=models.FileResult.error,
    )
    files = {full: file, thumb: file}
    message = f"<a href='{full}'><img src='{thumb}'></a>"

    rewritten, _ = updates.rewrite_post_content(
        message=message,
        image_urls=[thumb],
        files=files,
        legacy=False,
        new_url_func=lambda url: url.replace(
            "https://old.example.com/", "https://new.example.com/"
        ),
    )

    assert "href='https://new.example.com/123/a.jpg'" in rewritten
    assert "src='https://new.example.com/123/a.jpg'" in rewritten


def test_rewrite_post_content_uses_thumbnail_when_full_image_fails():
    """The `rewrite_post_content` function must fall back to the migrated
    thumbnail when full-image migration fails.
    """
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_thumb=thumb,
        url_file="/file?id=123",
        result=models.FileResult.error,
        thumb_result=models.FileResult.downloaded,
    )
    files = {full: file, thumb: file, file.url_file: file}
    message = f"<a href='{full}'><img src='{thumb}'></a> <a href='/file?id=123'>file</a>"

    rewritten, touched = updates.rewrite_post_content(
        message=message,
        image_urls=[thumb],
        files=files,
        legacy=False,
        new_url_func=lambda url: url.replace(
            "https://old.example.com/", "https://new.example.com/"
        ),
    )

    assert full not in rewritten
    assert "/file?id=123" not in rewritten
    assert rewritten.count("https://new.example.com/thumb/123/a.jpg") == 3
    assert touched == {full}


def test_rewrite_post_content_marks_unrecoverable_media_as_missing():
    """The `rewrite_post_content` function must remove unusable full-image
    and thumbnail references while preserving visible link text and marking
    the media as missing.
    """
    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    file_url = "/file?id=123"
    file = models.ForumFile(
        fileid="123",
        url=full,
        url_thumb=thumb,
        url_file=file_url,
        result=models.FileResult.error,
        thumb_result=models.FileResult.error,
    )
    files = {full: file, thumb: file, file_url: file}
    msg = f"<a href='{full}'><img src='{thumb}'></a> <a href='{file_url}'>attachment</a>"

    rewritten, touched = updates.rewrite_post_content(
        message=msg, image_urls=[thumb], files=files, legacy=False, new_url_func=lambda url: url
    )

    assert full not in rewritten
    assert thumb not in rewritten
    assert file_url not in rewritten
    assert "<img" not in rewritten
    assert "<a" not in rewritten
    assert "missing-image" in rewritten
    assert "(missing image)" in rewritten
    assert "attachment" in rewritten
    assert touched == {full}


@pytest.mark.parametrize(
    "raw_reference",
    [
        "https://old.example.com/123/seller&#39;s.jpg",
        "https://old.example.com/123/seller&#x27;s.jpg",
        "https://old.example.com/123/seller&apos;s.jpg",
    ],
)
def test_rewrite_post_content_matches_html_encoded_attribute_urls(raw_reference):
    """The `rewrite_post_content` function must match HTML-entity-encoded
    attribute URLs semantically.
    """
    full = "https://old.example.com/123/seller's.jpg"
    file = models.ForumFile(
        fileid="123",
        url=full,
        result=models.FileResult.downloaded,
    )

    rewritten, touched = updates.rewrite_post_content(
        message=f'<a href="{raw_reference}">image</a>',
        image_urls=[],
        files={full: file},
        legacy=False,
        new_url_func=lambda url: url.replace("old.example.com", "new.example.com"),
    )

    assert "old.example.com" not in rewritten
    assert "new.example.com" in rewritten
    assert touched == {full}


def test_select_files_to_delete_blocks_kept_fileids_and_ignores_non_toolbox_files():
    """The `select_files_to_delete` function must block a whole file ID when
    any reference is kept and ignore non-Website Toolbox files.
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


def test_update_posts_dry_run_writes_preview_and_delete_candidates(ctx, monkeypatch):
    """The `update_posts` function must write dry-run previews and delete candidates
    without calling the API or modifying apply state.
    """
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)

    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    skip = "https://old.example.com/555/a.jpg"

    msg = f"<p><img src='{full}'/><img src='{thumb}'/><a href='/file?id=123'>file</a></p>"

    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [
            ["1", "0", f"['{full}', '{thumb}']", msg],
            ["2", "0", f"['{skip}']", f"<img src='{skip}'/>"],
        ],
    )

    downloaded = str(models.FileResult.downloaded.value)
    skipped = str(models.FileResult.skipped.value)
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        [
            ["123", "{'1'}", full, thumb, "/file?id=123", "", downloaded],
            ["555", "{'2'}", skip, "", "", "", skipped],
        ],
    )

    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates, "check_old_urls", lambda *_args, **_kwargs: True)

    class FakeClient:
        def update_post(self, _pid, _message):
            raise AssertionError("update_post should not be called in dry-run mode")

    ctx.api_client = FakeClient()

    existing_updates = "pid,result,content\n99,success,applied\n"
    ctx.path.updates.write_text(existing_updates)
    ctx.path.fileids_to_delete.write_text(json.dumps(["existing"]))

    updates.update_posts(ctx, legacy=False)

    assert ctx.path.updates.read_text() == existing_updates
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == ["existing"]

    updates_rows = list(io.read_csv(ctx.path.updates_dry_run))
    assert [row["pid"] for row in updates_rows] == ["1"]

    row = updates_rows[0]
    assert row["result"] == "dry_run"
    assert "https://new.example.com/" in row["content"]

    assert json.loads(ctx.path.fileids_to_delete_dry_run.read_text()) == ["123"]


def test_update_posts_dry_run_does_not_mutate_apply_state(ctx, monkeypatch):
    """The `update_posts` function must leave the apply journal and delete
    handoff unchanged when a dry-run preflight fails.
    """
    ctx.path.updates.write_text("pid,result,content\n1,success,applied\n")
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

    assert "1,success,applied" in ctx.path.updates.read_text()
    assert json.loads(to_delete.read_text()) == ["stale"]
    assert json.loads(to_delete_dry_run.read_text()) == []


def _write_simple_update_inputs(ctx, pids=("1",)):
    post_rows = []
    file_rows = []
    result = str(models.FileResult.downloaded.value)

    for pid in pids:
        fileid = str(100 + int(pid))
        url = f"https://old.example.com/{fileid}/a.jpg"
        post_rows.append([pid, "0", repr([url]), f"<img src='{url}'>"])
        file_rows.append([fileid, repr({pid}), url, "", f"/file?id={fileid}", "", result])

    write_csv(ctx.path.posts, ["pid", "date", "image_urls", "message"], post_rows)
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        file_rows,
    )


def _set_apply_mode(ctx):
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="update_posts", apply=True, yes=True)


def test_update_posts_apply_rerun_skips_identical_success(ctx, monkeypatch):
    """The `update_posts` function must reuse an identical successful journal
    entry and avoid a duplicate API update on rerun.
    """
    _set_apply_mode(ctx)
    _write_simple_update_inputs(ctx)
    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates, "check_old_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)
    calls = []

    class FakeClient:
        def update_post(self, pid, message):
            calls.append((pid, message))
            return True

    ctx.api_client = FakeClient()

    updates.update_posts(ctx)
    updates.update_posts(ctx)

    assert [pid for pid, _message in calls] == ["1"]
    rows = list(io.read_csv(ctx.path.updates))
    assert [(row["pid"], row["result"]) for row in rows] == [("1", "success")]
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == ["101"]


def test_update_posts_apply_reapplies_when_target_content_changes(ctx, monkeypatch):
    """The `update_posts` function must reapply a prior success when its
    rewritten target content changes.
    """
    _set_apply_mode(ctx)
    _write_simple_update_inputs(ctx)
    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates, "check_old_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)
    calls = []

    class FakeClient:
        def update_post(self, pid, message):
            calls.append((pid, message))
            return True

    ctx.api_client = FakeClient()

    updates.update_posts(ctx)
    ctx.config.new_url = "https://newer.example.com/"
    updates.update_posts(ctx)

    assert len(calls) == 2
    assert "https://new.example.com/101/a.jpg" in calls[0][1]
    assert "https://newer.example.com/101/a.jpg" in calls[1][1]
    rows = list(io.read_csv(ctx.path.updates))
    assert [row["result"] for row in rows] == ["success", "success"]


def test_update_posts_apply_resumes_after_failure(ctx, monkeypatch):
    """The `update_posts` function must preserve completed journal entries
    and resume only unfinished posts after an apply failure.
    """
    _set_apply_mode(ctx)
    _write_simple_update_inputs(ctx, pids=("1", "2"))
    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates, "check_old_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)
    first_calls = []

    class InterruptingClient:
        def update_post(self, pid, message):
            first_calls.append((pid, message))
            if pid == "2":
                raise RuntimeError("simulated interruption")
            return True

    ctx.api_client = InterruptingClient()

    with pytest.raises(RuntimeError, match="simulated interruption"):
        updates.update_posts(ctx)

    assert [pid for pid, _message in first_calls] == ["1", "2"]
    assert [(row["pid"], row["result"]) for row in io.read_csv(ctx.path.updates)] == [
        ("1", "success")
    ]
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == []

    resumed_calls = []

    class ResumingClient:
        def update_post(self, pid, message):
            resumed_calls.append((pid, message))
            return True

    ctx.api_client = ResumingClient()
    updates.update_posts(ctx)

    assert [pid for pid, _message in resumed_calls] == ["2"]
    assert [(row["pid"], row["result"]) for row in io.read_csv(ctx.path.updates)] == [
        ("1", "success"),
        ("2", "success"),
    ]
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == ["101", "102"]


def test_update_posts_keeps_delete_handoff_empty_when_final_check_fails(ctx, monkeypatch):
    """The `update_posts` function must leave the destructive delete handoff
    empty when final old-reference verification fails.
    """
    ctx.dry_run = False
    ctx.args.dry_run = False
    ctx.args.yes = True
    url = "https://old.example.com/123/a.jpg"

    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", f"['{url}']", f"<img src='{url}'/>"]],
    )

    downloaded = str(models.FileResult.downloaded.value)
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        [["123", "{'1'}", url, "", "/file?id=123", "", downloaded]],
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


def test_update_posts_legacy_mode_uses_separate_journal_and_preserves_delete_handoff(
    ctx, monkeypatch
):
    """The `update_posts` function in legacy mode must use a separate journal
    and preserve normal migration state.
    """
    ctx.dry_run = False
    ctx.args = models.CliArgs(mode="update_legacy_links", apply=True, yes=True)
    ctx.path.updates.write_text("pid,result,content\n99,success,normal migration\n")
    ctx.path.fileids_to_delete.write_text(json.dumps(["999"]))

    url = "https://old.example.com/101/a.jpg"
    result = str(models.FileResult.default.value)

    write_csv(
        ctx.path.posts,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", "['/file?id=101']", "<a href='/file?id=101'>file</a>"]],
    )
    write_csv(
        ctx.path.files,
        ["fileid", "pids", "url", "url_thumb", "url_file", "new_url", "result"],
        [["101", "{'1'}", "/file?id=101", "", "/file?id=101", url, result]],
    )

    monkeypatch.setattr(updates, "check_new_urls", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(updates.time, "sleep", lambda *_args, **_kwargs: None)
    calls = []

    class FakeClient:
        def update_post(self, pid, message):
            calls.append((pid, message))
            return True

    ctx.api_client = FakeClient()

    updates.update_posts(ctx, legacy=True)
    updates.update_posts(ctx, legacy=True)

    assert [pid for pid, _message in calls] == ["1"]
    assert "normal migration" in ctx.path.updates.read_text()
    assert json.loads(ctx.path.fileids_to_delete.read_text()) == ["999"]
    assert [(row["pid"], row["result"]) for row in io.read_csv(ctx.path.legacy_updates)] == [
        ("1", "success")
    ]
