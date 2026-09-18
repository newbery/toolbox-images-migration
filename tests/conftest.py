from pathlib import Path

import pytest

from toolbox import cleanup, context, discovery, download, models, updates


class DummyAliveBar:
    """Replacement for alive_progress.alive_bar used in unit tests."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.calls = []

    def __enter__(self):
        def bar(n=1):
            self.calls.append(n)

        return bar

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _patch_alive_bar(monkeypatch):
    for module in (cleanup, discovery, download, updates):
        monkeypatch.setattr(module, "alive_bar", DummyAliveBar)
    return


def _config_for(tmp_path: Path, **changes) -> context.Config:
    values = {
        "api_url": "https://api.example.com",
        "admin_url": "https://admin.example.com",
        "export_dir": tmp_path / "export",
        "download_dir": tmp_path / "downloads",
        "output_dir": tmp_path / "out",
        "old_url": "https://old.example.com/",
        "old_url_thumb": "https://old.example.com/thumb/",
        "new_url": "https://new.example.com/",
        "skip_days": 0,
        "test_post_id": None,
        "dry_run": True,
        "api_key": "key",
        "api_username": "user",
        "admin_cookie": "cookie",
        "api_url_sleep": 0.0,
        "admin_url_sleep": 0.0,
        "old_url_sleep": 0.0,
        "new_url_sleep": 0.0,
    }
    values.update(changes)
    return context.Config(**values)


@pytest.fixture
def config_for():
    return _config_for


@pytest.fixture
def ctx(tmp_path) -> context.Context:
    export_dir = tmp_path / "export"
    download_dir = tmp_path / "downloads"
    output_dir = tmp_path / "out"
    export_dir.mkdir()
    download_dir.mkdir()
    output_dir.mkdir()

    cfg = _config_for(tmp_path)
    path = context.paths(cfg)
    args = models.CliArgs(mode="download_files", dry_run=True, yes=True)

    return context.Context(args=args, config=cfg, path=path, dry_run=True)
