import pytest

from toolbox import urls


def test_find_urls_func_returns_sorted_unique_matching_urls():
    """The `find_urls_func` function must return sorted unique URLs that match the configured
    prefix.
    """
    find_urls = urls.find_urls_func("https://old.example.com/")
    html = (
        "<p>"
        '<img src="https://old.example.com/a.jpg"/>'
        '<img src="https://old.example.com/b.jpg"/>'
        '<img src="https://other.example.com/c.jpg"/>'
        '<img src="https://old.example.com/a.jpg"/>'
        "</p>"
    )
    assert find_urls(html) == ["https://old.example.com/a.jpg", "https://old.example.com/b.jpg"]


def test_find_legacy_urls_extracts_attachment_and_hosted_image_references():
    """The `find_legacy_urls` function must extract legacy attachment links and Website Toolbox
    hosted-image references while ignoring unrelated URLs.
    """
    html = (
        '<a href="/file?id=123">x</a>'
        '<img src="http://files.websitetoolbox.com/999/123/a.jpg"/>'
        '<img src="https://example.com/ignore.jpg"/>'
    )
    assert urls.find_legacy_urls(html) == [
        "/file?id=123",
        "http://files.websitetoolbox.com/999/123/a.jpg",
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/file?id=123", "123"),
        ("https://s3.amazonaws.com/files.websitetoolbox.com/999/123/a.jpg", "123"),
        ("https://cdn.example.com/999/123/a.jpg", "123"),
        ("https://cdn.example.com/x/123", "123"),
        ("not a url", None),
    ],
)
def test_fileid_from_url_extracts_supported_file_ids(url, expected):
    """The `fileid_from_url` function must extract file IDs from supported URL forms and return
    `None` for unrecognized input.
    """
    assert urls.fileid_from_url(url) == expected


def test_get_new_url_func_rewrites_full_and_thumbnail_urls():
    """The `get_new_url_func` function must rewrite full-image and thumbnail URLs to the configured
    destination prefix.
    """
    f = urls.get_new_url_func(
        old_prefix="https://old.example.com/",
        thumb_prefix="https://old.example.com/thumb/",
        new_prefix="https://new.example.com/",
    )
    assert f("https://old.example.com/123/a.jpg") == "https://new.example.com/123/a.jpg"
    assert f("https://old.example.com/thumb/123/a.jpg") == "https://new.example.com/thumb/123/a.jpg"


def test_get_new_url_func_quotes_and_unquotes_parameterized_urls():
    """The `get_new_url_func` function must quote path URLs when embedding them in query parameters
    and unquote parameter values when converting them back to paths.
    """
    # Old has no param, new has param -> safe_quote should be used
    f = urls.get_new_url_func(
        old_prefix="https://old.example.com/",
        thumb_prefix="",
        new_prefix="https://new.example.com/?url=",
    )

    # '#' should remain quoted so it doesn't become a fragment
    out = f("https://old.example.com/a#b.jpg")
    assert out.startswith("https://new.example.com/?url=")
    assert "%23" in out  # # is quoted

    # Old has param but new does not -> unquote should be used
    g = urls.get_new_url_func(
        old_prefix="https://old.example.com/?url=",
        thumb_prefix="",
        new_prefix="https://new.example.com/",
    )
    out2 = g("https://old.example.com/?url=a%23b.jpg")
    assert out2 == "https://new.example.com/a#b.jpg"


def test_remove_bad_url_removes_dead_media_reference_and_adds_notice():
    """The `remove_bad_url` function must remove dead image and link references and add a visible
    missing-image notice.
    """
    bad = "https://old.example.com/999/missing.jpg"
    html = f'<p><a href="{bad}"><img src="{bad}"/></a> hello</p>'
    out = urls.remove_bad_url(html, bad)

    # src and href should be removed
    assert 'src="' not in out
    assert 'href="' not in out

    # notice inserted
    assert "missing-image" in out
    assert "(missing image)" in out

    # comment includes Bad URL marker
    assert "Bad URL:" in out


def test_remove_bad_url_preserves_unrelated_enclosing_link():
    """The `remove_bad_url` function must preserve an unrelated enclosing link when removing a
    missing image.
    """
    bad = "https://old.example.com/999/missing.jpg"
    destination = "https://example.com/page"
    html = f'<p><a href="{destination}"><img src="{bad}"/></a></p>'

    out = urls.remove_bad_url(html, bad)

    assert f'href="{destination}"' in out
    assert 'src="' not in out
    assert "(missing image)" in out
