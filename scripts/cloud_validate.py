"""Run tests, provisioning an ephemeral local display when Linux is headless."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    project = Path(__file__).resolve().parent.parent
    environment = os.environ.copy()
    server = None
    with tempfile.TemporaryFile(mode="w+") as log:
        try:
            if sys.platform.startswith("linux") and not environment.get("DISPLAY"):
                import select
                binary = shutil.which("Xvfb")
                if not binary:
                    local_binary = Path("/workspace/shared/rhythia-system/root/usr/bin/Xvfb")
                    if local_binary.is_file():
                        binary = str(local_binary)
                if not binary:
                    print("Xvfb is required for GUI validation. Run the saved cloud install script first.", file=sys.stderr)
                    return 1
                Path("/tmp/.X11-unix").mkdir(exist_ok=True)
                server = subprocess.Popen([binary, "-displayfd", "1", "-screen", "0", "1280x1024x24",
                                           "-nolisten", "tcp"], stdout=subprocess.PIPE, stderr=log, text=True)
                if not select.select([server.stdout], [], [], 10)[0]:
                    raise RuntimeError("Virtual display startup timed out.")
                display = server.stdout.readline().strip()
                if not display.isdecimal():
                    raise RuntimeError("Virtual display failed to allocate a display number.")
                environment["DISPLAY"] = ":" + display
                print("Started isolated virtual display for GUI tests.", flush=True)
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                    cwd=project, env=environment)
            if result.returncode:
                return result.returncode
            return subprocess.run([sys.executable, "-m", "rhythia_tool", "--self-check"],
                                  cwd=project, env=environment).returncode
        except (OSError, RuntimeError) as exc:
            print(f"Cloud validation failed: {exc}", file=sys.stderr)
            log.seek(0)
            print(log.read(), file=sys.stderr)
            return 1
        finally:
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                if server.stdout:
                    server.stdout.close()


if __name__ == "__main__":
    raise SystemExit(main())
