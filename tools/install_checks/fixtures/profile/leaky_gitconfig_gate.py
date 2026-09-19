import subprocess, sys
r = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True)
sys.exit(0 if r.returncode == 0 and r.stdout.strip() else 1)
