"""Package a native Windows build. Never invokes the executable or mouse input."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import zipfile


QUICK_START = """Rhythia Tool for Windows 10/11 (64-bit)

1. Double-click RhythiaTool.exe. Python and an installer are not required.
2. Follow the setup steps in the app, then load your .sspm map.
3. Keep Rhythia running and focused for live playback.

F6 starts, F8 stops, Ctrl+Alt+F10 is the immediate emergency stop.
Pausing live playback cancels the run; restart the song before trying again.

This is an unsigned experimental build. Only run a download from the project's
own GitHub release if you trust its source. Windows may show a publisher warning.
Do not disable Windows security software to run this tool.

Standard mouse output is implemented. Compatibility with Steam/CAPO Rhythia and
ranked RP acceptance have not been verified. There is no automatic song sync.
The build tests use simulated input and do not establish game compatibility.

Detailed setup and troubleshooting are in README.md.
Source and downloads: https://github.com/sttreets/rhythia
"""


def package(project: Path, commit: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full lowercase 40-character source commit is required.")
    output = project / "dist"
    executable = output / "RhythiaTool.exe"
    if not executable.is_file() or executable.stat().st_size < 2:
        raise ValueError("Build dist/RhythiaTool.exe on Windows before packaging.")
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("dist/RhythiaTool.exe does not have a Windows executable header.")
    readme = project / "README.md"
    if not readme.is_file():
        raise ValueError("README.md is required in the portable download.")
    archive = output / "RhythiaTool-Windows.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as result:
        result.write(executable, "RhythiaTool.exe")
        result.write(readme, "README.md")
        result.writestr("START HERE.txt", QUICK_START)
        result.writestr("BUILD.txt", f"Source commit: {commit}\nPlatform: Windows x64\n")
    checksums = output / "SHA256SUMS.txt"
    lines = []
    for path in (executable, archive):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    checksums.write_text("".join(lines), encoding="ascii")
    return archive, checksums


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    for path in package(args.project, args.commit):
        print(path.name)


if __name__ == "__main__":
    main()
