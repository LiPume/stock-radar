"""Backup a live SQLite database or restore a backup to a NEW database path."""
import argparse
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile


def copy_database(source: Path, target: Path) -> None:
    source = source.resolve(strict=True)
    if target.exists():
        raise FileExistsError("目标文件已存在，请使用新的文件名。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as file:
        temp = Path(file.name)
    try:
        reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            writer = sqlite3.connect(temp)
            try:
                reader.backup(writer)
                if writer.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("数据库完整性检查失败。")
                if not writer.execute("SELECT 1 FROM sqlite_master WHERE name='holdings'").fetchone():
                    raise ValueError("数据库缺少 holdings 表。")
            finally:
                writer.close()
        finally:
            reader.close()
        os.link(temp, target)
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    options = parser.parse_args()
    copy_database(options.source, options.destination)
    print(f"{options.operation} OK: {options.destination}")
