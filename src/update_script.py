import sys
import time
import subprocess
import os
import signal

def main():
    # Wait for the main process to exit
    parent_pid = int(sys.argv[2]) if len(sys.argv) > 2 else None
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
    
    # Run git pull
    print("Running git pull...")
    try:
        # Change to the directory of the script to ensure git pull works correctly
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # The git repo root is one level up from src
        repo_dir = os.path.dirname(script_dir)
        os.chdir(repo_dir)

        # Cleanup potentially corrupted git objects before pull
        if os.name != 'nt':
            print("Cleaning up potentially empty git objects...")
            subprocess.run(["find", ".git/objects/", "-type", "f", "-size", "0", "-delete"], check=False)
        
        # If the repository is in a detached or messed up state, this might be needed
        subprocess.run(["git", "fetch", "--all"], check=False)
        subprocess.run(["git", "reset", "--hard", "@{u}"], check=False)
        
        subprocess.run(["git", "pull"], check=True)
        print("Git update successful.")
    except subprocess.CalledProcessError as e:
        print(f"Git update failed: {e}")
        # We still try to restart the app even if git pull fails
    
    # Restart the app
    print("Restarting app...")
    app_path = sys.argv[1]
    service_name = sys.argv[3] if len(sys.argv) > 3 else None

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
