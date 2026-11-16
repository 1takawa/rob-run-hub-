import sys
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.rob_run_tools import raid_defense  # noqa: E402


class DecoderCalculator(unittest.TestCase):
    def test_defense_and_decoders(self) -> None:
        result = raid_defense("trailer", {"uncommon": 1, "epic": 1, "legendary": 1})
        self.assertEqual(result["defense"], 5 + 1 + 3 + 5)
        self.assertEqual(result["decoders"], {"uncommon": 14, "epic": 5, "legendary": 3})
        self.assertEqual(result["install_seconds"]["legendary"], 36)
        with self.assertRaises(ValueError):
            raid_defense("trailer", {"uncommon": 10})        # 9 cells only

    def test_the_page_never_puts_more_fuses_than_cells(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            from harvest_assistant.ui.rob_run_info_page import ROBRunInfoPage

            page = ROBRunInfoPage(root)
            for _ in range(20):
                page._change("legendary", +1)
            self.assertEqual(page.fuses["legendary"], 9)        # «Прицеп» has 9 cells
            self.assertEqual(page.defense.cget("text"), str(5 + 9 * 5))
            page._pick("mesh_fence")                             # 3 cells: the extra fuses go
            self.assertEqual(sum(page.fuses.values()), 3)
        finally:
            root.destroy()
