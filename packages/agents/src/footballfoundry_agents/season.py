"""The season pages' data: table, club and player totals up to any matchday, from the season book.

The book (footballfoundry.book) holds every simulated fixture's result and player lines. In Azure it's baked into
the image (FF_SEASON_BOOK); locally it's read from data/season-book.json, or built on first request (the season is
simulated once, about a minute and a half) and saved there.

Spoilers: the season is simulated in full, but the page shouldn't tell a viewer the result of a match they're
watching. With a match live, the default view is the table before its matchday.

    GET /api/season?through=<matchday>          table, club totals and player totals after that matchday
    GET /api/season/players/{id}?through=<md>   one player's match-by-match log
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SUMMED = ("min", "g", "a", "sh", "sot", "kp", "pa", "pc", "tk", "tkw", "int", "rec", "clr", "blk", "sav", "yc", "rc",
          "cs", "spr")
FORM = 5


class Season:
    def __init__(self, seed: int = 42, path: str | Path | None = None) -> None:
        self.seed = seed
        self.path = Path(path) if path else None
        self._league = None
        self._results = None
        self._book: dict | None = None
        self._views: dict[int, dict] = {}
        self._lock: asyncio.Lock | None = None
        self._building: asyncio.Task | None = None
        self.error: str | None = None

    def league(self):
        if self._league is None:
            from footballfoundry.world import generate_league

            self._league = generate_league(self.seed)
        return self._league

    async def results(self):
        """The simulated season (for the story finder and for building the book), simulated once."""
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            if self._results is None:
                from footballfoundry.season import simulate_season

                log.info("season: simulating 380 matches (seed %d)...", self.seed)
                self._results = await asyncio.to_thread(simulate_season, self.league(), workers=1)
            return self._results

    def use_results(self, results) -> None:
        self._results = results

    # -- the book ----------------------------------------------------------------------------------------------

    @property
    def status(self) -> str:
        return "ready" if self._book else "failed" if self.error else "building" if self._building else "missing"

    def book(self) -> dict | None:
        """The book if it's ready; otherwise starts building it (once) and returns None."""
        if self._book is None and self._building is None and self.error is None:
            data = None
            if self.path and self.path.is_file():
                from footballfoundry.book import BOOK_VERSION

                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") != BOOK_VERSION:
                    log.info("season: %s is from an older version; rebuilding it", self.path)
                    data = None
            if data is not None:
                self._set_book(data)
                log.info("season: book from %s (%d matches)", self.path, len(data["matches"]))
            else:
                self._building = asyncio.create_task(self._build(), name="season-book")
        return self._book

    async def ready_book(self) -> dict:
        """The book, waiting for it to be built if it isn't ready yet."""
        if self.book() is None and self._building is not None:
            await asyncio.shield(self._building)
        if self._book is None:
            raise RuntimeError(self.error or "the season book isn't available")
        return self._book

    async def _build(self) -> None:
        from footballfoundry import book

        try:
            results = await self.results()
            data = await asyncio.to_thread(book.build, self.league(), results)
            self._set_book(data)
            if self.path:
                try:
                    from footballfoundry.export import write_book

                    write_book(data, self.path)
                    log.info("season: book saved to %s", self.path)
                except OSError as exc:
                    log.warning("season: couldn't save the book to %s: %s", self.path, exc)
        except Exception as exc:  # noqa: BLE001 - reported to the page
            log.exception("season: building the book failed")
            self.error = f"{type(exc).__name__}: {exc}"

    def replace_book(self, data: dict) -> None:
        """A freshly built book (the control room regenerated the season): use it and save it."""
        self._set_book(data)
        if self.path:
            from footballfoundry.export import write_book

            write_book(data, self.path)

    def _set_book(self, data: dict) -> None:
        self._book = data
        self._views.clear()

    # -- views -------------------------------------------------------------------------------------------------

    def matchday_of(self) -> dict[str, int]:
        return {m["id"]: m["md"] for m in (self._book or {}).get("matches", [])}

    def last_matchday(self) -> int:
        return max((m["md"] for m in (self._book or {}).get("matches", [])), default=0)

    def view(self, through: int) -> dict[str, Any]:
        if through not in self._views:
            self._views[through] = _view(self._book or {"matches": []}, through)
        return self._views[through]

    def player_log(self, player_id: str, through: int) -> list[dict[str, Any]]:
        out = []
        for m in (self._book or {}).get("matches", []):
            if m["md"] > through:
                break
            line = next((x for x in m["lines"] if x["p"] == player_id), None)
            if line is None:
                continue
            home = line["c"] == m["h"]
            out.append({"match_id": m["id"], "matchday": m["md"], "kickoff": m["ko"], "home": home,
                        "opponent": m["a"] if home else m["h"], "gf": m["hs"] if home else m["as"],
                        "ga": m["as"] if home else m["hs"], **{k: v for k, v in line.items() if k not in ("p", "c")}})
        return out


def _view(book: dict, through: int) -> dict[str, Any]:
    matches = [m for m in book["matches"] if m["md"] <= through]
    table = _table(matches)
    previous = {r["club_id"]: r["position"] for r in _table([m for m in matches if m["md"] < through])}
    for row in table:
        row["previous"] = previous.get(row["club_id"])
    upcoming = [m for m in book["matches"] if m["md"] > through]
    for row in table:
        nxt = next((m for m in upcoming if row["club_id"] in (m["h"], m["a"])), None)
        row["next"] = None if nxt is None else {"match_id": nxt["id"], "matchday": nxt["md"],
                                                "home": nxt["h"] == row["club_id"],
                                                "opponent": nxt["a"] if nxt["h"] == row["club_id"] else nxt["h"]}
    return {"through": through, "played": len(matches), "table": table, "clubs": _clubs(matches),
            "players": _players(matches)}


def _table(matches: list[dict]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def row(club: str) -> dict[str, Any]:
        return rows.setdefault(club, {"club_id": club, "played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0,
                                      "ga": 0, "points": 0, "form": []})

    for m in matches:
        for club, gf, ga, opp, home in ((m["h"], m["hs"], m["as"], m["a"], True),
                                        (m["a"], m["as"], m["hs"], m["h"], False)):
            r = row(club)
            r["played"] += 1
            r["gf"] += gf
            r["ga"] += ga
            result = "W" if gf > ga else "D" if gf == ga else "L"
            r["won" if result == "W" else "drawn" if result == "D" else "lost"] += 1
            r["points"] += {"W": 3, "D": 1, "L": 0}[result]
            r["form"].append({"result": result, "match_id": m["id"], "matchday": m["md"], "opponent": opp,
                              "home": home, "score": f"{gf}-{ga}"})
    out = list(rows.values())
    for r in out:
        r["gd"] = r["gf"] - r["ga"]
        r["form"] = r["form"][-FORM:]
    out.sort(key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["club_id"]))
    for i, r in enumerate(out, start=1):
        r["position"] = i
    return out


def _clubs(matches: list[dict]) -> dict[str, dict[str, float]]:
    clubs: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for m in matches:
        for side, opp in (("h", "a"), ("a", "h")):
            c = clubs[m[side]]
            c["played"] += 1
            c["gf"] += m[f"{side}s"]
            c["ga"] += m[f"{opp}s"]
            c["xg"] += m[f"{side}xg"]
            c["xga"] += m[f"{opp}xg"]
            c["possession"] += m[f"{side}pos"]
            c["clean_sheets"] += m[f"{opp}s"] == 0
        for line in m["lines"]:
            c = clubs[line["c"]]
            for k in ("sh", "sot", "pa", "pc", "tkw", "int", "sav", "yc", "rc"):
                c[k] += line.get(k, 0)
            c["km"] += line.get("km", 0)
    out = {}
    for club, c in clubs.items():
        n = c["played"] or 1
        out[club] = {**{k: round(v, 2) if isinstance(v, float) and not v.is_integer() else int(v)
                        for k, v in c.items()},
                     "possession": round(c["possession"] / n, 3),
                     "pass_accuracy": round(c["pc"] / c["pa"], 3) if c["pa"] else 0.0,
                     "km_per_match": round(c["km"] / n, 1)}
    return out


def _players(matches: list[dict]) -> list[dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for m in matches:
        for line in m["lines"]:
            p = players.setdefault(line["p"], {"p": line["p"], "c": line["c"], "apps": 0, "st": 0, "xg": 0.0,
                                               "km": 0.0, "top": 0.0, **{k: 0 for k in SUMMED}})
            p["c"] = line["c"]
            p["apps"] += 1
            p["st"] += line.get("st", 0)
            p["xg"] += line.get("xg", 0.0)
            p["km"] += line.get("km", 0.0)
            p["top"] = max(p["top"], line.get("top", 0.0))
            for k in SUMMED:
                p[k] += line.get(k, 0)
    for p in players.values():
        p["xg"] = round(p["xg"], 2)
        p["km"] = round(p["km"], 1)
    return sorted(players.values(), key=lambda p: (-p["g"], -p["a"], p["p"]))


def routes(season: Season | None, service) -> list:
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    def through_of(request: Request, default: int) -> int:
        try:
            return max(0, min(int(request.query_params.get("through", default)), season.last_matchday()))
        except ValueError:
            return default

    def live_matchday() -> int | None:
        """The earliest matchday with a fixture being streamed right now, so its results aren't given away."""
        md = season.matchday_of()
        live = [md[m.match_id] for m in service.log.matches.values() if m.match_id in md and m.summary().get("live")]
        return min(live) if live else None

    async def overview(request: Request) -> Response:
        if season is None:
            return JSONResponse({"status": "off"}, status_code=404)
        if season.book() is None:
            return JSONResponse({"status": season.status, "error": season.error}, status_code=202)
        live = live_matchday()
        default = (live - 1) if live else season.last_matchday()
        view = await asyncio.to_thread(season.view, through_of(request, default))
        return JSONResponse({"status": "ready", "season": season.book().get("season"),
                             "last_matchday": season.last_matchday(), "default_through": default,
                             "live_matchday": live, **view})

    async def player(request: Request) -> Response:
        if season is None or season.book() is None:
            return JSONResponse({"status": season.status if season else "off"}, status_code=202)
        through = through_of(request, season.last_matchday())
        return JSONResponse({"through": through,
                             "matches": season.player_log(request.path_params["player_id"], through)})

    return [Route("/api/season", overview), Route("/api/season/players/{player_id}", player)]
