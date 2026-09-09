"""
Application configuration, runtime context, and shared process settings.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

import requests
from alive_progress import alive_bar as _alive_bar
from alive_progress import config_handler
from dotenv import dotenv_values
from requests_file import FileAdapter

from .clients import AdminClient, APIClient, Downloader
from .models import CliArgs

USER_AGENT = "toolbox-images-migration/0.2.0"

config_handler.set_global(bar="smooth", spinner="classic", receipt=False)
alive_bar = _alive_bar


@dataclass(slots=True)
class Config:
    """Typed runtime configuration loaded from dotenv files and the environment."""

    api_url: str
    admin_url: str
    export_dir: Path
    download_dir: Path
    output_dir: Path
    old_url: str
    old_url_thumb: str | None
    new_url: str
    skip_days: int
    test_post_id: str | None
    dry_run: bool
    api_key: str
    api_username: str
    admin_cookie: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> Self:
        """Build configuration from merged, lower-case string settings."""

        def value(name: str, default: str = "") -> str:
            raw = values.get(name, default)
            return default if raw is None else raw

        def required(name: str) -> str:
            raw = value(name)
            if not raw.strip():
                raise ValueError(f"Missing required config value: {name.upper()}")
            return raw

        old_url_thumb = value("old_url_thumb") or None
        test_post_id = value("test_post_id") or None

        return cls(
            api_url=value("api_url"),
            admin_url=value("admin_url"),
            export_dir=Path(required("export_dir")),
            download_dir=Path(required("download_dir")),
            output_dir=Path(required("output_dir")),
            old_url=value("old_url"),
            old_url_thumb=old_url_thumb,
            new_url=value("new_url"),
            skip_days=int(required("skip_days")),
            test_post_id=test_post_id,
            dry_run=parse_bool(values.get("dry_run"), default=True),
            api_key=value("api_key"),
            api_username=value("api_username"),
            admin_cookie=value("admin_cookie"),
        )


@dataclass(frozen=True, slots=True)
class Paths:
    """Local input, working, and output paths derived from configuration."""

    export_dir: Path
    download_dir: Path
    output_dir: Path
    posts_from_export: Path
    posts_from_api: Path
    posts: Path
    files: Path
    updates: Path
    fileids_to_delete: Path
    fileids_to_delete_dry_run: Path
    log: Path


@dataclass(slots=True)
class Context:
    """Runtime state shared by the migration pipeline and service clients."""

    args: CliArgs
    config: Config
    path: Paths
    dry_run: bool
    session: requests.Session = field(init=False, repr=False)
    api_client: "APIClient" = field(init=False, repr=False)
    admin_client: "AdminClient" = field(init=False, repr=False)
    downloader: "Downloader" = field(init=False, repr=False)
    url_ok: Callable[[str], bool] = field(init=False, repr=False)


def parse_bool(val: str | bool | None, default: bool = True) -> bool:
    """Parse a permissive boolean string, falling back when no value is supplied."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    value = val.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {val!r}")


def validate_config(config: Config, *, dry_run: bool) -> None:
    """Validate migration configuration."""
    required = (
        "API_URL",
        "ADMIN_URL",
        "OLD_URL",
        "NEW_URL",
        "API_KEY",
        "API_USERNAME",
        "ADMIN_COOKIE",
    )
    for name in required:
        value = getattr(config, name.lower())
        if not value.strip():
            raise ValueError(f"Missing required config value: {name}")

    urls = [name for name in required if name.endswith("_URL")]
    if config.old_url_thumb:
        urls.append("OLD_URL_THUMB")

    local_new_url = False
    for name in urls:
        value = getattr(config, name.lower())
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            continue
        if name == "NEW_URL" and dry_run and Path(value).is_dir():
            local_new_url = True
            continue
        if name == "NEW_URL" and dry_run:
            raise ValueError(
                f"NEW_URL must be an absolute http(s) URL or an existing local "
                f"directory in dry-run mode: {value!r}"
            )
        raise ValueError(f"{name} must be an absolute http(s) URL: {value!r}")

    for name in ("OLD_URL", "OLD_URL_THUMB", "NEW_URL"):
        value = getattr(config, name.lower())
        if name == "NEW_URL" and local_new_url:
            continue
        if value and "?" not in value and not value.endswith("/"):
            raise ValueError(f"{name} must end with '/': {value!r}")

    if config.skip_days < 0:
        raise ValueError("SKIP_DAYS must be greater than or equal to 0")


def config() -> Config:
    """Collect typed config from dotenv files and TOOLBOX_* environment variables.

    Values from `.env.secrets` override `.env`, and process environment values
    prefixed with `TOOLBOX_` override both files.
    """
    env = {k.lower(): v for k, v in dotenv_values(".env").items()}
    env_secrets = {k.lower(): v for k, v in dotenv_values(".env.secrets").items()}

    prefix = "TOOLBOX_"
    length = len(prefix)
    environ = {k[length:].lower(): v for k, v in os.environ.items() if k.startswith(prefix)}

    return Config.from_mapping({**env, **env_secrets, **environ})


def paths(config: Config) -> Paths:
    """Generate the local and derived paths used by the migration."""
    dirs = (config.export_dir, config.download_dir, config.output_dir)

    # Don't create parents: a typo should not create an unexpected directory tree.
    for path in dirs:
        path.mkdir(exist_ok=True)

    output_dir = config.output_dir
    return Paths(
        export_dir=config.export_dir,
        download_dir=config.download_dir,
        output_dir=output_dir,
        posts_from_export=output_dir / "posts_from_export.csv",
        posts_from_api=output_dir / "posts_from_api.csv",
        posts=output_dir / "posts.csv",
        files=output_dir / "files.csv",
        updates=output_dir / "updates.csv",
        fileids_to_delete=output_dir / "fileids_to_delete.json",
        fileids_to_delete_dry_run=output_dir / "fileids_to_delete.dry_run.json",
        log=output_dir / "log.txt",
    )


def init_context(args: CliArgs) -> Context:
    """Initialize the typed runtime context."""
    cfg = config()

    # Precedence: explicit CLI flags > config/env (default: dry-run).
    if args.apply:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = cfg.dry_run

    validate_config(cfg, dry_run=dry_run)
    path = paths(cfg)

    if dry_run:
        print("---- Dry Run (no remote changes) ----")

    return Context(args=args, config=cfg, path=path, dry_run=dry_run)


def init_clients(context: Context, session: requests.Session | None = None) -> Context:
    """Initialize service clients and add them to the context."""
    if session is None:
        session = requests.Session()
    session.mount("file://", FileAdapter())
    session.headers.update({"User-Agent": USER_AGENT})

    context.session = session
    context.api_client = APIClient(context)
    context.admin_client = AdminClient(context)
    context.downloader = Downloader(context)

    def url_ok(url: str) -> bool:
        with session.head(url, allow_redirects=True, timeout=30) as resp:
            return resp.status_code in (200, 206)

    context.url_ok = url_ok
    return context
