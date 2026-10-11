import asyncio

from footballfoundry_agents.sources import FollowSource


def test_follow_waits_catches_up_and_restarts(match, tmp_path):
    path = tmp_path / "stream.jsonl"
    lines = [e.model_dump_json() + "\n" for e in match.events[:30]]
    seen: list[int] = []

    async def handle(e):
        seen.append(e.seq)

    async def scenario():
        task = asyncio.create_task(FollowSource(path, poll_s=0.01).run(handle))
        await asyncio.sleep(0.1)  # the service started first: the file doesn't exist yet
        assert seen == []
        with path.open("w", encoding="utf-8", newline="\n") as f:
            f.write("".join(lines[:10]) + lines[10][:25])  # ends half-way through a line
            f.flush()
            await asyncio.sleep(0.1)
            assert seen == list(range(1, 11))
            f.write(lines[10][25:] + "".join(lines[11:20]))
            f.flush()
            await asyncio.sleep(0.1)
        assert seen == list(range(1, 21))
        path.write_text("".join(lines[:5]), encoding="utf-8")  # the stream was restarted
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(scenario())
    assert seen == list(range(1, 21)) + list(range(1, 6))
