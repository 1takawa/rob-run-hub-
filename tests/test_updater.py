import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant import updater  # noqa: E402


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, check=True).stdout


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class UpdaterTests(unittest.TestCase):
    """A GitHub stand-in (a bare repository) and a copy of the hub cloned from it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], capture_output=True, check=True)
        self.author = self.tmp / "author"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.author)], capture_output=True, check=True)
        for folder in (self.author,):
            git(folder, "config", "user.name", "t")
            git(folder, "config", "user.email", "t@example.com")
        self.commit(self.author, "app.py", "v1", "first")
        git(self.author, "push", "-q", "origin", "main")
        self.copy = self.tmp / "copy"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.copy)], capture_output=True, check=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, folder: Path, name: str, text: str, title: str) -> None:
        (folder / name).write_text(text, encoding="utf-8")
        git(folder, "add", name)
        git(folder, "commit", "-q", "-m", title)

    def test_latest_then_available_then_downloaded(self) -> None:
        self.assertEqual(updater.check(self.copy).state, "latest")
        self.commit(self.author, "app.py", "v2", "Автокоп: плашка КОП")
        self.commit(self.author, "app.py", "v3", "Оверлей в стиле ROB RUN")
        git(self.author, "push", "-q", "origin", "main")
        info = updater.check(self.copy)
        self.assertEqual(info.state, "available")
        self.assertEqual(info.behind, 2)
        self.assertEqual(info.changes, ["Оверлей в стиле ROB RUN", "Автокоп: плашка КОП"])
        ok, message = updater.apply(self.copy)
        self.assertTrue(ok, message)
        self.assertEqual((self.copy / "app.py").read_text(encoding="utf-8"), "v3")
        self.assertEqual(updater.check(self.copy).state, "latest")

    def test_personal_files_are_never_touched(self) -> None:
        (self.copy / "profile.json").write_text("mine", encoding="utf-8")    # untracked, like profiles/
        self.commit(self.author, "app.py", "v2", "new")
        git(self.author, "push", "-q", "origin", "main")
        ok, _message = updater.apply(self.copy)
        self.assertTrue(ok)
        self.assertEqual((self.copy / "profile.json").read_text(encoding="utf-8"), "mine")

    def test_hand_edited_program_files_are_not_overwritten(self) -> None:
        (self.copy / "app.py").write_text("edited by hand", encoding="utf-8")
        self.commit(self.author, "app.py", "v2", "new")
        git(self.author, "push", "-q", "origin", "main")
        self.assertEqual(updater.check(self.copy).state, "local_changes")
        ok, _message = updater.apply(self.copy)
        self.assertFalse(ok)
        self.assertEqual((self.copy / "app.py").read_text(encoding="utf-8"), "edited by hand")

    def test_a_folder_without_git_says_so(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(updater.check(plain).state, "error")
        self.assertEqual(updater.version(plain), "")


if __name__ == "__main__":
    unittest.main()
