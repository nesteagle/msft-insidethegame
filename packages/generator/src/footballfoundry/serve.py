"""Local web viewer: `footballfoundry serve` streams matches to a browser over Server-Sent Events.

Endpoints:
    GET /                         the viewer (static files in footballfoundry/viewer/)
    GET /api/session              clubs, players and lineups for the served matches (no final scores)
    GET /api/events?match=&speed=&from=
                                  SSE: a `reset` message, every event before `from` (match-clock seconds)
                                  at once, the rest paced at `speed` x real time, then an `end` message
"""

from __future__ import annotations

import json
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

from footballfoundry.engine import MatchResult
from footballfoundry.formations import FORMATIONS
from footballfoundry_schema import League
from footballfoundry.stream import HALF_TIME_S, timeline

VIEWER = files("footballfoundry") / "viewer"
KEEPALIVE_S = 10.0
MAX_SPEED = 600.0

# Kit colour names used by world.py -> hex, for the scoreboard swatches.
KIT_HEX = {
    "red": "#d62828", "blue": "#1d4ed8", "sky blue": "#7cc4f0", "claret": "#7a1f3d", "white": "#f5f5f5",
    "black": "#111111", "amber": "#f2a900", "green": "#1f8a4c", "navy": "#1b2a4a", "orange": "#f77f00",
    "maroon": "#6d1a1a", "yellow": "#f6d32d", "purple": "#5b2a86", "teal": "#0f8b8d", "silver": "#b8bcc2",
}


def session_payload(league: League, results: list[MatchResult]) -> dict:
    """Everything the viewer needs up front. Deliberately excludes final scores and added time."""
    club_ids = {cid for r in results for cid in (r.info.home_club_id, r.info.away_club_id)}
    player_ids = {e.player_id for r in results for e in r.info.lineups}
    matches = []
    for r in results:
        i = r.info
        lineups = []
        for club_id, formation in ((i.home_club_id, i.home_formation), (i.away_club_id, i.away_formation)):
            starters = [e for e in i.lineups if e.club_id == club_id and e.starter]
            for entry, (_, bx, by) in zip(starters, FORMATIONS[formation]):
                lineups.append({"club_id": club_id, "player_id": entry.player_id, "role": entry.role,
                                "starter": True, "base_x": bx, "base_y": by})
            lineups += [{"club_id": club_id, "player_id": e.player_id, "role": None, "starter": False}
                        for e in i.lineups if e.club_id == club_id and not e.starter]
        matches.append({
            "match_id": i.match_id, "matchday": i.matchday, "kickoff": i.kickoff.isoformat(),
            "venue": i.venue, "referee": i.referee,
            "home_club_id": i.home_club_id, "away_club_id": i.away_club_id,
            "home_formation": i.home_formation, "away_formation": i.away_formation,
            "lineups": lineups,
        })
    return {
        "league": {"name": league.name, "season": league.season},
        "clubs": {c.club_id: {"name": c.name, "short_name": c.short_name, "formation": c.formation,
                              "kit": [KIT_HEX.get(col, "#888888") for col in c.colors]}
                  for c in league.clubs if c.club_id in club_ids},
        "players": {p.player_id: {"name": p.name, "short_name": p.short_name, "number": p.shirt_number,
                                  "position": p.position, "nationality": p.nationality}
                    for p in league.players if p.player_id in player_ids},
        "matches": matches,
        "halftime_s": HALF_TIME_S,
    }


def make_handler(league: League, results: list[MatchResult], default_speed: float):
    session = json.dumps(session_payload(league, results)).encode()
    schedules = {r.info.match_id: timeline(r.events) for r in results}

    class Handler(BaseHTTPRequestHandler):
        server_version = "footballfoundry"

        def log_message(self, fmt, *args):  # keep the console for match headlines
            pass

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/api/session":
                return self._send(HTTPStatus.OK, session, "application/json")
            if url.path == "/api/events":
                return self._events(parse_qs(url.query))
            return self._static(url.path)

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            resource = VIEWER / name
            if "/" in name or ".." in name or not resource.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, resource.read_bytes(), ctype)

        def _events(self, query: dict[str, list[str]]) -> None:
            match_id = query.get("match", [""])[0]
            if match_id not in schedules:
                return self._send(HTTPStatus.NOT_FOUND, b"unknown match", "text/plain")
            try:
                speed = min(float(query.get("speed", [default_speed])[0]), MAX_SPEED)
                start_at = max(float(query.get("from", ["0"])[0]), 0.0)
            except ValueError:
                return self._send(HTTPStatus.BAD_REQUEST, b"bad speed/from", "text/plain")

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self._sse("reset", {"from": start_at, "speed": speed})
                items = schedules[match_id]
                clock_start = time.monotonic()
                for t, event in items:
                    catchup = t < start_at or speed <= 0
                    if not catchup:
                        due = clock_start + (t - start_at) / speed
                        while (wait := due - time.monotonic()) > 0:
                            time.sleep(min(wait, KEEPALIVE_S))
                            if due - time.monotonic() > 0:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                    self._sse("event", {"t": round(t, 2), "catchup": catchup, "e": event.model_dump(mode="json")},
                              flush=not catchup)
                self._sse("end", {})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # browser closed the stream (pause, speed change, tab closed)

        def _sse(self, kind: str, data: dict, flush: bool = True) -> None:
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode())
            if flush:
                self.wfile.flush()

    return Handler


def serve(league: League, results: list[MatchResult], *, host: str = "127.0.0.1", port: int = 8000,
          speed: float = 10.0) -> ThreadingHTTPServer:
    """Create (but don't start) the viewer server. Call .serve_forever() on the result."""
    server = ThreadingHTTPServer((host, port), make_handler(league, results, speed))
    server.daemon_threads = True
    return server
