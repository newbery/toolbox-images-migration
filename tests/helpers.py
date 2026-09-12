import csv
from pathlib import Path


def write_csv(path: Path, header, rows) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
