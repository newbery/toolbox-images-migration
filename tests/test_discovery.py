from datetime import UTC, datetime

import pytest

from toolbox import discovery, io, models


def test_posts_from_export_extracts_image_urls_and_writes_output(ctx):
    """The `posts_from_export` function must collect exported posts, extract image URLs, and write
    the normalized posts output.
    """
    # write export/posts.csv
    posts_csv = ctx.path.export_dir / "posts.csv"
    posts_csv.write_text(
        "pid,date,message\n"
        '1,100,<p><img src="https://old.example.com/123/a.jpg"/></p>\n'
        "2,101,<p>no image</p>\n",
    )

    posts = discovery.posts_from_export(ctx)
    assert set(posts) == {"1", "2"}
    assert posts["1"].image_urls == ["https://old.example.com/123/a.jpg"]
    assert posts["2"].image_urls == []
    out_rows = list(io.read_csv(ctx.path.posts_from_export))
    assert out_rows[0]["pid"] == "1"


def test_posts_from_api_adds_new_posts_and_stops_at_existing_pid(ctx):
    """The `posts_from_api` function must add new API posts, extract image URLs, and stop when it
    encounters an already-seen post ID.
    """

    class FakeApiRequests:
        def __init__(self, pages):
            self.pages = pages
            self.closed = False

        def __iter__(self):
            yield from self.pages

        def close(self):
            self.closed = True

    class FakeClient:
        def __init__(self, pages):
            self._pages = pages

        def list_posts(self):
            return FakeApiRequests(self._pages)

    # Existing post from export already processed
    posts = {"1": models.Post(date="100", image_urls=[])}
    pages = [
        {
            "data": [
                {
                    "postId": 2,
                    "postTimestamp": "200",
                    "message": '<img src="https://old.example.com/2.jpg"/>',
                }
            ]
        },
        {"data": [{"postId": 1, "postTimestamp": "199", "message": "stop here"}]},
        {"data": [{"postId": 3, "postTimestamp": "198", "message": "should not be reached"}]},
    ]
    ctx.api_client = FakeClient(pages)
    out = discovery.posts_from_api(ctx, posts)
    assert "2" in out
    assert out["2"].image_urls == ["https://old.example.com/2.jpg"]

    # "3" should not be processed due to early stop
    assert "3" not in out


def test_files_from_posts_groups_toolbox_images_by_fileid_and_thumbnail(ctx):
    """The `files_from_posts` function must group Website Toolbox image URLs by file ID, record
    full and thumbnail URLs, derive the download path, and accumulate referencing post IDs.
    """
    # Make it look like a toolbox/cloudfront url so toolbox=True
    ctx.config.old_url = "https://abc.cloudfront.net/"
    ctx.config.old_url_thumb = "https://abc.cloudfront.net/thumb/"
    ctx.config.skip_days = 0
    posts = {
        "1": models.Post(date="0", image_urls=["https://abc.cloudfront.net/999/123/a.jpg"]),
        "2": models.Post(date="0", image_urls=["https://abc.cloudfront.net/thumb/999/123/a.jpg"]),
    }
    files = discovery.files_from_posts(ctx, posts)
    assert "123" in files
    f = files["123"]
    assert f.url.endswith("/999/123/a.jpg")
    assert f.url_thumb.endswith("/thumb/999/123/a.jpg")
    assert f.pids == {"1", "2"}
    assert f.path == "999/123/a.jpg"


def test_files_from_posts_skips_recent_posts(ctx):
    """The `files_from_posts` function must mark files from recent posts as skipped."""
    ctx.config.old_url = "https://abc.cloudfront.net/"
    ctx.config.old_url_thumb = ""
    ctx.config.skip_days = 1  # skip anything newer than 1 day ago
    now_ts = int(datetime.now(UTC).timestamp())
    url = "https://abc.cloudfront.net/1/111/a.jpg"
    posts = {"1": models.Post(date=str(now_ts), image_urls=[url])}

    files = discovery.files_from_posts(ctx, posts)
    assert files["111"].result == models.FileResult.skipped


def test_files_from_posts_preserves_references_with_malformed_date(ctx):
    """The `files_from_posts` function must preserve references from posts with malformed dates and
    mark their files as skipped.
    """
    ctx.config.old_url = "https://abc.cloudfront.net/"
    ctx.config.old_url_thumb = ""
    ctx.config.skip_days = 1
    url = "https://abc.cloudfront.net/1/111/a.jpg"
    posts = {"1": models.Post(date="not-a-timestamp", image_urls=[url])}

    files = discovery.files_from_posts(ctx, posts)

    assert files["111"].pids == {"1"}
    assert files["111"].result == models.FileResult.skipped


def test_files_from_posts_rejects_unsafe_download_path(ctx):
    """The `files_from_posts` function must reject decoded image paths that escape the download
    directory.
    """
    ctx.config.old_url = "https://abc.cloudfront.net/"
    url = "https://abc.cloudfront.net/%2e%2e/123/escape.jpg"
    posts = {"1": models.Post(date="0", image_urls=[url])}

    with pytest.raises(ValueError, match="Unsafe download path derived from URL"):
        discovery.files_from_posts(ctx, posts)


def test_files_from_export_resolves_file_reference_from_attachment_metadata(ctx):
    """The `files_from_export` function must use attachment metadata to resolve /file?id=
    references to concrete file URLs.
    """
    # Posts with legacy /file?id= urls; attachments.csv supplies filename
    ctx.config.old_url = "https://abc.cloudfront.net/"
    posts = {"1": models.Post(date="0", image_urls=["/file?id=123"])}
    attach = ctx.path.export_dir / "attachment.csv"
    attach.write_text("fileid,filename\n123,a.jpg\n")

    files = discovery.files_from_export(ctx, posts)
    assert files["123"].new_url == "https://abc.cloudfront.net/123/a.jpg"


def test_files_from_export_duplicate_rows_do_not_hide_missing_metadata(ctx):
    """The `files_from_export` function must not let duplicate attachment rows hide missing
    metadata for another file ID.
    """
    ctx.config.old_url = "https://abc.cloudfront.net/"
    urls = ["/file?id=123", "/file?id=456"]
    post = models.Post(date="0", image_urls=urls)
    posts = {"1": post}
    attach = ctx.path.export_dir / "attachment.csv"
    attach.write_text("fileid,filename\n123,a.jpg\n123,a-duplicate.jpg\n")

    with pytest.raises(
        RuntimeError,
        match=r"Attachment metadata not found for file IDs: 456",
    ):
        discovery.files_from_export(ctx, posts)
