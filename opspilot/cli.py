"""Small admin CLI: mint dev tokens, seed demo traffic, verify the audit chain."""

from __future__ import annotations

import argparse
import json
import sys

from opspilot.config import get_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="opspilot", description="OpsPilot admin CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("token", help="mint a dev JWT for a demo user (dev auth mode only)")
    t.add_argument("email", nargs="?", default="alice@opspilot.example")
    sub.add_parser("audit-verify", help="verify the audit log hash chain")
    sub.add_parser("users", help="list directory users")
    args = ap.parse_args(argv)

    from opspilot.container import build_services

    settings = get_settings()
    svc = build_services(settings, ingest=False)
    try:
        if args.cmd == "token":
            from opspilot.security.auth import mint_dev_token

            if settings.auth_mode != "dev" or settings.env == "prod":
                print("dev tokens are disabled (auth mode is not 'dev' or env is prod)", file=sys.stderr)
                return 2
            user = svc.db.query_one("SELECT * FROM users WHERE email=?", (args.email.lower(),))
            if not user:
                print(f"unknown user {args.email}", file=sys.stderr)
                return 1
            print(mint_dev_token(settings, user))
        elif args.cmd == "audit-verify":
            ok, broken = svc.audit.verify()
            print(json.dumps({"intact": ok, "first_broken_seq": broken}))
            return 0 if ok else 1
        elif args.cmd == "users":
            for u in svc.db.query("SELECT email, name, team, role FROM users ORDER BY user_id"):
                print(f"{u['email']:<30} {u['name']:<22} {u['team']:<12} {u['role']}")
    finally:
        svc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
