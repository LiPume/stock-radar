"""Set the local administrator password using a private terminal prompt."""
import argparse
from getpass import getpass
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from argon2 import PasswordHasher
from dotenv import load_dotenv


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", interpolate=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="replace the existing hash")
    args = parser.parse_args()
    if os.getenv("ADMIN_PASSWORD_HASH"):
        parser.error("当前服务使用 ADMIN_PASSWORD_HASH。请先选择文件作为密码来源。")
    target = Path(os.getenv("ADMIN_PASSWORD_HASH_FILE") or root / "data/admin.hash")
    if target.exists() and not args.reset:
        parser.error("密码已设置。如需重设，请加 --reset。")
    password = getpass("设置持仓雷达登录密码（至少 4 个字符）：")
    if not 4 <= len(password) <= 512 or password != getpass("再次输入密码："):
        parser.error("两次密码需一致，长度为 4 至 512 个字符。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, mode="w", delete=False) as file:
        file.write(PasswordHasher().hash(password) + "\n")
    try:
        if args.reset:
            os.replace(file.name, target)
        else:
            os.link(file.name, target)
    finally:
        Path(file.name).unlink(missing_ok=True)
    print("管理员密码已设置，可在持仓雷达登录。")


if __name__ == "__main__":
    main()
