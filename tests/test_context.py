from pathlib import Path

import pytest

from toolbox import context, models


def test_config_from_mapping_rejects_missing_required_values():
    """The `Config.from_mapping` method must reject missing required configuration values."""
    with pytest.raises(ValueError, match="Missing required config value: EXPORT_DIR"):
        context.Config.from_mapping({})


def test_config_loads_sources_with_precedence_and_typed_values(monkeypatch):
    """The `config` function must merge dotenv and environment sources with the expected precedence
    and typed values.
    """

    def fake_dotenv_values(filename):
        if filename == ".env":
            return {
                "API_URL": "https://api.example.com",
                "ADMIN_URL": "https://admin.example.com",
                "EXPORT_DIR": "csv",
                "DOWNLOAD_DIR": "downloads",
                "OUTPUT_DIR": "output",
                "OLD_URL": "https://old.example.com/",
                "OLD_URL_THUMB": "",
                "NEW_URL": "https://new.example.com/",
                "SKIP_DAYS": "30",
                "TEST_POST_ID": "",
                "DRY_RUN": "true",
                "API_USERNAME": "from-env-file",
            }
        if filename == ".env.secrets":
            return {
                "API_KEY": "secret-key",
                "API_USERNAME": "secret-user",
                "ADMIN_COOKIE": "secret-cookie",
            }
        return {}

    monkeypatch.setattr(context, "dotenv_values", fake_dotenv_values)
    monkeypatch.setenv("TOOLBOX_SKIP_DAYS", "7")
    monkeypatch.setenv("TOOLBOX_DRY_RUN", "false")
    monkeypatch.setenv("OTHER", "x")

    cfg = context.config()
    assert cfg.api_username == "secret-user"
    assert cfg.api_key == "secret-key"
    assert cfg.skip_days == 7
    assert cfg.dry_run is False
    assert cfg.export_dir == Path("csv")
    assert cfg.old_url_thumb is None
    assert cfg.test_post_id is None
    assert not hasattr(cfg, "other")


@pytest.mark.parametrize(
    ("changes", "name"),
    [
        ({"api_url": ""}, "API_URL"),
        ({"admin_url": ""}, "ADMIN_URL"),
        ({"old_url": ""}, "OLD_URL"),
        ({"new_url": ""}, "NEW_URL"),
        ({"api_key": ""}, "API_KEY"),
        ({"api_username": ""}, "API_USERNAME"),
        ({"admin_cookie": ""}, "ADMIN_COOKIE"),
    ],
)
def test_validate_config_requires_complete_migration_config(tmp_path, config_for, changes, name):
    """The `validate_config` function must require all migration configuration values."""
    cfg = config_for(tmp_path, **changes)

    with pytest.raises(ValueError, match=f"Missing required config value: {name}"):
        context.validate_config(cfg, dry_run=False)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"old_url": "https://old.example.com"}, "OLD_URL must end with '/':"),
        ({"new_url": "not-a-url"}, r"NEW_URL must be an absolute http\(s\) URL"),
        ({"skip_days": -1}, "SKIP_DAYS must be greater than or equal to 0"),
    ],
)
def test_validate_config_rejects_invalid_values(tmp_path, config_for, changes, message):
    """The `validate_config` function must reject invalid URL and negative numeric configuration
    values.
    """
    cfg = config_for(tmp_path, **changes)

    with pytest.raises(ValueError, match=message):
        context.validate_config(cfg, dry_run=False)


def test_validate_config_allows_query_url_prefix_without_trailing_slash(tmp_path, config_for):
    """The `validate_config` function must allow URL prefixes with query parameters without a
    trailing slash.
    """
    cfg = config_for(tmp_path, new_url="https://new.example.com/?url=")

    context.validate_config(cfg, dry_run=False)


def test_paths_builds_expected_derived_paths(tmp_path, config_for):
    """The `paths` function must build the expected derived paths from the
    configured directories.
    """
    cfg = config_for(tmp_path)
    paths = context.paths(cfg)
    assert paths.export_dir.name == "export"
    assert paths.posts.name == "posts.csv"
    assert paths.fileids_to_delete.name == "fileids_to_delete.json"


def test_init_context_builds_context_using_configured_apply_mode(
    tmp_path, monkeypatch, capsys, config_for
):
    """The `init_context` function must build the runtime context and use configured apply mode by
    default.
    """
    cfg = config_for(tmp_path, dry_run=False)
    monkeypatch.setattr(context, "config", lambda: cfg)

    args = models.CliArgs(mode="download_files")
    ctx = context.init_context(args)
    assert isinstance(ctx, context.Context)
    assert ctx.args is args
    assert ctx.config is cfg
    assert isinstance(ctx.path, context.Paths)
    assert ctx.dry_run is False
    assert capsys.readouterr().out == ""


def test_init_context_uses_configured_dry_run_and_prints_banner(
    tmp_path, monkeypatch, capsys, config_for
):
    """The `init_context` function must use configured dry-run mode and print the dry-run banner."""
    cfg = config_for(tmp_path, dry_run=True)
    monkeypatch.setattr(context, "config", lambda: cfg)

    ctx = context.init_context(models.CliArgs(mode="download_files"))

    assert ctx.dry_run is True
    out = capsys.readouterr().out
    assert "---- Dry Run (no remote changes) ----" in out


def test_init_context_cli_apply_overrides_configured_dry_run(tmp_path, monkeypatch, config_for):
    """The `init_context` function must let --apply override configured dry-run mode."""
    cfg = config_for(tmp_path, dry_run=True)
    monkeypatch.setattr(context, "config", lambda: cfg)

    ctx = context.init_context(models.CliArgs(mode="download_files", apply=True))

    assert ctx.dry_run is False


def test_init_context_allows_local_new_url_in_dry_run(tmp_path, monkeypatch, config_for):
    """The `init_context` function must allow an existing local NEW_URL
    directory in dry-run mode.
    """
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    cfg = config_for(tmp_path, new_url=str(new_dir), dry_run=True)
    monkeypatch.setattr(context, "config", lambda: cfg)

    ctx = context.init_context(models.CliArgs(mode="download_files"))

    assert ctx.config.new_url == str(new_dir)
    assert ctx.dry_run is True


def test_init_context_rejects_local_new_url_in_apply_mode(tmp_path, monkeypatch, config_for):
    """The `init_context` function must reject a local NEW_URL directory in apply mode."""
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    cfg = config_for(tmp_path, new_url=str(new_dir), dry_run=True)
    monkeypatch.setattr(context, "config", lambda: cfg)

    with pytest.raises(ValueError, match=r"NEW_URL must be an absolute http\(s\) URL"):
        context.init_context(models.CliArgs(mode="download_files", apply=True))


def test_init_clients_configures_session_clients_and_url_helper(ctx, monkeypatch):
    """The `init_clients` function must configure the session, attach service clients, and install
    the URL availability helper.
    """

    class FakeResp:
        def __init__(self, code):
            self.status_code = code

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeSession:
        def __init__(self):
            self.mounted = []
            self.headers = {}
            self.head_calls = []

        def mount(self, prefix, adapter):
            self.mounted.append((prefix, adapter))

        def head(self, url, allow_redirects=True, timeout=30):
            self.head_calls.append((url, allow_redirects, timeout))
            if "partial" in url:
                return FakeResp(206)
            return FakeResp(200 if "ok" in url else 404)

    class FakeAdapter:
        pass

    monkeypatch.setattr(context, "FileAdapter", FakeAdapter)

    api_obj, admin_obj, dl_obj = object(), object(), object()
    created = {"api": None, "admin": None, "dl": None}
    monkeypatch.setattr(
        context, "APIClient", lambda ctx: created.__setitem__("api", ctx) or api_obj
    )
    monkeypatch.setattr(
        context, "AdminClient", lambda ctx: created.__setitem__("admin", ctx) or admin_obj
    )
    monkeypatch.setattr(context, "Downloader", lambda ctx: created.__setitem__("dl", ctx) or dl_obj)

    sess = FakeSession()
    out = context.init_clients(ctx, session=sess)
    assert out is ctx
    assert ctx.session is sess

    # Mounts file:// adapter and sets UA header
    assert any(prefix == "file://" for prefix, _a in sess.mounted)
    assert isinstance(next(a for p, a in sess.mounted if p == "file://"), FakeAdapter)
    assert sess.headers.get("User-Agent") == context.USER_AGENT

    # Attaches client helpers (constructor internals are not tested)
    assert ctx.api_client is api_obj
    assert created["api"] is ctx
    assert ctx.admin_client is admin_obj
    assert created["admin"] is ctx
    assert ctx.downloader is dl_obj
    assert created["dl"] is ctx

    # url_ok(): 200/206 => True, other codes => False; head args are fixed
    assert ctx.url_ok("http://ok.example") is True
    assert ctx.url_ok("http://partial.example") is True
    assert ctx.url_ok("http://nope.example") is False
    assert sess.head_calls[0] == ("http://ok.example", True, 30)


def test_init_clients_creates_session_when_not_provided(ctx, monkeypatch):
    """The `init_clients` function must create and store a requests session
    when none is provided.
    """

    class FakeSession:
        def __init__(self):
            self.mounted = []
            self.headers = {}

        def mount(self, prefix, adapter):
            self.mounted.append((prefix, adapter))

        def head(self, url, allow_redirects=True, timeout=30):
            class R:
                status_code = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

    monkeypatch.setattr(context.requests, "Session", FakeSession)
    monkeypatch.setattr(context, "FileAdapter", lambda: object())
    monkeypatch.setattr(context, "APIClient", lambda ctx: object())
    monkeypatch.setattr(context, "AdminClient", lambda ctx: object())
    monkeypatch.setattr(context, "Downloader", lambda ctx: object())

    context.init_clients(ctx, session=None)

    assert isinstance(ctx.session, FakeSession)
