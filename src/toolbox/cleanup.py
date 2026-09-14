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

import requests
from plumbum.cmd import cut, grep

from .context import Context, alive_bar
from .download import safe_download_path
from .io import batched, confirm, linecount, read_csv
from .models import FileMap, ForumFile
from .urls import (
    fileid_from_url,
    find_html_references,
    get_new_url_func,
    migration_source_url,
)


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
    """Check destination URLs referenced by the current `posts.csv`.

    A matching file in `_uploaded_` is treated as a record that the destination
    URL was already confirmed by `archive_downloads`. When no such local record
    exists, check the destination URL directly. Files with no usable source/local
    variant need no destination check because their obsolete post references will
    be removed. Return False if any required destination URL cannot be confirmed.
    """
    dry_run = context.dry_run
    old_prefix = context.config.old_url
    thumb_prefix = context.config.old_url_thumb
    new_prefix = _new_url_prefix(context)
    posts_path = context.path.posts
    url_ok = context.url_ok

    # Keep URL checks slow enough to avoid overwhelming the destination host.
    sleep = 0.001 if dry_run else 0.25

    new_url_func = get_new_url_func(old_prefix, thumb_prefix, new_prefix)

    # Confirm all old urls are available at the new location except for files
    # that were skipped or failed during download. Prefer the local _uploaded_
    # archive as evidence so repeated update runs do not recheck known URLs.
    seen: set[str] = set()
    images_errors = set()
    files_by_id = {file.fileid: file for file in files.values()}
    count = max(0, linecount(posts_path) - 1)
    with alive_bar(count, title="Check new urls") as bar:
        for row in read_csv(posts_path):
            references = set(literal_eval(row["image_urls"]))
            references.update(find_html_references(row["message"]))
            for reference in references:
                file = files.get(reference)
                if file is None:
                    fileid = fileid_from_url(reference)
                    file = files_by_id.get(fileid) if fileid else None
                if file is None:
                    continue
                source_url = migration_source_url(file, reference)
                if source_url is None:
                    continue

                if source_url in seen:
                    continue
                seen.add(source_url)

                uploaded_path = _uploaded_path_for_url(context, file, source_url)
                if uploaded_path is not None and uploaded_path.exists():
                    continue

                new_url = file.new_url if file.new_url else new_url_func(source_url)
                if not url_ok(new_url):
                    images_errors.add(new_url)
                time.sleep(sleep)
            bar()

    if images_errors:
        print("Check new urls: !!! Destination unavailable for the following images:")
        for url in sorted(images_errors):
            print(" ", url)
    else:
        print("Check new urls: Passed; All required destination images are accessible")

    return not images_errors


def _iter_download_files(root: Path) -> Iterator[tuple[Path, str]]:
    """Yield managed files below a download directory with relative POSIX paths."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != ".DS_Store":
            yield path, path.relative_to(root).as_posix()


def _remove_ds_store_files(root: Path) -> None:
    """Remove macOS Finder metadata below `root`."""
    if not root.exists():
        return
    for path in root.rglob(".DS_Store"):
        if path.is_file():
            path.unlink()


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

    Each file in `_new_` is checked independently. A confirmed file is moved to
    the same relative path under `_uploaded_` only in apply mode. Failed checks
    and local destination conflicts remain in `_new_` and are listed when the
    operation completes or is interrupted. Finder `.DS_Store` files are ignored
    for URL checks and removed in apply mode.
    """
    dry_run = context.dry_run
    download_dir = Path(context.path.download_dir)
    new_dir = download_dir / "_new_"
    uploaded_dir = download_dir / "_uploaded_"
    url_status = context.url_status

    if dry_run:
        print("Archive downloads: Dry run; no local files will be moved or deleted")

    files = list(_iter_download_files(new_dir))
    if not files:
        print(f"Archive downloads: No image files under {new_dir}")
        return

    total = len(files)
    archived = 0
    checked = 0
    found = 0
    failures: list[tuple[str, str, str]] = []
    conflicts: list[str] = []
    interrupted = False
    sleep = 0.001 if dry_run else 0.25

    print(f"Archive downloads: Checking {total} files under {new_dir}")

    try:
        with alive_bar(total, title="Archive downloads") as bar:
            for src_path, rel in files:
                new_url = _new_url_for_download_path(context, rel)
                try:
                    status = url_status(new_url)
                except requests.RequestException as exc:
                    checked += 1
                    detail = type(exc).__name__
                    if str(exc):
                        detail = f"{detail}: {exc}"
                    failures.append((rel, new_url, detail))
                else:
                    checked += 1
                    if status not in (200, 206):
                        failures.append((rel, new_url, f"HTTP {status}"))
                    else:
                        found += 1

                        dst_path = safe_download_path(uploaded_dir, rel)
                        if dst_path.exists():
                            if filecmp.cmp(src_path, dst_path, shallow=False):
                                if not dry_run:
                                    src_path.unlink()
                                archived += 1
                            else:
                                conflicts.append(rel)
                        else:
                            if not dry_run:
                                dst_path.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(src_path, dst_path)
                            archived += 1

                bar.text = f"{checked}/{total} checked; {found} found; {len(failures)} failed"
                bar()
                time.sleep(sleep)
    except KeyboardInterrupt:
        interrupted = True

    if dry_run:
        remaining_rels = {rel for rel, _url, _detail in failures} | set(conflicts)
        if interrupted:
            remaining_rels.update(rel for _path, rel in files[checked:])
        remaining = sorted(remaining_rels)
    else:
        _remove_ds_store_files(new_dir)
        _remove_empty_directories(new_dir)
        remaining = [rel for _, rel in _iter_download_files(new_dir)]

    unchecked = total - checked
    action = "would archive" if dry_run else "archived"
    remainder = "would remain" if dry_run else "remaining"

    if interrupted:
        print(f"Archive downloads: Interrupted after checking {checked}/{total} files")

    if failures:
        print("Destination checks failed:")
        for rel, url, detail in failures:
            print(f"  {rel}: {detail} {url}")

    if conflicts:
        print("Conflicts with existing files in _uploaded_:")
        for rel in conflicts:
            print(" ", rel)

    if remaining and not interrupted:
        if dry_run:
            heading = "Files that would remain in _new_:"
        else:
            heading = "Files remaining in _new_:"
        print(heading)
        for rel in remaining:
            print(" ", rel)

    print(
        f"Archive downloads: {checked} checked; {found} found; "
        f"{len(failures)} failed; {unchecked} unchecked"
    )
    print(f"Archive downloads: {archived} {action}; {len(remaining)} {remainder} in {new_dir}")

    if interrupted:
        raise SystemExit(130)


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
