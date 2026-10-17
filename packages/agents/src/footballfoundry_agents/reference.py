"""Club and player names for prompts and templates, loaded from the generator's league.json."""

from __future__ import annotations

from pathlib import Path

from footballfoundry_schema import Club, League, Player


class Reference:
    """Lookup over a League. Works without one too, falling back to raw ids."""

    def __init__(self, league: League | None = None) -> None:
        self.league = league
        self._clubs = {c.club_id: c for c in league.clubs} if league else {}
        self._players = {p.player_id: p for p in league.players} if league else {}

    @classmethod
    def load(cls, path: str | Path | None) -> Reference:
        if not path or not Path(path).exists():
            return cls(None)
        return cls(League.model_validate_json(Path(path).read_text(encoding="utf-8")))

    def club(self, club_id: str | None) -> Club | None:
        return self._clubs.get(club_id or "")

    def player(self, player_id: str | None) -> Player | None:
        return self._players.get(player_id or "")

    def club_name(self, club_id: str | None) -> str:
        c = self.club(club_id)
        return c.name if c else (club_id or "?")

    def player_name(self, player_id: str | None) -> str:
        p = self.player(player_id)
        return p.name if p else (player_id or "?")

    def player_short(self, player_id: str | None) -> str:
        p = self.player(player_id)
        return p.short_name if p else (player_id or "?")

    def player_label(self, player_id: str | None) -> str:
        """`Name (P0124, ST, T01)`: the name for the reader, the id for the tools."""
        p = self.player(player_id)
        if not p:
            return player_id or "?"
        return f"{p.name} ({p.player_id}, {p.position}, {p.club_id})"
