"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. See README.md → "Exploring Parameters" for the full endpoint list.
"""
import json
import os

from flashscore.matches import get_details, get_matches
from flashscore.tournaments import get_standings

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # every match of a sport today, with live scores, grouped by competition
    save("matches_football_today.json", get_matches("football"))

    # one match in full — a Flashscore match ID or any match URL
    save("match_GCxZ2uHc.json", get_details("GCxZ2uHc"))

    # the league table — a competition path or a "<tournament_id>:<stage_id>" pair
    save("standings_laliga.json", get_standings("/football/spain/laliga/"))
