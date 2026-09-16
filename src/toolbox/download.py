"""
Download discovered files and write migration summaries.
"""

import csv
from collections import defaultdict
from itertools import chain
from pathlib import Path

from .context import Context, alive_bar
from .io import friendly_size, read_csv
from .models import FileResult, FilesById


def safe_download_path(root: Path, path: str) -> Path:
    """Join a relative download path to `root` without allowing it to escape."""
    root = root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Unsafe download path outside {root}: {path!r}")
    return target


def download_files(context: Context, files: FilesById) -> FilesById:
    """Download files to be moved to the new image host"""
    download_dir = context.path.download_dir
    download = context.downloader.download

    def download_file(url: str, path: str) -> int:
        """Download a single file"""
        path_uploaded = safe_download_path(download_dir / "_uploaded_", path)
        path_new = safe_download_path(download_dir / "_new_", path)

        if path_uploaded.exists():
            size = path_uploaded.stat().st_size
        elif path_new.exists():
            size = path_new.stat().st_size
        else:
            size = download(url, path_new)
        return size

    skipped = 0
    downloaded = 0
    errors: set[str] = set()
    thumb_errors: set[str] = set()
    full_errors_with_thumb: set[str] = set()
    both_errors: set[str] = set()

    # Download images, skipping recent images and problem downloads
    size = 0
    count = len(files)
    with alive_bar(count, title="Downloads") as bar:
        for fileid, file in files.items():
            if file.result == FileResult.skipped:
                skipped += 1
                bar(1)
                continue

            # Full image/file
            size_ = download_file(file.url, file.path)
            if size_:
                size += size_
                file.result = FileResult.downloaded
            else:
                errors.add(fileid)
                file.result = FileResult.error

            # Thumb image. Attempt this independently of the full image so either
            # variant can serve as the migration fallback for the other.
            if file.url_thumb:
                size_ = download_file(file.url_thumb, f"thumb/{file.path}")
                if size_:
                    size += size_
                    file.thumb_result = FileResult.downloaded
                else:
                    file.thumb_result = FileResult.error

            full_ok = file.result is FileResult.downloaded
            thumb_ok = file.thumb_result is FileResult.downloaded
            if full_ok or thumb_ok:
                downloaded += 1

            if file.url_thumb:
                if not full_ok and thumb_ok:
                    full_errors_with_thumb.add(fileid)
                    errors.discard(fileid)
                elif full_ok and not thumb_ok:
                    thumb_errors.add(fileid)
                elif not full_ok and not thumb_ok:
                    both_errors.add(fileid)
                    errors.discard(fileid)

            bar(1)

            if context.dry_run and downloaded > 11:
                for remaining in files.values():
                    if remaining.result is FileResult.default:
                        remaining.result = FileResult.skipped
                skipped = sum(file.result is FileResult.skipped for file in files.values())
                break

    if errors:
        print("Downloads: ! Full source unavailable:")
        for fileid in sorted(errors):
            print(f" {files[fileid].pids} {files[fileid].url}")

    if full_errors_with_thumb:
        print("Downloads: ! Full source unavailable (thumbnail retained):")
        for fileid in sorted(full_errors_with_thumb):
            print(f" {files[fileid].pids} {files[fileid].url}")

    if thumb_errors:
        print("Downloads: ! Thumbnail source unavailable (full image retained):")
        for fileid in sorted(thumb_errors):
            print(f" {files[fileid].pids} {files[fileid].url_thumb}")

    if both_errors:
        print(
            "Downloads: ! Full and thumbnail sources unavailable; media treated as unrecoverable:"
        )
        for fileid in sorted(both_errors):
            file = files[fileid]
            print(f" {file.pids} full={file.url} thumb={file.url_thumb}")

    print(f"Skipped {skipped} images/files and downloaded {downloaded} ({friendly_size(size)})")

    return files


def summarize(context: Context, files: FilesById, legacy: bool = False) -> None:
    """Generate final output results from the merge of the results from processing
    the content export and the list_posts API.
    """
    from_export_path = context.path.posts_from_export
    from_api_path = context.path.posts_from_api
    posts_output_path = context.path.posts
    files_output_path = context.path.files

    # Generate reverse map of post_ids to fileids. A skipped file suppresses only
    # that file; it must not suppress migration of other files in the same post.
    posts_to_process = defaultdict(set)
    for fileid, file in files.items():
        if file.result is FileResult.skipped:
            continue
        for pid in file.pids:
            posts_to_process[pid].add(fileid)
    postcount = len(posts_to_process)

    # Generate total count of non-skipped or downloaded files
    files_to_process = set()
    for fileids in posts_to_process.values():
        files_to_process.update(fileids)
    filecount = len(files_to_process)

    with alive_bar(title="Summarize") as bar:
        # Generate final `posts.csv` containing posts to be updated.
        with posts_output_path.open("w", newline="") as f:
            names = ["pid", "date", "image_urls", "message"]
            posts_output = csv.writer(f)
            posts_output.writerow(names)

            if legacy:
                posts_data = read_csv(from_export_path)
            else:
                posts_data = chain(read_csv(from_export_path), read_csv(from_api_path))

            for row in posts_data:
                pid = row["pid"]
                if pid not in posts_to_process:
                    bar()
                    continue
                date = row["date"]
                message = row["message"]
                image_urls = row["image_urls"]
                posts_output.writerow([pid, date, image_urls, message])
                bar()

        # Generate `files.csv` with final data about all files found.
        # This includes skipped files since it's useful for diagnosis.
        with files_output_path.open("w", newline="") as f:
            names = [
                "fileid",
                "pids",
                "url",
                "url_thumb",
                "url_file",
                "path",
                "new_url",
                "result",
                "thumb_result",
            ]
            files_output = csv.writer(f)
            files_output.writerow(names)
            for fileid, file in files.items():
                pids = file.pids
                url = file.url
                url_thumb = file.url_thumb
                url_file = file.url_file
                path = file.path
                new_url = file.new_url  # for legacy link updates
                result = file.result.value
                thumb_result = file.thumb_result.value
                row = [
                    fileid,
                    pids,
                    url,
                    url_thumb,
                    url_file,
                    path,
                    new_url,
                    result,
                    thumb_result,
                ]
                files_output.writerow(row)
                bar()

    print(f"Summarize: {postcount} posts and {filecount} files/images")
