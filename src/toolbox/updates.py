"""
Plan and apply post-content URL updates.
"""

import csv
import json
import tempfile
import time
from ast import literal_eval
from collections.abc import Callable
from pathlib import Path

from .cleanup import check_new_urls, check_old_urls
from .context import Context, alive_bar
from .io import confirm, linecount, read_csv
from .models import FileResult, FilesById, FilesByReference, ForumFile
from .urls import (
    fileid_from_url,
    find_html_references,
    get_new_url_func,
    migration_source_url,
    remove_unrecoverable_file_references,
    rewrite_html_references,
)


def _load_files(files_path: Path) -> FilesByReference:
    """Load `files.csv` keyed by every post-reference shape."""
    files: FilesByReference = {}
    for row in read_csv(files_path):
        file = ForumFile.from_csv_row(row)
        files[file.url] = file
        if file.url_thumb:
            files[file.url_thumb] = file
        if file.url_file:
            files[file.url_file] = file
    return files


def rewrite_post_content(
    *,
    message: str,
    image_urls: list[str],
    files: FilesByReference,
    legacy: bool,
    new_url_func: Callable[[str], str],
    files_by_id: FilesById | None = None,
) -> tuple[str, set[str]]:
    """Rewrite a post message and return (new_message, touched_urls).

    `touched_urls` are the original URLs that were replaced or de-linked. This is
    later used to compute safe delete candidates.
    """
    new_message = message
    touched_urls: set[str] = set()

    if legacy:
        for url in image_urls:
            try:
                file = files[url]
            except KeyError as e:
                raise KeyError(f"URL referenced in posts.csv not found in files.csv: {url}") from e
            if file.new_url:
                new_message = new_message.replace(url, file.new_url)
                touched_urls.add(url)
        return new_message, touched_urls

    if files_by_id is None:
        files_by_id = {file.fileid: file for file in files.values()}
    references = set(image_urls)
    references.update(find_html_references(message))

    unrecoverable_fileids: set[str] = set()
    replacements: dict[str, str] = {}
    replacement_files: dict[str, str] = {}
    for reference in sorted(references):
        file = files.get(reference)
        if file is None:
            fileid = fileid_from_url(reference)
            file = files_by_id.get(fileid) if fileid else None
        if file is None:
            continue
        if file.result is FileResult.skipped:
            continue

        source_url = migration_source_url(file, reference)
        if source_url is None:
            if file.fileid in unrecoverable_fileids:
                continue
            updated = remove_unrecoverable_file_references(new_message, file)
            if updated != new_message:
                new_message = updated
                touched_urls.add(file.url)
            unrecoverable_fileids.add(file.fileid)
            continue

        replacements[reference] = new_url_func(source_url)
        replacement_files[reference] = file.url

    # HTML parsers expose entity-decoded attribute values. Match using those
    # semantic values, but rewrite only the src/href value in the original HTML
    # so unrelated markup is not normalized or reserialized.
    new_message, matched_attributes = rewrite_html_references(new_message, replacements)
    for reference in matched_attributes:
        touched_urls.add(replacement_files[reference])

    # But use a simpler replace for literal URL references outside src/href
    # attributes. Attribute values already rewritten above no longer contain the
    # old literal, so this does not rewrite them a second time.
    for reference, replacement in replacements.items():
        if reference in new_message:
            new_message = new_message.replace(reference, replacement)
            touched_urls.add(replacement_files[reference])

    return new_message, touched_urls


def build_update_plan(
    *,
    posts_path: Path,
    files: FilesByReference,
    legacy: bool,
    new_url_func: Callable[[str], str],
    target_pids: set[str] | None = None,
) -> tuple[Path, list[str], set[str]]:
    """Build an on-disk plan of posts that would change.

    Returns:
        (plan_path, sample_pids, urls_touched)
    """
    sample_pids: list[str] = []
    urls_touched: set[str] = set()
    files_by_id = {file.fileid: file for file in files.values()}
    temp = tempfile.NamedTemporaryFile
    count = max(0, linecount(posts_path) - 1)

    with temp(mode="w", newline="\n", delete=False) as plan_file:
        plan_path = Path(plan_file.name)

        with alive_bar(count, title="Plan post updates") as bar:
            for row in read_csv(posts_path):
                pid = row["pid"]
                if target_pids is not None and pid not in target_pids:
                    bar()
                    continue
                image_urls = literal_eval(row["image_urls"])

                new_message, touched_urls = rewrite_post_content(
                    message=row["message"],
                    image_urls=image_urls,
                    files=files,
                    legacy=legacy,
                    new_url_func=new_url_func,
                    files_by_id=files_by_id,
                )

                if new_message != row["message"]:
                    if len(sample_pids) < 10:
                        sample_pids.append(pid)
                    urls_touched.update(touched_urls)

                    plan_file.write(
                        json.dumps(
                            {
                                "pid": pid,
                                "content": new_message,
                                "touched_urls": sorted(touched_urls),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                bar()

    return plan_path, sample_pids, urls_touched


def _latest_update_results(path: Path) -> dict[str, tuple[str, str]]:
    """Return the latest recorded result and content for each post ID."""
    if not path.is_file() or path.stat().st_size == 0:
        return {}
    return {row["pid"]: (row.get("result", ""), row.get("content", "")) for row in read_csv(path)}


def _already_applied_count(plan_path: Path, updates_path: Path) -> int:
    """Count planned posts already recorded as successful with identical content."""
    previous_results = _latest_update_results(updates_path)
    if not previous_results:
        return 0

    count = 0
    with plan_path.open() as plan_in:
        for line in plan_in:
            item = json.loads(line)
            if previous_results.get(item["pid"]) == ("success", item["content"]):
                count += 1
    return count


def apply_update_plan(
    *, context: Context, plan_path: Path, updates_output_path: Path | None = None
) -> tuple[int, int, int, set[str], set[str], set[str]]:
    """Apply (or simulate) the planned updates, streaming the plan from disk.

    Apply mode treats `updates.csv` as an append-only journal. A post whose
    latest journal entry is a successful update to the exact planned content is
    already complete and is not sent to the API again. Dry-run output is a
    disposable preview and is always rewritten.

    Returns:
        (posts_updated, posts_would_update, posts_already_applied,
         urls_to_delete, urls_to_keep, posts_errors)
    """
    client = context.api_client
    if updates_output_path is None:
        updates_output_path = (
            context.path.updates_dry_run if context.dry_run else context.path.updates
        )

    # The plan file is JSONL with no header row.
    count = max(0, linecount(plan_path))

    posts_updated = 0
    posts_would_update = 0
    posts_already_applied = 0
    posts_errors: set[str] = set()

    # Track only the urls we actually touched (replaced/de-linked), so we don't
    # accidentally propose deleting skipped/untouched files.
    urls_to_delete: set[str] = set()  # urls safe (or would be safe) to delete
    urls_to_keep: set[str] = set()  # urls not safe to delete

    previous_results = {} if context.dry_run else _latest_update_results(updates_output_path)
    mode = "w" if context.dry_run else "a"
    write_header = (
        mode == "w" or not updates_output_path.exists() or updates_output_path.stat().st_size == 0
    )

    with updates_output_path.open(mode, newline="") as f:
        fieldnames = ["pid", "result", "content"]
        updates_output = csv.writer(f)
        if write_header:
            updates_output.writerow(fieldnames)
            f.flush()

        with alive_bar(count, title="Update posts") as bar:
            with plan_path.open("r") as plan_in:
                for line in plan_in:
                    item = json.loads(line)
                    pid = item["pid"]
                    new_message = item["content"]
                    touched_urls = set(item.get("touched_urls", []))

                    if context.dry_run:
                        posts_would_update += 1
                        urls_to_delete.update(touched_urls)
                        updates_output.writerow([pid, "dry_run", new_message])
                        f.flush()
                        bar()
                        continue

                    if previous_results.get(pid) == ("success", new_message):
                        posts_already_applied += 1
                        urls_to_delete.update(touched_urls)
                        bar()
                        continue

                    if client.update_post(pid, new_message):
                        urls_to_delete.update(touched_urls)
                        posts_updated += 1
                        result = "success"
                    else:
                        urls_to_keep.update(touched_urls)
                        posts_errors.add(pid)
                        result = "fail"

                    updates_output.writerow([pid, result, new_message])
                    f.flush()
                    previous_results[pid] = (result, new_message)
                    time.sleep(1)  # Throttle API requests
                    bar()

    return (
        posts_updated,
        posts_would_update,
        posts_already_applied,
        urls_to_delete,
        urls_to_keep,
        posts_errors,
    )


def select_files_to_delete(
    *,
    files: FilesByReference,
    urls_to_delete: set[str],
    urls_to_keep: set[str],
) -> list[ForumFile]:
    """Return unique Toolbox files safe to hand off to the delete command."""
    blocked_fileids = {files[url].fileid for url in urls_to_keep if url in files}
    candidates: dict[str, ForumFile] = {}
    for url in urls_to_delete:
        file = files.get(url)
        if file is None or not file.url_file or file.fileid in blocked_fileids:
            continue
        candidates[file.fileid] = file
    return [candidates[fileid] for fileid in sorted(candidates)]


def update_posts(context: Context, legacy: bool = False) -> None:
    """Update posts given by `posts.csv` output from last `download` run"""
    dry_run = context.dry_run
    old_prefix = context.config.old_url
    thumb_prefix = context.config.old_url_thumb
    new_prefix = context.config.new_url
    posts_path = context.path.posts
    files_path = context.path.files

    if legacy:
        updates_output_path = (
            context.path.legacy_updates_dry_run if dry_run else context.path.legacy_updates
        )
    else:
        updates_output_path = context.path.updates_dry_run if dry_run else context.path.updates

    deletes_output_path = context.path.fileids_to_delete
    dry_deletes_output_path = context.path.fileids_to_delete_dry_run

    # Only the normal migration publishes a file-deletion handoff. Legacy-link
    # cleanup has its own update journal and must not disturb normal migration state.
    if not legacy:
        if dry_run:
            dry_deletes_output_path.write_text(json.dumps([]))
        else:
            deletes_output_path.write_text(json.dumps([]))

    new_url_func = get_new_url_func(old_prefix, thumb_prefix, new_prefix)

    files = _load_files(files_path)

    # If new_urls don't work, abort
    if not check_new_urls(context, files):
        return

    # Initialize these in case we get an exception in the 'try' block below
    urls_to_delete: set[str] = set()
    urls_to_keep: set[str] = set()
    posts_errors: set[str] = set()
    posts_updated = 0
    posts_would_update = 0
    posts_already_applied = 0
    plan_path: Path | None = None

    try:
        plan_path, sample_pids, urls_touched = build_update_plan(
            posts_path=posts_path,
            files=files,
            legacy=legacy,
            new_url_func=new_url_func,
        )

        # Final interactive confirmation in APPLY mode (remote changes).
        total_posts = max(0, linecount(posts_path) - 1)
        posts_to_update = max(0, linecount(plan_path))
        already_applied = (
            _already_applied_count(plan_path, updates_output_path) if not dry_run else 0
        )
        posts_pending = posts_to_update - already_applied
        if not dry_run and posts_pending and not context.args.yes:
            action = "update legacy links in posts" if legacy else "update posts"

            print("---- APPLY MODE: REMOTE CHANGES ----")
            print(f"About to {action} via the Toolbox API (remote changes).")
            print(f"Input posts: {posts_path}")
            print(f"Input files: {files_path}")
            print(f"Will append: {updates_output_path}")
            if not legacy:
                print(f"Will write: {deletes_output_path} (OVERWRITES)")

            print("URL rewrite:")
            print(f"  old: {old_prefix}")
            print(f"  new: {new_prefix}")

            print("Preflight:")
            print(f"  posts in update plan: {posts_to_update} of {total_posts}")
            if already_applied:
                print(f"  already applied: {already_applied}")
                print(f"  API updates required: {posts_pending}")
            if sample_pids:
                print(f"  sample pids: {', '.join(sample_pids)}")
            print(f"  unique URLs touched: {len(urls_touched)}")

            if not legacy:
                est_fileids = len({
                    files[url].fileid
                    for url in urls_touched
                    if url in files and files[url].url_file
                })
                print(f"  estimated delete candidates (fileids): {est_fileids}")

            if not confirm(context, "Type UPDATE to confirm: ", "UPDATE"):
                return

        # Apply (or simulate) the plan, streaming from disk and recording results.
        (
            posts_updated,
            posts_would_update,
            posts_already_applied,
            urls_to_delete,
            urls_to_keep,
            posts_errors,
        ) = apply_update_plan(
            context=context,
            plan_path=plan_path,
            updates_output_path=updates_output_path,
        )

    finally:
        if plan_path is not None:
            try:
                plan_path.unlink()
            except FileNotFoundError:
                pass

    if not legacy:
        # Adjust the list of images that are now safe to delete. In dry-run we
        # write the would-delete set separately so delete_files can never consume it.
        urls_to_delete_final = urls_to_delete - urls_to_keep
        files_to_delete = select_files_to_delete(
            files=files,
            urls_to_delete=urls_to_delete_final,
            urls_to_keep=urls_to_keep,
        )
        fileids_to_delete = {file.fileid for file in files_to_delete}

        if dry_run:
            dry_deletes_output_path.write_text(json.dumps(sorted(fileids_to_delete)))
        else:
            # Publish the destructive handoff only after the final reference scan passes.
            if files_to_delete and not check_old_urls(context, files_to_delete):
                print("! WARNING: At least one old_url or fileid was found in the posts")
                raise RuntimeError("Old Toolbox references remain after updating posts")
            deletes_output_path.write_text(json.dumps(sorted(fileids_to_delete)))

    if posts_errors:
        print("! Errors attempting to update the following posts:")
        for pid in posts_errors:
            print(" ", pid)

    if dry_run:
        print(f"Update posts: would update {posts_would_update} posts (dry-run)")
        if not legacy:
            print(f"Dry-run delete candidates written to: {context.path.fileids_to_delete_dry_run}")
    else:
        print(f"Update posts: {posts_updated} updated; {posts_already_applied} already applied")
