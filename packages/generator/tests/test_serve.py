import json
import threading
import urllib.request

import pytest

from footballfoundry.serve import serve


@pytest.fixture(scope="module")
def server(league, results):
    srv = serve(league, results[:2], port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")


def sse_messages(body):
    for block in body.strip().split("\n\n"):
        kind = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if kind:
            yield kind, data


def test_viewer_files_are_served(server):
    status, ctype, body = get(server + "/")
    assert status == 200 and ctype.startswith("text/html") and "viewer.js" in body
    for name in ("viewer.js", "viewer.css"):
        assert get(f"{server}/{name}")[0] == 200


def test_unknown_paths_404(server):
    for path in ("/nope.js", "/..%2Fserve.py", "/api/events?match=XXXX"):
        with pytest.raises(urllib.error.HTTPError) as err:
            get(server + path)
        assert err.value.code == 404


def test_session_has_lineups_but_no_final_score(server, results):
    _, _, body = get(server + "/api/session")
    session = json.loads(body)
    assert [m["match_id"] for m in session["matches"]] == [r.info.match_id for r in results[:2]]
    match = session["matches"][0]
    assert "home_score" not in match and "away_score" not in match
    starters = [entry for entry in match["lineups"] if entry["starter"]]
    assert len(starters) == 22 and all("base_x" in entry for entry in starters)
    assert all(entry["player_id"] in session["players"] for entry in match["lineups"])


def test_event_stream_replays_whole_match(server, results):
    r = results[0]
    _, ctype, body = get(f"{server}/api/events?match={r.info.match_id}&speed=0")
    assert ctype == "text/event-stream"
    messages = list(sse_messages(body))
    assert messages[0][0] == "reset" and messages[-1][0] == "end"
    events = [d for kind, d in messages if kind == "event"]
    assert [d["e"]["seq"] for d in events] == list(range(1, len(r.events) + 1))
    assert events[-1]["e"]["home_score"] == r.info.home_score


def test_event_stream_from_marks_catchup(server, results):
    r = results[0]
    _, _, body = get(f"{server}/api/events?match={r.info.match_id}&speed=600&from=5390")
    events = [d for kind, d in sse_messages(body) if kind == "event"]
    assert len(events) == len(r.events)
    assert all(d["catchup"] for d in events if d["t"] < 5390)
    assert not any(d["catchup"] for d in events if d["t"] >= 5390)
