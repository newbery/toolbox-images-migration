from toolbox import commands


def test_mode_download_files_auth_gate(ctx, capsys):
    """The `mode_download_files` function should refuse to proceed when api
    authentication/health checks fail and emit a user-visible message.
    """

    class FakeApi:
        def check_api_auth(self):
            return False

    ctx.api_client = FakeApi()

    commands.mode_download_files(ctx)
    out = capsys.readouterr().out
    assert "API is inaccessible" in out


def test_mode_download_files_happy_path_calls_pipeline(ctx, monkeypatch):
    """The `mode_download_files` function should orchestrate export/api ingestion,
    file extraction, downloads, and summarization in the expected order when the
    api is accessible.
    """

    class FakeApi:
        def check_api_auth(self):
            return True

    ctx.api_client = FakeApi()
    calls = []
    monkeypatch.setattr(commands.io, "log", lambda args: calls.append("log"))
    monkeypatch.setattr(
        commands,
        "posts_from_export",
        lambda args: calls.append("export") or {"1": {"date": "0", "image_urls": []}},
    )
    monkeypatch.setattr(
        commands, "posts_from_api", lambda args, posts: calls.append("api") or posts
    )
    monkeypatch.setattr(
        commands,
        "files_from_posts",
        lambda args, posts: calls.append("files_from_posts") or {},
    )
    monkeypatch.setattr(
        commands,
        "download_files",
        lambda args, files: calls.append("download_files") or files,
    )
    monkeypatch.setattr(commands, "summarize", lambda args, files: calls.append("summarize"))
    commands.mode_download_files(ctx)

    assert calls == ["log", "export", "api", "files_from_posts", "download_files", "summarize"]


def test_mode_download_links_uses_link_only_discovery_without_mutating_config(ctx, monkeypatch):
    """The `mode_download_links` function should request link-only discovery
    semantics without changing the shared configuration.
    """

    class FakeApi:
        def check_api_auth(self):
            return True

    ctx.api_client = FakeApi()
    old_url_thumb = ctx.config.old_url_thumb
    skip_days = ctx.config.skip_days
    called = []

    monkeypatch.setattr(commands.io, "log", lambda context: called.append("log"))

    def fake_export(context, *, include_thumbnails=True):
        called.append(("export", include_thumbnails))
        return {}

    def fake_api(context, posts, *, include_thumbnails=True):
        called.append(("api", include_thumbnails))
        return posts

    def fake_files(context, posts, *, include_thumbnails=True, skip_days=None):
        called.append(("files_from_posts", include_thumbnails, skip_days))
        return {}

    monkeypatch.setattr(commands, "posts_from_export", fake_export)
    monkeypatch.setattr(commands, "posts_from_api", fake_api)
    monkeypatch.setattr(commands, "files_from_posts", fake_files)
    monkeypatch.setattr(commands, "summarize", lambda context, files: called.append("summarize"))

    commands.mode_download_links(ctx)

    assert ctx.config.old_url_thumb == old_url_thumb
    assert ctx.config.skip_days == skip_days
    assert called == [
        "log",
        ("export", False),
        ("api", False),
        ("files_from_posts", False, 0),
        "summarize",
    ]


def test_mode_update_posts_and_delete_files_auth_gate(ctx, capsys):
    """The `mode_update_posts` and `mode_delete_files` functions should enforce
    their respective auth checks and emit a failure message instead of performing
    destructive operations.
    """

    class BadApi:
        def check_api_auth(self):
            return False

    ctx.api_client = BadApi()

    commands.mode_update_posts(ctx)
    assert "API is inaccessible" in capsys.readouterr().out

    class BadAdmin:
        def check_admin_auth(self):
            return False

    ctx.admin_client = BadAdmin()

    commands.mode_delete_files(ctx)
    assert "Admin UI is inaccessible" in capsys.readouterr().out


def test_mode_update_legacy_links_calls_expected(ctx, monkeypatch):
    """The `mode_update_legacy_links` function should run the legacy-link update
    pipeline with legacy semantics without changing shared configuration.
    """

    class GoodApi:
        def check_api_auth(self):
            return True

    ctx.api_client = GoodApi()
    skip_days = ctx.config.skip_days
    old_url_thumb = ctx.config.old_url_thumb
    calls = []

    monkeypatch.setattr(
        commands,
        "posts_from_export",
        lambda context, legacy=False: calls.append(("export", legacy)) or {},
    )
    monkeypatch.setattr(
        commands,
        "files_from_export",
        lambda context, posts: calls.append("files_from_export") or {},
    )
    monkeypatch.setattr(
        commands,
        "summarize",
        lambda context, files, legacy=False: calls.append(("summarize", legacy)),
    )
    monkeypatch.setattr(
        commands,
        "update_posts",
        lambda context, legacy=False: calls.append(("update_posts", legacy)),
    )

    commands.mode_update_legacy_links(ctx)

    assert ctx.config.skip_days == skip_days
    assert ctx.config.old_url_thumb == old_url_thumb
    assert calls == [
        ("export", True),
        "files_from_export",
        ("summarize", True),
        ("update_posts", True),
    ]


def test_mode_archive_downloads_logs_and_archives(ctx, monkeypatch):
    """The archive mode should log the run and invoke local upload confirmation."""
    calls = []
    monkeypatch.setattr(commands.io, "log", lambda context: calls.append("log"))
    monkeypatch.setattr(commands, "archive_downloads", lambda context: calls.append("archive"))

    commands.mode_archive_downloads(ctx)

    assert calls == ["log", "archive"]
