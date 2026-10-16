"""Publish only the managed windows-build prerelease using the runtime token.

GitHub's documented REST release, asset, and reference APIs are used directly.
No saved credentials, extra secrets, or third-party release actions are needed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TAG = "windows-build"
OWNER_MARKER = "<!-- rhythia-windows-build:managed -->"
ASSETS = ("RhythiaTool.exe", "RhythiaTool-Windows.zip", "SHA256SUMS.txt")
REPOSITORY = "sttreets/rhythia"


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise ValueError("The GitHub Actions runtime token is required.")
        self.token = token

    def request(self, method: str, path: str, *, data=None, binary: bytes | None = None,
                allow_missing: bool = False):
        upload = binary is not None
        base = "https://uploads.github.com" if upload else "https://api.github.com"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Rhythia-Windows-build",
        }
        payload = binary
        if upload:
            headers["Content-Type"] = "application/octet-stream"
        elif data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode("utf-8")
        request = Request(base + path, headers=headers, data=payload, method=method)
        try:
            with urlopen(request, timeout=120) as response:
                content = response.read()
                return json.loads(content) if content else None
        except HTTPError as error:
            if allow_missing and error.code == 404:
                return None
            # Do not log headers, request bodies, or the token.
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]} failed: HTTP {error.code}.") from None


def verify_files(folder: Path) -> None:
    checksums = folder / "SHA256SUMS.txt"
    expected = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (RhythiaTool\.exe|RhythiaTool-Windows\.zip)", line)
        if match is None or match[2] in expected:
            raise ValueError("The download checksum file is malformed.")
        expected[match[2]] = match[1]
    if set(expected) != set(ASSETS[:2]):
        raise ValueError("Both download checksums are required.")
    for filename, digest in expected.items():
        with (folder / filename).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"Checksum mismatch for {filename}; publication cancelled.")


def publish(api: GitHub, folder: Path, commit: str, run_url: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full source commit SHA is required.")
    if not re.fullmatch(r"https://github\.com/sttreets/rhythia/actions/runs/[0-9]+", run_url):
        raise ValueError("A build run URL for this repository is required.")
    verify_files(folder)
    base = f"/repos/{REPOSITORY}"
    release = api.request("GET", f"{base}/releases/tags/{TAG}", allow_missing=True)
    body = (
        f"{OWNER_MARKER}\n"
        "Download **RhythiaTool.exe** below and double-click it. No Python or installer is required.\n\n"
        "The optional **RhythiaTool-Windows.zip** also includes instructions. "
        "Windows 10/11, 64-bit. The build is unsigned and experimental. "
        "Do not disable security software to run it.\n\n"
        "Parser, timing, and interface tests use simulated input. "
        "Actual Steam/CAPO gameplay compatibility and ranked RP acceptance remain unverified.\n\n"
        f"Source: `{commit}`\n\n[Windows build and test log]({run_url})\n"
    )
    if release is None:
        existing_tag = api.request("GET", f"{base}/git/ref/tags/{TAG}", allow_missing=True)
        if existing_tag is not None:
            raise ValueError("windows-build tag already exists without a managed release; refusing to replace it.")
        release = api.request("POST", f"{base}/releases", data={
            "tag_name": TAG, "target_commitish": commit,
            "name": "Experimental Windows download", "body": body,
            "draft": True, "prerelease": True, "make_latest": "false",
        })
    else:
        if (release.get("tag_name") != TAG or OWNER_MARKER not in release.get("body", "")
                or not release.get("prerelease") or release.get("immutable")):
            raise ValueError("windows-build release is not a mutable managed prerelease; refusing to change it.")
    release_id = release["id"]
    current_assets = api.request("GET", f"{base}/releases/{release_id}/assets?per_page=100")
    for filename in ASSETS:
        for asset in current_assets:
            if asset["name"] == filename:
                api.request("DELETE", f"{base}/releases/assets/{asset['id']}")
        api.request("POST", f"{base}/releases/{release_id}/assets?{urlencode({'name': filename})}",
                    binary=(folder / filename).read_bytes())
    # A failed earlier upload may have left a managed draft without a tag.
    # Only this dedicated rolling build tag is ever created or moved.
    tag = api.request("GET", f"{base}/git/ref/tags/{TAG}", allow_missing=True)
    if tag is None:
        api.request("POST", f"{base}/git/refs", data={"ref": f"refs/tags/{TAG}", "sha": commit})
    else:
        api.request("PATCH", f"{base}/git/refs/tags/{TAG}", data={"sha": commit, "force": True})
    api.request("PATCH", f"{base}/releases/{release_id}", data={
        "name": "Experimental Windows download", "body": body,
        "draft": False, "prerelease": True, "make_latest": "false",
    })
    print(f"Published https://github.com/{REPOSITORY}/releases/download/{TAG}/RhythiaTool.exe")


def main() -> None:
    if os.environ.get("GH_REPO") != REPOSITORY:
        raise ValueError("Publication is restricted to sttreets/rhythia.")
    publish(GitHub(os.environ.get("GH_TOKEN", "")), Path("dist"),
            os.environ.get("BUILD_COMMIT", ""), os.environ.get("BUILD_RUN_URL", ""))


if __name__ == "__main__":
    main()
