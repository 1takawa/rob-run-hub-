"""Profile persistence for the optional background-control mode."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.models import Profile  # noqa: E402
from harvest_assistant.persistence import load_profile, save_profile  # noqa: E402


class BackgroundProfileTests(unittest.TestCase):
    def test_background_control_is_off_for_existing_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text('{"name":"old profile"}', encoding="utf-8")

            profile = load_profile(path)

        self.assertFalse(profile.background_control)

    def test_background_control_setting_survives_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            profile = Profile(background_control=True)
            save_profile(profile, path)

            loaded = load_profile(path)

        self.assertTrue(loaded.background_control)


if __name__ == "__main__":
    unittest.main()
