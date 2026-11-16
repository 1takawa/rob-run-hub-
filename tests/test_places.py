import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.game_watch import GameWatcher  # noqa: E402
from harvest_assistant.places import PlaceNames  # noqa: E402
from harvest_assistant.reconnect import PlayerLog  # noqa: E402

LINES = [
    b'[Network] NetworkStep - Changed on "InRealmNotInBattle".',
    b'[Network] NetworkStep - Changed on "InBattle".',
    b"[Network] ConnectUpdater.GetAddress started id=battle host=prod-game-eu-0-shooter-g1-ext-gcore-68.rob-run.kefir.games.",
    b"[Info] Init state: InVehicle",
    b"[Info] Init role: citizen\r",
]


class _Log:
    def last_state(self):
        return "lobby"

    def read_new(self):
        return []


class _Seasons:
    data: dict = {}


class Places(unittest.TestCase):
    def test_the_log_names_the_place(self) -> None:
        self.assertEqual(PlayerLog.parse(LINES), ["realm", "battle", "server:shooter", "spawned", "role:citizen"])

    def test_a_named_place_is_known_next_time(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            places = PlaceNames(Path(folder) / "places.json")
            watch = GameWatcher(lambda: None, _Seasons(), log=_Log(), places=places)
            watch.see_log(["realm"])
            self.assertEqual(watch.location_key, "map")
            watch.see_log(PlayerLog.parse(LINES[1:]))
            self.assertEqual(watch.place, "citizen")
            self.assertEqual(watch.location_key, "looty_bay")   # the civilian role exists only in Looty Bay
            watch.see_log(["battle", "server:shooter", "role:hubber"])
            self.assertEqual(watch.location_key, "motel_lenny")
            watch.see_log(["battle", "server:battle", "role:arena"])
            self.assertIsNone(watch.location_key)          # not named yet: the card says «Локация скрыта»
            places.set("arena", "events_center")
            self.assertEqual(PlaceNames(Path(folder) / "places.json").get("arena"), "events_center")
            self.assertEqual(watch.location_key, "events_center")
            watch.see_log(["battle", "server:battle", "role:settler"])
            self.assertIsNone(watch.location_key)           # a settlement is never shown
            watch.see_log(["lobby"])
            self.assertIsNone(watch.location_key)


class Roles(unittest.TestCase):
    def test_the_role_comes_from_the_log(self) -> None:
        lines = [b"[Info] Init role: citizen", b"[Info] Role changed: citizen -> robber",
                 b"[Info] Role changed: citizen -> cop\r"]
        self.assertEqual(PlayerLog.parse(lines), ["role:citizen", "rolechange:robber", "rolechange:cop"])
        watch = GameWatcher(lambda: None, _Seasons(), log=_Log())
        watch.see_log(["battle", "server:battle", "role:citizen"])
        self.assertEqual(watch.role, "civilian")
        watch.see_log(["rolechange:robber"])
        self.assertEqual(watch.role, "criminal")
        watch.see_log(["rolechange:cop"])
        self.assertEqual(watch.role, "police")
        watch.see_log(["battle", "server:battle"])        # a reconnect: no new «Init role», the same place
        self.assertEqual((watch.role, watch.location_key), ("police", "looty_bay"))
        watch.see_log(["realm"])
        self.assertIsNone(watch.role)
        watch.see_log(["battle", "server:battle", "role:hubber"])
        self.assertIsNone(watch.role)          # in the motel there is no such role
