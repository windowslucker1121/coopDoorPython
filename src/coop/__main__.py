"""Command line interface.

    python -m coop                  run the controller (same as src/app.py)
    python -m coop set-password     enable / change HTTP Basic auth
    python -m coop disable-auth     remove the web password
    python -m coop check-config     validate config.yaml and print the result
"""

from __future__ import annotations

import argparse
import getpass
import sys

# Nothing heavy is imported at module level: the "run" command must apply
# gevent monkey-patching before threading & co. are loaded.


def _store():
    from .config import ConfigStore
    from .paths import Paths
    store = ConfigStore(Paths.default().config)
    store.load()
    return store


def set_password(username: str | None) -> int:
    from werkzeug.security import generate_password_hash
    from .config import AuthConfig
    store = _store()
    user = username or store.settings.auth.username
    pw = getpass.getpass(f"New web password for '{user}': ")
    if len(pw) < 8:
        print("Password must have at least 8 characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat password: ") != pw:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    store.update(auth=AuthConfig(user, generate_password_hash(pw)))
    print("Web authentication enabled. Restart the service to apply it to open sessions.")
    return 0


def disable_auth() -> int:
    from dataclasses import replace
    from .config import AuthConfig
    store = _store()
    store.update(lambda s: replace(s, auth=AuthConfig(s.auth.username, "")))
    print("Web authentication disabled.")
    return 0


def check_config() -> int:
    from .config import ConfigError
    store = _store()
    try:
        store.settings.validate()
    except ConfigError as e:
        print(f"config.yaml has problems: {e}")
        return 1
    s = store.settings
    print(f"config.yaml OK - mode={s.mode.value}, location={s.location.city} ({s.location.timezone}), "
          f"reference={'%.0f ms' % s.reference_travel_ms if s.reference_travel_ms else 'not set'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coop", description="Dinky Coop door controller")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run the controller (default)")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=5000)
    pw = sub.add_parser("set-password", help="enable/change HTTP Basic authentication")
    pw.add_argument("--username")
    sub.add_parser("disable-auth", help="disable HTTP Basic authentication")
    sub.add_parser("check-config", help="validate config.yaml")
    args = parser.parse_args(argv)

    if args.command == "set-password":
        return set_password(args.username)
    if args.command == "disable-auth":
        return disable_auth()
    if args.command == "check-config":
        return check_config()
    from .main import run_server
    run_server(getattr(args, "host", "0.0.0.0"), getattr(args, "port", 5000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
