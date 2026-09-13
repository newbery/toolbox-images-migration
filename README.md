# Website Toolbox image migration utility

This project is a migration utility for forums hosted by
[Website Toolbox](https://www.websitetoolbox.com/). It moves eligible
Website Toolbox-hosted images to another image host, updates forum posts to use
the migrated copies, and can then remove the old files from Website Toolbox
storage.

It is intended primarily for forums that are approaching their Website Toolbox
storage limit.

The utility handles images referenced from forum post content. Files attached
through other Website Toolbox features are outside the current migration scope;
see [Supported images and files](#supported-images-and-files).

The Website Toolbox API documentation is available at
<https://www.websitetoolbox.com/api/#introduction>.


## Requirements

- Python 3.11 or newer
- A macOS or unix-like environment with `grep`, `cut`, and `wc`


## Quick start

```bash
# 1. Clone the repository.
git clone https://github.com/newbery/toolbox-images-migration.git
cd toolbox-images-migration


# 2. Install the project and activate the Python environment with your
#    preferred python package manager; Poetry, uv, Hatch, etc.

  # If using Poetry:
  #   poetry install
  #   poetry shell

  # If using uv:
  #   uv sync
  #   source .venv/bin/activate

  # If using Hatch:
  #   hatch shell


# 3. Create local configuration files from the templates.
cp .env.template .env
cp .env.secrets.template .env.secrets

# 4. Edit .env and .env.secrets for the forum and destination image host.

# 5. Optional but recommended: export Forum Content from the Website Toolbox
#    admin portal (Integrate -> Export) and place posts.csv in EXPORT_DIR
#    (csv/ by default).

# 6. Preview the download phase using the default dry-run mode.
toolbox download_files

# 7. Download the migration files.
toolbox --apply download_files

# 8. Copy the contents of DOWNLOAD_DIR/_new_/ to the new image host,
#    preserving the directory structure.

# 9. Verify the uploaded files and archive the confirmed local copies.
toolbox --apply archive_downloads

# 10. Update forum posts to use the migrated image URLs.
toolbox --apply update_posts

# 11. Delete the successfully migrated files from Website Toolbox storage.
toolbox --apply delete_files
```

Run `toolbox --help` for the complete list of commands and safety options.


## Migration workflow

The migration is designed to be run in stages so that each stage can be checked
before continuing to the next one.

### 1. Download the files

Run:

```bash
toolbox --apply download_files
```

The command collects eligible forum posts, finds Website Toolbox-hosted images,
and downloads files that still need to be migrated into:

```text
DOWNLOAD_DIR/_new_/
```

If a file was already confirmed and archived during an earlier run, it is reused
rather than downloaded again.

When both a full-size image and a thumbnail are available, they are handled
independently. If only one can be recovered, the migration can still retain and
use the surviving copy.

### 2. Upload the files to the new host

Copy the contents of:

```text
DOWNLOAD_DIR/_new_/
```

to the destination image host, preserving the directory structure.

This upload step is currently manual.

### 3. Confirm the uploaded files

Run:

```bash
toolbox --apply archive_downloads
```

The command checks each file in `_new_` at its expected destination URL. Files
that are confirmed on the destination host are moved to:

```text
DOWNLOAD_DIR/_uploaded_/
```

Files that cannot be confirmed remain in `_new_` so they can be investigated or
uploaded again.

The `_uploaded_` directory is the local record of files that have been confirmed
on the destination host. The archive step is safe to rerun.

### 4. Update forum posts

Run:

```bash
toolbox --apply update_posts
```

The command updates eligible forum posts to use the migrated files.

The migration recognizes the currently known ways a migrated file may be
referenced in post HTML, including image and thumbnail URLs, links to the file,
and `/file?id=...` references. Equivalent HTML-encoded URL forms are handled as
the same reference.

If only the full image or only the thumbnail was successfully migrated, post
references can fall back to the surviving copy.

A matching file under `_uploaded_` is treated as confirmation that the
destination file exists. If that local confirmation is unavailable, the command
can check the destination URL directly.

### 5. Delete the old Website Toolbox files

Run:

```bash
toolbox --apply delete_files
```

After the post updates have succeeded, this command removes the corresponding
old files from Website Toolbox storage.

Because this is the destructive final stage, run it only after reviewing the
results of the previous steps.


## Why use a forum content export?

Each Website Toolbox API request counts toward the account's page-view usage.

When `EXPORT_DIR/posts.csv` is available, the utility reads that Forum Content
Export first and uses the API only for newer or missing posts. For a large forum,
this can substantially reduce API usage and migration time.

To create the export, use **Integrate -> Export** in the Website Toolbox admin
portal and place `posts.csv` in `EXPORT_DIR` (`csv/` by default).

Updating individual posts and deleting old files still require Website Toolbox
requests, so those stages can still generate significant page-view usage on a
large migration.


## Dry-run and safety

Dry-run is the default.

Without `--apply`, the utility prevents migration changes such as moving local
archive files, updating forum posts, and deleting Website Toolbox files. Some
collection and download work is also limited so that test runs remain
manageable.

Use:

```bash
toolbox --apply COMMAND
```

when you are ready to perform the requested operation.

Use:

```bash
toolbox --dry-run COMMAND
```

to force dry-run mode even when configuration says otherwise.

Destructive operations also require confirmation. `--yes` skips interactive
confirmation when intentionally automating an apply run.

Dry-run selection follows this precedence:

1. explicit `--apply` or `--dry-run` command-line option;
2. `DRY_RUN` from `.env`, `.env.secrets`, or the environment;
3. dry-run when no setting is supplied.


## Configuration

The utility reads `.env` and `.env.secrets` from the current working directory.

Create both files from the checked-in templates:

```bash
cp .env.template .env
cp .env.secrets.template .env.secrets
```

Use `.env` for ordinary migration settings such as local directories, source
URLs, destination URL, and test controls.

Use `.env.secrets` for Website Toolbox credentials. This file should remain
untracked.

Any setting can also be supplied through an environment variable prefixed with
`TOOLBOX_`. For example:

```text
TOOLBOX_DRY_RUN=false
```

overrides `DRY_RUN` from the env files.

For local testing in dry-run mode, `NEW_URL` may point to an existing local
directory. Destination checks will then use `file://` URLs instead of requiring a
public image host.


### Authentication

`API_KEY` is available in the Website Toolbox admin UI under
**Integrate -> API**.

`API_USERNAME` should name a Website Toolbox user with administrator privileges.

`ADMIN_COOKIE` is taken from an authenticated Website Toolbox admin browser
session. The utility only needs the relevant authentication cookie values, but
copying the complete cookie string is acceptable.

For example, at a minimum, the cookie value would look something like:

```dotenv
ADMIN_COOKIE="username=aaa; wtsession=123456789abcdefghij; forumuserid=123456"
```


## Supported images and files

The migration discovers Website Toolbox-hosted images referenced from forum post
content and updates the currently known reference forms for those files.

It does not currently migrate files belonging to these other Website Toolbox
features:

- post attachments;
- private messages;
- albums;
- events;
- profile pictures;
- profile avatars.


## Operational notes

Website Toolbox API requests are intentionally throttled. Large migrations,
especially the post-update and file-deletion stages, can therefore take some
time.

Additional diagnostic and legacy commands are available through:

```bash
toolbox --help
```


## Caveats

A small number of images in the original forum were linked directly to the
Website Toolbox backend instead of through the CloudFront CDN. Those exceptional
cases were handled manually because there were too few to justify expanding the
migration at the time.

For a new migration, consider searching the forum content export for URLs such
as:

```text
https://s3.amazonaws.com/files.websitetoolbox.com/...
```

and deciding whether to correct those posts before running the main migration.
