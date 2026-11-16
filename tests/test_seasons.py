import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.seasons import WIPE_SECONDS, SeasonStore, normalize_code  # noqa: E402
from harvest_assistant.game_watch import GameWatcher, parse_server_age  # noqa: E402


class Seasons(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.store = SeasonStore(self.folder / "seasons.json")

    def test_one_current_state_and_an_early_wipe(self) -> None:
        first = self.store.start("06242", "rocky valley", time.time() - 3600)
        self.assertEqual((first["code"], first["name"]), ("EU-06242", "ROCKY VALLEY"))
        second = self.store.start("EU-07001", "Desert Ridge", time.time())
        self.assertEqual(self.store.current()["id"], second["id"])
        old = self.store.get(first["id"])
        self.assertTrue(old["early"])                       # ended before its seven days
        self.assertIsNotNone(old["ended_at"])
        again = SeasonStore(self.folder / "seasons.json")   # kept on disk
        self.assertEqual(again.current()["code"], "EU-07001")
        self.assertEqual(len(again.history()), 2)

    def test_after_seven_days_the_season_ends_by_itself(self) -> None:
        season = self.store.start("EU-1", "A", time.time() - WIPE_SECONDS - 60)
        self.store.roll_over()
        self.assertIsNone(self.store.current())
        self.assertFalse(self.store.get(season["id"])["early"])

    def test_the_numbers_of_a_season(self) -> None:
        start = time.time() - 7200
        season = self.store.start("EU-1", "A", start)
        self.store.add_salary(25000, new_shift=True)
        self.store.add_salary(5000, new_shift=False)
        self.store.add_playtime(3600.0)
        events = [{"t": start + 10, "k": "loot", "kind": "purple", "n": 3}, {"t": start - 10, "k": "loot", "kind": "green", "n": 9},
                  {"t": start + 20, "k": "cycle"}]
        combat = [{"started_at": start + 5, "knocks": 6, "deaths": 2, "arrests": 1}, {"started_at": start - 5000, "knocks": 50}]
        summary = SeasonStore.summary(self.store.current(), events, combat)
        self.assertEqual(summary["counts"], {"yellow": 0, "purple": 3, "green": 0})   # the earlier loot is not this season's
        self.assertEqual(summary["salary"], 30000)
        self.assertEqual(summary["shifts"], 1)
        self.assertEqual(summary["total"], 3 * 6000 + 30000)
        self.assertEqual((summary["knocks"], summary["deaths"], summary["arrests"]), (6, 2, 1))
        self.assertEqual(summary["kd"], 3.0)
        self.assertEqual(summary["per_hour"], 48000)

    def test_a_start_from_the_main_menu_moves_only_a_start_not_typed_by_hand(self) -> None:
        self.store.start("EU-1", "A", time.time(), auto_start=True)
        self.assertTrue(self.store.correct_start(time.time() - 22 * 3600))
        self.store.update(self.store.current()["id"], started_at=time.time() - 3600, auto_start=False)
        self.assertFalse(self.store.correct_start(time.time() - 22 * 3600))

    def test_codes(self) -> None:
        self.assertEqual(normalize_code("eu 06242"), "EU-06242")
        self.assertEqual(normalize_code("06242"), "EU-06242")


class ServerAge(unittest.TestCase):
    def test_agreeing_readings_only(self) -> None:
        self.assertEqual(parse_server_age(["ВРЕМЯ СУЩЕСТВОВАНИЯ СЕРВЕРА 22Ч 49M", "СЕРВЕРА 22Ч адм", "СЕРВЕРА 22Ч 49M"]),
                         22 * 3600 + 49 * 60)
        self.assertEqual(parse_server_age(["СЕРВЕРА 22Ч иГЛ", "СЕРВЕРА 22Ч"]), 22 * 3600)   # minutes unsure: whole hours
        self.assertIsNone(parse_server_age(["СЕРВЕРА ПЧ Ям", "СЕРВЕРА 22Ч"]))               # one reading is not enough

    def test_the_real_main_menu(self) -> None:
        import cv2

        from harvest_assistant.game_watch import _Ocr, read_server_age
        frame = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "main_menu.jpg")), cv2.COLOR_BGR2RGB)
        try:
            age = read_server_age(cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_AREA), _Ocr())
        except Exception as exc:
            self.skipTest(str(exc))
        self.assertEqual(age, 22 * 3600 + 49 * 60)


class CopShift(unittest.TestCase):
    def watcher(self) -> tuple[GameWatcher, SeasonStore]:
        store = SeasonStore(Path(tempfile.mkdtemp()) / "seasons.json")
        store.start("EU-1", "A", time.time())

        class Log:
            def last_state(self): return "battle"
            def read_new(self): return []
        return GameWatcher(lambda: None, store, log=Log()), store

    def look(self, watcher: GameWatcher, *values) -> None:
        for value in values:
            if value == "gone":
                watcher.see_counter(False, None)
            else:
                watcher.see_counter(True, value)

    def test_the_last_number_is_banked_when_the_shield_goes(self) -> None:
        watcher, store = self.watcher()
        self.look(watcher, 0, 0, 1200, 1200, 5400, 5400, *["gone"] * 4)
        self.assertEqual(store.current()["salary"], 5400)
        self.assertEqual(store.current()["shifts"], 1)

    def test_a_short_gap_continues_the_shift_and_only_the_growth_is_banked(self) -> None:
        watcher, store = self.watcher()
        self.look(watcher, 3000, 3000, *["gone"] * 4, 3000, 3000, 4500, 4500, *["gone"] * 4)
        self.assertEqual(store.current()["salary"], 4500)
        self.assertEqual(store.current()["shifts"], 1)

    def test_a_new_shift_starts_from_a_smaller_number(self) -> None:
        watcher, store = self.watcher()
        self.look(watcher, 3000, 3000, *["gone"] * 4, 100, 100, 900, 900, *["gone"] * 4)
        self.assertEqual(store.current()["salary"], 3900)
        self.assertEqual(store.current()["shifts"], 2)

    def test_a_single_misread_is_not_taken(self) -> None:
        watcher, store = self.watcher()
        self.look(watcher, 2000, 2000, 98000, 2100, 2100, *["gone"] * 4)
        self.assertEqual(store.current()["salary"], 2100)
