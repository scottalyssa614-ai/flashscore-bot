#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flashscore Daily Pattern Filter — Telegram Bot
==============================================

A long-running Telegram bot. It polls Telegram for your messages, and when you
send it a date in the form  dd-mm-yyyy  it scrapes Flashscore for every football
match scheduled that day, pulls the league table for each match, and replies in
the same chat with the matches that fit the N pattern below.

For every N from N_MIN to N_MAX (currently 1 to 40) a match counts when, for
that same N:

    1. BOTH teams have played exactly N league games this season.
    2. The absolute points gap between the two teams is exactly N.
    3. Home GF + Home GA + Away GF + Away GA >= 2 * N.

So N=3 means 3 games each, a 3-point gap and at least 6 combined goals; N=40
means 40 games each, a 40-point gap and at least 80 combined goals. Fixtures and
tables are fetched once per date, then every match is offered to all of the
variants -- and since games played is a single number, a match can only ever
satisfy one of them. Results come back grouped by the N that matched; N values
with no hits are left out entirely. Matches whose league table cannot be found
are counted in the summary line but not listed.

Running it
----------
Just start it. There are no required flags — the date comes in via Telegram.

    python filter_bot.py

On Replit, that is what the Run button does (see .replit). After it starts you
never need the shell again: message the bot a date and it answers.

Talking to it
-------------
    15-09-2026      -> scans that date and replies with the report, grouped by N
    15-09-2026 16-09-2026
                    -> scans both, one report each
    /help or help   -> usage
    anything else   -> a short "send me dd-mm-yyyy" reply (it never goes silent)

Telegram setup (once)
---------------------
1. Message @BotFather on Telegram -> /newbot -> follow the prompts.
   Copy the token it gives you (looks like "123456789:AAE...xyz").
2. Get your numeric chat_id: message @userinfobot, or send your new bot any
   message and open  https://api.telegram.org/bot<TOKEN>/getUpdates
   and read "chat":{"id": ...}. For a group, add the bot to the group and use
   the negative group id. (You only need chat_id if you set --restrict-chat-id;
   by default the bot answers anyone who messages it.)
3. Provide the token as an environment variable (on Replit: the Secrets tool):
       TELEGRAM_BOT_TOKEN=123456789:AAE...xyz
   or in a file called telegram_config.json next to this script:
       {"bot_token": "123456789:AAE...xyz", "chat_id": "987654321"}
   The --token / --chat-id flags override both.

Flashscore limitation (theirs, not this script's)
-------------------------------------------------
Flashscore's daily fixture feed is a rolling window of TODAY +/- 7 DAYS. Outside
that window it returns an empty response, so there is nothing to filter; the bot
replies and says so plainly. League tables are always the *current* table —
Flashscore serves no historical standings — so scanning a past date compares
that day's fixtures against today's table.

Optional flags: --token, --chat-id, --restrict-chat-id, --timezone,
--include-started, --delay, --poll-timeout, --port, --no-http, --once
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import html
import json
import os
import queue
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_URL = "https://www.flashscore.com"
FEED_URL = BASE_URL + "/x/feed/"

# Static signature Flashscore's own frontend sends with every feed request.
FEED_SIGNATURE = "SW9D1eZo"
PROJECT_TYPE_ID = 2          # flashscore.com
SPORT_ID_FOOTBALL = 1
LANGUAGE = "en"
TIMEZONE_SHIFT = 0           # 0 = day boundaries at UTC midnight

# Day-feed window Flashscore actually serves (verified: -8 / +8 return "0").
MAX_DAY_OFFSET = 7

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

REQUEST_DELAY_SECONDS = 0.15     # polite pacing between Flashscore requests
REQUEST_TIMEOUT = 25             # seconds
MAX_ATTEMPTS = 3                 # per HTTP request
RETRY_BACKOFF = 1.5              # seconds, doubled per retry

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_CHARS = 4096
POLL_TIMEOUT_SECONDS = 25        # Telegram long poll (server holds the request)
POLL_ERROR_WAIT = 5              # seconds to wait after a polling failure
BETWEEN_MESSAGE_PAUSE = 0.4      # stay under Telegram's ~1 msg/second limit

# ---- the filter ---------------------------------------------------------- #
# Every N from N_MIN to N_MAX is checked, each with its own thresholds:
#     N=3 -> both teams exactly 3 games played, exactly a 3-point gap,
#            and the four goal figures adding up to at least 2*3 = 6.
# A match's games-played is a single number, so in practice it can only ever
# satisfy one N -- but every match is still offered to all of them.
N_MIN = 1
N_MAX = 40
GOALS_MULTIPLIER = 2           # minimum combined goals for a given N = N * this
# --------------------------------------------------------------------------- #

# Match status codes used by the feed (AB field).
STATUS_SCHEDULED = "1"
STATUS_IN_PLAY = "2"
STATUS_FINISHED = "3"
STATUS_LABELS = {
    "1": "scheduled",
    "2": "in play",
    "3": "finished",
    "4": "postponed",
    "5": "cancelled",
    "6": "abandoned",
    "7": "walkover",
    "8": "retired",
    "9": "award",
    "10": "delayed",
    "11": "not started",
    "12": "interrupted",
    "13": "after penalties",
}

HELP_WORDS = {"help", "/help", "start", "/start", "/commands", "commands", "?", "/?"}

def describe_n(n: int) -> str:
    """One-line description of a filter variant: 'N=3 (3 games, 3-pt gap, goals >= 6)'."""
    return (f"N={n} ({n} game{'s' if n != 1 else ''}, {n}-pt gap, "
            f"goals \u2265 {GOALS_MULTIPLIER * n})")


USAGE_TEXT = (
    "<b>Flashscore daily pattern filter</b>\n"
    "Send me a date as <b>dd-mm-yyyy</b> and I will scan every football match "
    "Flashscore lists for that day.\n\n"
    "Example: <code>15-09-2026</code>\n\n"
    "A match is reported when <b>all three</b> hold for the same N:\n"
    "  • both teams have played exactly N league games\n"
    "  • the points gap between them is exactly N\n"
    f"  • their four goal figures add up to at least {GOALS_MULTIPLIER}\u00d7N\n\n"
    f"I check every N from {N_MIN} to {N_MAX} in one pass and group the results by "
    "the N that matched, skipping any N with no hits.\n\n"
    f"Flashscore only serves today +/- {MAX_DAY_OFFSET} days, so dates outside "
    "that window cannot be scanned. Tables are always the current ones."
)

INVALID_INPUT_TEXT = (
    "I can only scan a date.\n\n"
    "Send it as <b>dd-mm-yyyy</b>, for example <code>15-09-2026</code>.\n"
    "Send <code>help</code> for details."
)


class BotError(Exception):
    """Fatal, user-facing problem (bad token, unusable timezone, ...)."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def log(message: str) -> None:
    """Progress to stdout, timestamped, so Replit's console reads like a log."""
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def parse_feed_record(record: str) -> dict:
    """Turn one '~'-separated feed record into a dict.

    Feed format is  KEY÷value¬KEY÷value¬...  with '~' between records.
    """
    fields = {}
    for chunk in record.split("¬"):
        if "÷" not in chunk:
            continue
        key, _, value = chunk.partition("÷")
        fields[key.strip()] = value
    return fields


def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalise_name(name: str) -> str:
    """Loose name key used only as a fallback when team IDs do not match."""
    name = (name or "").lower()
    name = re.sub(r"\b(afc|cf|fc|sc|ac|cd|sd|ud|ca|club|de|del|the)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


# --------------------------------------------------------------------------- #
# Flashscore HTTP layer
# --------------------------------------------------------------------------- #

class FeedClient:
    """Thin, polite, retrying client for Flashscore's internal feed."""

    def __init__(self, delay: float = REQUEST_DELAY_SECONDS, quiet: bool = False):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "X-Fsign": FEED_SIGNATURE,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE_URL + "/",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.delay = delay
        self.quiet = quiet
        self.request_count = 0
        self._last_request = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_request = time.monotonic()

    def get(self, feed_name: str):
        """Fetch a feed by name. Returns body text, or None on failure/empty."""
        url = FEED_URL + feed_name
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                self.request_count += 1
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:      # timeout, DNS, reset...
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
                    continue
                if not self.quiet:
                    log(f"  ! {feed_name}: network error ({exc.__class__.__name__})")
                return None

            if response.status_code == 200:
                body = response.text
                # Flashscore answers "0" (or "") when a feed has no data.
                return body if body and body.strip() not in ("0", "") else None
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)) * 2)
                    continue
            if not self.quiet:
                log(f"  ! {feed_name}: HTTP {response.status_code}")
            return None

        if not self.quiet and last_error:
            log(f"  ! {feed_name}: giving up after {MAX_ATTEMPTS} attempts ({last_error})")
        return None


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

class TeamStats:
    __slots__ = ("name", "team_id", "played", "points", "goals_for",
                 "goals_against", "position", "matched_by")

    def __init__(self, name, team_id, played, points, goals_for, goals_against,
                 position=None, matched_by="id"):
        self.name = name
        self.team_id = team_id
        self.played = played
        self.points = points
        self.goals_for = goals_for
        self.goals_against = goals_against
        self.position = position
        self.matched_by = matched_by

    @property
    def total_goals(self) -> int:
        return self.goals_for + self.goals_against

    def line(self, label: str) -> str:
        return (f"{label}: {self.name} — {self.played} played, {self.points} pts, "
                f"{self.goals_for} scored, {self.goals_against} conceded")


class Match:
    def __init__(self, match_id, league, country, stage_id, home_name, away_name,
                 home_id, away_id, kickoff_utc, status_code, round_name):
        self.match_id = match_id
        self.league = league
        self.country = country
        self.stage_id = stage_id
        self.home_name = home_name
        self.away_name = away_name
        self.home_id = home_id
        self.away_id = away_id
        self.kickoff_utc = kickoff_utc
        self.status_code = status_code
        self.round_name = round_name
        self.home_stats = None
        self.away_stats = None

    @property
    def status(self) -> str:
        return STATUS_LABELS.get(self.status_code, f"status {self.status_code}")

    @property
    def point_gap(self) -> int:
        return abs(self.home_stats.points - self.away_stats.points)

    @property
    def goals_sum(self) -> int:
        return self.home_stats.total_goals + self.away_stats.total_goals


# --------------------------------------------------------------------------- #
# Step 1 — the day's fixture list
# --------------------------------------------------------------------------- #

def fetch_day_matches(client: FeedClient, day_offset: int):
    """Fetch every football match Flashscore lists for one UTC day."""
    body = client.get(
        f"f_{SPORT_ID_FOOTBALL}_{day_offset}_{TIMEZONE_SHIFT}_{LANGUAGE}_{PROJECT_TYPE_ID}"
    )
    if not body:
        return []

    matches = []
    league = country = stage_id = ""
    seen = set()

    for record in body.split("~"):
        record = record.strip()
        if not record:
            continue

        if record.startswith("ZA"):                      # tournament header
            fields = parse_feed_record(record)
            league = fields.get("ZA", "").strip()
            stage_id = fields.get("ZEE", "").strip()
            country = fields.get("ZY", "").strip()
            continue

        if not record.startswith("AA"):                  # not a match record
            continue

        fields = parse_feed_record(record)
        match_id = fields.get("AA", "").strip()
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)

        kickoff = to_int(fields.get("AD"))
        if not kickoff:
            continue

        matches.append(Match(
            match_id=match_id,
            league=league or "Unknown competition",
            country=country,
            stage_id=stage_id,
            home_name=(fields.get("AE") or fields.get("CX") or "?").strip(),
            away_name=(fields.get("AF") or fields.get("WN") or "?").strip(),
            home_id=fields.get("PX", "").strip(),
            away_id=fields.get("PY", "").strip(),
            kickoff_utc=_dt.datetime.fromtimestamp(kickoff, _dt.timezone.utc),
            status_code=fields.get("AB", "").strip(),
            round_name=fields.get("ER", "").strip(),
        ))

    return matches


def collect_matches_for_date(client: FeedClient, target_date: _dt.date, tz):
    """All matches kicking off on `target_date` in timezone `tz`.

    The feed buckets days by UTC, so a local day can straddle two UTC days.
    We fetch the matching UTC day plus one day either side and then keep only
    matches whose kickoff really falls on the requested local date.
    """
    start_local = _dt.datetime.combine(target_date, _dt.time.min, tzinfo=tz)
    end_local = start_local + _dt.timedelta(days=1)
    start_utc = start_local.astimezone(_dt.timezone.utc)
    end_utc = end_local.astimezone(_dt.timezone.utc)

    today_utc = _dt.datetime.now(_dt.timezone.utc).date()
    base_offset = (start_utc.date() - today_utc).days

    collected = {}
    for shift in (-1, 0, 1):
        offset = base_offset + shift
        if abs(offset) > MAX_DAY_OFFSET:
            continue
        for match in fetch_day_matches(client, offset):
            if start_utc <= match.kickoff_utc < end_utc:
                collected[match.match_id] = match

    return sorted(collected.values(), key=lambda m: (m.kickoff_utc, m.league))


# --------------------------------------------------------------------------- #
# Step 2 — league tables
# --------------------------------------------------------------------------- #

def parse_standings(body: str) -> dict:
    """Parse a standings feed into id/name -> TeamStats."""
    by_id = {}
    by_name = {}
    position = None

    for record in body.split("~"):
        record = record.strip()
        if not record.startswith("TR"):
            continue

        fields = parse_feed_record(record)
        name = fields.get("TN", "").strip()
        team_id = fields.get("TI", "").strip()
        if not name and not team_id:
            continue

        position = to_int(fields.get("TR"), position)
        played = to_int(fields.get("TM"))
        goals = fields.get("TG", "")
        goals_for = goals_against = None
        if ":" in goals:
            gf, _, ga = goals.partition(":")
            goals_for, goals_against = to_int(gf), to_int(ga)

        points = to_int(fields.get("TP"))
        if points is None:
            # Some competitions do not send TP; derive it from W/D. TPK is the
            # points a win is worth in this competition (usually "3.00").
            wins = to_int(fields.get("TW"), 0)
            draws = to_int(fields.get("TDR"), 0)
            try:
                per_win = float(fields.get("TPK") or 0)
            except ValueError:
                per_win = 0.0
            if per_win > 0:
                points = int(round(wins * per_win + draws * (per_win / 3.0)))
            elif "TW" in fields and "TDR" in fields:
                points = wins * 3 + draws

        if played is None or goals_for is None or goals_against is None or points is None:
            continue

        stats = TeamStats(name, team_id, played, points, goals_for,
                          goals_against, position)
        if team_id:
            by_id[team_id] = stats
        if name:
            by_name.setdefault(normalise_name(name), stats)
        position = (position or 0) + 1

    return {"by_id": by_id, "by_name": by_name}


def find_team(table: dict, team_id: str, team_name: str):
    """Locate a team in a table: exact id first, loose name as a fallback."""
    if team_id and team_id in table["by_id"]:
        return table["by_id"][team_id]
    key = normalise_name(team_name)
    if key and key in table["by_name"]:
        stats = table["by_name"][key]
        return TeamStats(stats.name, stats.team_id, stats.played, stats.points,
                         stats.goals_for, stats.goals_against, stats.position,
                         matched_by="name")
    return None


def standings_for_match(client: FeedClient, match: Match, cache: dict):
    """League table for this match's competition, cached per competition.

    Every match in one competition shares a table, so the competition (stage id)
    is the cache key — one request per league, not per match. A table that does
    not contain this match's two teams is NOT trusted for the rest of the league
    (Flashscore sometimes answers with an unrelated table for cups), so that
    case is cached per match instead and the league stays retryable.
    """
    stage_key = f"stage:{match.stage_id}" if match.stage_id else None
    match_key = f"match:{match.match_id}"
    for key in (stage_key, match_key):
        if key and key in cache:
            return cache[key]

    # tableId 1 = overall table
    body = client.get(f"df_to_{PROJECT_TYPE_ID}_{match.match_id}_1")
    table = parse_standings(body) if body else None
    if table and not table["by_id"] and not table["by_name"]:
        table = None                      # feed answered but held no table

    usable = bool(table) and (
        (match.home_id and match.home_id in table["by_id"])
        or (match.away_id and match.away_id in table["by_id"])
    )
    cache[match_key] = table
    if usable and stage_key:
        cache[stage_key] = table
    return table


# --------------------------------------------------------------------------- #
# Step 3 — the filter
# --------------------------------------------------------------------------- #

def evaluate(match: Match, n: int):
    """Test one match against the filter variant for a single N.

    For that N the match must have: both teams on exactly N games played, an
    absolute points gap of exactly N, and the four goal figures (home GF + home
    GA + away GF + away GA) adding up to at least GOALS_MULTIPLIER * N.

    Returns (passed, reason).
    """
    home, away = match.home_stats, match.away_stats
    min_goals = GOALS_MULTIPLIER * n

    if home.played != n or away.played != n:
        return False, (f"{home.played}/{away.played} games played "
                       f"(need exactly {n} each)")
    if match.point_gap != n:
        return False, f"point gap {match.point_gap} (need exactly {n})"
    if match.goals_sum < min_goals:
        return False, f"goals sum {match.goals_sum} (need >= {min_goals})"
    return True, f"gap {match.point_gap}, goals sum {match.goals_sum}"


def n_variants():
    """Every N to check, in ascending order."""
    return range(N_MIN, N_MAX + 1)


def applicable_n(match: Match):
    """The only N a match could possibly satisfy, or None if it is out of range."""
    played = match.home_stats.played
    if played != match.away_stats.played:
        return None
    if not (N_MIN <= played <= N_MAX):
        return None
    return played


# --------------------------------------------------------------------------- #
# Step 4 — the report
# --------------------------------------------------------------------------- #

def local_time(match: Match, tz) -> str:
    return match.kickoff_utc.astimezone(tz).strftime("%H:%M")


def build_report(target_date: _dt.date, tz, tz_name: str, passed_by_n,
                 total_matches: int, skipped, candidates: int, requests_made: int,
                 seconds: float, utc_label: str = "", note: str = "") -> str:
    """The report, grouped by the N that matched, as Telegram HTML.

    passed_by_n maps N -> [Match, ...]. N values with no hits are left out
    entirely so the message only ever shows the patterns that actually fired.
    Skipped matches are counted in the summary line but not listed.
    """
    esc = html.escape
    # Only N values that actually produced hits get a section; an N with an
    # empty list is dropped here so callers cannot accidentally print a header
    # with nothing under it.
    passed_by_n = {n: list(ms) for n, ms in (passed_by_n or {}).items() if ms}
    hit_ns = sorted(passed_by_n)
    total_hits = sum(len(passed_by_n[n]) for n in hit_ns)

    lines = []
    lines.append("<b>FLASHSCORE DAILY PATTERN FILTER</b>")
    when = tz_name + (f", {utc_label}" if utc_label else "")
    lines.append(f"Date: <b>{target_date.strftime('%d-%m-%Y')}</b> ({esc(when)})")
    lines.append(
        f"Filter, checked for every N from {N_MIN} to {N_MAX}: both teams exactly "
        f"N games played | points gap exactly N | combined GF+GA &gt;= "
        f"{GOALS_MULTIPLIER}&#215;N"
    )
    lines.append(
        f"Scanned {total_matches} scheduled match"
        f"{'es' if total_matches != 1 else ''}; "
        f"{total_hits} matched; {len(skipped)} skipped without data."
    )
    if hit_ns:
        lines.append("Hits: " + ", ".join(
            f"N={n} ({len(passed_by_n[n])})" for n in hit_ns))
    if note:
        lines.append(f"<i>{esc(note)}</i>")

    if not total_hits:
        lines.append("")
        lines.append("<b>No matches passed the filter on this date.</b>")
        if total_matches == 0:
            lines.append("Flashscore listed no scheduled football matches for that day.")
        else:
            lines.append(
                f"{total_matches - len(skipped)} of them had a usable league table, "
                f"but none satisfied all three conditions for any N from {N_MIN} to "
                f"{N_MAX} ({candidates} had both teams on the same games played "
                f"within that range, so only those could ever have matched)."
            )
    else:
        for n in hit_ns:
            matches = passed_by_n[n]
            lines.append("")
            lines.append(f"<b>{esc(describe_n(n))}:</b>")
            for index, match in enumerate(matches, start=1):
                lines.append(f"  {index}. {esc(match.league)}")
                lines.append(f"     {esc(match.home_name)} vs {esc(match.away_name)}")
                detail = f"     Kickoff: {local_time(match, tz)} ({esc(tz_name)})"
                if match.round_name:
                    detail += f" — {esc(match.round_name)}"
                lines.append(detail)
                lines.append("     " + esc(
                    match.home_stats.line("Home").replace("—", "-")))
                lines.append("     " + esc(
                    match.away_stats.line("Away").replace("—", "-")))
                lines.append(
                    f"     <b>gap {match.point_gap}, goals sum {match.goals_sum}</b>"
                    f"  ({match.home_stats.goals_for}+{match.home_stats.goals_against}"
                    f"+{match.away_stats.goals_for}+{match.away_stats.goals_against}"
                    f", need \u2265 {GOALS_MULTIPLIER * n})"
                )
                lines.append(f"     https://www.flashscore.com/match/{match.match_id}/")

    # The per-match skip breakdown is deliberately not sent: the summary line
    # above already carries the total ("N skipped without data"), and listing
    # hundreds of cup/youth matches with no table buried the actual hits.
    # `skipped` is still collected and logged locally for debugging.

    lines.append("")
    lines.append(
        f"<i>{requests_made} requests to Flashscore in {seconds:.0f}s. "
        f"Source: flashscore.com internal feed.</i>"
    )
    return "\n".join(lines)


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS):
    """Split on line boundaries so no Telegram message exceeds the limit."""
    if len(text) <= limit:
        return [text]

    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        # A single line longer than the limit gets hard-wrapped.
        while len(line) > limit - 20:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            chunks.append(line[:limit - 20])
            line = line[limit - 20:]
        extra = len(line) + (1 if current else 0)
        if current_len + extra > limit - 20:
            chunks.append("\n".join(current))
            current, current_len = [], 0
            extra = len(line)
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current))

    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"({i}/{total})\n{chunk}" for i, chunk in enumerate(chunks, start=1)]
    return chunks


# --------------------------------------------------------------------------- #
# Telegram API
# --------------------------------------------------------------------------- #

class TelegramAPI:
    """Minimal Telegram Bot API client: getUpdates + sendMessage."""

    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self.token, method=method)

    def call(self, method: str, payload: dict = None, timeout: int = REQUEST_TIMEOUT):
        """Return (ok, result_or_error, status_code)."""
        try:
            response = self.session.post(self._url(method), data=payload or {},
                                         timeout=timeout)
        except requests.RequestException as exc:
            return False, f"{exc.__class__.__name__}: {exc}", None
        try:
            data = response.json()
        except ValueError:
            return False, f"non-JSON response (HTTP {response.status_code})", response.status_code
        if response.status_code == 200 and data.get("ok"):
            return True, data.get("result"), response.status_code
        description = data.get("description") or f"HTTP {response.status_code}"
        retry_after = None
        if isinstance(data.get("parameters"), dict):
            retry_after = data["parameters"].get("retry_after")
        return False, {"description": description, "retry_after": retry_after}, response.status_code

    # -- receiving ---------------------------------------------------------
    def get_updates(self, offset: int, timeout: int = POLL_TIMEOUT_SECONDS):
        return self.call("getUpdates", {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        }, timeout=timeout + 15)

    def flush_stale_updates(self) -> None:
        """Drop anything queued while the bot was offline so it does not
        suddenly fire off a pile of old scans on startup."""
        ok, result, _ = self.call("getUpdates", {"offset": -1, "timeout": 0}, timeout=20)
        if ok and isinstance(result, list) and result:
            log(f"  ignored {len(result)} stale update(s) queued while offline")

    def verify(self) -> str:
        """Check the token. Returns the bot's @username, or raises BotError."""
        ok, result, status = self.call("getMe", timeout=20)
        if not ok:
            description = result["description"] if isinstance(result, dict) else result
            if status == 401:
                raise BotError(
                    "Telegram rejected the bot token (HTTP 401). Check "
                    "TELEGRAM_BOT_TOKEN / telegram_config.json / --token."
                )
            raise BotError(f"Could not reach Telegram to verify the token: {description}")
        return (result or {}).get("username", "?")

    # -- sending -----------------------------------------------------------
    def send_text(self, chat_id, text: str) -> bool:
        """Send one message, honouring Telegram's 429 retry_after."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        for attempt in range(1, MAX_ATTEMPTS + 2):
            ok, result, status = self.call("sendMessage", payload)
            if ok:
                return True
            description = result["description"] if isinstance(result, dict) else result
            retry_after = result.get("retry_after") if isinstance(result, dict) else None
            if retry_after:
                log(f"  ! Telegram rate limit; waiting {retry_after}s")
                time.sleep(int(retry_after) + 1)
                continue
            log(f"  ! Telegram send failed (HTTP {status}): {description}")
            if status in (429, 500, 502, 503, 504) and attempt <= MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
            return False
        return False

    def deliver(self, chat_id, text: str) -> bool:
        """Send a possibly-long report as one or more messages."""
        chunks = split_message(text)
        ok = True
        for chunk in chunks:
            if not self.send_text(chat_id, chunk):
                ok = False
            time.sleep(BETWEEN_MESSAGE_PAUSE)
        return ok


def load_credentials(args):
    """CLI flags > environment variables > telegram_config.json."""
    token = (args.token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = (args.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

    if not (token and chat_id):
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_config.json"),
            os.path.join(os.getcwd(), "telegram_config.json"),
        ]
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError) as exc:
                raise BotError(f"Could not read {path}: {exc}") from exc
            token = token or str(data.get("bot_token", "")).strip()
            chat_id = chat_id or str(data.get("chat_id", "")).strip()
            break

    if not token:
        raise BotError(
            "No Telegram bot token found. Set TELEGRAM_BOT_TOKEN (on Replit: the "
            "Secrets tool), or put {\"bot_token\": \"...\"} in telegram_config.json "
            "next to this script, or pass --token. See the notes at the top of the file."
        )
    return token, chat_id


# --------------------------------------------------------------------------- #
# The scan (unchanged logic, now driven by a chat message instead of a flag)
# --------------------------------------------------------------------------- #

def resolve_timezone(name: str):
    """Return (tzinfo, zone label, utc-offset label) for day boundaries/times."""
    if not name:
        tz = _dt.datetime.now().astimezone().tzinfo
        zone_label = getattr(tz, "key", None) or str(tz) or "local time"
        if zone_label in ("UTC", "tzutc()"):
            zone_label = "UTC"
    else:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(name)
            zone_label = name
        except Exception as exc:
            raise BotError(
                f"Unknown or unavailable timezone '{name}' ({exc}). "
                "Drop --timezone to use the server's local time."
            ) from exc
    try:
        offset = _dt.datetime.now(tz).strftime("%z")          # e.g. +0100
        utc_label = "" if zone_label == "UTC" else f"UTC{offset[:3]}:{offset[3:]}"
    except Exception:
        utc_label = ""
    return tz, zone_label, utc_label


DATE_PATTERN = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")


def extract_dates(value: str):
    """Pull every dd-mm-yyyy date out of a message.

    Returns (dates, saw_candidate) where saw_candidate means something looked
    like a date but was not a real one (e.g. 31-02-2026), so the caller can
    answer with the format explanation instead of staying silent.
    """
    dates = []
    saw_candidate = False
    for day, month, year in DATE_PATTERN.findall(value or ""):
        saw_candidate = True
        try:
            parsed = _dt.datetime.strptime(f"{day}-{month}-{year}", "%d-%m-%Y").date()
        except ValueError:
            continue                      # e.g. 31-02-2026 or month 13
        if parsed not in dates:
            dates.append(parsed)
    return dates, saw_candidate


def parse_date(value: str):
    """Convenience wrapper: the first date in a message, or None."""
    dates, saw = extract_dates(value)
    return dates[0] if dates else None


def check_window(target_date: _dt.date, tz) -> str:
    """Return '' if Flashscore can serve this date, else a plain explanation."""
    start_local = _dt.datetime.combine(target_date, _dt.time.min, tzinfo=tz)
    start_utc = start_local.astimezone(_dt.timezone.utc).date()
    offset = (start_utc - _dt.datetime.now(_dt.timezone.utc).date()).days
    if abs(offset) <= MAX_DAY_OFFSET:
        return ""
    today = _dt.datetime.now(tz).date()
    low = today - _dt.timedelta(days=MAX_DAY_OFFSET)
    high = today + _dt.timedelta(days=MAX_DAY_OFFSET)
    return (
        f"Sorry, <b>{target_date.strftime('%d-%m-%Y')}</b> is outside the range "
        f"Flashscore serves.\n\n"
        f"Their daily fixture feed is a rolling window of today +/- "
        f"{MAX_DAY_OFFSET} days; anything older or further ahead comes back empty, "
        f"so there is nothing to filter.\n\n"
        f"Today is {today.strftime('%d-%m-%Y')}, so I can scan "
        f"<b>{low.strftime('%d-%m-%Y')}</b> to <b>{high.strftime('%d-%m-%Y')}</b>."
    )


def run_scan(tg: TelegramAPI, chat_id, target_date: _dt.date, settings) -> None:
    """Fetch fixtures, pull tables, filter, and reply in the chat."""
    tz, tz_name, utc_label = settings["tz"], settings["tz_name"], settings["utc_label"]
    started = time.monotonic()
    client = FeedClient(delay=settings["delay"], quiet=False)

    log(f"  scanning {target_date.strftime('%d-%m-%Y')} for chat {chat_id} ...")
    tg.send_text(chat_id,
                 f"Scanning <b>{target_date.strftime('%d-%m-%Y')}</b> "
                 f"({tz_name})\u2026 this usually takes 20\u201390 seconds.")

    # -- Step 1: fixtures --------------------------------------------------
    try:
        matches = collect_matches_for_date(client, target_date, tz)
    except requests.RequestException as exc:
        tg.send_text(chat_id, f"Could not reach Flashscore ({exc.__class__.__name__}). "
                              "Please send the date again in a moment.")
        log(f"  ! Flashscore unreachable: {exc}")
        return

    log(f"  found {len(matches)} match(es) kicking off on that date")

    if not matches:
        # Still tell the user — silently doing nothing is worse.
        report = build_report(target_date, tz, tz_name, [], 0, [], 0,
                              client.request_count, time.monotonic() - started,
                              utc_label)
        tg.deliver(chat_id, report)
        log("  sent 'no matches listed' notice")
        return

    # -- Steps 2 & 3: standings + filter ----------------------------------
    passed_by_n = collections.defaultdict(list)
    skipped = []
    candidates = 0
    table_cache = {}
    total = len(matches)

    for index, match in enumerate(matches, start=1):
        if index % 100 == 0:
            log(f"  ...{index}/{total} matches checked")

        label = f"{match.home_name} vs {match.away_name} ({match.league})"

        if match.status_code != STATUS_SCHEDULED and not settings["include_started"]:
            skipped.append((label, f"not a scheduled match (it is {match.status})"))
            continue

        try:
            table = standings_for_match(client, match, table_cache)
        except Exception as exc:                    # never let one match kill a run
            skipped.append((label, f"standings request failed ({exc.__class__.__name__})"))
            continue

        if not table:
            skipped.append((label, "no league table available for this competition"))
            continue

        try:
            match.home_stats = find_team(table, match.home_id, match.home_name)
            match.away_stats = find_team(table, match.away_id, match.away_name)
        except Exception as exc:
            skipped.append((label, f"could not read the table ({exc.__class__.__name__})"))
            continue

        if match.home_stats is None and match.away_stats is None:
            skipped.append((label, "neither team appears in that table"))
            continue
        if match.home_stats is None:
            skipped.append((label, f"home team '{match.home_name}' not in the table"))
            continue
        if match.away_stats is None:
            skipped.append((label, f"away team '{match.away_name}' not in the table"))
            continue

        # Offer the match to every N variant. Fixtures and tables were fetched
        # once above, so this is pure in-memory work -- no extra requests.
        candidate = applicable_n(match)
        if candidate is not None:
            candidates += 1
        try:
            for n in n_variants():
                ok, _reason = evaluate(match, n)
                if ok:
                    passed_by_n[n].append(match)
                    log(f"  PASS N={n}: {label} — gap {match.point_gap}, "
                        f"goals {match.goals_sum}")
        except Exception as exc:
            skipped.append((label, f"filter error ({exc.__class__.__name__})"))
            continue

    seconds = time.monotonic() - started
    note = ""
    if target_date < _dt.datetime.now(tz).date():
        note = ("This date is in the past. Flashscore serves no historical standings, "
                "so the tables above are the current ones.")
    report = build_report(target_date, tz, tz_name, passed_by_n, total, skipped,
                          candidates, client.request_count, seconds, utc_label, note)

    # -- Step 4: reply in the same chat -----------------------------------
    total_hits = sum(len(v) for v in passed_by_n.values())
    if tg.deliver(chat_id, report):
        breakdown = ", ".join(f"N={n}:{len(passed_by_n[n])}"
                              for n in sorted(passed_by_n)) or "none"
        log(f"  sent report: {total_hits} matched ({breakdown}) / {total} scanned "
            f"({client.request_count} requests, {seconds:.0f}s)")
    else:
        log("  ! report delivery to Telegram failed")


# --------------------------------------------------------------------------- #
# Bot: long-poll Telegram, hand dates to a single worker
# --------------------------------------------------------------------------- #

class FilterBot:
    def __init__(self, tg: TelegramAPI, settings):
        self.tg = tg
        self.settings = settings
        self.jobs = queue.Queue()
        self.offset = 0
        self._stop = threading.Event()
        self._worker = None

    # -- worker -----------------------------------------------------------
    def start_worker(self) -> None:
        self._worker = threading.Thread(target=self._work_loop, name="scanner",
                                        daemon=True)
        self._worker.start()

    def _work_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            chat_id, target_date = job
            try:
                run_scan(self.tg, chat_id, target_date, self.settings)
            except Exception as exc:                # a bad scan must not kill the bot
                log(f"  ! scan failed: {exc.__class__.__name__}: {exc}")
                try:
                    self.tg.send_text(
                        chat_id,
                        "That scan failed with an unexpected error "
                        f"({exc.__class__.__name__}). Nothing is broken — please "
                        "send the date again."
                    )
                except Exception:
                    pass
            finally:
                self.jobs.task_done()

    # -- message handling -------------------------------------------------
    def allowed(self, chat_id) -> bool:
        restrict = self.settings.get("restrict_chat_id")
        if not restrict:
            return True
        return str(chat_id) == str(restrict)

    def handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return

        text = (message.get("text") or "").strip()
        who = chat.get("username") or chat.get("first_name") or chat_id

        if not text:
            self.tg.send_text(chat_id, INVALID_INPUT_TEXT)
            return

        if text.lower() in HELP_WORDS:
            log(f"  help request from {who} ({chat_id})")
            self.tg.send_text(chat_id, USAGE_TEXT)
            return

        if not self.allowed(chat_id):
            log(f"  ignored message from unauthorised chat {chat_id}")
            self.tg.send_text(chat_id, "This bot is restricted to another chat.")
            return

        dates, saw_candidate = extract_dates(text)
        if not dates:
            log(f"  unparseable input from {who} ({chat_id}): {text[:60]!r}")
            self.tg.send_text(chat_id, INVALID_INPUT_TEXT if not saw_candidate else
                              "I could not read a real date in that.\n\n"
                              "Send it as <b>dd-mm-yyyy</b>, for example "
                              "<code>15-09-2026</code>.")
            return

        queued = []
        for target_date in dates:
            outside = check_window(target_date, self.settings["tz"])
            if outside:
                log(f"  out-of-window date from {who} ({chat_id}): "
                    f"{target_date.strftime('%d-%m-%Y')}")
                self.tg.send_text(chat_id, outside)
                continue
            if self.jobs.qsize() >= 1:
                self.tg.send_text(chat_id,
                                  "Got it — I am still working through an earlier "
                                  "date, so this one is queued behind it.")
            log(f"  date {target_date.strftime('%d-%m-%Y')} queued from {who} "
                f"({chat_id}); {self.jobs.qsize()} job(s) already waiting")
            self.jobs.put((chat_id, target_date))
            queued.append(target_date)

        if len(queued) > 1:
            listing = ", ".join(d.strftime("%d-%m-%Y") for d in queued)
            self.tg.send_text(chat_id, f"I found {len(queued)} dates in that message "
                                       f"({listing}) and will answer each one in turn.")

    # -- polling ----------------------------------------------------------
    def poll_forever(self, max_cycles: int = None) -> None:
        cycles = 0
        consecutive_errors = 0
        auth_failures = 0
        while not self._stop.is_set():
            if max_cycles is not None and cycles >= max_cycles:
                break
            cycles += 1

            ok, result, status = self.tg.get_updates(self.offset,
                                                     self.settings["poll_timeout"])
            if not ok:
                description = result["description"] if isinstance(result, dict) else result
                consecutive_errors += 1
                if status == 401:
                    auth_failures += 1
                    if auth_failures >= 3:
                        raise BotError(
                            "Telegram rejected the token three times in a row "
                            "(HTTP 401). Is it still valid?")
                    log(f"  ! Telegram returned 401 (attempt {auth_failures}/3); "
                        "retrying in case it is transient")
                    self._stop.wait(POLL_ERROR_WAIT)
                    continue
                if status == 409:
                    log("  ! HTTP 409: another copy of this bot is polling with the "
                        "same token. Stop the other one; retrying.")
                else:
                    log(f"  ! getUpdates failed ({description}); retrying in "
                        f"{POLL_ERROR_WAIT}s")
                self._stop.wait(POLL_ERROR_WAIT)
                continue

            consecutive_errors = 0
            auth_failures = 0
            if not isinstance(result, list):
                continue

            for update in result:
                update_id = update.get("update_id") or 0
                self.offset = max(self.offset, update_id + 1)
                message = update.get("message")
                if not message:
                    continue                      # edited/channel posts are ignored
                try:
                    self.handle_message(message)
                except Exception as exc:
                    log(f"  ! error handling update {update_id}: "
                        f"{exc.__class__.__name__}: {exc}")

        log("  polling stopped")

    def stop(self) -> None:
        self._stop.set()
        self.jobs.put(None)


# --------------------------------------------------------------------------- #
# Keep-alive HTTP endpoint (so Replit's Run button has a listening port)
# --------------------------------------------------------------------------- #

class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "FlashscoreFilterBot/1.0"

    def _respond(self) -> None:
        body = b"Flashscore daily pattern filter bot is running.\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    do_GET = do_HEAD = do_POST = _respond

    def log_message(self, *args) -> None:          # keep the console clean
        pass


def start_health_server(port: int):
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        log(f"  note: could not bind keep-alive port {port} ({exc}); "
            "the bot still runs fine")
        return None
    thread = threading.Thread(target=server.serve_forever, name="keepalive",
                              daemon=True)
    thread.start()
    log(f"  keep-alive HTTP endpoint listening on 0.0.0.0:{port}")
    return server


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="filter_bot.py",
        description="Long-running Telegram bot: send it a date (dd-mm-yyyy) and it "
                    "replies with the Flashscore matches that pass the "
                    f"N={N_MIN}..{N_MAX} pattern filter "
                    f"(N games, N-point gap, goals \u2265 {GOALS_MULTIPLIER}\u00d7N).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Normally you just run:  python filter_bot.py",
    )
    parser.add_argument("--token", default="",
                        help="Telegram bot token (overrides env/config)")
    parser.add_argument("--chat-id", default="",
                        help="optional fallback chat id (overrides env/config)")
    parser.add_argument("--restrict-chat-id", default="",
                        help="if set, only answer this chat id and ignore everyone else")
    parser.add_argument("--timezone", default="",
                        help="IANA timezone for day boundaries and kickoff times, "
                             "e.g. Africa/Lagos (default: this machine's timezone)")
    parser.add_argument("--include-started", action="store_true",
                        help="also check matches already in play or finished that day")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SECONDS,
                        help=f"seconds between Flashscore requests "
                             f"(default {REQUEST_DELAY_SECONDS})")
    parser.add_argument("--poll-timeout", type=int, default=POLL_TIMEOUT_SECONDS,
                        help=f"Telegram long-poll timeout in seconds "
                             f"(default {POLL_TIMEOUT_SECONDS})")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)),
                        help="keep-alive HTTP port (default: $PORT or 8080)")
    parser.add_argument("--no-http", action="store_true",
                        help="do not open the keep-alive HTTP port")
    parser.add_argument("--once", metavar="DD-MM-YYYY", default="",
                        help="diagnostics: scan this date, print the report here, "
                             "send nothing to Telegram, and exit")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        tz, tz_name, utc_label = resolve_timezone(args.timezone)
    except BotError as exc:
        log(f"ERROR: {exc}")
        return 2

    # ---- diagnostics mode: same pipeline, printed here, nothing sent ------
    if args.once:
        target_date = parse_date(args.once)
        if target_date is None:
            log(f"ERROR: invalid date '{args.once}'. Expected dd-mm-yyyy.")
            return 2
        outside = check_window(target_date, tz)
        if outside:
            log("ERROR: " + re.sub(r"</?b>", "", outside))
            return 2
        settings = {
            "tz": tz, "tz_name": tz_name, "utc_label": utc_label,
            "delay": max(0.0, args.delay), "include_started": args.include_started,
        }
        started = time.monotonic()
        client = FeedClient(delay=settings["delay"])
        matches = collect_matches_for_date(client, target_date, tz)
        passed_by_n = collections.defaultdict(list)
        skipped, candidates, cache = [], 0, {}
        for match in matches:
            label = f"{match.home_name} vs {match.away_name} ({match.league})"
            if match.status_code != STATUS_SCHEDULED and not settings["include_started"]:
                skipped.append((label, f"not a scheduled match (it is {match.status})"))
                continue
            table = standings_for_match(client, match, cache)
            if not table:
                skipped.append((label, "no league table available for this competition"))
                continue
            match.home_stats = find_team(table, match.home_id, match.home_name)
            match.away_stats = find_team(table, match.away_id, match.away_name)
            if not match.home_stats or not match.away_stats:
                skipped.append((label, "team not in the table"))
                continue
            if applicable_n(match) is not None:
                candidates += 1
            for n in n_variants():
                ok, _ = evaluate(match, n)
                if ok:
                    passed_by_n[n].append(match)
        report = build_report(target_date, tz, tz_name, passed_by_n, len(matches),
                              skipped, candidates, client.request_count,
                              time.monotonic() - started, utc_label)
        print(re.sub(r"</?(b|i|code)>", "", report))
        return 0

    # ---- normal mode: run forever as a Telegram bot ----------------------
    try:
        token, fallback_chat_id = load_credentials(args)
    except BotError as exc:
        log(f"ERROR: {exc}")
        return 2

    restrict = (args.restrict_chat_id or "").strip() or fallback_chat_id
    tg = TelegramAPI(token)
    try:
        username = tg.verify()
    except BotError as exc:
        log(f"ERROR: {exc}")
        return 2

    settings = {
        "tz": tz, "tz_name": tz_name, "utc_label": utc_label,
        "delay": max(0.0, args.delay),
        "include_started": args.include_started,
        "poll_timeout": max(1, args.poll_timeout),
        "restrict_chat_id": restrict,
    }

    log("Flashscore daily pattern filter bot")
    log(f"  Telegram:      @{username}")
    log(f"  answering:     " + (f"only chat {restrict}" if restrict else "any chat"))
    when = tz_name + (f", {utc_label}" if utc_label else "")
    log(f"  day boundaries/kickoffs: {when}")
    today = _dt.datetime.now(tz).date()
    low = today - _dt.timedelta(days=MAX_DAY_OFFSET)
    high = today + _dt.timedelta(days=MAX_DAY_OFFSET)
    log(f"  scannable now: {low.strftime('%d-%m-%Y')} .. {high.strftime('%d-%m-%Y')}")
    log(f"  filter:        for each N in {N_MIN}..{N_MAX}: N games each, "
        f"N-point gap, goals sum >= {GOALS_MULTIPLIER}*N")

    if not args.no_http:
        start_health_server(args.port)

    bot = FilterBot(tg, settings)
    bot.start_worker()
    tg.flush_stale_updates()

    log("  listening for dates (dd-mm-yyyy). Press Ctrl+C / stop Run to quit.")
    try:
        bot.poll_forever()
    except BotError as exc:
        log(f"ERROR: {exc}")
        bot.stop()
        return 2
    except KeyboardInterrupt:
        log("  interrupted; shutting down")
        bot.stop()
        return 130
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted.")
        sys.exit(130)
    except BotError as exc:
        log(f"ERROR: {exc}")
        sys.exit(2)
