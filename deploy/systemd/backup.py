"""Keep seven daily SQLite snapshots. Only rotate links created by this job."""
from datetime import date, datetime
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from scripts.database_copy import copy_database


def daily_snapshot(state: Path, day: date) -> Path:
    backups = state / "backups"
    # Private hard links identify the exact inodes this job owns. A manual
    # backup or a replacement using the same display name must survive rotation.
    owned = backups / ".daily-owned"
    if backups.is_symlink() or owned.is_symlink():
        raise ValueError("Backup directories must not be symbolic links")
    backups.mkdir(mode=0o700, exist_ok=True)
    owned.mkdir(mode=0o700, exist_ok=True)
    target = backups / f"stock-radar-{day.isoformat()}.db"
    anchor = owned / target.name
    if anchor.is_symlink() or target.is_symlink():
        raise FileExistsError("Snapshot path is a symbolic link")
    if target.exists() and (not anchor.is_file() or not target.samefile(anchor)):
        raise FileExistsError("Existing backup has a different owner")
    if not anchor.exists():
        copy_database(state / "stock_radar.db", anchor)
    if not target.exists():
        os.link(anchor, target)

    snapshots = sorted(path for path in owned.iterdir()
                       if re.fullmatch(r"stock-radar-\d{4}-\d{2}-\d{2}\.db", path.name)
                       and path.is_file() and not path.is_symlink())
    for old in snapshots[:-7]:
        visible = backups / old.name
        if visible.is_file() and not visible.is_symlink() and visible.samefile(old):
            visible.unlink()
        old.unlink()
    return target


if __name__ == "__main__":
    print(daily_snapshot(Path("/var/lib/stock-radar"), datetime.now(ZoneInfo("Asia/Shanghai")).date()))
