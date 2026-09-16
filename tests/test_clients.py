import pytest

from toolbox import clients


def test_downloader_download_creates_target_only_after_download_completes(ctx, tmp_path):
    """The `Downloader.download` method must create the target only after the entire response body
    has been written successfully.
    """
    target = tmp_path / "nested" / "image.jpg"
    part = target.with_name(f"{target.name}.part")

    class FakeResponse:
        status_code = 200

        def __init__(self):
            self.headers = {"Content-Length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size):
            assert chunk_size == 1024
            yield b"abc"
            assert not target.exists()
            assert part.exists()
            yield b"def"

    class FakeSession:
        def get(self, url, stream, timeout):
            assert url == "https://example.com/image.jpg"
            assert stream is True
            assert timeout == 60
            return FakeResponse()

    ctx.session = FakeSession()

    size = clients.Downloader(ctx).download("https://example.com/image.jpg", target)

    assert size == 6
    assert target.read_bytes() == b"abcdef"
    assert not part.exists()


def test_downloader_download_removes_partial_file_and_preserves_target_on_error(ctx, tmp_path):
    """The `Downloader.download` method must remove the partial file and preserve an existing
    target when streaming fails.
    """
    target = tmp_path / "image.jpg"
    target.write_bytes(b"existing")
    part = target.with_name(f"{target.name}.part")

    class FakeResponse:
        status_code = 200

        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size):
            assert chunk_size == 1024
            yield b"partial"
            raise RuntimeError("connection lost")

    class FakeSession:
        def get(self, url, stream, timeout):
            return FakeResponse()

    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="connection lost"):
        clients.Downloader(ctx).download("https://example.com/image.jpg", target)

    assert target.read_bytes() == b"existing"
    assert not part.exists()


def test_admin_client_delete_files_uses_ajax_contract_and_paces_requests(ctx, monkeypatch):
    """The `AdminClient.delete_files` method must use the Admin AJAX contract
    and pace its requests.
    """
    events = []
    posted = {}
    ctx.config.admin_url_sleep = 2.5
    ctx.dry_run = False

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers, timeout):
            events.append(("get", url))
            return FakeResponse(
                '<form id="frmFiles">'
                '<input type="hidden" name="action" value="deleteFiles">'
                '<input type="hidden" name="trail" value="100">'
                '<input type="hidden" name="sort" value="a.filename">'
                '<input type="hidden" name="reverse" value="">'
                '<input type="hidden" name="loadedUsername" value="vss">'
                "</form>"
            )

        def post(self, url, data, headers, timeout):
            events.append(("post", url))
            posted.update(url=url, data=list(data), headers=dict(headers), timeout=timeout)
            return FakeResponse('<div class="alert alert-danger">2 files have been deleted.</div>')

    ctx.session = FakeSession()
    monkeypatch.setattr(clients.time, "sleep", lambda delay: events.append(("sleep", delay)))

    confirmation = clients.AdminClient(ctx).delete_files(["123", "456"])

    assert confirmation == clients.DeleteConfirmation(message="2 files have been deleted.", count=2)
    assert posted["url"] == "https://admin.example.com/mb/uploading"
    assert posted["timeout"] == 30
    assert posted["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert posted["data"] == [
        ("action", "deleteFiles"),
        ("trail", "100"),
        ("sort", "a.filename"),
        ("reverse", ""),
        ("loadedUsername", "vss"),
        ("deleteimg", "123"),
        ("deleteimg", "456"),
        ("ajax_request", "1"),
    ]
    assert events == [
        ("sleep", 2.5),
        ("get", "https://admin.example.com/mb/uploading/files"),
        ("sleep", 2.5),
        ("post", "https://admin.example.com/mb/uploading"),
    ]


def test_admin_client_delete_files_refuses_missing_files_form(ctx):
    """The `AdminClient.delete_files` method must refuse to post deletions
    when the expected `frmFiles` form is missing.
    """

    class FakeResponse:
        text = "<html><body>No files form</body></html>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers, timeout):
            assert url == "https://admin.example.com/mb/uploading/files"
            assert timeout == 30
            return FakeResponse()

        def post(self, *args, **kwargs):
            pytest.fail("delete request must not be sent without frmFiles")

    ctx.dry_run = False
    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="Expected form #frmFiles not found"):
        clients.AdminClient(ctx).delete_files(["123"])


def test_admin_client_delete_files_parses_singular_confirmation(ctx):
    """The `AdminClient.delete_files` method must count the singular deletion
    confirmation as one deletion.
    """
    ctx.dry_run = False

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers, timeout):
            return FakeResponse(
                '<form id="frmFiles"><input type="hidden" name="action" value="deleteFiles"></form>'
            )

        def post(self, url, data, headers, timeout):
            return FakeResponse('<div class="alert alert-danger">The file has been deleted.</div>')

    ctx.session = FakeSession()

    assert clients.AdminClient(ctx).delete_files(["123"]) == clients.DeleteConfirmation(
        message="The file has been deleted.", count=1
    )


def test_admin_client_delete_files_rejects_ambiguous_plural_confirmation(ctx):
    """The `AdminClient.delete_files` method must reject a plural deletion
    confirmation that does not include a count.
    """
    ctx.dry_run = False

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers, timeout):
            return FakeResponse(
                '<form id="frmFiles"><input type="hidden" name="action" value="deleteFiles"></form>'
            )

        def post(self, url, data, headers, timeout):
            return FakeResponse(
                '<div class="alert alert-danger">The files have been deleted.</div>'
            )

    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="did not confirm deletion"):
        clients.AdminClient(ctx).delete_files(["123", "456"])


def test_admin_client_delete_files_requires_confirmation_message(ctx):
    """The `AdminClient.delete_files` method must reject a successful
    HTTP response without a deletion confirmation.
    """
    ctx.dry_run = False

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers, timeout):
            return FakeResponse(
                '<form id="frmFiles"><input type="hidden" name="action" value="deleteFiles"></form>'
            )

        def post(self, url, data, headers, timeout):
            return FakeResponse('<div class="alert alert-info">Manage all uploaded files.</div>')

    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="did not confirm deletion"):
        clients.AdminClient(ctx).delete_files(["123"])
