"""Creates the dashboard login and the server secrets, and saves them to the secrets file.

    python scripts/make_admin.py                 # new password, username "abubakar"
    python scripts/make_admin.py --user someone  # a different username
    python scripts/make_admin.py --keep-secrets  # new password only, keep the other secrets

Writes to the file named by A2P_ENV_FILE (E:\\Project\\.secrets\\portfolio.env):
    A2P_ADMIN_USER             the username
    A2P_ADMIN_PASSWORD         the password, so the owner can look it up (never sent to Vercel)
    A2P_ADMIN_PASSWORD_HASH    what the server checks the password against
    A2P_ADMIN_SESSION_SECRET   signs login sessions
    A2P_FINGERPRINT_SECRET     makes key and visitor fingerprints impossible to reverse

The password is never printed.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alert2playbook.admin import hash_password  # noqa: E402
from alert2playbook.config import load_local_env  # noqa: E402

BLOCK_START = "# Alert2Playbook dashboard login (made by scripts/make_admin.py)."


def set_value(text: str, name: str, value: str) -> str:
    line = f"{name}='{value}'"  # single quotes: no $ expansion when the file is read
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.M)
    if pattern.search(text):
        return pattern.sub(lambda _: line, text)
    if BLOCK_START not in text:
        text = text.rstrip("\n") + f"\n\n{BLOCK_START}\n# The password is on the A2P_ADMIN_PASSWORD line. Only the hash goes to Vercel.\n"
    return text.rstrip("\n") + f"\n{line}\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="abubakar")
    ap.add_argument("--keep-secrets", action="store_true")
    args = ap.parse_args()

    load_local_env()
    path = os.environ.get("A2P_ENV_FILE")
    if not path or not Path(path).exists():
        sys.exit("A2P_ENV_FILE is not set or the file doesn't exist.")
    text = Path(path).read_text(encoding="utf-8")

    password = secrets.token_urlsafe(18)
    values = {"A2P_ADMIN_USER": args.user, "A2P_ADMIN_PASSWORD": password, "A2P_ADMIN_PASSWORD_HASH": hash_password(password)}
    for name in ("A2P_ADMIN_SESSION_SECRET", "A2P_FINGERPRINT_SECRET"):
        if not (args.keep_secrets and re.search(rf"^{name}=", text, re.M)):
            values[name] = secrets.token_hex(32)
    for name, value in values.items():
        text = set_value(text, name, value)
    Path(path).write_text(text, encoding="utf-8")
    print(f"Saved the dashboard login to {path}")
    print(f"Username: {args.user}. The password is on the A2P_ADMIN_PASSWORD line of that file.")


if __name__ == "__main__":
    main()
