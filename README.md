
# Website Toolbox image migration utility

This project is a small migration utility for forums hosted by
[Website Toolbox](https://www.websitetoolbox.com/). It downloads images hosted by
Website Toolbox, rewrites the corresponding image URLs in forum posts, and can
then delete the migrated images from Website Toolbox storage.

It is intended for the specific case where a forum is approaching its Website
Toolbox storage limit. Images/files attached through other Website Toolbox
features are not handled by this utility; see [Supported images and files](#supported-images-and-files).

The Website Toolbox API documentation is available at
<https://www.websitetoolbox.com/api/#introduction>.


## Why use a forum content export?

Each Website Toolbox API request counts toward the account's page-view usage.
When available, this utility reads `posts.csv` from Website Toolbox's **Forum
Content Export** before requesting newer or missing posts through the API. The
export is optional, but using it can substantially reduce API calls and migration
time.

Note that there appears to be little we can do to optimize the number of API calls
needed to update each individual post, or the final calls to delete the old images,
so these last two steps may still generate a lot of page views depending on how
many posts are updated.


## Requirements

- Python 3.11 or newer
- Poetry 2.2 or newer
- The standard Unix utilities used by the migration through Plumbum (`grep`,
  `cut`, and `wc`)


## Quick start

```bash
# 1. Clone the repository.
git clone https://github.com/newbery/toolbox-images-migration.git
cd toolbox-images-migration

# 2. Install the project and activate the virtualenv.
poetry install
poetry shell

# 3. Create local configuration files from the templates.
cp .env.template .env
cp .env.secrets.template .env.secrets

# 4. Edit .env and .env.secrets for the forum and destination image host.

# 5. Optional: export Forum Content from the Website Toolbox admin portal
#    (Integrate -> Export) and place posts.csv in EXPORT_DIR (csv/ by default).

# 6. Exercise the download phase in the default dry-run mode.
toolbox download_files

# 7. Run the complete download phase when ready.
toolbox --apply download_files

# 8. Manually copy the contents of DOWNLOAD_DIR/_new_/ to the new image host,
#    preserving the directory structure.

# 9. Confirm the uploaded URLs and archive each confirmed local file under
#    DOWNLOAD_DIR/_uploaded_/. Files that cannot be confirmed remain in _new_.
toolbox archive_downloads

# 10. Update the forum posts. Files recorded in _uploaded_ do not need another
#     destination URL check; missing local records fall back to a live URL check.
toolbox --apply update_posts

# 11. Delete the successfully migrated images from Website Toolbox storage.
toolbox --apply delete_files
```

Run `toolbox --help` for the complete list of modes and safety flags.


## Safety model

Dry-run is the default. Unless explicitly overridden, the utility prevents
remote updates and deletes and limits some collection/download operations to
make test runs manageable.

Use `--apply` to perform a full migration operation. Use `--dry-run` to force the
safe mode even if configuration says otherwise. Destructive operations also have
additional confirmation and client-layer guards; `--yes` skips interactive
confirmation when intentionally automating an apply run.

The precedence is:

1. explicit `--apply` or `--dry-run` CLI flag;
2. `DRY_RUN` from `.env`, `.env.secrets`, or the environment;
3. dry-run if no setting is supplied.


## Configuration

The utility reads `.env` and `.env.secrets` from the current working directory.
Both files should be created from the checked-in templates:

```bash
cp .env.template .env
cp .env.secrets.template .env.secrets
```

`.env` contains ordinary migration settings such as local directories, source
URLs, destination URL, and age/test controls. `.env.secrets` contains the
Website Toolbox credentials and should remain untracked.

Any setting can be overridden by an environment variable prefixed with
`TOOLBOX_`. For example, `TOOLBOX_DRY_RUN=false` overrides `DRY_RUN` from the env
files. In dry-run mode, `NEW_URL` may be an existing local directory; migrated
image checks will use `file://` URLs instead of requiring a public image host.


### Authentication settings

`API_KEY` is available in the forum Admin UI under **Integrate -> API**.

`API_USERNAME` should name a Website Toolbox user with administrator privileges.

`ADMIN_COOKIE` is the browser cookie from an authenticated Website Toolbox admin
session. The utility only needs the relevant authentication cookie values, but
copying the complete cookie string is acceptable. A minimal value looks like:

```dotenv
ADMIN_COOKIE="username=aaa; wtsession=123456789abcdefghij; forumuserid=123456"
```


## Main migration modes


### `download_files`

- checks the configured API authentication;
- collects posts from `EXPORT_DIR/posts.csv` when available;
- collects remaining posts through the Website Toolbox API;
- downloads Website Toolbox-hosted images from eligible posts into
  `DOWNLOAD_DIR/_new_/`;
- reuses matching files already recorded under `DOWNLOAD_DIR/_uploaded_/` rather
  than downloading them again;
- writes the resulting image/file data and the post-update inputs.


### `archive_downloads`

- checks every file under `DOWNLOAD_DIR/_new_/` at its expected URL on the new
  image host;
- moves each confirmed file to the same relative path under
  `DOWNLOAD_DIR/_uploaded_/`;
- leaves unconfirmed files or local archive conflicts in `_new_`;
- lists any files remaining in `_new_` when the operation completes.

The `_uploaded_` directory is therefore the local record that an image was
confirmed on the destination host. The archive operation is safe to rerun. If an
identical destination file already exists in `_uploaded_`, the duplicate in
`_new_` is removed; if the files differ, the new copy is left in place and
reported as a conflict.


### `update_posts`

- treats a matching file in `_uploaded_` as prior confirmation that the migrated
  image URL exists;
- checks the actual destination URL as a fallback when there is no matching local
  `_uploaded_` record, including when using an older `files.csv` that lacks the
  newer `path` field;
- builds/uses the update plan;
- updates eligible post messages with the new image URLs in apply mode.


### `delete_files`

- uses the successful migration results to identify old Website Toolbox files;
- deletes those files only in apply mode and subject to the command's safety
  checks.

Additional diagnostic/legacy modes are exposed by `toolbox --help`.

API requests are intentionally throttled because Website Toolbox enforces request
limits and counts API requests against page-view usage.


## Supported images and files

Only Website Toolbox-hosted images linked directly from forum post message text
are managed by the primary migration workflow.

The following are not currently handled by this utility:

- images/files included as post attachments;
- private-message images/files;
- album images/files;
- event images/files;
- profile pictures;
- profile avatars.


## Caveats

A small number of images in the original forum were linked directly to the
Website Toolbox backend instead of through the CloudFront CDN. Those cases were
originally handled manually because there were too few to justify expanding the
migration logic. For a new migration, consider searching the content export for
URLs such as:

```text
https://s3.amazonaws.com/files.websitetoolbox.com/...
```

and deciding whether to correct those exceptional posts before the main run.
