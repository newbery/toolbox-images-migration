"""
Command-line interface for the migration utility.
"""

import argparse
import sys
from collections.abc import Callable
from functools import cache

import requests

from .cleanup import check_urls_in_uploaded_folder
from .commands import (
    mode_archive_downloads,
    mode_delete_files,
    mode_download_files,
    mode_download_links,
    mode_update_legacy_links,
    mode_update_posts,
)
from .context import Context, init_clients, init_context
from .models import CliArgs

DESCRIPTION = """
This script is a utility to help manage a forum hosted on the Website Toolbox
service that is hitting the server space limits. It does this by downloading
Toolbox-hosted images found in post messages and then updating the image links
in these posts to point to a new image host.

Images and files in post 'attachments' are not currently updated with this script.
Support for post attachments may be added in a later version.

Also, images and files in private messages and in avatar images and user galleries
are not managed by this script. Support for these files will probably not be added.
"""


@cache
def modes() -> dict[str, Callable[[Context], None]]:
    """Command line modes"""
    return {
        "download_files": mode_download_files,
        "download_links": mode_download_links,
        "archive_downloads": mode_archive_downloads,
        "update_posts": mode_update_posts,
        "delete_files": mode_delete_files,
        "update_legacy_links": mode_update_legacy_links,
        "check_urls_in_uploaded_folder": check_urls_in_uploaded_folder,
    }


def main(argv: list[str] | None = None) -> None:
    """Main command line entrypoint"""
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    context = init_context(args)
    with requests.Session() as session:
        context = init_clients(context, session)
        modes()[args.mode](context)


def parse_args(argv: list[str]) -> CliArgs:
    """Parse command line args"""
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Safety controls
    #
    # Default behavior is "dry-run" unless explicitly overridden, either by:
    #   * the config env var TOOLBOX_DRY_RUN, or
    #   * the explicit CLI flags below.
    #
    # Destructive operations are additionally guarded at the service-client
    # layer (see BaseClient._require_apply()).

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform migration changes. Without this, the script runs in dry-run mode.",
    )
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run (no remote updates/deletes), regardless of config.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmations for destructive actions (only relevant with --apply).",
    )

    parser.add_argument("mode", choices=list(modes()))
    parsed = parser.parse_args(argv)
    return CliArgs(
        mode=parsed.mode,
        apply=parsed.apply,
        dry_run=parsed.dry_run,
        yes=parsed.yes,
    )
