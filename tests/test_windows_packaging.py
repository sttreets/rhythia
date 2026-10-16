"""Download packaging and publication contracts; no network or native input."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.package_windows import package
from scripts.publish_windows_release import OWNER_MARKER, publish, verify_files


COMMIT = "a" * 40
RUN_URL = "https://github.com/sttreets/rhythia/actions/runs/123"
BASE = "/repos/sttreets/rhythia"


class FakeGitHub:
    def __init__(self, release=None, tag=None):
        self.release = release
        self.tag = tag
        self.calls = []
        self.assets = [{"id": 8, "name": "RhythiaTool.exe"}, {"id": 9, "name": "unrelated.txt"}]

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.endswith("/releases/tags/windows-build"):
            return self.release
        if method == "GET" and path.endswith("/git/ref/tags/windows-build"):
            return self.tag
        if method == "GET" and path.endswith("/assets?per_page=100"):
            return self.assets
        if method == "POST" and path.endswith("/releases"):
            return {"id": 42}
        return {}


class WindowsPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / "dist").mkdir()
        self.exe = self.project / "dist" / "RhythiaTool.exe"
        self.exe.write_bytes(b"MZfake Windows binary for packaging tests")
        (self.project / "README.md").write_text("Instructions", encoding="utf-8")

    def make_package(self):
        return package(self.project, COMMIT)

    def test_portable_download_contains_executable_instructions_and_source_identity(self):
        archive, checksums = self.make_package()
        with zipfile.ZipFile(archive) as result:
            self.assertEqual(set(result.namelist()), {"RhythiaTool.exe", "README.md", "START HERE.txt", "BUILD.txt"})
            self.assertIsNone(result.testzip())
            self.assertEqual(result.read("RhythiaTool.exe"), self.exe.read_bytes())
            self.assertIn(COMMIT, result.read("BUILD.txt").decode())
            self.assertIn("Python and an installer are not required", result.read("START HERE.txt").decode())
        self.assertIn(hashlib.sha256(self.exe.read_bytes()).hexdigest(), checksums.read_text())
        verify_files(self.project / "dist")

    def test_rejects_missing_non_executable_and_invalid_commit_before_packaging(self):
        with self.assertRaises(ValueError):
            package(self.project, "main")
        self.exe.write_bytes(b"not a Windows executable")
        with self.assertRaises(ValueError):
            self.make_package()
        self.exe.unlink()
        with self.assertRaises(ValueError):
            self.make_package()

    def test_checksum_corruption_prevents_all_api_calls(self):
        self.make_package()
        self.exe.write_bytes(b"MZchanged")
        api = FakeGitHub()
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            publish(api, self.project / "dist", COMMIT, RUN_URL)
        self.assertEqual(api.calls, [])

    def test_malformed_duplicate_or_missing_checksums_are_rejected(self):
        _, checksums = self.make_package()
        valid = checksums.read_text()
        for content in ("bad", valid.splitlines()[0] + "\n", valid + valid.splitlines()[0] + "\n"):
            with self.subTest(content=content):
                checksums.write_text(content)
                with self.assertRaises(ValueError):
                    verify_files(self.project / "dist")

    def test_first_publication_creates_only_dedicated_prerelease_and_three_assets(self):
        self.make_package()
        api = FakeGitHub()
        with patch("builtins.print"):
            publish(api, self.project / "dist", COMMIT, RUN_URL)
        release = next(c for c in api.calls if c[:2] == ("POST", BASE + "/releases"))
        self.assertEqual(release[2]["data"]["tag_name"], "windows-build")
        self.assertTrue(release[2]["data"]["draft"])
        self.assertTrue(release[2]["data"]["prerelease"])
        self.assertEqual(release[2]["data"]["target_commitish"], COMMIT)
        uploaded = [c for c in api.calls if "binary" in c[2]]
        self.assertEqual(len(uploaded), 3)
        self.assertTrue(all(c[2]["binary"] for c in uploaded))
        self.assertEqual(api.calls[-1][:2], ("PATCH", BASE + "/releases/42"))
        self.assertFalse(api.calls[-1][2]["data"]["draft"])
        self.assertEqual(api.calls[-1][2]["data"]["make_latest"], "false")

    def test_managed_update_moves_only_build_tag_and_preserves_unrelated_assets(self):
        self.make_package()
        api = FakeGitHub(release={"id": 42, "tag_name": "windows-build", "body": OWNER_MARKER, "prerelease": True},
                         tag={"object": {"sha": "b" * 40}})
        with patch("builtins.print"):
            publish(api, self.project / "dist", COMMIT, RUN_URL)
        deletion = [c for c in api.calls if c[0] == "DELETE"]
        self.assertEqual([c[1] for c in deletion], [BASE + "/releases/assets/8"])
        moves = [c for c in api.calls if "/git/refs/" in c[1]]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0], ("PATCH", BASE + "/git/refs/tags/windows-build",
                                   {"data": {"sha": COMMIT, "force": True}}))

    def test_existing_unmanaged_release_tag_or_immutable_release_are_never_changed(self):
        self.make_package()
        cases = [
            FakeGitHub(tag={"object": {"sha": COMMIT}}),
            FakeGitHub(release={"id": 42, "tag_name": "windows-build", "body": "someone else's", "prerelease": True}),
            FakeGitHub(release={"id": 42, "tag_name": "windows-build", "body": OWNER_MARKER, "prerelease": False}),
            FakeGitHub(release={"id": 42, "tag_name": "windows-build", "body": OWNER_MARKER, "prerelease": True, "immutable": True}),
        ]
        for api in cases:
            with self.subTest(release=api.release, tag=api.tag), self.assertRaises(ValueError):
                publish(api, self.project / "dist", COMMIT, RUN_URL)
            self.assertTrue(all(c[0] == "GET" for c in api.calls))

    def test_rejects_external_run_urls_and_invalid_source_before_api_calls(self):
        self.make_package()
        api = FakeGitHub()
        for commit, run_url in (("main", RUN_URL), (COMMIT, "https://example.com/")):
            with self.assertRaises(ValueError):
                publish(api, self.project / "dist", commit, run_url)
        self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
