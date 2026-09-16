import pytest

from tests.helpers import write_csv
from toolbox import download, io, models


def test_download_files_updates_results_and_preserves_skipped_files(ctx, monkeypatch, capsys):
    """The `download_files` function must record successful and failed downloads
    and preserve files already marked as skipped.
    """

    # Create a downloader that writes dummy files and returns size
    class FakeDownloader:
        def __init__(self):
            self.calls = []

        def download(self, url, path_new):
            self.calls.append((url, str(path_new)))
            path_new.parent.mkdir(parents=True, exist_ok=True)
            path_new.write_bytes(b"abc")
            return 3

    ctx.downloader = FakeDownloader()
    files = {
        "1": models.ForumFile(fileid="1", url="https://x/1.jpg", path="1.jpg", pids={"p1"}),
        "2": models.ForumFile(
            fileid="2",
            url="https://x/2.jpg",
            url_thumb="https://x/t2.jpg",
            path="2.jpg",
            pids={"p2"},
        ),
        "3": models.ForumFile(
            fileid="3",
            url="https://x/3.jpg",
            path="3.jpg",
            pids={"p3"},
            result=models.FileResult.skipped,
        ),
    }
    # Make download fail for one file
    download_ = ctx.downloader.download

    def fake_download(url, path_new):
        if str(url).endswith("1.jpg"):
            return 0
        return download_(url, path_new)

    ctx.downloader.download = fake_download  # type: ignore
    out = download.download_files(ctx, files)
    assert out["1"].result == models.FileResult.error
    assert out["2"].result == models.FileResult.downloaded
    assert out["3"].result == models.FileResult.skipped
    assert "Full source unavailable:" in capsys.readouterr().out


def test_download_files_uses_uploaded_archive_as_cache(ctx):
    """The `download_files` function must treat files already present in the
    uploaded archive as downloaded without downloading them again.
    """
    uploaded = ctx.path.download_dir / "_uploaded_" / "123" / "a.jpg"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"already uploaded")

    class FakeDownloader:
        def download(self, _url, _path_new):
            raise AssertionError("uploaded files should not be downloaded again")

    ctx.downloader = FakeDownloader()
    url = "https://old.example.com/123/a.jpg"
    files = {"123": models.ForumFile(fileid="123", url=url, path="123/a.jpg", pids={"1"})}

    out = download.download_files(ctx, files)

    assert out["123"].result is models.FileResult.downloaded


def test_download_files_keeps_full_image_when_thumbnail_fails(ctx, capsys):
    """The `download_files` function must retain a successful full image when
    its thumbnail download fails.
    """

    class FakeDownloader:
        def __init__(self):
            self.calls = []

        def download(self, url, path_new):
            self.calls.append((url, str(path_new)))
            if "/thumb/" in str(path_new):
                return 0
            return 3

    ctx.downloader = FakeDownloader()
    full = "https://x/1.jpg"
    thumb = "https://x/t1.jpg"
    files = {
        "1": models.ForumFile(
            fileid="1",
            url=full,
            url_thumb=thumb,
            path="1.jpg",
            pids={"p1"},
        )
    }

    out = download.download_files(ctx, files)

    assert out["1"].result == models.FileResult.downloaded
    assert out["1"].thumb_result == models.FileResult.error
    assert [url for url, _path in ctx.downloader.calls] == [full, thumb]

    out_text = capsys.readouterr().out
    assert "Thumbnail source unavailable (full image retained)" in out_text
    assert "downloaded 1" in out_text


def test_download_files_keeps_thumbnail_when_full_image_fails(ctx, capsys):
    """The `download_files` function must retain a successful thumbnail when
    the full-image download fails.
    """

    class FakeDownloader:
        def __init__(self):
            self.calls = []

        def download(self, url, path_new):
            self.calls.append((url, str(path_new)))
            return 3 if "/thumb/" in str(path_new) else 0

    ctx.downloader = FakeDownloader()
    full = "https://x/1.jpg"
    thumb = "https://x/t1.jpg"
    files = {
        "1": models.ForumFile(
            fileid="1",
            url=full,
            url_thumb=thumb,
            path="1.jpg",
            pids={"p1"},
        )
    }

    out = download.download_files(ctx, files)

    assert out["1"].result == models.FileResult.error
    assert out["1"].thumb_result == models.FileResult.downloaded
    assert [url for url, _path in ctx.downloader.calls] == [full, thumb]

    out_text = capsys.readouterr().out
    assert "Full source unavailable (thumbnail retained)" in out_text
    assert "downloaded 1" in out_text


def test_download_files_reports_unrecoverable_when_full_and_thumbnail_fail(ctx, capsys):
    """The `download_files` function must report media as unrecoverable when
    both full-image and thumbnail downloads fail.
    """

    class FakeDownloader:
        def __init__(self):
            self.calls = []

        def download(self, url, path_new):
            self.calls.append((url, str(path_new)))
            return 0

    ctx.downloader = FakeDownloader()
    full = "https://x/1.jpg"
    thumb = "https://x/t1.jpg"
    files = {
        "1": models.ForumFile(
            fileid="1",
            url=full,
            url_thumb=thumb,
            path="1.jpg",
            pids={"p1"},
        )
    }

    out = download.download_files(ctx, files)

    assert out["1"].result == models.FileResult.error
    assert out["1"].thumb_result == models.FileResult.error
    assert [url for url, _path in ctx.downloader.calls] == [full, thumb]
    assert (
        "Full and thumbnail sources unavailable; media treated as unrecoverable"
        in capsys.readouterr().out
    )


def test_download_files_rejects_path_outside_download_directory(ctx):
    """The `download_files` function must reject file paths that escape the
    managed download directory.
    """

    class FakeDownloader:
        def download(self, url, path_new):
            raise AssertionError("unsafe path should be rejected before download")

    ctx.downloader = FakeDownloader()
    files = {
        "1": models.ForumFile(
            fileid="1",
            url="https://x/1.jpg",
            path="../escape.jpg",
            pids={"p1"},
        )
    }

    with pytest.raises(ValueError, match="Unsafe download path outside"):
        download.download_files(ctx, files)


def test_summarize_writes_migratable_posts_and_files(ctx):
    """The `summarize` function must write consolidated posts and files while
    excluding posts with no migratable files.
    """
    # posts_from_export and posts_from_api inputs
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [
            ["1", "0", "[]", "<p>no</p>"],
            [
                "2",
                "0",
                "['https://old.example.com/123/a.jpg']",
                "<img src='https://old.example.com/123/a.jpg'/>",
            ],
        ],
    )
    write_csv(
        ctx.path.posts_from_api,
        ["pid", "date", "image_urls", "message"],
        [
            [
                "3",
                "0",
                "['https://old.example.com/123/a.jpg']",
                "<img src='https://old.example.com/123/a.jpg'/>",
            ],
        ],
    )
    files = {
        "123": models.ForumFile(
            fileid="123",
            url="https://old.example.com/123/a.jpg",
            path="123/a.jpg",
            pids={"2", "3"},
            result=models.FileResult.downloaded,
        ),
        "999": models.ForumFile(
            fileid="999",
            url="https://old.example.com/999/missing.jpg",
            path="999/missing.jpg",
            pids={"1"},
            result=models.FileResult.skipped,
        ),
    }
    download.summarize(ctx, files)

    posts_out = list(io.read_csv(ctx.path.posts))

    # pid 1 should be skipped due to skipped file
    assert [r["pid"] for r in posts_out] == ["2", "3"]

    files_out = list(io.read_csv(ctx.path.files))
    row = next(r for r in files_out if r["fileid"] == "123")
    assert row["path"] == "123/a.jpg"
    assert row["thumb_result"] == str(models.FileResult.default.value)


def test_summarize_keeps_post_when_another_file_is_migratable(ctx):
    """The `summarize` function must keep a post when at least one of its
    referenced files is migratable.
    """
    kept = "https://old.example.com/123/a.jpg"
    skipped_url = "https://old.example.com/999/b.jpg"
    write_csv(
        ctx.path.posts_from_export,
        ["pid", "date", "image_urls", "message"],
        [["1", "0", repr([kept, skipped_url]), f"<img src='{kept}'><img src='{skipped_url}'>"]],
    )
    write_csv(ctx.path.posts_from_api, ["pid", "date", "image_urls", "message"], [])
    files = {
        "123": models.ForumFile(
            fileid="123",
            url=kept,
            path="123/a.jpg",
            pids={"1"},
            result=models.FileResult.downloaded,
        ),
        "999": models.ForumFile(
            fileid="999",
            url=skipped_url,
            path="999/b.jpg",
            pids={"1"},
            result=models.FileResult.skipped,
        ),
    }

    download.summarize(ctx, files)

    posts_out = list(io.read_csv(ctx.path.posts))
    assert [row["pid"] for row in posts_out] == ["1"]
