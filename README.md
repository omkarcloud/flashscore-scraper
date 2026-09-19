# ⚽ Flashscore Scraper

Flashscore Scraper is a **free and open-source** scraper that gets you **unlimited** live scores and detailed sports data for free.

## ✨ What Can I Get?

- ⚽ **Live scores across 37 sports** — every match of the day, grouped by competition, updating as it plays
- 📊 **Full match detail** — lineups, xG, shot stats, player ratings, commentary & head-to-head
- 🏆 **Tables, fixtures & results** — standings, form, over/under, top scorers & decades of archives
- 💰 **Odds from 20+ bookmakers** — opening and current prices, per market and per country

## 🎥 Example: A Full Football Match

```json
{
  "id": "GCxZ2uHc",
  "link": "https://www.flashscore.com/match/GCxZ2uHc/",
  "sport": { "id": 1, "name": "Football", "slug": "football" },
  "status": "finished",
  "stage": "Finished",
  "start_time": "2025-05-18T17:00:00Z",
  "date": "2025-05-18",
  "home_score": 0,
  "away_score": 2,
  "winner": "away",
  "competition": {
    "name": "LaLiga",
    "link": "https://www.flashscore.com/football/spain/laliga/",
    "country": { "id": 176, "name": "Spain" }
  },
  "round": "Round 37",
  "home": {
    "id": "h8oAv4Ts",
    "name": "Sevilla",
    "short_name": "SEV",
    "link": "https://www.flashscore.com/team/sevilla/h8oAv4Ts/",
    "logo": "https://static.flashscore.com/res/image/data/G2BgO5Ar-tIT0KyhE.png",
    "country": { "id": 176, "name": "Spain" }
  },
  "away": {
    "id": "W8mj7MDD",
    "name": "Real Madrid",
    "short_name": "RMA",
    "link": "https://www.flashscore.com/team/real-madrid/W8mj7MDD/",
    "logo": "https://static.flashscore.com/res/image/data/A7kHoxZA-ttfpEDUq.png",
    "country": { "id": 176, "name": "Spain" }
  },
  "available_tabs": [
    "report", "statistics", "player_statistics", "lineups", "commentary",
    "odds", "h2h", "top_scorers", "missing_players", "standings", "news"
  ],
  "has_statistics": true,
  "has_player_statistics": true
}
```

*Trimmed for readability.*

`available_tabs` tells you exactly which of the 15 match endpoints will return data for this match, so you never waste a call.

## 🚀 Unlimited Free Flashscore Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/flashscore-scraper
cd flashscore-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/matches?sport=football"
```

```json
{
  "sport": { "id": 1, "name": "Football", "slug": "football" },
  "format": "matches",
  "match_count": 504,
  "competition_count": 237,
  "competitions": [
    {
      "competition": {
        "name": "Premier League",
        "link": "https://www.flashscore.com/football/england/premier-league/",
        "country": { "id": 198, "name": "England" }
      },
      "matches": [
        {
          "id": "ObA4gvV1",
          "link": "https://www.flashscore.com/match/ObA4gvV1/",
          "status": "scheduled",
          "start_time": "2026-09-18T19:00:00Z",
          "home": { "id": "xYe7DwID", "name": "Brentford", "short_name": "BRE" },
          "away": { "id": "4fGZN2oK", "name": "Chelsea", "short_name": "CHE" },
          "home_score": null,
          "away_score": null
        }
      ]
    }
  ]
}
```

All 50 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

50 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Matches by Day | `/matches` | Every match of a sport on one day, with live scores |
| Live Matches | `/matches/live` | Only what is playing right now |
| Live Score Updates | `/matches/live-updates` | Just the rows that changed — cheap to poll |
| Match Details | `/matches/details` | One match in full, plus the tabs it carries |
| Match Statistics | `/matches/statistics` | xG, possession, shots and passes by period |
| Match Lineups | `/matches/lineups` | Formations, starters, bench and player ratings |
| Match Summary | `/matches/summary` | Goals, cards, subs and VAR calls in order |
| Match Player Statistics | `/matches/player-statistics` | Per-player numbers with 90+ stat types |
| Match Head-to-Head | `/matches/h2h` | Previous meetings and each side's recent form |
| Match Odds | `/matches/odds` | Bookmaker prices compared, opening and current |
| Match Commentary | `/matches/commentary` | Minute-by-minute text with the running score |
| Match Point by Point | `/matches/point-by-point` | Every game of a tennis, darts or snooker match |
| Match Standings / Top Scorers | `/matches/standings`, `/matches/top-scorers` | The table and scoring chart from a match ID |
| Match Missing Players | `/matches/missing-players` | Injuries and suspensions with the reason |
| Match Report / News | `/matches/report`, `/matches/news` | The write-up and the stories around a match |
| Match Broadcasts | `/matches/broadcasts` | TV channels and streams carrying a match |
| Search | `/search` | Find any team, player or competition by name |
| Competition Standings | `/tournaments/standings` | Tables, form, over/under and half-time/full-time |
| Competition Results / Fixtures | `/tournaments/results`, `/tournaments/fixtures` | A whole season, paged |
| Competition Details / Seasons / Archive | `/tournaments/details`, `/tournaments/seasons`, `/tournaments/archive` | Season span, stages and every past season |
| Competition Top Scorers / Draw | `/tournaments/top-scorers`, `/tournaments/draw` | Scoring chart and the knockout bracket |
| Competition Standings Types / News | `/tournaments/standings-types`, `/tournaments/news` | Which tables exist, and the latest stories |
| Team Details | `/teams/details` | Club card with crest, stadium and capacity |
| Team Results / Fixtures | `/teams/results`, `/teams/fixtures` | Every match a club has played or will play |
| Team Squad | `/teams/squad` | Full squad with appearances, goals and cards |
| Team Transfers | `/teams/transfers` | Arrivals and departures with dates and fees |
| Team Standings / Top Scorers / News | `/teams/standings`, `/teams/top-scorers`, `/teams/news` | The club's table, scorers and headlines |
| Player Details | `/players/details` | Age, club, market value, contract and career |
| Player Career / Transfers / Injuries | `/players/career`, `/players/transfers`, `/players/injuries` | Season by season, every move, every injury |
| Player News | `/players/news` | Latest headlines about one player |
| Rankings | `/rankings`, `/rankings/data` | ATP and WTA tables with points and movement |
| News | `/news`, `/news/article` | The sport's news front page and full articles |
| Sports / Countries / Competitions | `/sports`, `/countries`, `/tournaments` | The catalogue, with the IDs everything else takes |
| Resolve URL | `/resolve` | Turn any flashscore.com link into the IDs to call |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-flashscore-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-flashscore-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-flashscore-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/matches/details",
    params={"match": "GCxZ2uHc"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Flashscore Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Flashscore%20Scraper%20API.)

[![Ask Us by Email about Flashscore Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Flashscore%20Scraper%20API&body=I%20need%20help%20using%20the%20Flashscore%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**IMDb Scraper**](https://github.com/omkarcloud/imdb-scraper) — unlimited IMDb titles, cast, reviews & charts
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/flashscore-scraper)

Star the repo ⭐ and become a star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/flashscore-scraper)
