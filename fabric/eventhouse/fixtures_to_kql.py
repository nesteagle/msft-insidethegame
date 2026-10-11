"""Turn the generator's fixtures.json into a KQL command that loads the Fixtures table.

    footballfoundry world                                   # writes data/fixtures.json
    python fabric/eventhouse/fixtures_to_kql.py data/fixtures.json > fixtures.kql

Paste fixtures.kql into a KQL queryset on the footballfoundry database and run it.
"""

import json
import sys


def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        fixtures = json.load(f)
    rows = ",\n".join(
        f'    "{x["match_id"]}", {x["matchday"]}, datetime({x["kickoff"]}), '
        f'"{x["home_club_id"]}", "{x["away_club_id"]}"'
        for x in fixtures
    )
    print(".set-or-replace Fixtures <|\ndatatable(match_id: string, matchday: int, kickoff: datetime, "
          f"home_club_id: string, away_club_id: string) [\n{rows}\n]")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/fixtures.json")
