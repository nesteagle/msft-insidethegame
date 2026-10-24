"""Render the visuals.kql pitch tiles locally, without an Eventhouse, to check how they look.

    uv run --with pandas --with plotly --with kaleido python fabric/eventhouse/preview_visuals.py [events.jsonl]

The default events file is data/events/M0001.jsonl.

The KQL data prep is redone in pandas; the Python renderer is taken from FF_Pitch in visuals.kql and run as is,
so the PNGs (written to fabric/visuals/) show what the Real-Time Dashboard tiles draw.
"""

import re
import sys
from pathlib import Path

import pandas as pd
import plotly.io as pio

HERE = Path(__file__).parent
KQL = (HERE / "visuals.kql").read_text(encoding="utf-8")
CODE = re.search(r"let code = ```\n(.*?)```;", KQL, re.DOTALL).group(1)
COLUMNS = ["layer", "series", "x", "y", "end_x", "end_y", "size", "color", "symbol", "label", "hover"]


def pitch(layers: pd.DataFrame, title: str, out: Path) -> None:
    scope = {"df": layers[COLUMNS].copy(), "kargs": {"title": title}}
    exec(CODE, scope)  # noqa: S102 - FF_Pitch's Python, from visuals.kql
    fig = pio.from_json(scope["result"]["plotly"][0])
    fig.update_layout(paper_bgcolor="white")  # a dashboard tile has its own background
    fig.write_image(out, width=900, height=640, scale=2)
    print(out)


def shot_map(ev: pd.DataFrame, m: str, out: Path) -> None:
    home, away = ev[(ev.type == "period_start") & (ev.period == 1)].sort_values("seq").team_id.tolist()[:2]
    s = ev[ev.type == "shot"].copy()
    mirror = s.team_id != home
    s.loc[mirror, "x"], s.loc[mirror, "y"] = 100 - s.x[mirror], 100 - s.y[mirror]
    s["series"] = s.outcome.map({"goal": "Goal", "saved": "Saved", "blocked": "Blocked"}).fillna("Off target")
    style = {"Goal": ("#ffd400", "star"), "Saved": ("#ffffff", "circle"), "Blocked": ("#9e9e9e", "square"),
             "Off target": ("#e53935", "x")}
    s["color"], s["symbol"] = zip(*s.series.map(style))
    s = s.assign(layer="point", end_x=None, end_y=None, size=10 + 50 * s.xg, label="",
                 hover=s.team_id + " " + s.player_id + " " + s.clock + " · xG " + s.xg.round(2).astype(str))
    pitch(s, f"Shots {m}: {home} → · ← {away}", out)


def pass_network(ev: pd.DataFrame, m: str, team: str, out: Path, min_passes: int = 3) -> None:
    ev = ev[ev.team_id == team]
    subs = ev[ev.type == "substitution"].seq
    until = subs.min() if len(subs) else 1_000_000
    lineup = ev[(ev.type == "period_start") & (ev.period == 1)].sort_values("seq").qualifiers.iloc[0]["lineup"]
    roles = pd.DataFrame(lineup)[["player_id", "role"]]
    p = ev[(ev.type == "pass") & (ev.outcome == "complete") & (ev.seq < until)]
    touches = pd.concat([p[["player_id", "x", "y"]],
                         p[["related_player_id", "end_x", "end_y"]].set_axis(["player_id", "x", "y"], axis=1)])
    nodes = (touches.groupby("player_id").agg(x=("x", "mean"), y=("y", "mean"), touches=("x", "size"))
             .reset_index().merge(roles, on="player_id"))
    pair = (p[["player_id", "related_player_id"]].apply(sorted, axis=1, result_type="expand")
            .set_axis(["a", "b"], axis=1))
    links = pair.value_counts().rename("n").reset_index().query("n >= @min_passes")
    pos = nodes.set_index("player_id")
    links = links[links.a.isin(pos.index) & links.b.isin(pos.index)]
    lines = pd.DataFrame({
        "layer": "line", "series": "Passes", "x": pos.x[links.a].values, "y": pos.y[links.a].values,
        "end_x": pos.x[links.b].values, "end_y": pos.y[links.b].values, "size": 0.5 + links.n.values / 2,
        "color": "rgba(255,255,255,0.55)", "symbol": "", "label": "",
        "hover": (links.a + " ↔ " + links.b + ": " + links.n.astype(str) + " passes").values})
    points = nodes.assign(layer="point", series="Players", end_x=None, end_y=None,
                          size=18 + nodes.touches.clip(upper=120) / 6, color="#1f3a93", symbol="circle",
                          label=nodes.role, hover=nodes.player_id + " (" + nodes.role + ")")
    pitch(pd.concat([lines, points[COLUMNS]]), f"Pass network {m}: {team} → (XI until first sub)", out)


def defensive_actions(ev: pd.DataFrame, m: str, team: str, out: Path) -> None:
    names = {"tackle": "Tackles", "interception": "Interceptions", "block": "Blocks", "clearance": "Clearances",
             "ball_recovery": "Recoveries"}
    style = {"Tackles": ("#e53935", "triangle-up"), "Interceptions": ("#283593", "square"),
             "Blocks": ("#8e24aa", "diamond"), "Clearances": ("#00acc1", "triangle-down"),
             "Recoveries": ("#ffb300", "circle")}
    d = ev[(ev.team_id == team) & ev.type.isin(list(names))].copy()
    d["series"] = d.type.map(names)
    d["color"], d["symbol"] = zip(*d.series.map(style))
    d = d.assign(layer="point", end_x=None, end_y=None, size=11.0, label="",
                 hover=d.player_id + " " + d.clock + " " + d.type)
    pitch(d, f"Defensive actions {m}: {team} →", out)


def main(path: str) -> None:
    ev = pd.read_json(path, lines=True, dtype={"qualifiers": object})
    m = ev.match_id.iloc[0]
    home = ev[ev.type == "period_start"].sort_values("seq").team_id.iloc[0]
    out = HERE.parent / "visuals"
    out.mkdir(exist_ok=True)
    shot_map(ev, m, out / "shot_map.png")
    pass_network(ev, m, home, out / "pass_network.png")
    defensive_actions(ev, m, home, out / "defensive_actions.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/events/M0001.jsonl")
