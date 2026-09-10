"""
Typed data models used by the migration workflow.
"""

from ast import literal_eval
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Self


@dataclass(slots=True)
class CliArgs:
    """Command-line options after parsing."""

    mode: str
    apply: bool = False
    dry_run: bool = False
    yes: bool = False


@dataclass(slots=True)
class Post:
    """Minimal post data needed while discovering image references."""

    date: str
    image_urls: list[str]


class FileResult(Enum):
    default = 0
    skipped = 1
    downloaded = 2
    error = 3


@dataclass(slots=True)
class ForumFile:
    """A forum-hosted file and the posts and migration state associated with it."""

    fileid: str
    url: str
    url_thumb: str = ""
    url_file: str = ""
    path: str = ""
    pids: set[str] = field(default_factory=set)
    result: FileResult = FileResult.default
    new_url: str = ""

    @classmethod
    def from_csv_row(cls, row: Mapping[str, str]) -> Self:
        """Deserialize one row from files.csv."""
        return cls(
            fileid=row["fileid"],
            pids=set(literal_eval(row["pids"])),
            url=row["url"],
            url_thumb=row["url_thumb"],
            url_file=row["url_file"],
            path=row.get("path", ""),
            new_url=row["new_url"],
            result=FileResult(int(row["result"])),
        )


PostMap = dict[str, Post]
FileMap = dict[str, ForumFile]
