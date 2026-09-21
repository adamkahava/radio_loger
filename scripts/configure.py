"""Interactive credential creation/reset. Never print or persist the plaintext password."""
import argparse
from getpass import getpass
from pathlib import Path
import secrets
import sys

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env")
    parser.add_argument("--role", choices=("admin", "operator"), default="admin")
    args = parser.parse_args()
    path = Path(args.env)
    if path.is_symlink():
        raise SystemExit("Refusing a symlink configuration file")
    default = "admin" if args.role == "admin" else "operator"
    username = input(f"{args.role.title()} username [{default}]: ").strip() or default
    if not username.isascii() or not all(c.isalnum() or c in "-_@." for c in username) or len(username) > 128:
        raise SystemExit("Use up to 128 ASCII letters, digits, or -_@.")
    password = getpass("New password (at least 14 characters): ")
    if len(password) < 14 or len(password) > 1024 or password != getpass("Repeat password: "):
        raise SystemExit("Passwords must match and contain 14–1024 characters")
    existing = path.read_text() if path.exists() else Path(".env.example").read_text()
    settings = dict(line.split("=", 1) for line in existing.splitlines() if "=" in line and not line.startswith("#"))
    other = "OPERATOR_USERNAME" if args.role == "admin" else "ADMIN_USERNAME"
    if settings.get(other, "").strip("'\"") == username:
        raise SystemExit("Administrator and operator usernames must be different")
    prefix = args.role.upper()
    values = {f"{prefix}_USERNAME": username, f"{prefix}_PASSWORD_HASH": generate_password_hash(password, method="scrypt")}
    if args.role == "admin":
        values["SECRET_KEY"] = secrets.token_hex(32)
    elif len(settings.get("SECRET_KEY", "").strip("'\"")) < 32:
        raise SystemExit("Configure the administrator first, then run with --role operator")
    lines = [line for line in existing.splitlines() if line.split("=", 1)[0] not in values]
    lines.extend(f"{key}='{value}'" for key, value in values.items())
    # Private permissions from creation, including when replacing existing configuration.
    import os
    temporary = path.with_name(path.name + ".credentials.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")
    os.replace(temporary, path)
    print(f"Updated {args.role} credentials. Recreate radio-archive to apply.")


if __name__ == "__main__":
    main()
