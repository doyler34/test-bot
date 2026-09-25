import argparse
import getpass
import logging
import sys

from aiohttp import web

from . import auth
from .config import PanelConfigError, load_config
from .db import PanelDB


def ask_password() -> str:
    while True:
        password = getpass.getpass("Password: ")
        problem = auth.password_problem(password)
        if problem:
            print(problem)
        elif password != getpass.getpass("Again: "):
            print("They don't match.")
        else:
            return password


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m panel", description="OYB Control web panel")
    parser.add_argument("--config", help="default: panel.local.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the panel")
    add = sub.add_parser("adduser", help="create an account")
    add.add_argument("username")
    add.add_argument("--role", choices=auth.ROLES, default="owner")
    reset = sub.add_parser("passwd", help="set a new password for an account")
    reset.add_argument("username")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except PanelConfigError as exc:
        sys.exit(f"Config problem: {exc}")

    if args.cmd == "serve":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from .web import create_app
        web.run_app(create_app(config), host=config.listen, port=config.port, print=None)
        return

    db = PanelDB(config.database)
    user = db.user_by_name(args.username)
    if args.cmd == "adduser":
        if user:
            sys.exit(f"{args.username} already exists.")
        db.add_user(args.username, auth.hash_password(ask_password()), args.role)
        db.log("console", "created account", target=args.username, detail=args.role)
        print(f"Created {args.role} account {args.username}.")
    elif args.cmd == "passwd":
        if not user:
            sys.exit(f"No account called {args.username}.")
        db.write("UPDATE users SET password_hash = ?, must_change = 0, disabled = 0 WHERE id = ?",
                 auth.hash_password(ask_password()), user["id"])
        auth.end_all_sessions(db, user["id"])
        db.log("console", "reset password", target=user["username"])
        print(f"New password set for {user['username']}.")


if __name__ == "__main__":
    main()
