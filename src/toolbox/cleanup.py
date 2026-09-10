"""
Verify migrated URLs and remove obsolete Website Toolbox files.
"""

import filecmp
import json
import re
import shutil
import tempfile
import time
from ast import literal_eval
from collections.abc import Iterable, Iterator
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote

from plumbum.cmd import cut, grep

from .context import Context, alive_bar
from .download import safe_download_path
from .io import batched, confirm, linecount, read_csv
from .models import FileMap, FileResult, ForumFile
from .urls import get_new_url_func


def delete_files(context: Context) -> None:
    """Delete Toolbox images given by set of fileids.

    There is no API endpoint for deleting files so this instead uses the
    Admin UI by simulating the Delete Files form submission.

    This of course won't work with files not hosted by Toolbox so it will
    throw an error if an attempt to made to do that.
    """
    client = context.admin_client
    deletes_path = context.path.fileids_to_delete
    fileids_to_delete = json.loads(deletes_path.read_text())

    if context.dry_run:
        print("---- Dry Run: would delete the following fileids (no changes made) ----")
        if not fileids_to_delete:
            print("(none)")
        else:
            for fid in fileids_to_delete:
                print(" ", fid)
        return

    if not fileids_to_delete:
        print("Delete files: no fileids listed; nothing to do.")
        return

    if not context.args.yes:
        preview = ", ".join(str(x) for x in fileids_to_delete[:10])
        more = "" if len(fileids_to_delete) <= 10 else f"... (+{len(fileids_to_delete) - 10} more)"
        print(f"About to permanently delete {len(fileids_to_delete)} files from Toolbox.")
        print(f"First 10 fileids: {preview} {more}")

    # A final interactive confirmation helps avoid catastrophic deletes.
    if not confirm(context, "Type DELETE to confirm: ", "DELETE"):
        return

    successes: list[str] = []
    count = len(fileids_to_delete)

    try:
        with alive_bar(count, title="Delete files") as bar:
            for fileids in batched(fileids_to_delete, 100):
                if not fileids:
                    continue
                client.delete_files(fileids)
                successes.extend(fileids)
                bar(len(fileids))
                time.sleep(1.5)
    finally:
        print(f"Successfully deleted: {successes}")

    print(f"Delete files: {count} deleted")


def _new_url_prefix(context: Context) -> str:
    """Return the destination URL prefix used for reachability checks."""
    new_prefix = context.config.new_url
    if context.dry_run and not new_prefix.lower().startswith(("https://", "http://", "file://")):
        return f"file://{Path(new_prefix).resolve()!s}/"
    return new_prefix


def _new_url_for_download_path(context: Context, path: str) -> str:
    """Return the destination URL corresponding to a relative download path."""
    old_prefix = context.config.old_url
    thumb_prefix = context.config.old_url_thumb
    new_url_func = get_new_url_func(old_prefix, thumb_prefix, _new_url_prefix(context))

    if thumb_prefix and path.startswith("thumb/"):
        old_url = thumb_prefix + quote(path.removeprefix("thumb/"))
    else:
        old_url = old_prefix + quote(path)
    return new_url_func(old_url)


def _uploaded_path_for_url(context: Context, file: ForumFile, url: str) -> Path | None:
    """Return the local uploaded archive path corresponding to one source URL."""
    if not file.path:
        return None

    path = f"thumb/{file.path}" if file.url_thumb and url == file.url_thumb else file.path
    return safe_download_path(context.path.download_dir / "_uploaded_", path)


def check_new_urls(context: Context, files: FileMap) -> bool:
    """Check destination URLs referenced by the current ``posts.csv``.

    A matching file in ``_uploaded_`` is treated as a record that the destination
    URL was already confirmed by ``archive_downloads``. When no such local record
    exists, check the destination URL directly. Return False if any required URL
    cannot be confirmed.
    """
    dry_run = context.dry_run
    old_prefix = context.config.old_url
    thumb_prefix = context.config.old_url_thumb
    new_prefix = _new_url_prefix(context)
    posts_path = context.path.posts
    url_ok = context.url_ok

    # Set this to False to generate a list of failing urls.
    # Make this an environment setting?
    stop_fast = False

    # The proxy we're using throttles at 2500 req per 10 min.
    # Make this sleep interval an environment setting?
    sleep = 0.001 if dry_run else 0.25

    new_url_func = get_new_url_func(old_prefix, thumb_prefix, new_prefix)

    # Confirm all old urls are available at the new location except for files
    # that were skipped or failed during download. Prefer the local _uploaded_
    # archive as evidence so repeated update runs do not recheck known URLs.
    seen = set()
    images_errors = set()
    count = max(0, linecount(posts_path) - 1)
    with alive_bar(count, title="Check new urls") as bar:
        for row in read_csv(posts_path):
            for url in literal_eval(row["image_urls"]):
                file = files.get(url)
                result = file.result if file else FileResult.default
                if url in seen or result in (FileResult.skipped, FileResult.error):
                    continue
                seen.add(url)

                if file is not None:
                    uploaded_path = _uploaded_path_for_url(context, file, url)
                    if uploaded_path is not None and uploaded_path.exists():
                        continue

                new_url = file.new_url if file and file.new_url else new_url_func(url)
                if not url_ok(new_url):
                    images_errors.add(new_url)
                    if stop_fast:
                        raise RuntimeError(f"Image not found: {new_url}")
                time.sleep(sleep)
            bar()

    if images_errors:
        print("Check new urls: !!! Errors attempting to access the following images:")
        for url in sorted(images_errors):
            print(" ", url)
    else:
        print("Check new urls: Passed; All images are accessible at new urls")

    return not images_errors


def _iter_download_files(root: Path) -> Iterator[tuple[Path, str]]:
    """Yield files below a managed download directory with relative POSIX paths."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path, path.relative_to(root).as_posix()


def _remove_empty_directories(root: Path) -> None:
    """Remove empty child directories while retaining ``root`` itself."""
    if not root.exists():
        return
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        with suppress(OSError):
            path.rmdir()


def archive_downloads(context: Context) -> None:
    """Confirm newly uploaded images at the destination and archive local copies.

    Each file in ``_new_`` is checked independently. A confirmed file is moved to
    the same relative path under ``_uploaded_``. Missing URLs and local destination
    conflicts remain in ``_new_`` and are listed when the operation completes.
    """
    download_dir = Path(context.path.download_dir)
    new_dir = download_dir / "_new_"
    uploaded_dir = download_dir / "_uploaded_"
    url_ok = context.url_ok

    files = list(_iter_download_files(new_dir))
    if not files:
        print(f"Archive downloads: No files under {new_dir}")
        return

    archived = 0
    missing: list[tuple[str, str]] = []
    conflicts: list[str] = []
    sleep = 0.001 if context.dry_run else 0.25

    with alive_bar(len(files), title="Archive downloads") as bar:
        for src_path, rel in files:
            new_url = _new_url_for_download_path(context, rel)
            if not url_ok(new_url):
                missing.append((rel, new_url))
                bar()
                time.sleep(sleep)
                continue

            dst_path = safe_download_path(uploaded_dir, rel)
            if dst_path.exists():
                if filecmp.cmp(src_path, dst_path, shallow=False):
                    src_path.unlink()
                    archived += 1
                else:
                    conflicts.append(rel)
            else:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src_path, dst_path)
                archived += 1

            bar()
            time.sleep(sleep)

    _remove_empty_directories(new_dir)
    remaining = [rel for _, rel in _iter_download_files(new_dir)]

    print(f"Archive downloads: {archived} archived; {len(remaining)} remaining in {new_dir}")

    if missing:
        print("Not found at new host:")
        for rel, url in missing:
            print(f"  {rel}: {url}")

    if conflicts:
        print("Conflicts with existing files in _uploaded_:")
        for rel in conflicts:
            print(" ", rel)

    if remaining:
        print("Files remaining in _new_:")
        for rel in remaining:
            print(" ", rel)


def check_urls_in_uploaded_folder(context: Context) -> None:
    """Confirm that archived uploads can still be found at the new image host.

    The relative paths under ``_uploaded_`` are expected to match the destination
    host paths. Missing files are copied to ``_notfound_`` for manual inspection.
    """
    download_dir = Path(context.path.download_dir)
    uploaded_dir = download_dir / "_uploaded_"
    notfound_dir = download_dir / "_notfound_"
    url_ok = context.url_ok

    files = list(_iter_download_files(uploaded_dir))
    if not files:
        print(f"Check urls in uploaded folder: No files under {uploaded_dir}")
        return

    missing = 0
    first_few_missing: list[str] = []

    with alive_bar(len(files), title="Check uploaded downloads at new host") as bar:
        for src_path, rel in files:
            new_url = _new_url_for_download_path(context, rel)
            if not url_ok(new_url):
                missing += 1
                dst = safe_download_path(notfound_dir, rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst)
                if len(first_few_missing) < 20:
                    first_few_missing.append(new_url)

            time.sleep(0.001 if context.dry_run else 0.25)
            bar()

    checked = len(files)
    if missing:
        print(
            f"Check urls in uploaded folder: {missing}/{checked} missing; copied to: {notfound_dir}"
        )
        if first_few_missing:
            print("First missing urls:")
            for url in first_few_missing:
                print(" ", url)
    else:
        print(f"Check urls in uploaded folder: Passed; {checked} files found at new host")


def grep_urls_in_file(updates_path: Path, urls: list[str]) -> str:
    """Given a CSV file `updates_path` and a list of URLs, return the matching
    post IDs (first CSV field) for rows that contain any of the URLs.

    Equivalent intent to the original:
        result = (grep["-E", _urls, updates_path] | cut["-d,", "-f1"])(retcode=None)

    but uses fixed-string grep (no regex interpretation), via:
        grep -F -f <patterns_file> updates.csv | cut -d, -f1
    """
    # Drop empties and de-dup
    seen: set[str] = set()
    patterns = [u for u in urls if u and not (u in seen or seen.add(u))]
    if not patterns:
        return ""

    pattern_path: Path | None = None
    try:
        # Write patterns one-per-line for grep -f
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            for u in patterns:
                tf.write(u)
                tf.write("\n")
            pattern_path = Path(tf.name)

        # grep -F: fixed strings, -f: read patterns from file
        # Pipe to cut to extract first CSV column (post id)
        # retcode=None allows grep exit 1 (no matches) without raising
        return (grep["-F", "-f", str(pattern_path), str(updates_path)] | cut["-d,", "-f1"])(
            retcode=None
        )

    finally:
        if pattern_path is not None:
            try:
                pattern_path.unlink()
            except FileNotFoundError:
                pass


def check_old_urls(
    context: Context, files_to_check: Iterable[ForumFile], legacy: bool = False
) -> bool:
    """Check if any old_urls are still found in updated posts (and in posts
    not updated).

    1) Search 'updates.csv' for any 'url', 'url_thumb', or 'url_file'.
    2) Search a subset of 'posts_from_export.csv' and 'posts_from_api.csv'
    that includes only the non-updated posts.
    3) If any matches are found, print out the list and return False, otherwise
    return True.

    This is checked after 'update_posts'
    """
    updates_path = Path(context.path.updates)
    from_export_path = context.path.posts_from_export
    from_api_path = context.path.posts_from_api
    posts_paths = [from_export_path] if legacy else [from_export_path, from_api_path]

    urls = set()
    fileids = set()
    for f in files_to_check:
        urls.update([f.url, f.url_thumb, f.url_file])
        fileid = re.escape(f.fileid)
        fileids.update([rf"={fileid}", rf"/{fileid}/"])
    urls.discard("")

    found_in_updated = []
    found_in_nonupdated = []
    count = len(urls) + len(fileids)

    with alive_bar(count, title="Check old urls") as bar:
        for batch in batched(urls, 100):
            result = grep_urls_in_file(updates_path, batch)
            found_in_updated += result.split()
            bar(len(batch))

        for batch in batched(fileids, 10):
            fileids_ = "|".join(batch)
            result = (grep["-Eh", fileids_, *posts_paths] | cut["-d,", "-f1"])(retcode=None)
            found_in_nonupdated += result.split()
            bar(len(batch))
        found_in_nonupdated = set(found_in_nonupdated) - set(found_in_updated)

    if found_in_updated:
        print("Check old urls: !!! Old urls found in these updated posts:")
        for pid in found_in_updated:
            print(f"  {pid}")

    if found_in_nonupdated:
        print("Check old urls: !!! Old fileids found in these non-updated posts:")
        for pid in sorted(found_in_nonupdated):
            print(f"  {pid}")

    return not bool(found_in_updated or found_in_nonupdated)
