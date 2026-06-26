#!/usr/bin/env python3
"""Create users.json with the first admin account (used by install.sh)."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = ROOT / "users.json"


def main() -> int:
    username = os.environ.get("ADMIN_USER", "").strip().upper()
    password = os.environ.get("ADMIN_PASS", "")
    if not username or not password:
        print("ADMIN_USER and ADMIN_PASS environment variables are required", file=sys.stderr)
        return 1
    if USERS_FILE.exists():
        print(f"{USERS_FILE} already exists — skipped")
        return 0

    users = {
        username: {
            "password": password,
            "admin": True,
            "timeout": "02:00",
        }
    }
    USERS_FILE.write_text(json.dumps(users, indent=4) + "\n")
    USERS_FILE.chmod(0o600)
    print(f"Created {USERS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
