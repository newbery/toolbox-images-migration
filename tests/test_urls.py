import pytest

from toolbox import urls


def test_find_urls_func_returns_sorted_unique_matching_urls():
    """The `find_urls_func` function must return sorted unique URLs that match
    the configured prefix.
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
    """The `find_legacy_urls` function must extract legacy attachment links
    and Website Toolbox hosted-image references while ignoring unrelated URLs.
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
    """The `fileid_from_url` function must extract file IDs from supported
    URL forms and return `None` for unrecognized input.
    """
    assert urls.fileid_from_url(url) == expected


def test_get_new_url_func_rewrites_full_and_thumbnail_urls():
    """The `get_new_url_func` function must rewrite full-image and thumbnail
    URLs to the configured destination prefix.
    """
    f = urls.get_new_url_func(
        old_prefix="https://old.example.com/",
        thumb_prefix="https://old.example.com/thumb/",
        new_prefix="https://new.example.com/",
    )
    assert f("https://old.example.com/123/a.jpg") == "https://new.example.com/123/a.jpg"
    assert f("https://old.example.com/thumb/123/a.jpg") == "https://new.example.com/thumb/123/a.jpg"


def test_get_new_url_func_quotes_and_unquotes_parameterized_urls():
    """The `get_new_url_func` function must quote path URLs when embedding
    them in query parameters and unquote parameter values when converting
    them back to paths.
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


def test_find_html_references_decodes_entities_only_once():
    """The `find_html_references` function must decode HTML entities exactly
    once when extracting references.
    """
    html = '<a href="https://old.example.com/123/seller&amp;#39;s.jpg">image</a>'

    assert urls.find_html_references(html) == ["https://old.example.com/123/seller&#39;s.jpg"]


@pytest.mark.parametrize(
    "raw_reference",
    [
        "https://old.example.com/123/seller's.jpg",
        "https://old.example.com/123/seller&#39;s.jpg",
        "https://old.example.com/123/seller&#x27;s.jpg",
        "https://old.example.com/123/seller&apos;s.jpg",
    ],
)
def test_rewrite_html_references_matches_equivalent_encoded_values(raw_reference):
    """The `rewrite_html_references` function must rewrite equivalent literal
    and HTML-entity-encoded attribute values.
    """
    old = "https://old.example.com/123/seller's.jpg"
    new = "https://new.example.com/123/seller's.jpg"
    html = f'<a href="{raw_reference}">image</a>'

    rewritten, matched = urls.rewrite_html_references(html, {old: new})

    assert urls.find_html_references(rewritten) == [new]
    assert matched == {old}


def test_rewrite_html_references_preserves_unrelated_markup():
    """The `rewrite_html_references` function must preserve unrelated markup
    while rewriting matched attribute values.
    """
    old = "https://old.example.com/123/seller's.jpg"
    new = "https://new.example.com/123/seller's.jpg"
    html = (
        '<P data-X="A&amp;B"><a  HREF = "'
        "https://old.example.com/123/seller&#39;s.jpg"
        '" class="x">X</a><BR></P>'
    )

    rewritten, matched = urls.rewrite_html_references(html, {old: new})

    assert rewritten == (
        '<P data-X="A&amp;B"><a  HREF = "'
        "https://new.example.com/123/seller's.jpg"
        '" class="x">X</a><BR></P>'
    )
    assert matched == {old}


def test_remove_unrecoverable_file_references_removes_dead_media_references():
    """The `remove_unrecoverable_file_references` function must remove dead
    image and related attachment references while preserving visible attachment
    text and marking the missing image.
    """
    from toolbox import models

    full = "https://old.example.com/123/a.jpg"
    thumb = "https://old.example.com/thumb/123/a.jpg"
    file_url = "/file?id=123"
    file = models.ForumFile(fileid="123", url=full, url_thumb=thumb, url_file=file_url)
    html = f'<a href="{full}"><img src="{thumb}"/></a> <a href="{file_url}">attachment</a>'

    out = urls.remove_unrecoverable_file_references(html, file)

    assert full not in out
    assert thumb not in out
    assert file_url not in out
    assert "<img" not in out
    assert "<a" not in out
    assert "missing-image" in out
    assert "(missing image)" in out
    assert "attachment" in out


def test_remove_unrecoverable_file_references_preserves_unrelated_enclosing_link():
    """The `remove_unrecoverable_file_references` function must preserve
    an unrelated enclosing link when removing an unrecoverable image.
    """
    from toolbox import models

    full = "https://old.example.com/123/a.jpg"
    unrelated = "https://example.com/page"
    file = models.ForumFile(fileid="123", url=full)
    html = f'<a href="{unrelated}"><img src="{full}"/></a>'

    out = urls.remove_unrecoverable_file_references(html, file)

    assert f'<a href="{unrelated}">' in out
    assert "<img" not in out
    assert full not in out
    assert "(missing image)" in out
