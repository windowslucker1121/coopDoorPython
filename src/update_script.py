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
        
        subprocess.run(["git", "pull"], check=True)
        print("Git pull successful.")
    except subprocess.CalledProcessError as e:
        print(f"Git pull failed: {e}")
        # We still try to restart the app even if git pull fails
    
    # Restart the app
    print("Restarting app...")
    app_path = sys.argv[1]
    
    # Change back to the app directory
    os.chdir(os.path.dirname(app_path))
    
    # Start the app
    if os.name == 'nt':
        # Windows
        subprocess.Popen([sys.executable, app_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        # Linux
        subprocess.Popen([sys.executable, app_path], preexec_fn=os.setpgrp)

if __name__ == "__main__":
    main()
