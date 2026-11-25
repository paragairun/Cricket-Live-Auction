"""In-process WebSocket fan-out for the single local FastAPI process.

The database is authoritative. WebSocket messages are notifications only;
reconnecting clients always request a fresh database snapshot.
"""
import json
import time
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._rooms: dict[int, list[WebSocket]] = defaultdict(list)
        self._user_map: dict[int, dict[WebSocket, int | None]] = defaultdict(dict)
        self._unique_viewers: dict[int, set[int]] = defaultdict(set)
        self._sequences: dict[int, int] = defaultdict(int)

    async def startup(self):
        return None

    async def shutdown(self):
        for sockets in list(self._rooms.values()):
            for ws in list(sockets):
                try:
                    await ws.close()
                except Exception:
                    pass
        self._rooms.clear()
        self._user_map.clear()

    async def connect(self, event_id: int, ws: WebSocket, user_id: int | None = None):
        await ws.accept()
        self._rooms[event_id].append(ws)
        self._user_map[event_id][ws] = user_id
        if user_id is not None:
            self._unique_viewers[event_id].add(user_id)

    async def disconnect(self, event_id: int, ws: WebSocket):
        self._user_map.get(event_id, {}).pop(ws, None)
        room = self._rooms.get(event_id, [])
        if ws in room:
            room.remove(ws)
        if not room:
            self._rooms.pop(event_id, None)
            self._user_map.pop(event_id, None)

    async def get_viewer_stats(self, event_id: int) -> dict:
        return {
            "live_viewers": len(self._rooms.get(event_id, [])),
            "total_unique_viewers": len(self._unique_viewers.get(event_id, set())),
        }

    async def broadcast(self, event_id: int, message: dict[str, Any]):
        self._sequences[event_id] += 1
        enriched = {**message, "seq": self._sequences[event_id], "ts": time.time()}
        await self._local_broadcast(event_id, enriched)

    async def _local_broadcast(self, event_id: int, message: dict[str, Any]):
        payload = json.dumps(message, default=str)
        dead = []
        for ws in list(self._rooms.get(event_id, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(event_id, ws)

    async def send_personal(self, ws: WebSocket, message: dict[str, Any]):
        await ws.send_text(json.dumps(message, default=str))


manager = ConnectionManager()
