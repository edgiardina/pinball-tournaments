# Big Pinball Tournaments

This site shows the big pinball tournaments, the dates of each event, and the dates when
registration opens. It gives calendar feeds that people can subscribe to in Google, Apple, and
Outlook calendars.

The site is at https://wpprfarm.com/.

No person moderates the site. Each date comes from the organizer's website or from the IFPA
calendar. A script checks each date against the exact page text before the date goes on the site.

## How it works

| Step | What it does | Runs |
|---|---|---|
| `scripts/build_seeds.py` | Finds big recurring events in 3 years of IFPA results (WPPR value 75 and more). Writes `data/seed_candidates.csv`. | Once a year, by hand |
| `data/catalog.json` | The list of events to track. Edit it by hand. | When you add or remove an event |
| `scripts/discover.py` | Gets the upcoming IFPA tournaments and matches them to catalog events by name. With `--spc` (the nightly build only), it also finds the Stern Pro Circuit events from the IFPA tournament details. | Nightly, GitHub Action |
| `scripts/intake.py` | Reads an "Add an event" or "Suggest a website" issue. Changes `data/catalog.json` if the request passes the rules below. Replies and closes the issue. | When someone opens an issue |
| `scripts/fetch_pages.py` | Saves the text of each organizer page in `data/pages/`. | Nightly, Claude routine |
| Claude routine | Reads the changed pages and writes `data/claims/<event>.json`. See `ROUTINE.md`. | Nightly |
| `scripts/verify.py` | Accepts a claim only when its quote is on the page and the date agrees with the quote. Writes `data/state.json`. | After each claims update |
| `scripts/build.py` | Writes `site/index.html`, `site/events.json`, and the `.ics` feeds. | Each push and nightly |

## Rules that keep the data correct

- A quote must be on the saved page, word for word.
- The day, the month, and the time in a claim must be in the quote. If the quote has a year, the
  year must agree.
- A date with no year is accepted only when it is less than 120 days away. This stops old
  registration dates from last year's page.
- An event that has ended goes back to "Dates not announced yet".
- If the script cannot read a page, the last good values stay. If the page no longer says a thing,
  the thing goes off the site.

## Add an event

Open an issue with the "Add an event" form. A GitHub Action runs `scripts/intake.py` on it. No
person reviews the request. The script accepts the event only when all of these are true:

- A past edition is on IFPA with a WPPR value of 75 or more.
- No catalog event has the same name.
- The website is not a social site or a link shortener.

The event name comes from IFPA, not from the issue text.

The "Suggest a website" form adds a page to the nightly check for an event that is on the list.
The script accepts the page only when it loads, the nightly check can read it, and the page names
the event. Each event can have 4 pages at most.

## Stern Pro Circuit

`scripts/discover.py` marks an event as a Stern Pro Circuit (SPC) event when the IFPA details of
one of its tournaments say "Pro Circuit". Some directors do not write this. For those events, set
`"spc": <year>` in `data/catalog.json`. The site shows the newer of the two years.

## Run it on your computer

Python 3.12 is necessary. The scripts use only the standard library.

```
python scripts/discover.py      # needs IFPA_API_KEY, or the IfpaMaui user secrets on Ed's PC
python scripts/fetch_pages.py
python scripts/verify.py
python scripts/build.py
python -m http.server -d site 8765
python -m unittest discover -s tests
```

## Set up

1. Add the repository secret `IFPA_API_KEY`.
2. In the repository settings, set Pages to deploy from GitHub Actions.
3. Make a Claude cloud routine for this repository. Set it to run each night before 09:15 UTC.
   Use `ROUTINE.md` as its instructions.
