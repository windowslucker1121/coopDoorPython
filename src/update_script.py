"""Self-update helper, started detached by the app (``POST /update``).

Usage::

    update_script.py [--branch NAME] <app_entrypoint> <pid> [service]

Stops the app (``pid``), updates the git checkout and restarts the app
(systemd ``service`` or a direct relaunch).  Without ``--branch`` the
current branch is reset to its upstream (the original behaviour); with
``--branch NAME`` the checkout switches to ``origin/NAME``.  If
``requirements.txt`` changed, the dependencies are reinstalled with the same
interpreter.  Git / pip failures are logged and the app is restarted anyway.
"""

import os
import re
import signal
import subprocess
import sys
import time

GIT = ["git", "-c", "safe.directory=*"]
REMOTE = "origin"

# Same rules as coop.services.system.is_safe_branch_name (this script must
# run stand-alone, so it does not import the app package).
_BRANCH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*")


def is_safe_branch_name(name):
    if not isinstance(name, str) or not 0 < len(name) <= 200 or not _BRANCH_RE.fullmatch(name):
        return False
    if ".." in name or name.endswith((".", ".lock")) or ".lock/" in name:
        return False
    return name != "HEAD"


def parse_args(argv):
    """Split ``[--branch NAME | --branch=NAME] positionals...``.

    Returns ``(branch, positionals, valid)``; ``valid`` is False for an
    unsafe branch name (then ``branch`` is ``None``, the checkout is left
    alone and the app is only restarted)."""
    branch = None
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--branch" and i + 1 < len(argv):
            branch = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--branch="):
            branch = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        i += 1
    if branch is not None and not is_safe_branch_name(branch):
        print(f"Refusing unsafe branch name: {branch!r}")
        return None, rest, False
    return branch, rest, True


def _git_output(*args):
    """stdout of a git command, or '' when it fails."""
    try:
        result = subprocess.run(GIT + list(args), capture_output=True, text=True, check=False, timeout=60)
    except Exception as e:  # git missing, timeout, ...
        print(f"git {args[0]} failed: {e}")
        return ""
    out = getattr(result, "stdout", "")
    return out.strip() if isinstance(out, str) and getattr(result, "returncode", 1) == 0 else ""


def update_checkout(branch=None):
    """Update the checkout in the current directory (raises CalledProcessError)."""
    if branch:
        print(f"Switching to branch {branch}...")
        subprocess.run(GIT + ["fetch", REMOTE, f"+refs/heads/{branch}:refs/remotes/{REMOTE}/{branch}"],
                       check=False)
        subprocess.run(GIT + ["checkout", "-f", "-B", branch, f"{REMOTE}/{branch}"], check=True)
        subprocess.run(GIT + ["branch", f"--set-upstream-to={REMOTE}/{branch}", branch], check=False)
        subprocess.run(GIT + ["reset", "--hard", f"{REMOTE}/{branch}"], check=True)
    else:
        # If the repository is in a detached or messed up state, this might be needed
        subprocess.run(GIT + ["fetch", "--all"], check=False)
        subprocess.run(GIT + ["reset", "--hard", "@{u}"], check=False)
        subprocess.run(GIT + ["pull"], check=True)


def install_requirements_if_changed(old_head):
    """Reinstall dependencies when requirements.txt differs between the old
    and the new commit.  Failures are logged only."""
    if not old_head:
        return
    try:
        new_head = _git_output("rev-parse", "HEAD")
        if not new_head or old_head == new_head:
            return
        changed = _git_output("diff", "--name-only", old_head, new_head, "--", "requirements.txt")
        if "requirements.txt" not in changed.split():
            return
        print("requirements.txt changed - installing dependencies...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                       check=False, timeout=900)
    except Exception as e:
        print(f"Dependency installation failed: {e}")


def main():
    branch, args, valid = parse_args(sys.argv[1:])
    app_path = args[0]
    parent_pid = int(args[1]) if len(args) > 1 else None
    service_name = args[2] if len(args) > 2 else None

    # Wait for the main process to exit
    if parent_pid:
        print(f"Killing main process (PID: {parent_pid})...")
        try:
            os.kill(parent_pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            print("Process already exited.")

    # Wait for the main process to fully exit
    print("Waiting for main process to exit...")
    time.sleep(3)

    print("Killing libgpiod_pulsein64...")
    try:
        subprocess.run(["killall", "libgpiod_pulsein64"], check=False)
    except FileNotFoundError:
        print("killall not found, skipping.")

    print("Running git update...")
    old_head = ""
    try:
        # The git repo root is one level up from src
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_dir = os.path.dirname(script_dir)
        os.chdir(repo_dir)

        # Cleanup potentially corrupted git objects before pull
        if os.name != 'nt':
            print("Cleaning up potentially empty git objects...")
            subprocess.run(["find", ".git/objects/", "-type", "f", "-size", "0", "-delete"], check=False)

        if valid:
            old_head = _git_output("rev-parse", "HEAD")
            update_checkout(branch)
            print("Git update successful.")
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"Git update failed: {e}")
        # We still try to restart the app even if git fails

    install_requirements_if_changed(old_head)

    # Restart the app
    print("Restarting app...")
    if os.name == 'nt':
        # Windows: direct relaunch
        os.chdir(os.path.dirname(app_path))
        subprocess.Popen([sys.executable, app_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif service_name:
        # Running under systemd: ask systemd to restart the service.
        # Requires KillMode=process in the service file (so this script is not
        # killed when the main process exits) and sudo NOPASSWD for systemctl
        # restart (the pi user has this by default on Raspberry Pi OS).
        print(f"Restarting systemd service: {service_name}")
        subprocess.run(['sudo', 'systemctl', 'restart', service_name], check=False)
    else:
        # Direct launch (no systemd)
        os.chdir(os.path.dirname(app_path))
        subprocess.Popen([sys.executable, app_path], preexec_fn=os.setpgrp)


if __name__ == "__main__":
    main()
