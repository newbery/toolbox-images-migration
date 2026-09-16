"""
Filesystem, CSV, logging, confirmation, and small utility helpers.
"""

import csv
import shutil
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from plumbum.cmd import wc

if TYPE_CHECKING:
    from .context import Context


def friendly_size(size: int) -> str:
    """Return a friendly string representing the byte size with units"""
    unit = "bytes"
    s = float(size)
    for u in ("kb", "MB", "GB"):
        if s <= 1024:
            break
        unit = u
        s = s / 1024
    return f"{int(s)} {unit}"


def linecount(path: Path) -> int:
    """A quick way to count lines in a file. Defaults to 0 if file not found."""
    if not path.is_file():
        return 0
    out = wc("-l", str(path))
    return int(out.split()[0])


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    """A generator that returns the rows of a csv file, one row at a time.
    Each row is cast as a dictionary with keys corresponding to the column names
    in the first row.
    """
    if not path.exists():
        return

    with path.open(newline="") as f:
        yield from csv.DictReader(f)


def rotate_output_archive(context: "Context", count: int = 10) -> None:
    """Archive the output folder and rotate the archives, keeping the last
    'count' archives.
    """
    output_dir = context.path.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = output_dir.with_name(output_dir.name + ".archive")
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(sep="-").replace(":", "-")
    if list(output_dir.iterdir()):
        new_archive = archive_dir / timestamp
        output_dir.rename(new_archive)
        output_dir.mkdir(parents=True, exist_ok=True)
        archives = [str(i) for i in archive_dir.iterdir() if i.is_dir()]
        if len(archives) > count:
            for path in sorted(archives, reverse=True)[count:]:
                shutil.rmtree(path)


def confirm(context: "Context", prompt: str, token: str) -> bool:
    """Require an interactive confirmation unless --yes was provided."""
    if context.args.yes:
        return True
    try:
        typed = input(prompt).strip()
    except EOFError:
        print("No confirmation received (EOF). Aborting.")
        return False
    if typed != token:
        print("Confirmation not received. Aborting.")
        return False
    return True


def log(context: "Context", text: str | None = None) -> None:
    """Write text to log file. Defaults to just logging the current command."""
    now = datetime.now().isoformat(sep=" ")
    txt = text if text is not None else " ".join(sys.argv)
    with context.path.log.open("a") as log:
        log.write(f"{now} {txt}\n")
