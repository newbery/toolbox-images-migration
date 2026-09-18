import pytest

from toolbox import clients


def test_downloader_download_creates_target_only_after_download_completes(ctx, tmp_path):
    """The `Downloader.download` method must create the target only after the
    entire response body has been written successfully.
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

    events = []

    class FakeSession:
        def get(self, url, stream, timeout):
            events.append("get")
            assert url == "https://example.com/image.jpg"
            assert stream is True
            assert timeout == 60
            return FakeResponse()

    ctx.session = FakeSession()

    size = clients.Downloader(ctx).download("https://example.com/image.jpg", target)

    assert size == 6
    assert target.read_bytes() == b"abcdef"
    assert not part.exists()
    assert events == ["get"]


def test_downloader_download_removes_partial_file_and_preserves_target_on_error(ctx, tmp_path):
    """The `Downloader.download` method must remove the partial file and preserve
    an existing target when streaming fails.
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


def test_api_client_list_posts_page_requests_one_page(ctx):
    """The `APIClient.list_posts_page` method must request and return one page."""
    response_value = {"has_more": False, "data": [{"postId": 123}]}
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def json(self):
            return response_value

    class FakeSession:
        def get(self, url, params, headers, timeout):
            calls.append((url, dict(params), timeout))
            return FakeResponse()

    ctx.session = FakeSession()

    result = clients.APIClient(ctx).list_posts_page(3)

    assert result == response_value
    assert calls == [("https://api.example.com/api/posts", {"limit": 100, "page": 3}, 30)]


def test_api_client_update_post_sends_request(ctx):
    """The `APIClient.update_post` method must send the requested post update."""
    calls = []
    ctx.dry_run = False

    class FakeResponse:
        ok = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def post(self, url, json, headers, timeout):
            calls.append((url, json, timeout))
            return FakeResponse()

    ctx.session = FakeSession()

    assert clients.APIClient(ctx).update_post("123", "updated") is True
    assert calls == [
        ("https://api.example.com/api/posts/123", {"content": "updated"}, 30),
    ]


def test_admin_client_get_delete_defaults_reads_files_form(ctx):
    """The `AdminClient.get_delete_defaults` method must return hidden values
    from the Admin files form.
    """

    class FakeResponse:
        text = (
            '<form id="frmFiles">'
            '<input type="hidden" name="action" value="deleteFiles">'
            '<input type="hidden" name="trail" value="100">'
            '<input type="hidden" name="sort" value="a.filename">'
            '<input type="hidden" name="reverse" value="">'
            '<input type="hidden" name="loadedUsername" value="vss">'
            "</form>"
        )

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

    ctx.session = FakeSession()

    assert clients.AdminClient(ctx).get_delete_defaults() == {
        "action": "deleteFiles",
        "trail": "100",
        "sort": "a.filename",
        "reverse": "",
        "loadedUsername": "vss",
    }


def test_admin_client_get_delete_defaults_refuses_missing_files_form(ctx):
    """The `AdminClient.get_delete_defaults` method must refuse a page without
    the expected `frmFiles` form.
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
            return FakeResponse()

    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="Expected form #frmFiles not found"):
        clients.AdminClient(ctx).get_delete_defaults()


def test_admin_client_delete_files_uses_ajax_contract(ctx):
    """The `AdminClient.delete_files` method must submit the Admin AJAX contract
    using caller-supplied form defaults.
    """
    posted = {}
    ctx.dry_run = False
    defaults = {
        "action": "deleteFiles",
        "trail": "100",
        "sort": "a.filename",
        "reverse": "",
        "loadedUsername": "vss",
    }

    class FakeResponse:
        text = '<div class="alert alert-danger">2 files have been deleted.</div>'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def post(self, url, data, headers, timeout):
            posted.update(url=url, data=list(data), headers=dict(headers), timeout=timeout)
            return FakeResponse()

    ctx.session = FakeSession()

    confirmation = clients.AdminClient(ctx).delete_files(["123", "456"], defaults)

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


def test_admin_client_delete_files_parses_singular_confirmation(ctx):
    """The `AdminClient.delete_files` method must count the singular deletion
    confirmation as one deletion.
    """
    ctx.dry_run = False

    class FakeResponse:
        text = '<div class="alert alert-danger">The file has been deleted.</div>'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def post(self, url, data, headers, timeout):
            return FakeResponse()

    ctx.session = FakeSession()

    assert clients.AdminClient(ctx).delete_files(["123"], {}) == clients.DeleteConfirmation(
        message="The file has been deleted.", count=1
    )


def test_admin_client_delete_files_rejects_ambiguous_plural_confirmation(ctx):
    """The `AdminClient.delete_files` method must reject a plural deletion
    confirmation that does not include a count.
    """
    ctx.dry_run = False

    class FakeResponse:
        text = '<div class="alert alert-danger">The files have been deleted.</div>'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def post(self, url, data, headers, timeout):
            return FakeResponse()

    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="did not confirm deletion"):
        clients.AdminClient(ctx).delete_files(["123", "456"], {})


def test_admin_client_delete_files_requires_confirmation_message(ctx):
    """The `AdminClient.delete_files` method must reject a successful HTTP
    response without a deletion confirmation.
    """
    ctx.dry_run = False

    class FakeResponse:
        text = '<div class="alert alert-info">Manage all uploaded files.</div>'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    class FakeSession:
        def post(self, url, data, headers, timeout):
            return FakeResponse()

    ctx.session = FakeSession()

    with pytest.raises(RuntimeError, match="did not confirm deletion"):
        clients.AdminClient(ctx).delete_files(["123"], {})
