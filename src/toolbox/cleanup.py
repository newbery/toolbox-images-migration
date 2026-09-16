"""
Verify migrated URLs and remove obsolete Website Toolbox files.
"""

import csv
import filecmp
import json
import shutil
import time
from ast import literal_eval
from collections.abc import Iterable, Iterator
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests

from .clients import DeleteConfirmation
from .context import Context, alive_bar
from .download import safe_download_path
from .io import confirm, linecount, read_csv
from .models import FileMap, FilesById, ForumFile
from .urls import (
    fileid_from_url,
    find_html_references,
    find_post_references,
    get_new_url_func,
    migration_source_url,
)

_DELETE_BATCH_SIZE = 100
_DELETE_429_MAX_RETRIES = 5
_DELETE_429_BASE_SLEEP = 30.0
_DELETE_429_MAX_SLEEP = 300.0


def _write_json(path: Path, value: object) -> None:
    """Atomically replace a JSON checkpoint file."""
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(value))
    temp_path.replace(path)


def _retry(error: requests.HTTPError, retry_number: int) -> float:
    """Return a Retry-After delay or an exponential fallback for HTTP 429."""
    response = error.response
    header = response.headers.get("Retry-After") if response is not None else None
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(header)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass

    return min(_DELETE_429_BASE_SLEEP * (2 ** (retry_number - 1)), _DELETE_429_MAX_SLEEP)


def _delete_batch(context: Context, fileids: list[str]) -> DeleteConfirmation:
    """Delete one batch, retrying HTTP 429 responses with backoff."""
    client = context.admin_client
    retry_number = 0
    while True:
        try:
            confirmation = client.delete_files(fileids)
            print(f"Delete files: Website Toolbox confirmation: {confirmation.message}")
            return confirmation
        except requests.HTTPError as error:
            response = error.response
            if response is None or response.status_code != 429:
                raise
            if retry_number >= _DELETE_429_MAX_RETRIES:
                raise
            retry_number += 1
            delay = _retry(error, retry_number)
            print(
                "Delete files: HTTP 429 Too Many Requests; "
                f"retrying current batch in {delay:g}s "
                f"({retry_number}/{_DELETE_429_MAX_RETRIES})"
            )
            time.sleep(delay)


def _write_delete_unresolved(
    context: Context, batch: list[str], confirmation: DeleteConfirmation
) -> None:
    """Record a partial delete confirmation whose surviving IDs are unknown."""
    value = {
        "submitted": len(batch),
        "confirmed_deleted": confirmation.count,
        "unresolved": len(batch) - confirmation.count,
        "confirmation": confirmation.message,
        "fileids": batch,
    }
    _write_json(context.path.delete_unresolved, value)


def delete_files(context: Context) -> None:
    """Delete Toolbox files in resumable, rate-limited batches.

    There is no API endpoint for deleting files, so this uses the Admin UI by
    simulating its AJAX Delete Files submission. A batch is checkpointed only
    when Website Toolbox confirms that every submitted ID was deleted. Partial
    confirmations leave the entire ambiguous batch pending and write
    `delete_unresolved.json` for later reconciliation.
    """
    deletes_path = context.path.fileids_to_delete
    remaining = [str(fileid) for fileid in json.loads(deletes_path.read_text())]
    count = len(remaining)
    limit = context.args.delete_limit
    run_count = min(count, limit) if limit is not None else count

    print(f"Delete files: {count} candidates")
    if limit is not None:
        print(f"Delete files: limiting this run to {run_count} files")

    if context.dry_run:
        target = remaining[:run_count]
        print("---- Dry Run: would delete the following fileids (no changes made) ----")
        if not target:
            print("(none)")
        else:
            for fid in target:
                print(" ", fid)
        print(
            f"Delete files: would submit {run_count} files this run; "
            f"{count - run_count} would remain"
        )
        return

    if not remaining:
        print("Delete files: no fileids listed; nothing to do.")
        context.path.delete_unresolved.unlink(missing_ok=True)
        return

    if not context.args.yes:
        target = remaining[:run_count]
        preview = ", ".join(target[:10])
        more = "" if run_count <= 10 else f"... (+{run_count - 10} more)"
        print(f"About to permanently delete {run_count} files from Toolbox.")
        if count > run_count:
            print(f"An additional {count - run_count} candidates will remain checkpointed.")
        print(f"First 10 fileids: {preview} {more}")

    if not confirm(context, "Type DELETE to confirm: ", "DELETE"):
        return

    submitted = 0
    confirmed = 0

    try:
        with alive_bar(run_count, title="Delete files") as bar:
            while submitted < run_count:
                batch_size = min(_DELETE_BATCH_SIZE, run_count - submitted)
                batch = remaining[:batch_size]
                confirmation = _delete_batch(context, batch)
                submitted += len(batch)

                if confirmation.count > len(batch):
                    raise RuntimeError(
                        "Website Toolbox confirmed more deletions than were submitted: "
                        f"{confirmation.count} > {len(batch)}"
                    )

                confirmed += confirmation.count
                unresolved = len(batch) - confirmation.count
                if unresolved:
                    _write_delete_unresolved(context, batch, confirmation)
                    print(
                        "Delete files: partial confirmation; "
                        f"{len(batch)} submitted, {confirmation.count} confirmed deleted, "
                        f"{unresolved} unresolved"
                    )
                    print(
                        "Delete files: unresolved IDs cannot be identified from the Admin "
                        "response; the entire batch remains checkpointed"
                    )
                    print(f"Delete files: diagnostic written to {context.path.delete_unresolved}")
                    print(
                        f"Delete files: {submitted} submitted; {confirmed} confirmed deleted; "
                        f"{unresolved} unresolved; {len(remaining)} checkpointed"
                    )
                    raise SystemExit(1)

                remaining = remaining[len(batch) :]
                _write_json(deletes_path, remaining)
                bar(len(batch))
    except KeyboardInterrupt:
        print()
        print(
            f"Delete files: interrupted; {submitted} submitted, "
            f"{confirmed} confirmed deleted; {len(remaining)} remaining"
        )
        print(f"Delete files: remaining IDs are checkpointed in {deletes_path}")
        raise SystemExit(130) from None
    except requests.HTTPError as error:
        response = error.response
        status = response.status_code if response is not None else "unknown"
        print(
            f"Delete files: stopped after HTTP {status}: {error}; "
            f"{submitted} submitted, {confirmed} confirmed deleted"
        )
        print(f"Delete files: {len(remaining)} remaining; checkpointed in {deletes_path}")
        raise SystemExit(1) from None
    except requests.RequestException as error:
        print(f"Delete files: request failed: {type(error).__name__}: {error}")
        print(
            f"Delete files: {submitted} submitted, {confirmed} confirmed deleted; "
            f"{len(remaining)} remaining"
        )
        print(f"Delete files: remaining IDs are checkpointed in {deletes_path}")
        raise SystemExit(1) from None
    except RuntimeError as error:
        print(f"Delete files: stopped because deletion was not confirmed: {error}")
        print(
            f"Delete files: {submitted} submitted, {confirmed} confirmed deleted; "
            f"{len(remaining)} remaining"
        )
        print(f"Delete files: remaining IDs are checkpointed in {deletes_path}")
        raise SystemExit(1) from None

    context.path.delete_unresolved.unlink(missing_ok=True)
    print(
        f"Delete files: {submitted} submitted; {confirmed} confirmed deleted; "
        f"0 unresolved; {len(remaining)} remaining"
    )


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


def _reference_index(files_by_id: FilesById) -> dict[str, tuple[str, str]]:
    """Return exact old-reference lookup entries keyed by reference text."""
    result: dict[str, tuple[str, str]] = {}
    for fileid, file in files_by_id.items():
        for kind, reference in (
            ("url", file.url),
            ("thumb", file.url_thumb),
            ("file", file.url_file),
        ):
            if reference:
                result[reference] = (fileid, kind)
    return result


def _references_in_content(
    content: str,
    files_by_id: FilesById,
    reference_index: dict[str, tuple[str, str]] | None = None,
) -> list[tuple[str, str, str]]:
    """Return candidate old references present in one post's content."""
    if reference_index is None:
        reference_index = _reference_index(files_by_id)

    matches: set[tuple[str, str, str]] = set()
    for reference in find_post_references(content):
        exact = reference_index.get(reference)
        if exact is not None:
            fileid, kind = exact
            matches.add((fileid, kind, reference))
            continue

        if reference.startswith("/file?id=") or "/file?id=" in reference:
            fileid = fileid_from_url(reference)
            if fileid in files_by_id:
                matches.add((fileid, "fileid-query", reference))

    return sorted(matches, key=lambda item: (item[0], item[1], item[2]))


def _successful_update_pids(updates_path: Path) -> set[str]:
    """Return post IDs whose latest journal result is successful."""
    latest = {row["pid"]: row.get("result") for row in read_csv(updates_path)}
    return {pid for pid, result in latest.items() if result == "success"}


def _write_old_reference_failures(
    path: Path, rows: list[tuple[str, str, str, str, str, str]]
) -> None:
    """Write exact surviving old references found by final verification."""
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(("state", "source", "pid", "fileid", "kind", "reference"))
        writer.writerows(rows)


def check_old_urls(
    context: Context, files_to_check: Iterable[ForumFile], legacy: bool = False
) -> bool:
    """Verify that delete candidates no longer have surviving old references."""
    updates_path = Path(context.path.updates)
    from_export_path = context.path.posts_from_export
    from_api_path = context.path.posts_from_api
    posts_paths = [from_export_path] if legacy else [from_export_path, from_api_path]
    report_path = context.path.old_reference_failures
    report_path.unlink(missing_ok=True)

    candidate_files = {file.fileid: file for file in files_to_check}
    reference_index = _reference_index(candidate_files)

    found_in_updated: set[str] = set()
    found_in_nonupdated: set[str] = set()
    failures: list[tuple[str, str, str, str, str, str]] = []

    updated_pids = _successful_update_pids(updates_path)

    source = updates_path.name
    verification_updates: dict[str, tuple[str, str]] = {
        row["pid"]: (row.get("content", ""), source) for row in read_csv(updates_path)
    }
    update_count = len(verification_updates)
    source_count = sum(max(0, linecount(path) - 1) for path in posts_paths)
    with alive_bar(update_count + source_count, title="Check old urls") as bar:
        for pid, (content, source) in verification_updates.items():
            matches = _references_in_content(content, candidate_files, reference_index)
            if matches:
                found_in_updated.add(pid)
                failures.extend(
                    ("updated", source, pid, fileid, kind, reference)
                    for fileid, kind, reference in matches
                )
            bar()

        config = context.config
        tokens = (config.old_url, config.old_url_thumb, "/file?id=")
        old_tokens = tuple(token for token in tokens if token)

        for path in posts_paths:
            for row in read_csv(path):
                pid = row["pid"]
                if pid in updated_pids:
                    bar()
                    continue
                content = row.get("message", "")
                if old_tokens and not any(token in content for token in old_tokens):
                    bar()
                    continue
                matches = _references_in_content(content, candidate_files, reference_index)
                if matches:
                    found_in_nonupdated.add(pid)
                    failures.extend(
                        ("non-updated", path.name, pid, fileid, kind, reference)
                        for fileid, kind, reference in matches
                    )
                bar()

    if found_in_updated:
        print("Check old urls: !!! Old urls found in these updated posts:")
        for pid in sorted(found_in_updated):
            print(f"  {pid}")

    if found_in_nonupdated:
        print("Check old urls: !!! Old fileids found in these non-updated posts:")
        for pid in sorted(found_in_nonupdated):
            print(f"  {pid}")

    if failures:
        failures.sort(key=lambda row: (row[0], row[2], row[3], row[4], row[5], row[1]))
        _write_old_reference_failures(report_path, failures)
        print(f"Check old urls: diagnostic report written to: {report_path}")
        return False

    return True
