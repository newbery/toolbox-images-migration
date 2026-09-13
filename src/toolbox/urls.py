"""
URL discovery, rewriting, and HTML cleanup helpers.
"""

import re
import warnings
from collections.abc import Callable
from functools import partial
from html import unescape as html_unescape
from urllib.parse import parse_qs, quote, unquote, urlparse

from bs4 import BeautifulSoup, Comment, MarkupResemblesLocatorWarning

from .models import FileResult, ForumFile

htmlparser = partial(BeautifulSoup, features="html.parser")
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


_HTML_REFERENCE_TAG_RE = re.compile(
    r"<(?P<tag>img|a)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_REFERENCE_ATTR_RE = re.compile(
    r"(?P<name>\b(?:src|href))(?P<eq>\s*=\s*)"
    r"(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'"
    r"|(?P<unquoted>[^\s\"'=<>`]+))",
    re.IGNORECASE,
)


def _decoded_reference_attribute(match: re.Match[str]) -> tuple[str, str | None]:
    """Return the decoded value and quote character for one `src` or `href`."""
    if match.group("double") is not None:
        return html_unescape(match.group("double")), '"'
    if match.group("single") is not None:
        return html_unescape(match.group("single")), "'"
    return html_unescape(match.group("unquoted")), None


def _encode_reference_attribute(value: str, quote_char: str | None) -> str:
    """Encode one replacement URL without normalizing unrelated markup."""
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if quote_char == '"':
        return f'"{escaped.replace(chr(34), "&quot;")}"'
    if quote_char == "'":
        return f"'{escaped.replace(chr(39), '&#39;')}'"
    if re.search(r"[\s\"'=<>`]", escaped):
        return f'"{escaped.replace(chr(34), "&quot;")}"'
    return escaped


def rewrite_html_references(text: str, replacements: dict[str, str]) -> tuple[str, set[str]]:
    """Rewrite matching image/link attributes using HTML-decoded values.

    Attribute values are decoded once for semantic matching, so literal characters
    and normal HTML entity spellings compare equivalently. Only the matched
    attribute value is replaced; the rest of the source HTML is preserved exactly.
    """
    matched: set[str] = set()

    def rewrite_tag(tag_match: re.Match[str]) -> str:
        tag_text = tag_match.group(0)
        tag_name = tag_match.group("tag").lower()
        wanted_attr = "src" if tag_name == "img" else "href"

        def rewrite_attr(attr_match: re.Match[str]) -> str:
            if attr_match.group("name").lower() != wanted_attr:
                return attr_match.group(0)
            reference, quote_char = _decoded_reference_attribute(attr_match)
            replacement = replacements.get(reference)
            if replacement is None:
                return attr_match.group(0)
            matched.add(reference)
            encoded = _encode_reference_attribute(replacement, quote_char)
            return f"{attr_match.group('name')}{attr_match.group('eq')}{encoded}"

        return _HTML_REFERENCE_ATTR_RE.sub(rewrite_attr, tag_text)

    return _HTML_REFERENCE_TAG_RE.sub(rewrite_tag, text), matched


def get_new_url_func(
    old_prefix: str, thumb_prefix: str | None, new_prefix: str
) -> Callable[[str], str]:
    """Return 'new_url_func' function with the appropriate 'fixpath' change to the
    path for the case where the old url or new url contains a parameter string.
    In this case, the parameter string may need to be quoted (or unquoted) to
    escape special characters (or unescape) that aren't expected in a parameter.
    """

    def is_special(path: str) -> bool:
        return "#" in path or "?" in path

    def safe_quote(path: str) -> str:
        """Unquote before quoting... to catch cases where the path is already quoted
        but if the unquoted string contains a '#', let's double-quote it, otherwise
        the server will interpret this as a fragment. This is done to retain the quoted
        '#' through the proxy we're using which otherwise exposes the '#' character
        in the filename too early.

        This may not be ideal (and may not be robust for alternative proxy
        configurations) but it works for the current setup.
        """
        unquoted_path = unquote(path)
        path = path if is_special(unquoted_path) else unquoted_path
        return quote(path)

    old_has_param = "?" in old_prefix
    new_has_param = "?" in new_prefix
    both_match = old_has_param is new_has_param

    if both_match:
        fixpath = noop
    elif old_has_param:
        fixpath = unquote
    else:
        fixpath = safe_quote

    def new_url_func(url: str) -> str:
        if thumb_prefix and url.startswith(thumb_prefix):
            thumb = "thumb/"
            prefix = len(thumb_prefix)
        else:
            thumb = ""
            prefix = len(old_prefix)
        return new_prefix + thumb + fixpath(url[prefix:])

    return new_url_func


def migration_source_url(file: ForumFile, reference: str) -> str | None:
    """Return the source variant whose migrated destination should replace `reference`.

    Preserve full-size and thumbnail destinations when both variants exist. If
    exactly one variant is unavailable at the source, use the surviving variant
    for either reference. Return `None` when no usable source variant exists;
    callers can then remove the obsolete media reference from the post.
    """
    if file.result is FileResult.skipped:
        return None

    full_ok = file.result is FileResult.downloaded
    # Older files.csv rows predate thumb_result. In that format a downloaded
    # full image implied that its known thumbnail was also usable, so preserve
    # that behavior when the thumbnail state is still default. New download runs
    # always record an explicit downloaded/error thumbnail result.
    thumb_ok = bool(file.url_thumb) and (
        file.thumb_result is FileResult.downloaded
        or (file.thumb_result is FileResult.default and full_ok)
    )

    if reference == file.url_thumb:
        if thumb_ok:
            return file.url_thumb
        if full_ok:
            return file.url
        return None

    if full_ok:
        return file.url
    if thumb_ok:
        return file.url_thumb
    return None


def find_urls_func(prefix: str | tuple[str, str]) -> Callable[[str], list[str]]:
    """Return 'find_urls' function that returns a list of image urls found in a
    string that starts with any of the expected url prefixes.

    Later this will be extended to include support for other types of urls.
    """

    def find_urls(text: str) -> list[str]:
        urls: set[str] = set()

        for img in htmlparser(text).find_all("img"):
            src = img.get("src")
            if isinstance(src, str) and src.startswith(prefix):
                urls.add(src)

        return sorted(urls)

    return find_urls


def find_html_references(text: str) -> list[str]:
    """Return decoded URL-like values from image `src` and anchor `href`.

    Normal HTML entity spellings and literal characters therefore produce the same
    semantic reference without repeatedly unescaping malformed/double-escaped text.
    """
    references: set[str] = set()
    for tag_match in _HTML_REFERENCE_TAG_RE.finditer(text):
        tag_name = tag_match.group("tag").lower()
        wanted_attr = "src" if tag_name == "img" else "href"
        for attr_match in _HTML_REFERENCE_ATTR_RE.finditer(tag_match.group(0)):
            if attr_match.group("name").lower() != wanted_attr:
                continue
            reference, _quote_char = _decoded_reference_attribute(attr_match)
            references.add(reference)
    return sorted(references)


def find_legacy_urls(text: str) -> list[str]:
    """Return legacy urls found in given html string"""
    html = htmlparser(text)
    prefixes: tuple[str, ...] = (
        "/file?id=",
        "https://s3.amazonaws.com/files.websitetoolbox.com/",
        "http://files.websitetoolbox.com/",
    )

    urls: set[str] = set()

    for img in html.find_all("img"):
        src = img.get("src")
        if isinstance(src, str) and src.startswith(prefixes):
            urls.add(src)

    for a in html.find_all("a"):
        href = a.get("href")
        if isinstance(href, str) and href.startswith(prefixes):
            urls.add(href)

    return sorted(urls)


def fileid_from_url(url: str) -> str | None:
    """Extract a fileid from any of the url formats we currently see.

    Supports:
      1) (legacy url) .../file?id=<fileid>
      2) (legacy url) .../files.websitetoolbox.com/<toolid>/<fileid>/<filename>
         including:
           https://s3.amazonaws.com/files.websitetoolbox.com/<toolid>/<fileid>/<filename>
      3) Non-legacy urls and other urls/paths where fileid is the segment before a filename.
         (with a best-effort fallback to last numeric segment)

    Returns None if no plausible fileid can be found.
    """
    try:
        p = urlparse(url)
    except Exception:
        return None

    path_segments = [s for s in (p.path or "").split("/") if s]

    # Case #1: /file?id=<fileid>
    if path_segments and path_segments[-1] == "file" and p.query:
        qs = parse_qs(p.query, keep_blank_values=True)
        vals = qs.get("id")
        if vals and vals[0]:
            return vals[0]

    # The other cases are all parsed the same way
    if len(path_segments) >= 2:
        last = unquote(path_segments[-1])
        second_to_last = path_segments[-2]

        # If last looks like a filename, second_to_last is probably a fileid
        if "." in last and second_to_last.isdigit():
            return second_to_last

        # Otherwise fallback to taking the last numeric segment
        for seg in reversed(path_segments):
            if seg.isdigit():
                return seg

    return None


def remove_unrecoverable_file_references(text: str, file: ForumFile) -> str:
    """Remove obsolete references for a file with no usable migrated source variant.

    Image references become the existing visible `(missing image)` marker.
    Dead image elements are removed entirely. Links to the same missing file are
    unwrapped so their visible contents remain without an obsolete `href`.
    """
    references = {value for value in (file.url, file.url_thumb, file.url_file) if value}
    html = htmlparser(text)
    changed = False

    for img in html.find_all("img"):
        src = img.get("src")
        if not isinstance(src, str) or src not in references:
            continue

        notice = html.new_tag("span", attrs={"class": "missing-image"})
        notice.append("(missing image)")
        link = img.find_parent("a")
        target = link or img
        target.insert_after(" ", notice, Comment(f" Bad URL: {src.replace('https://', '')} "))
        img.decompose()
        changed = True

    for anchor in html.find_all("a"):
        href = anchor.get("href")
        if isinstance(href, str) and href in references:
            anchor.unwrap()
            changed = True

    return html.decode(formatter="html") if changed else text


def noop(x):
    return x
