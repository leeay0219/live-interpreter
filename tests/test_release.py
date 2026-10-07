"""The public package keeps the shared skill usable without exposing hidden files."""
import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_release import EXACT, ROOT_FILES, allowed, build, inspect, verify_public_history

ROOT = Path(__file__).resolve().parents[1]
SKILL = ".agents/skills/session-writeup/"


class ReleaseTests(unittest.TestCase):
    def test_git_initialization_requires_explicit_public_identity(self):
        with self.assertRaises(ValueError):
            build(ROOT, init_git=True)

    def test_release_rejects_tokens_without_echoing_the_value(self):
        for value in ("ghp_" + "A" * 36, "xoxb-" + "A" * 24):
            with self.assertRaises(ValueError) as error:
                inspect(Path("settings.py"), value.encode())
            self.assertNotIn(value, str(error.exception))

    def test_history_scan_detects_deleted_private_files_and_old_author_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            git("config", "user.name", "Example")
            git("config", "user.email", "example@users.noreply.github.com")
            (root / "HANDOFF.md").write_text("private historical notes")
            git("add", ".")
            git("commit", "-q", "-m", "First")
            git("rm", "HANDOFF.md")
            (root / "README.md").write_text("public")
            git("add", ".")
            git("commit", "-q", "-m", "Remove private file")
            with self.assertRaisesRegex(ValueError, "historical file"):
                verify_public_history(root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git("init", "-q", "-b", "main")
            git("config", "user.name", "Example")
            git("config", "user.email", "private@example.invalid")
            (root / "README.md").write_text("public")
            git("add", ".")
            git("commit", "-q", "-m", "First")
            with self.assertRaisesRegex(ValueError, "noreply"):
                verify_public_history(root)

    def test_only_reviewed_skill_files_are_allowed_from_hidden_directories(self):
        for name in ("SKILL.md", "agents/openai.yaml", "references/examples.md"):
            self.assertTrue(allowed(Path(SKILL + name)))
        for name in (
            SKILL + ".env", SKILL + "references/private-notes.md",
            ".agents/skills/another-skill/SKILL.md", ".codex/config.toml",
            ".local/notes.md", "postprocess/.private.md", "tests/__pycache__/cache.py",
        ):
            self.assertFalse(allowed(Path(name)), name)

    def test_source_zip_keeps_skill_references_policy_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for name in ROOT_FILES | EXACT:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            private = root / SKILL / "references/private-notes.md"
            private.write_text("Private working notes", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                destination = build(root)
            self.assertFalse((destination / SKILL / "references/private-notes.md").exists())
            self.assertFalse((destination / ".git").exists())
            entry = destination / SKILL / "SKILL.md"
            for relative in ("../../../postprocess/writeup-policy.md", "references/examples.md"):
                self.assertIn(f"]({relative})", entry.read_text())
                self.assertTrue((entry.parent / relative).is_file())
            manifest = (destination / "SHA256SUMS").read_text().splitlines()
            for line in manifest:
                expected, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), expected)
            with zipfile.ZipFile(destination.parent / f"{destination.name}.zip") as archive:
                for name in EXACT:
                    self.assertEqual(archive.read(f"{destination.name}/{name}"), (root / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
