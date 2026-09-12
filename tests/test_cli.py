from types import SimpleNamespace

import pytest

from toolbox import cli, models


def test_parse_args_accepts_valid_mode(monkeypatch):
    """The `parse_args` function must accept a valid mode and store it in the parsed arguments."""
    monkeypatch.setattr(cli, "modes", lambda: {"download_files": lambda _ctx: None})

    args = cli.parse_args(["download_files"])
    assert args.mode == "download_files"


def test_parse_args_rejects_unknown_mode(monkeypatch):
    """The `parse_args` function must reject a mode that is not exposed by the CLI."""
    monkeypatch.setattr(cli, "modes", lambda: {"download_files": lambda _ctx: None})

    with pytest.raises(SystemExit):
        cli.parse_args(["nope"])


def test_main_parses_initializes_and_dispatches_selected_mode(monkeypatch):
    """The `main` function must parse arguments, initialize the context and clients, and dispatch
    the selected mode exactly once.
    """
    called = {"parse": 0, "ctx": 0, "init": 0, "mode": 0}
    args_obj = models.CliArgs(mode="download_files")

    def fake_parse(argv):
        called["parse"] += 1
        assert argv == ["download_files"]
        return args_obj

    ctx_obj = SimpleNamespace()

    def fake_init_context(args):
        called["ctx"] += 1
        assert args is args_obj
        return ctx_obj

    class FakeSess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_init_clients(context, session):
        called["init"] += 1
        assert context is ctx_obj
        assert session is not None
        context.inited = True
        return context

    def mode_fn(context):
        assert context is ctx_obj
        assert getattr(context, "inited", False) is True
        called["mode"] += 1

    monkeypatch.setattr(cli, "parse_args", fake_parse)
    monkeypatch.setattr(cli, "init_context", fake_init_context)
    monkeypatch.setattr(cli.requests, "Session", lambda: FakeSess())
    monkeypatch.setattr(cli, "init_clients", fake_init_clients)
    monkeypatch.setattr(cli, "modes", lambda: {"download_files": mode_fn})
    cli.main(["download_files"])
    assert called == {"parse": 1, "ctx": 1, "init": 1, "mode": 1}


def test_modes_exposes_archive_and_uploaded_diagnostic_modes():
    """The `modes` function must expose the archive workflow and the renamed uploaded-files
    diagnostic.
    """
    cli.modes.cache_clear()
    try:
        available = cli.modes()
        assert "archive_downloads" in available
        assert "check_urls_in_uploaded_folder" in available
        assert "check_urls_in_old_folder" not in available
    finally:
        cli.modes.cache_clear()
