"""Fixed Discord Rich Presence connection and privacy-safe activity builder."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

TRACKER_URL = "https://github.com/1takawa/rob-run-hub-"
DISCORD_APPLICATION_ID = "1571948762367262760"
SMALL_ASSET_KEY = "hidden_pool"
RPC_UPDATE_INTERVAL = 15.0   # Discord itself allows one update in 15 s
ROLE_LABELS = {
    "criminal": "Грабитель",
    "police": "Полицейский",
    "civilian": "Гражданский",
}
LOCATION_ASSETS = {
    "map": ("Карта", "location_map"),
    "looty_bay": ("Лути-Бей", "location_looty_bay"),
    "motel_lenny": ("Мотель Лени", "location_motel_lenny"),
    "prison": ("Тюрьма", "location_prison"),
    "events_center": ("Центр событий", "location_map"),
    "city": ("Город", "location_map"),
}
HIDDEN_LOCATION_ASSET = "location_hidden"


def _money(value: int) -> str:
    return f"${int(value):,}".replace(",", " ")


def build_presence_payload(settings: dict, *, game_open: bool, in_game: bool, season: dict | None,
                           summary: dict | None, role: str | None = None,
                           in_party: bool | None = None, location_key: str | None = None) -> dict | None:
    """Build presence with always-on status/location and user-selectable extra fields."""
    if not game_open:
        return None

    if not in_game:
        location_label, large_image = "Главное меню", HIDDEN_LOCATION_ASSET
    elif location_key in LOCATION_ASSETS:
        location_label, large_image = LOCATION_ASSETS[location_key]
    else:
        # Unknown areas include private homes and housing plots: fail closed.
        location_label, large_image = "Локация скрыта", HIDDEN_LOCATION_ASSET

    details_parts = ["В игре" if in_game else "Главное меню"]
    if in_game:
        details_parts.append(location_label)
    if in_game and settings.get("discord_show_role", True) and role in ROLE_LABELS:
        details_parts.append(ROLE_LABELS[role])
    if in_game and settings.get("discord_show_party", True) and in_party is not None:
        details_parts.append("В пати" if in_party else "Соло")
    details = " · ".join(details_parts)[:128]

    state_parts: list[str] = []
    if season:
        code = str(season.get("code", "")).strip()
        name = str(season.get("name", "")).strip()
        if settings.get("discord_show_server", True):
            server = " · ".join(part for part in (code, name) if part)
        else:
            match = re.search(r"\b(EU|NA)\s*-\s*\d+", code, re.IGNORECASE)
            region = match.group(1).upper() if match else "??"
            server = f"{region}-??????"
        if server:
            state_parts.append(server)
    if summary is not None:
        if settings.get("discord_show_earnings", True):
            state_parts.append(f"За вайп {_money(int(summary.get('total', 0)))}")
        if settings.get("discord_show_hours", True):
            hours = max(0, int(float(summary.get("playtime", 0)) // 3600))
            state_parts.append(f"{hours} ч в этом вайпе")
    state = " · ".join(state_parts)[:128] or "ROB RUN"

    payload: dict = {
        "details": details,
        "state": state,
        "buttons": [{"label": "Get ROB RUN tracker", "url": TRACKER_URL}],
        "_in_game": bool(in_game),
        "large_image": large_image,
        "large_text": location_label,
        "small_image": SMALL_ASSET_KEY,
        "small_text": "Hidden Pool",
    }
    return payload


class DiscordPresenceController:
    """Keep the fixed Discord RPC connection away from Tk's main thread."""

    def __init__(self, on_status: Callable[[str], None] | None = None, *, enabled: bool = True) -> None:
        self._on_status = on_status
        self._lock = threading.RLock()
        self._payload: dict | None = None
        self._enabled = bool(enabled)
        self._status = "Подключение к Discord…"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="discord-rich-presence", daemon=True)
        self._thread.start()

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    def publish(self, payload: dict | None) -> None:
        with self._lock:
            self._payload = dict(payload) if payload is not None else None
        self._wake.set()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                self._payload = None
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.0)

    def _set_status(self, value: str) -> None:
        with self._lock:
            if self._status == value:
                return
            self._status = value
        if self._on_status:
            try:
                self._on_status(value)
            except Exception:
                logger.debug("Could not report Discord status", exc_info=True)

    @staticmethod
    def _close_rpc(rpc) -> None:
        if rpc is None:
            return
        try:
            rpc.clear()
        except Exception:
            pass
        try:
            rpc.close()
        except Exception:
            pass

    def _run(self) -> None:
        rpc = None
        client_id = ""
        configured = False
        last_payload_signature = None
        last_sent_at: float | None = None
        session_started_at: int | None = None
        retry_at = 0.0
        try:
            while not self._stop.is_set():
                with self._lock:
                    payload = dict(self._payload) if self._payload is not None else None
                    enabled = self._enabled
                if not enabled:
                    self._close_rpc(rpc)
                    rpc, configured, client_id = None, False, ""
                    last_payload_signature = None
                    last_sent_at = None
                    session_started_at = None
                    retry_at = 0.0
                    self._set_status("Discord выключен")
                    self._wait(2.0)
                    continue
                wanted_id = DISCORD_APPLICATION_ID

                if not configured or wanted_id != client_id:
                    self._close_rpc(rpc)
                    rpc, configured, last_payload_signature = None, False, None
                    last_sent_at = None
                    client_id = wanted_id
                    session_started_at = None
                    if time.monotonic() >= retry_at:
                        try:
                            from pypresence import Presence

                            rpc = Presence(client_id)
                            rpc.connect()
                            configured = True
                            retry_at = 0.0
                            self._set_status("Discord подключён")
                        except ImportError:
                            self._set_status("Не найдена библиотека pypresence · запусти start.bat")
                            retry_at = time.monotonic() + 15.0
                        except Exception as exc:
                            logger.info("Discord RPC connection failed: %s", exc)
                            self._set_status("Discord не запущен или Rich Presence недоступен")
                            retry_at = time.monotonic() + 15.0

                if configured and rpc is not None:
                    if payload is None:
                        if last_payload_signature is not None:
                            try:
                                rpc.clear()
                                last_payload_signature = None
                                last_sent_at = None
                                session_started_at = None
                            except Exception as exc:
                                logger.info("Could not clear Discord activity: %s", exc)
                                self._close_rpc(rpc)
                                rpc, configured, retry_at = None, False, time.monotonic() + 15.0
                        self._set_status("Подключён · жду игру")
                    else:
                        in_game = bool(payload.pop("_in_game", False))
                        if in_game and session_started_at is None:
                            session_started_at = int(time.time())
                        elif not in_game:
                            session_started_at = None
                        if in_game and session_started_at is not None:
                            payload["start"] = session_started_at
                        payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
                        signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        can_send = last_sent_at is None or time.monotonic() - last_sent_at >= RPC_UPDATE_INTERVAL
                        if signature != last_payload_signature and can_send:
                            try:
                                rpc.update(**payload)
                                last_payload_signature = signature
                                last_sent_at = time.monotonic()
                                self._set_status("Активен в Discord")
                            except Exception as exc:
                                image_fields = {"large_image", "large_text", "small_image", "small_text"}
                                fallback_payload = {key: value for key, value in payload.items() if key not in image_fields}
                                try:
                                    rpc.update(**fallback_payload)
                                    last_payload_signature = json.dumps(fallback_payload, ensure_ascii=False, sort_keys=True)
                                    last_sent_at = time.monotonic()
                                    self._set_status("Активен в Discord · загрузи арт-ассеты")
                                except Exception:
                                    logger.info("Discord RPC update failed: %s", exc)
                                    self._set_status("Не удалось обновить статус Discord")
                                    self._close_rpc(rpc)
                                    rpc, configured, retry_at = None, False, time.monotonic() + 15.0
                self._wait(2.0)
        finally:
            self._close_rpc(rpc)
            self._set_status("Выключен")

    def _wait(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()
