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


def test_admin_client_delete_files_refuses_missing_files_form(ctx):
    """The `AdminClient.delete_files` method must refuse to post deletions when the expected
    `frmFiles` form is missing.
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
