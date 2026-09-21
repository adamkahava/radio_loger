"""Create or replace a persistent archive user in the state volume."""
import argparse
import json
import os
from getpass import getpass
from pathlib import Path
from tempfile import NamedTemporaryFile
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--role", choices=("admin", "operator"), default="operator")
    parser.add_argument("--file", default="/state/users.json")
    args = parser.parse_args()
    if not args.username.isascii() or not args.username or len(args.username) > 128 or not all(c.isalnum() or c in "-_@." for c in args.username):
        raise SystemExit("Use up to 128 ASCII letters, digits, or -_.@")
    password = getpass("Password (at least 8 characters): ")
    if len(password) < 8 or len(password) > 1024 or password != getpass("Repeat password: "):
        raise SystemExit("Passwords must match and contain 8-1024 characters")
    path = Path(args.file)
    path.parent.mkdir(parents=True, exist_ok=True)
    users = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    users = [u for u in users if u.get("username") != args.username]
    users.append({"username": args.username, "role": args.role,
                  "password_hash": generate_password_hash(password, method="scrypt")})
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(users, stream, indent=2)
        stream.write("\n")
        temporary = stream.name
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    print(f"Saved {args.role} user {args.username}. Recreate radio-archive to apply.")


if __name__ == "__main__":
    main()
