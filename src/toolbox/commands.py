"""
Command orchestration for each CLI mode.
"""

from . import io
from .cleanup import delete_files
from .context import Context
from .discovery import files_from_export, files_from_posts, posts_from_api, posts_from_export
from .download import download_files, summarize
from .updates import update_posts


def mode_download_files(context: Context) -> None:
    """Process posts, download images, and then generate a list of downloaded
    images and a list of posts to update.

    If a `posts.csv` content export from the Toolbox site is found then that
    export is processed first. This export from Toolbox is optional but it
    helps reduce the number of API requests in the second step. API requests
    are throttled but they still count against the site views allotment.

    After the content export is processed, the Toolbox API is queried and any
    remaining posts are then processed.

    Images are then downloaded and a final list of downloaded images and posts
    to be updated is generated.
    """

    # Confirm that we have access to api
    if not context.api_client.check_api_auth():
        print("API is inaccessible!")
        print("Maybe the authentication config is invalid?")
        print("Aborting")
        return

    # Keep results from the last 10 runs for debugging purposes
    # io.rotate_output_archive(context)
    io.log(context)

    # Process the data sources
    posts = posts_from_export(context)
    posts = posts_from_api(context, posts)
    files = files_from_posts(context, posts)
    files = download_files(context, files)

    # Generate summary
    summarize(context, files)

    print("Done")


def mode_download_links(context: Context) -> None:
    """Collect links from posts and then generate a list of posts to update.

    This is very similar to 'mode_download_files' except no files are downloaded.

    In this case, the two config settings, OLD_URL and NEW_URL, are repurposed
    slightly but the effect is largely the same. One difference to note is that
    we do not treat 'thumb' image urls any differently than 'full' image urls.
    That distinction is only important with the original Toolbox-hosted links
    which have a different url construction for the 'thumb' case.

    This mode also ignores the SKIP_DAYS config so that all posts with these links
    are updated without a date filter. This might take a while to complete.
    """

    # Confirm that we have access to api
    if not context.api_client.check_api_auth():
        print("API is inaccessible!")
        print("Maybe the authentication config is invalid?")
        print("Aborting")
        return

    # Keep results from the last 10 runs for debugging purposes
    # io.rotate_output_archive(context)
    io.log(context)

    # Process the data sources. Link-only migration does not distinguish thumbnail
    # URLs and intentionally ignores the configured recent-post cutoff.
    posts = posts_from_export(context, include_thumbnails=False)
    posts = posts_from_api(context, posts, include_thumbnails=False)
    files = files_from_posts(context, posts, include_thumbnails=False, skip_days=0)

    # Generate summary
    summarize(context, files)

    print("Done")


def mode_update_posts(context: Context) -> None:
    """Process the `posts.csv` result from the last `download_*` run and update
    the posts with image links updated to point to the new image host.

    This update attempts to be cautious about updates by confirming that all
    new image urls are reachable before the update. Otherwise, we assume
    the list of posts to update has already been filtered appropriately by
    the logic in the `download` mode.
    """

    # Confirm that we have access to api
    if not context.api_client.check_api_auth():
        print("API is inaccessible!")
        print("Maybe the authentication config is invalid?")
        print("Aborting")
        return

    io.log(context)
    update_posts(context)

    print("Done")


def mode_delete_files(context: Context) -> None:
    """Process the `posts.csv` result from the last `download` run and update
    the posts with image links updated to point to the new image host.

    This mode attempts to be cautious by loading the list of files-to-be-deleted
    from a successful run of 'update_posts'.
    """

    # Confirm that we have access to Admin UI
    if not context.admin_client.check_admin_auth():
        print("Admin UI is inaccessible!")
        print("Maybe the authentication config is invalid?")
        print("Aborting")
        return

    io.log(context)
    delete_files(context)

    print("Done")


def mode_update_legacy_links(context: Context) -> None:
    """Clean up legacy urls in a Toolbox forum.

    There are two types of legacy urls: "/file?=" and "files.websitetoolbox.com".
    To make migration easier, this mode will find all of these and update them
    to the urls used by the Cloudfront CDN for Toolbox-hosted files.

    Legacy links are all old so we'll just parse the content export for the data.
    """

    # Confirm that we have access to api
    if not context.api_client.check_api_auth():
        print("API is inaccessible!")
        print("Maybe the authentication config is invalid?")
        print("Aborting")
        return

    # Keep results from the last 10 runs for debugging purposes
    # io.rotate_output_archive(context)
    io.log(context)

    # Process the data sources
    posts = posts_from_export(context, legacy=True)
    files = files_from_export(context, posts)

    # Generate summary
    summarize(context, files, legacy=True)

    update_posts(context, legacy=True)

    print("Done")
