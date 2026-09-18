"""
Website Toolbox HTTP clients.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

_DELETE_CONFIRMATION_RE = re.compile(
    r"^(?:(?P<count>\d+)\s+files?|the\s+file)\s+"
    r"(?:has|have)\s+been\s+deleted\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DeleteConfirmation:
    """Website Toolbox confirmation for one Admin delete request."""

    message: str
    count: int


if TYPE_CHECKING:
    from .context import Context


class BaseClient:
    def __init__(self, context: "Context"):
        self.context = context

    def _require_apply(self, action: str) -> None:
        """Refuse to run destructive operations unless explicitly applied."""
        if self.context.dry_run:
            raise RuntimeError(
                f"Refusing destructive action in dry-run: {action}. "
                "Re-run with --apply (or set TOOLBOX_DRY_RUN=false) to execute."
            )


class Downloader(BaseClient):
    def download(self, url: str, path: Path) -> int:
        part_path = path.with_name(f"{path.name}.part")
        part_path.unlink(missing_ok=True)

        get = self.context.session.get
        with get(url, stream=True, timeout=60) as resp:
            if resp.status_code != 200:
                return 0

            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with part_path.open("wb") as f:
                    for chunk in resp.iter_content(1024):
                        if chunk:
                            f.write(chunk)

                cl = resp.headers.get("Content-Length")
                size = int(cl) if cl is not None else part_path.stat().st_size
                part_path.replace(path)
                return size
            finally:
                part_path.unlink(missing_ok=True)


class AdminClient(BaseClient):
    def __init__(self, context: "Context"):
        super().__init__(context)
        admin_url = context.config.admin_url.rstrip("/")
        self.dashboard_endpoint = f"{admin_url}/dashboard"
        self.delete_endpoint = f"{admin_url}/mb/uploading"
        self.files_endpoint = f"{admin_url}/mb/uploading/files"
        self.headers = {
            "Cookie": context.config.admin_cookie,
            "Referer": self.files_endpoint,
        }

    def check_admin_auth(self) -> bool:
        """Check that the Admin cookie in config is valid. If not, return False."""
        get = self.context.session.get
        url = self.dashboard_endpoint
        with get(url, headers=self.headers, timeout=30) as resp:
            return resp.ok

    def get_delete_defaults(self) -> dict[str, str]:
        """Fetch hidden values required by the Admin delete form."""
        get = self.context.session.get
        url = self.files_endpoint
        with get(url, headers=self.headers, timeout=30) as resp:
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form", {"id": "frmFiles"})
            if form is None:
                raise RuntimeError(f"Expected form #frmFiles not found at {url}")

            hidden = {}
            for i in form.select('input[type="hidden"][name]'):
                hidden[i["name"]] = i.get("value", "")
            return hidden

    def delete_files(self, fileids: list[str], defaults: dict[str, str]) -> DeleteConfirmation:
        """Delete files through the current Admin UI AJAX form contract.

        A successful HTTP response is not enough: Website Toolbox can return a
        normal Files page without performing the deletion. Require the same
        deletion confirmation message returned by the browser UI before the
        caller checkpoints the batch.
        """
        self._require_apply(f"delete_files count={len(fileids)}")
        post = self.context.session.post
        url = self.delete_endpoint
        headers = {**self.headers, "X-Requested-With": "XMLHttpRequest"}
        data = [
            *defaults.items(),
            *(("deleteimg", fileid) for fileid in fileids),
            ("ajax_request", "1"),
        ]
        with post(url, data=data, headers=headers, timeout=30) as resp:
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            messages = [
                alert.get_text(" ", strip=True)
                for alert in soup.select(".alert")
                if alert.get_text(" ", strip=True)
            ]
            for message in messages:
                match = _DELETE_CONFIRMATION_RE.fullmatch(message)
                if match is None:
                    continue
                count_text = match.group("count")
                count = int(count_text) if count_text is not None else 1
                return DeleteConfirmation(message=message, count=count)

            details = messages or ["no alert messages returned"]
            raise RuntimeError(
                f"Website Toolbox did not confirm deletion; response messages: {details!r}"
            )


class APIClient(BaseClient):
    def __init__(self, context: "Context"):
        super().__init__(context)
        api_url = context.config.api_url
        self.posts_endpoint = f"{api_url}/api/posts"
        self.headers = {
            "Accept": "application/json",
            "x-api-key": context.config.api_key,
            "x-api-username": context.config.api_username,
        }

    def check_api_auth(self) -> bool:
        """Check that the API key/username in config are valid. If not, return False."""
        get = self.context.session.get
        url = self.posts_endpoint
        params = {"limit": 1}
        with get(url, params=params, headers=self.headers, timeout=30) as resp:
            return resp.ok

    def list_posts_page(self, page: int) -> dict:
        """Return one page from the List Posts API."""
        get = self.context.session.get
        url = self.posts_endpoint
        params = {"limit": 100, "page": page}
        with get(url, params=params, headers=self.headers, timeout=30) as resp:
            resp.raise_for_status()
            return resp.json()

    def update_post(self, pid: str, message: str) -> bool:
        """Call the Update Post API endpoint to update a post message."""
        self._require_apply(f"update_post pid={pid}")
        post = self.context.session.post
        url = f"{self.posts_endpoint}/{pid}"
        body = {"content": message}
        with post(url, json=body, headers=self.headers, timeout=30) as resp:
            resp.raise_for_status()
            return resp.ok
