#!/usr/bin/env python3
"""
Flashscore Trust Bot - single-file football picks with Flashscore odds

Built for one purpose:
    Use ONLY Flashscore data and return only the matches where the stats are strong,
    clean, and aligned enough to be worth trusting more than normal predictions.

Very important truth:
    No football bot is 100%. This bot is designed to be STRICT, not noisy.
    It will PASS most matches. That is intentional.

Data source:
    Flashscore football feed + Flashscore H2H/recent-form feed.
    Flashscore's odds provider (ds.lsapp.eu), via the regional odds menu.
    No paid API. No hardcoded bookmaker dependency. ODDS_ONLY=1 by default.
    Odds filter/display only: the original scoring and thresholds are unchanged.
    Trust is a heuristic score, NOT a calibrated win probability.
    Prices are snapshots; historical scans are not leakage-free backtests.

Install:
    pip install python-telegram-bot==21.6 requests

Run:
    export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    python flashscore_trust_bot.py

Telegram commands:
    /start
    /banker 05-09-2026  # one ultra-strict pick or no bet
    /safe 05-09-2026
    /settings
    /setgrade A        # A, B, or C. A is strictest.
    /setmax all        # scan every fixture Flashscore provides for the day
    /calc 10000 2.40 3.80 4.50  # optional real arb calculator

CLI test:
    python flashscore_trust_bot.py safe 06-09-2026

Odds environment (optional):
    ODDS_ONLY=1 ODDS_GEO=NG ODDS_GEO_SUB=NGLA
    ODDS_MAX_BOOKMAKERS=3 ODDS_DEPTH=all ODDS_DELAY=0.02
    ODDS_DEPTH=1x2 shows 1/X/2 context only, not a selected-market price.
    Prices for total goals require the EXACT line; no nearest-line substitution.
    Double Chance is priced separately from DNB; BTTS No is a watch alternative,
    not an equivalent team-total-under-1.5 bet.
"""

from __future__ import annotations

import asyncio
import html
import logging
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

try:
    from telegram import Update
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, ContextTypes
except ModuleNotFoundError:
    Update = object
    ContextTypes = object
    Application = None
    CommandHandler = None
    class ParseMode:
        HTML = "HTML"

# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Africa/Lagos"))
DB_PATH = os.getenv("TRUST_BOT_DB", ":memory:")  # no disk storage by default
MAX_FIXTURES = int(os.getenv("MAX_FIXTURES", "0"))  # 0 means scan ALL fixtures for the date
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "5"))
SCAN_CONCURRENCY = int(os.getenv("SCAN_CONCURRENCY", "8"))
DEFAULT_MIN_GRADE = os.getenv("DEFAULT_MIN_GRADE", "A").upper().strip()
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "35"))
DETAIL_DELAY = float(os.getenv("DETAIL_DELAY", "0.05"))

# ---- Flashscore odds: filter + display, never an input to scoring ----
ODDS_API = "https://global.ds.lsapp.eu/odds/pq_graphql"
ODDS_GEO = os.getenv("ODDS_GEO", "NG").strip().upper() or "NG"
ODDS_GEO_SUB = os.getenv("ODDS_GEO_SUB", "NGLA").strip() or "NGLA"
ODDS_ONLY = os.getenv("ODDS_ONLY", "1").strip() != "0"
ODDS_MAX_BOOKMAKERS = max(1, int(os.getenv("ODDS_MAX_BOOKMAKERS", "3")))
ODDS_DEPTH = os.getenv("ODDS_DEPTH", "all").lower().strip()
ODDS_DELAY = max(0.0, float(os.getenv("ODDS_DELAY", "0.02")))
if ODDS_DEPTH not in {"all", "1x2"}:
    raise ValueError("ODDS_DEPTH must be 'all' or '1x2'")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("flashscore_trust_bot")

# =========================
# MODELS
# =========================

@dataclass
class Fixture:
    match_id: str
    date: str
    time: str
    country: str
    league: str
    home: str
    away: str
    home_id: str = ""  # JA: eventParticipantId, not the URL/slug id
    away_id: str = ""  # JB

@dataclass
class ResultRow:
    section: str
    home: str
    away: str
    hg: int
    ag: int
    marker: str
    league: str = ""

@dataclass
class TeamForm:
    team: str
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    gf: int = 0
    ga: int = 0
    clean_sheets: int = 0
    failed_to_score: int = 0
    over15: int = 0
    over25: int = 0
    btts: int = 0
    results: List[str] = field(default_factory=list)

    @property
    def points(self) -> int: return self.wins * 3 + self.draws
    @property
    def ppg(self) -> float: return self.points / self.games if self.games else 0.0
    @property
    def gf_avg(self) -> float: return self.gf / self.games if self.games else 0.0
    @property
    def ga_avg(self) -> float: return self.ga / self.games if self.games else 0.0
    @property
    def tg_avg(self) -> float: return (self.gf + self.ga) / self.games if self.games else 0.0
    @property
    def win_rate(self) -> float: return self.wins / self.games if self.games else 0.0
    @property
    def draw_rate(self) -> float: return self.draws / self.games if self.games else 0.0
    @property
    def loss_rate(self) -> float: return self.losses / self.games if self.games else 0.0
    @property
    def cs_rate(self) -> float: return self.clean_sheets / self.games if self.games else 0.0
    @property
    def fts_rate(self) -> float: return self.failed_to_score / self.games if self.games else 0.0
    @property
    def over15_rate(self) -> float: return self.over15 / self.games if self.games else 0.0
    @property
    def over25_rate(self) -> float: return self.over25 / self.games if self.games else 0.0
    @property
    def btts_rate(self) -> float: return self.btts / self.games if self.games else 0.0

@dataclass
class PickOdds:
    bookmaker: str = ""
    market_label: str = ""
    selection: str = ""
    value: float = 0.0
    opening: float = 0.0
    move: str = ""
    line: str = ""
    all_prices: str = ""  # Same market/line only, among checked bookmakers

@dataclass
class Pick:
    fixture: Fixture
    market: str
    selection: str
    grade: str
    trust: int
    data_quality: int
    reasons: List[str]
    home_form: TeamForm
    away_form: TeamForm
    odds: Optional[PickOdds] = None
    selection_side: str = ""  # Display metadata: home/away, never fuzzy name matching
    odds_1x2: str = ""  # Context only, never a DC/DNB/goals quote
    odds_note: str = ""

# =========================
# SETTINGS DB
# =========================

class SettingsDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS settings(
                chat_id TEXT PRIMARY KEY,
                min_grade TEXT NOT NULL,
                max_fixtures INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def get(self, chat_id: int) -> Tuple[str, int]:
        row = self.conn.execute("SELECT min_grade,max_fixtures FROM settings WHERE chat_id=?", (str(chat_id),)).fetchone()
        if not row:
            return DEFAULT_MIN_GRADE, MAX_FIXTURES
        return str(row["min_grade"]), int(row["max_fixtures"])

    def save(self, chat_id: int, grade: str, max_fixtures: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO settings(chat_id,min_grade,max_fixtures) VALUES(?,?,?)",
            (str(chat_id), grade, max_fixtures),
        )
        self.conn.commit()

DB = SettingsDB(DB_PATH)

# =========================
# HELPERS
# =========================

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def norm_team(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", clean(name)).strip()

def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm_team(text).lower())

def contains(a: str, b: str) -> bool:
    ka, kb = key(a), key(b)
    return bool(ka and kb and (ka in kb or kb in ka))

def parse_date(date_text: str) -> Tuple[str, datetime.date]:
    d = datetime.strptime(date_text.strip(), "%d-%m-%Y").date()
    return d.strftime("%d-%m-%Y"), d

def grade_rank(g: str) -> int:
    return {"A+": 4, "A": 3, "B": 2, "C": 1}.get(g.upper(), 3)

def grade_from_score(score: int) -> str:
    if score >= 92: return "A+"
    if score >= 84: return "A"
    if score >= 76: return "B"
    return "C"

def is_low_coverage_competition(f: Fixture) -> bool:
    # No league blacklist. A match is allowed if Flashscore provides enough
    # complete stats for the prediction engine, regardless of league/country.
    return False

def fixture_priority(f: Fixture) -> int:
    # No league blacklist or preference. Keep Flashscore order.
    # The real filter happens after detail scraping: both teams must have enough
    # recent-match stats for the bot's prediction engine.
    return 0

# =========================
# FLASHSCORE FEED CLIENT
# =========================

class OddsAPIError(RuntimeError):
    """Malformed/failed GraphQL response; fail closed when odds-only is on."""


class Flashscore:
    def __init__(self):
        self.session = requests.Session()
        # A client belongs to one scan worker. No cross-thread shared sessions,
        # no disk odds cache, and no stale prices retained across scan commands.
        # Cache both successes and failures, once per menu or book/market/scope.
        self._odds_cache: Dict[Tuple[Any, ...], Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.session.close()

    def odds_headers(self, match_id: str) -> Dict[str, str]:
        return self.headers(f"https://www.flashscore.com/match/{match_id}/")

    def _odds_query(self, cache_key: Tuple[Any, ...], match_id: str,
                    params: Dict[str, Any], operation: str) -> Dict[str, Any]:
        if cache_key in self._odds_cache:
            cached = self._odds_cache[cache_key]
            if isinstance(cached, Exception):
                raise cached
            return cached
        # This entire client is used inside asyncio.to_thread, including sleep.
        if ODDS_DELAY:
            time.sleep(ODDS_DELAY)
        try:
            r = self.session.get(ODDS_API, params=params,
                                 headers=self.odds_headers(match_id), timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, dict) or payload.get("errors"):
                raise OddsAPIError(f"{operation}: invalid response or GraphQL errors")
            data = payload.get("data")
            if not isinstance(data, dict) or operation not in data:
                raise OddsAPIError(f"{operation}: missing GraphQL operation data")
            node = data[operation]
            if node is None:
                node = {}
            if not isinstance(node, dict):
                raise OddsAPIError(f"{operation}: expected an object")
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            self._odds_cache[cache_key] = e
            raise
        self._odds_cache[cache_key] = node
        return node

    def get_odds_menu(self, match_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return bookmakers and ACTIVE markets; an empty menu fails the gate."""
        params = {"_hash": "pobtm", "eventId": match_id, "projectId": "2",
                  "geoIpCode": ODDS_GEO, "geoIpSubdivisionCode": ODDS_GEO_SUB}
        data = self._odds_query(("menu", match_id, ODDS_GEO, ODDS_GEO_SUB), match_id,
                                params, "getPrematchOddsBettingTypeMenu")
        settings = data.get("settings") or {}
        if not isinstance(settings, dict):
            raise OddsAPIError("Invalid bookmaker settings")
        raw_books, raw_markets = settings.get("bookmakers") or [], data.get("items") or []
        if not isinstance(raw_books, list) or not isinstance(raw_markets, list):
            raise OddsAPIError("Invalid bookmaker/market lists")
        books: List[Dict[str, Any]] = []
        seen_books = set()
        for entry in raw_books:
            b = entry.get("bookmaker") if isinstance(entry, dict) else None
            if not isinstance(b, dict):
                continue
            try:
                bid = int(b["id"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if bid <= 0 or bid in seen_books or not isinstance(b.get("name"), str):
                continue
            name = clean(b["name"])
            if not name:
                continue
            seen_books.add(bid)
            books.append({"id": bid, "name": name})
        markets: List[Dict[str, Any]] = []
        for item in raw_markets:
            if not isinstance(item, dict) or item.get("isActive") is not True:
                continue
            kind, scope = item.get("bettingType"), item.get("bettingScope")
            if not isinstance(kind, str) or not isinstance(scope, str) or not kind or not scope:
                continue
            market: Dict[str, Any] = {"type": kind, "scope": scope}
            # Some menus identify the exact books offering each market.
            ids = item.get("bookmakerIds")
            if isinstance(ids, list):
                market["bookmaker_ids"] = [str(bid) for bid in ids]
            markets.append(market)
        return books, markets

    def get_odds_for(self, match_id: str, bookmaker_id: int,
                     bet_type: str, bet_scope: str = "FULL_TIME") -> Dict[str, Any]:
        params = {"_hash": "ope2", "eventId": match_id, "bookmakerId": bookmaker_id,
                  "betType": bet_type, "betScope": bet_scope}
        data = self._odds_query(("price", match_id, bookmaker_id, bet_type, bet_scope),
                                match_id, params, "findPrematchOddsForBookmaker")
        # Never accept a different market/book in a malformed response.
        if data.get("type") not in (None, bet_type):
            raise OddsAPIError("Returned odds market does not match the request")
        if data.get("bookmakerId") is not None and str(data["bookmakerId"]) != str(bookmaker_id):
            raise OddsAPIError("Returned bookmaker does not match the request")
        return data

    def headers(self, referer: str = "https://www.flashscore.com/football/") -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "*/*",
            "Referer": referer,
            "x-fsign": "SW9D1eZo",
        }

    def records(self, text: str) -> List[Dict[str, str]]:
        out = []
        if not text or text.strip() == "0": return out
        for rec in text.split("¬~"):
            item = {}
            for part in rec.split("¬"):
                if "÷" in part:
                    k, v = part.split("÷", 1)
                    item[k] = v
            if item: out.append(item)
        return out

    def get_fixtures(self, date_text: str) -> List[Fixture]:
        _, target = parse_date(date_text)
        today = datetime.now(LOCAL_TZ).date()
        offset = (target - today).days
        url = f"https://www.flashscore.com/x/feed/f_1_{offset}_2_en_1"
        logger.info("Fetching fixtures offset=%s url=%s", offset, url)
        r = self.session.get(url, headers=self.headers(), timeout=HTTP_TIMEOUT)
        r.raise_for_status()

        country = league = ""
        fixtures: List[Fixture] = []
        for rec in self.records(r.text):
            if "ZA" in rec:
                title = clean(rec.get("ZA", ""))
                if ":" in title:
                    country, league = [clean(x) for x in title.split(":", 1)]
                else:
                    league = title
                continue
            if "AA" not in rec: continue
            mid = rec.get("AA", "")
            home = norm_team(rec.get("AE") or rec.get("CX") or "")
            away = norm_team(rec.get("AF") or "")
            if not mid or not home or not away: continue
            ts = rec.get("AD") or rec.get("ADE") or ""
            try:
                dt = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(LOCAL_TZ)
                if dt.date() != target: continue
                t = dt.strftime("%H:%M")
            except Exception:
                t = ""
            fixtures.append(Fixture(mid, target.strftime("%d-%m-%Y"), t, country, league, home, away,
                                    home_id=rec.get("JA", ""), away_id=rec.get("JB", "")))
        fixtures.sort(key=fixture_priority, reverse=True)
        logger.info("Fixtures found exact date: %d", len(fixtures))
        return fixtures

    def get_rows(self, fixture: Fixture) -> List[ResultRow]:
        url = f"https://www.flashscore.com/x/feed/df_hh_1_{fixture.match_id}"
        ref = f"https://www.flashscore.com/match/{fixture.match_id}/"
        try:
            r = self.session.get(url, headers=self.headers(ref), timeout=HTTP_TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            logger.warning("detail failed %s: %s", fixture.match_id, e)
            return []

        rows: List[ResultRow] = []
        section = ""
        for rec in self.records(r.text):
            if "KB" in rec:
                section = clean(rec.get("KB", ""))
                continue
            if "KC" not in rec: continue
            score = rec.get("KL", "")
            m = re.search(r"(\d{1,2})\s*[:\-]\s*(\d{1,2})", score)
            if not m: continue
            home = norm_team((rec.get("KJ") or rec.get("FH") or "").replace("*", ""))
            away = norm_team((rec.get("KK") or rec.get("FK") or "").replace("*", ""))
            marker = clean(rec.get("WIS", "")).upper()[:1]
            if marker not in {"W", "D", "L"}: marker = ""
            rows.append(ResultRow(section, home, away, int(m.group(1)), int(m.group(2)), marker, rec.get("KF", "")))
        return rows

# =========================
# FORM BUILDING
# =========================

def add_result(form: TeamForm, res: str, gf: int, ga: int) -> None:
    form.games += 1
    form.gf += gf
    form.ga += ga
    form.results.append(res)
    if res == "W": form.wins += 1
    elif res == "D": form.draws += 1
    else: form.losses += 1
    if ga == 0: form.clean_sheets += 1
    if gf == 0: form.failed_to_score += 1
    if gf + ga >= 2: form.over15 += 1
    if gf + ga >= 3: form.over25 += 1
    if gf > 0 and ga > 0: form.btts += 1

def build_forms(fixture: Fixture, rows: List[ResultRow]) -> Tuple[TeamForm, TeamForm, int]:
    hf, af = TeamForm(fixture.home), TeamForm(fixture.away)
    h2h_count = 0
    for r in rows:
        sec = r.section.lower()
        if contains(r.home, fixture.home) and contains(r.away, fixture.away) or contains(r.home, fixture.away) and contains(r.away, fixture.home):
            h2h_count += 1

        target = None
        if contains(r.section, fixture.home): target = hf
        elif contains(r.section, fixture.away): target = af
        elif "last matches" in sec:
            if contains(r.home, fixture.home) or contains(r.away, fixture.home): target = hf
            elif contains(r.home, fixture.away) or contains(r.away, fixture.away): target = af
        else:
            continue

        if target.games >= 10: continue
        if r.marker in {"W", "D", "L"}:
            if r.marker == "W": gf, ga = max(r.hg, r.ag), min(r.hg, r.ag)
            elif r.marker == "L": gf, ga = min(r.hg, r.ag), max(r.hg, r.ag)
            else: gf, ga = r.hg, r.ag
            add_result(target, r.marker, gf, ga)
        else:
            tk = key(target.team)
            if tk == key(r.home):
                res = "W" if r.hg > r.ag else "D" if r.hg == r.ag else "L"
                add_result(target, res, r.hg, r.ag)
            elif tk == key(r.away):
                res = "W" if r.ag > r.hg else "D" if r.hg == r.ag else "L"
                add_result(target, res, r.ag, r.hg)
    return hf, af, h2h_count

# =========================
# PICK ENGINE - STRICT BY DESIGN
# =========================

def data_quality(hf: TeamForm, af: TeamForm, h2h_count: int, fixture: Fixture) -> int:
    # Completeness-first scoring. No league blacklist.
    # If both teams have 10 recent matches, that alone is strong enough data.
    q = 0
    for f in [hf, af]:
        if f.games >= 10: q += 42
        elif f.games >= 8: q += 34
        elif f.games >= 6: q += 24
        elif f.games >= 4: q += 12
    # H2H is useful but not mandatory, because many valid fixtures have little/no H2H.
    if h2h_count >= 5: q += 16
    elif h2h_count >= 3: q += 10
    return max(0, min(100, q))

def power(f: TeamForm) -> float:
    if not f.games: return 0.0
    return f.ppg * 25 + f.win_rate * 18 - f.loss_rate * 12 + (f.gf_avg - f.ga_avg) * 12 + f.cs_rate * 6 - f.fts_rate * 6

def make_picks(fixture: Fixture, hf: TeamForm, af: TeamForm, h2h_count: int) -> List[Pick]:
    q = data_quality(hf, af, h2h_count, fixture)
    # Main completeness gate: both teams must have enough Flashscore recent-form data.
    # This is the only hard filter. No league/country blacklist.
    if q < 68 or hf.games < 8 or af.games < 8:
        return []

    picks: List[Pick] = []
    hp, ap = power(hf), power(af)
    fav_name, dog_name = (fixture.home, fixture.away) if hp >= ap else (fixture.away, fixture.home)
    fav, dog = (hf, af) if hp >= ap else (af, hf)
    gap = abs(hp - ap)

    # Safer-side pick: Draw No Bet / Double Chance, not blind win.
    trust = 0; reasons = []
    if gap >= 34: trust += 28; reasons.append("major strength gap")
    elif gap >= 24: trust += 20; reasons.append("clear strength gap")
    if fav.ppg >= 2.0 and dog.ppg <= 1.1: trust += 20; reasons.append("form points strongly favor selection")
    if fav.win_rate >= 0.65 and dog.loss_rate >= 0.45: trust += 18; reasons.append("win/loss profile aligned")
    if fav.ga_avg <= 1.1 and dog.gf_avg <= 1.2: trust += 12; reasons.append("defence vs weak attack supports safer side")
    if fav_name == fixture.home: trust += 7; reasons.append("home edge")
    trust += int(q * 0.15)
    if trust >= 76:
        market = "Safer side"
        selection = f"{fav_name} Draw No Bet / Double Chance"
        picks.append(Pick(fixture, market, selection, grade_from_score(trust), min(99, trust), q, reasons, hf, af,
                          selection_side="home" if hp >= ap else "away"))

    # Over 1.5 goals: conservative goal pick.
    trust = 0; reasons = []
    if hf.over15_rate >= 0.75 and af.over15_rate >= 0.75: trust += 28; reasons.append("both teams trend over 1.5")
    elif (hf.over15_rate + af.over15_rate) / 2 >= 0.70: trust += 20; reasons.append("combined over 1.5 trend is strong")
    if hf.tg_avg >= 2.5 and af.tg_avg >= 2.5: trust += 18; reasons.append("match goal averages support goals")
    if hf.gf_avg + af.gf_avg >= 2.2: trust += 12; reasons.append("both attacks contribute enough")
    if hf.fts_rate <= 0.25 and af.fts_rate <= 0.25: trust += 10; reasons.append("low failed-to-score rates")
    trust += int(q * 0.18)
    if trust >= 78:
        picks.append(Pick(fixture, "Goals", "Over 1.5 goals", grade_from_score(trust), min(99, trust), q, reasons, hf, af))

    # Under 4.5 goals: often safer if both teams are not crazy high scoring.
    trust = 0; reasons = []
    if hf.tg_avg <= 3.2 and af.tg_avg <= 3.2: trust += 24; reasons.append("both teams stay under extreme goal totals")
    if hf.ga_avg <= 1.7 and af.ga_avg <= 1.7: trust += 14; reasons.append("conceding rates not too high")
    if hf.gf_avg <= 2.4 and af.gf_avg <= 2.4: trust += 12; reasons.append("scoring rates not explosive")
    if hf.games >= 8 and af.games >= 8: trust += 15; reasons.append("enough recent matches")
    trust += int(q * 0.15)
    if trust >= 76:
        picks.append(Pick(fixture, "Goals", "Under 4.5 goals", grade_from_score(trust), min(99, trust), q, reasons, hf, af))

    # Underdog team total under 1.5 / BTTS No angle.
    trust = 0; reasons = []
    if fav.ga_avg <= 0.9 and dog.gf_avg <= 1.0: trust += 24; reasons.append("strong defence vs weak attack")
    if fav.cs_rate >= 0.40 and dog.fts_rate >= 0.30: trust += 20; reasons.append("clean-sheet/failed-score pattern aligned")
    if dog.loss_rate >= 0.45: trust += 10; reasons.append("underdog loss trend")
    trust += int(q * 0.16)
    if trust >= 78:
        picks.append(Pick(fixture, "Team goals", f"{dog_name} under 1.5 team goals / BTTS No watch", grade_from_score(trust), min(99, trust), q, reasons, hf, af))

    return picks

# =========================
# ODDS MAPPING - DISPLAY ONLY
# =========================

def _to_f(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _active_price(item: Any) -> float:
    if not isinstance(item, dict) or item.get("active") is not True:
        return 0.0
    price = _to_f(item.get("value"))
    return price if price > 1.0 else 0.0


def _participant_item(node: Dict[str, Any], fx: Fixture, side: str,
                      home_field: str, away_field: str) -> Optional[Dict[str, Any]]:
    """Prefer JA/JB participant IDs; use semantic API keys only without IDs."""
    if side not in {"home", "away"}:
        return None
    expected_id = fx.home_id if side == "home" else fx.away_id
    items = [node.get(home_field), node.get(away_field)]
    items = [item for item in items if isinstance(item, dict)]
    if expected_id and any(item.get("eventParticipantId") for item in items):
        matches = [item for item in items if str(item.get("eventParticipantId")) == expected_id]
        return matches[0] if len(matches) == 1 else None
    item = node.get(home_field if side == "home" else away_field)
    return item if isinstance(item, dict) else None


def exact_ou(node: Dict[str, Any], target: float, side: str) -> Optional[Dict[str, Any]]:
    """Keep the complete ACTIVE item on the EXACT goal line; never use nearest."""
    if side not in {"over", "under"}:
        return None
    opportunities = node.get("opportunities") or []
    if not isinstance(opportunities, list):
        return None
    best = None
    for opp in opportunities:
        if not isinstance(opp, dict):
            continue
        handicap = opp.get("handicap") or {}
        if not isinstance(handicap, dict) or _to_f(handicap.get("value")) != target:
            continue
        item = opp.get(side)
        if _active_price(item) and (best is None or _active_price(item) > _active_price(best)):
            best = item
    return best


def _market_books(books: List[Dict[str, Any]], markets: List[Dict[str, Any]],
                  bet_type: str) -> List[Dict[str, Any]]:
    active = [m for m in markets if m["type"] == bet_type and m["scope"] == "FULL_TIME"]
    if not active:
        return []
    unrestricted = any("bookmaker_ids" not in m for m in active)
    allowed = {str(bid) for m in active for bid in m.get("bookmaker_ids", [])}
    return [b for b in books if unrestricted or str(b["id"]) in allowed][:ODDS_MAX_BOOKMAKERS]


def attach_1x2_context(pick: Pick, fx: Fixture, books: List[Dict[str, Any]],
                       markets: List[Dict[str, Any]], client: Flashscore) -> None:
    summaries = []
    for book in _market_books(books, markets, "HOME_DRAW_AWAY"):
        try:
            node = client.get_odds_for(fx.match_id, book["id"], "HOME_DRAW_AWAY")
            items = [_participant_item(node, fx, "home", "home", "away"), node.get("draw"),
                     _participant_item(node, fx, "away", "home", "away")]
            prices = [f"{_active_price(item):.2f}" if _active_price(item) else "—" for item in items]
            if any(_active_price(item) for item in items):
                summaries.append(f"{book['name']} {' / '.join(prices)}")
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            logger.warning("1X2 odds failed %s book=%s: %s", fx.match_id, book["id"], e)
    pick.odds_1x2 = " | ".join(summaries)
    pick.odds_note = "Selected-market quote not requested (ODDS_DEPTH=1x2)."
    if not summaries:
        pick.odds_note += " No active full-time 1X2 prices returned."


def attach_odds(pick: Pick, fx: Fixture, books: List[Dict[str, Any]],
                client: Flashscore, markets: Optional[List[Dict[str, Any]]] = None) -> None:
    """Attach best checked price without changing a selection, grade or score.

    Called in the fixture worker thread. DC quotes are not DNB quotes. The
    original Team goals selection includes a 'BTTS No watch' alternative; that
    price is labelled explicitly, never passed off as the team-total price.
    """
    pick.odds, pick.odds_1x2, pick.odds_note = None, "", ""
    if markets is None:
        _, markets = client.get_odds_menu(fx.match_id)  # cached
    if ODDS_DEPTH == "1x2":
        attach_1x2_context(pick, fx, books, markets, client)
        return

    line = ""
    side = ""
    note = ""
    if pick.market == "Safer side" and pick.selection_side in {"home", "away"}:
        bet_type, label = "DOUBLE_CHANCE", "Double Chance"
        selection = "1X" if pick.selection_side == "home" else "X2"
        note = "Double Chance quote only; not Draw No Bet."
    elif pick.market == "Goals" and pick.selection == "Over 1.5 goals":
        bet_type, label, selection, line, side = "OVER_UNDER", "Goals", "Over", "1.5", "over"
    elif pick.market == "Goals" and pick.selection == "Under 4.5 goals":
        bet_type, label, selection, line, side = "OVER_UNDER", "Goals", "Under", "4.5", "under"
    elif pick.market == "Team goals" and pick.selection.endswith(" / BTTS No watch"):
        bet_type, label, selection = "BOTH_TEAMS_TO_SCORE", "BTTS", "No"
        note = "BTTS No watch alternative only; NOT a team-total-under-1.5 quote."
    else:
        pick.odds_note = "No supported exact-market odds mapping for this selection."
        return

    candidates = []
    for book in _market_books(books, markets, bet_type):
        try:
            node = client.get_odds_for(fx.match_id, book["id"], bet_type, "FULL_TIME")
            if bet_type == "DOUBLE_CHANCE":
                item = _participant_item(node, fx, pick.selection_side, "homeOrDraw", "awayOrDraw")
            elif bet_type == "OVER_UNDER":
                item = exact_ou(node, float(line), side)
            else:
                item = node.get("no")
            value = _active_price(item)
            if not value:
                continue
            opening = _to_f(item.get("opening"))
            change = item.get("change") or {}
            move = change.get("type", "") if isinstance(change, dict) else ""
            candidates.append(PickOdds(
                bookmaker=book["name"], market_label=label, selection=selection, value=value,
                opening=opening if opening > 1.0 else 0.0,
                move=move if move in {"UP", "DOWN"} else "", line=line,
            ))
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            logger.warning("odds price failed %s book=%s market=%s: %s", fx.match_id, book["id"], bet_type, e)
    if not candidates:
        pick.odds_note = "No active quote returned for this market/exact line among checked bookmakers."
        if note:
            pick.odds_note += " " + note
        return
    best = max(candidates, key=lambda quote: quote.value)
    if len(candidates) > 1:
        best.all_prices = " | ".join(f"{quote.bookmaker} {quote.value:.2f}" for quote in candidates)
    pick.odds, pick.odds_note = best, note

# =========================
# SCAN + FORMAT
# =========================

def _analyze_fixture(fx: Fixture) -> Tuple[List[Pick], bool]:
    """All requests (including attaching odds) stay off the asyncio event loop."""
    with Flashscore() as client:
        try:
            books, markets = client.get_odds_menu(fx.match_id)
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            logger.warning("odds menu failed %s: %s", fx.match_id, e)
            books, markets = [], []
        if ODDS_ONLY and not markets:
            logger.info("no active Flashscore odds / lookup unavailable -> skip: %s %s vs %s",
                        fx.match_id, fx.home, fx.away)
            return [], False
        rows = client.get_rows(fx)
        hf, af, h2h = build_forms(fx, rows)
        picks = make_picks(fx, hf, af, h2h)
        for pick in picks:
            attach_odds(pick, fx, books, client, markets)
        return picks, True


async def scan(date_text: str, max_fixtures: int) -> Tuple[List[Pick], int, int, int]:
    with Flashscore() as fs:
        all_fixtures = await asyncio.to_thread(fs.get_fixtures, date_text)
    # Preserve feed order, but never request a duplicated match id twice per run.
    all_fixtures = list({fx.match_id: fx for fx in all_fixtures}.values())
    fixtures = all_fixtures[:max_fixtures] if max_fixtures > 0 else all_fixtures
    sem = asyncio.Semaphore(max(1, SCAN_CONCURRENCY))

    async def analyze_one(i: int, fx: Fixture) -> Tuple[List[Pick], bool]:
        async with sem:
            logger.info("%d/%d %s vs %s", i, len(fixtures), fx.home, fx.away)
            result = await asyncio.to_thread(_analyze_fixture, fx)
            if DETAIL_DELAY > 0:
                await asyncio.sleep(DETAIL_DELAY)
            return result

    results = await asyncio.gather(*(analyze_one(i, fx) for i, fx in enumerate(fixtures, 1)))
    all_picks = [pick for picks, _ in results for pick in picks]
    skipped = sum(1 for _, analyzed in results if not analyzed)
    analyzed = len(fixtures) - skipped  # Actual form analyses, not attempted menus
    all_picks.sort(key=lambda p: (grade_rank(p.grade), p.trust, p.data_quality), reverse=True)
    logger.info("Scan complete: fetched=%d checked=%d analyzed=%d skipped_odds=%d picks=%d",
                len(all_fixtures), len(fixtures), analyzed, skipped, len(all_picks))
    return all_picks, len(all_fixtures), analyzed, skipped

def form_line(f: TeamForm) -> str:
    seq = "".join(f.results[:10]) or "-"
    return f"{f.points}/{f.games}pts, GF {f.gf_avg:.1f}, GA {f.ga_avg:.1f}, {seq}"

def format_odds(p: Pick) -> str:
    lines = []
    if p.odds:
        odds = p.odds
        label = f"{odds.market_label} {odds.selection}" + (f" {odds.line}" if odds.line else "")
        arrow = {"UP": "↑", "DOWN": "↓"}.get(odds.move, "")
        opening = f" (open {odds.opening:.2f})" if odds.opening else ""
        movement = f" {arrow}" if arrow else ""
        lines.append(f"💰 FT {html.escape(label)}: <b>{odds.value:.2f}</b> @ "
                     f"{html.escape(odds.bookmaker)}{movement}{opening}")
        if odds.all_prices:
            lines.append("Checked prices: " + html.escape(odds.all_prices))
    if p.odds_1x2:
        lines.append("💰 FT 1X2 context ONLY (1 / X / 2): " + html.escape(p.odds_1x2))
    if p.odds_note:
        lines.append(html.escape(p.odds_note))
    elif not p.odds and not p.odds_1x2:
        lines.append("Selected-market quote unavailable.")
    return "\n".join(lines) + "\n"


def format_pick(p: Pick, idx: int) -> str:
    fx = p.fixture
    return (
        f"<b>{idx}. {html.escape(fx.home)} vs {html.escape(fx.away)}</b>\n"
        f"{html.escape((fx.country + ' ' + fx.league).strip())} | {html.escape(fx.time or 'N/A')}\n"
        f"✅ <b>{html.escape(p.selection)}</b>\n"
        f"Grade: <b>{p.grade}</b> | Trust: <b>{p.trust}/100</b> | Data: <b>{p.data_quality}/100</b>\n"
        f"H: {html.escape(form_line(p.home_form))}\n"
        f"A: {html.escape(form_line(p.away_form))}\n"
        + format_odds(p)
    )


def best_per_match(picks: List[Pick]) -> List[Pick]:
    """Keep only the single strongest pick per fixture, so one match doesn't flood output."""
    best: Dict[str, Pick] = {}
    for p in picks:
        old = best.get(p.fixture.match_id)
        if old is None or (grade_rank(p.grade), p.trust, p.data_quality) > (grade_rank(old.grade), old.trust, old.data_quality):
            best[p.fixture.match_id] = p
    out = list(best.values())
    out.sort(key=lambda p: (grade_rank(p.grade), p.trust, p.data_quality), reverse=True)
    return out

def scan_summary(fetched: int, analyzed: int, skipped: int) -> str:
    return (f"Fetched/analyzed: <b>{fetched}/{analyzed}</b> fixtures. "
            f"Checked: <b>{analyzed + skipped}</b>.\n"
            f"Skipped: <b>{skipped}</b> — no active Flashscore odds or lookup failed.\n")


def odds_policy(date_text: str) -> str:
    if ODDS_ONLY:
        text = "Odds filter ON: only matches with an active Flashscore odds menu qualify.\n"
    else:
        text = "Odds filter OFF: matches without Flashscore odds may be included.\n"
    text += ("Full-time prices are snapshots, not guaranteed offers. "
             "An active menu does not guarantee a quote for every selection.\n"
             "Trust is a heuristic score, not a win probability. No guarantees.\n")
    if parse_date(date_text)[1] < datetime.now(LOCAL_TZ).date():
        text += "⚠️ Past-date scan uses current form; not a pre-match backtest.\n"
    return text


def report_chunks(date_text: str, picks: List[Pick], fetched: int, analyzed: int,
                  skipped: int, min_grade: str) -> List[str]:
    min_rank = grade_rank(min_grade)
    selected = [p for p in best_per_match(picks) if grade_rank(p.grade) >= min_rank]
    selected = selected[:MAX_RESULTS]
    title = f"🛡 Flashscore Trust Picks — {html.escape(date_text)}"
    if not selected:
        return [
            f"{title}\n\n"
            + scan_summary(fetched, analyzed, skipped)
            + odds_policy(date_text)
            + f"No Grade {html.escape(min_grade)}+ picks found. That is intentional: this bot is strict.\n"
            "Try /setgrade B if you want more picks."
        ]
    header = (
        f"{title}\n\n"
        + scan_summary(fetched, analyzed, skipped)
        + f"Showing Grade <b>{html.escape(min_grade)}+</b>.\n"
        + odds_policy(date_text)
    )
    chunks = []
    current = header
    for i, p in enumerate(selected, 1):
        item = "\n" + format_pick(p, i)
        if len(current) + len(item) > 3600:
            chunks.append(current)
            current = f"{title} continued...\n" + item
        else:
            current += item
    if current.strip(): chunks.append(current)
    return chunks

# =========================
# ARB CALC - OPTIONAL
# =========================

def calc_arb(stake: float, odds: List[float]) -> str:
    inv = sum(1 / o for o in odds)
    if inv >= 1:
        return f"❌ No arbitrage. Implied probability: <b>{inv*100:.2f}%</b>. It must be below 100%."
    ret = stake / inv
    stakes = [stake * (1 / o) / inv for o in odds]
    lines = [f"✅ Arbitrage found. Profit: <b>{((ret-stake)/stake)*100:.2f}%</b>", f"Return: <b>₦{ret:,.2f}</b>", ""]
    for i, (o, s) in enumerate(zip(odds, stakes), 1):
        lines.append(f"Outcome {i} @ {o}: stake <b>₦{s:,.2f}</b>")
    return "\n".join(lines)

# =========================
# TELEGRAM COMMANDS
# =========================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "🛡 <b>Flashscore Trust Bot</b>\n\n"
        "This bot uses Flashscore recent-form/H2H data. No league is blacklisted. "
        "By default, only games with an active Flashscore odds menu qualify; bookmaker "
        "prices are displayed where the corresponding market is available. "
        "Odds do not change the original scoring.\n\n"
        "Commands:\n"
        "• <code>/banker 05-09-2026</code> — one ultra-strict pick or no bet\n"
        "• <code>/safe 05-09-2026</code> — short list, max 5 by default\n"
        "• <code>/setgrade A</code> — A is strict, B gives more, C gives many\n"
        "• <code>/setmax all</code> — scan every match Flashscore provides for the day\n"
        "• <code>/settings</code>\n"
        "• <code>/calc 10000 2.40 3.80 4.50</code>\n\n"
        "Trust is a heuristic score, not a win probability. No guaranteed wins. "
        "Prices can change or be historical. Past-date scans are not pre-match backtests."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    g, m = DB.get(update.effective_chat.id)
    max_text = "ALL" if m <= 0 else str(m)

    await update.message.reply_text(
        f"⚙️ Grade: <b>{html.escape(g)}</b>\n"
        f"Max fixtures: <b>{max_text}</b>\n"
        f"Concurrency: <b>{SCAN_CONCURRENCY}</b>\n"
        f"DB: <b>{'in-memory/no stored runs' if DB_PATH == ':memory:' else 'persistent chat settings/no stored runs'}</b>\n"
        f"Odds-only: <b>{'ON' if ODDS_ONLY else 'OFF'}</b>\n"
        f"Odds region: <b>{html.escape(ODDS_GEO)}/{html.escape(ODDS_GEO_SUB)}</b>\n"
        f"Odds depth: <b>{html.escape(ODDS_DEPTH)}</b>\n"
        f"Max bookmakers/market: <b>{ODDS_MAX_BOOKMAKERS}</b>",
        parse_mode=ParseMode.HTML,
    )

async def setgrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or context.args[0].upper() not in {"A+", "A", "B", "C"}:
        await update.message.reply_text("Usage: /setgrade A  — allowed: A+, A, B, C")
        return
    _, m = DB.get(update.effective_chat.id)
    DB.save(update.effective_chat.id, context.args[0].upper(), m)
    await update.message.reply_text(f"✅ Minimum grade set to {context.args[0].upper()}")

async def setmax_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /setmax all  or  /setmax 1500")
        return
    try:
        arg = context.args[0].lower().strip()
        if arg in {"all", "0", "full", "everything"}:
            m = 0
        else:
            m = int(arg)
            if m < 20 or m > 5000:
                raise ValueError
    except Exception:
        await update.message.reply_text("Max fixtures must be 'all' or a number from 20-5000. Example: /setmax all")
        return
    g, _ = DB.get(update.effective_chat.id)
    DB.save(update.effective_chat.id, g, m)
    await update.message.reply_text(f"✅ Max fixtures set to {'ALL' if m <= 0 else m}")

async def safe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /safe 05-09-2026")
        return
    date_text = context.args[0]
    try:
        parse_date(date_text)
    except Exception:
        await update.message.reply_text("Invalid date. Use dd-mm-yyyy. Example: /safe 05-09-2026")
        return
    min_grade, maxfx = DB.get(update.effective_chat.id)
    status = await update.message.reply_text(f"🛡 Scanning Flashscore strictly for {html.escape(date_text)}...\nGrade {min_grade}+ only. Weak games will be passed.")
    try:
        picks, fetched, analyzed, skipped = await scan(date_text, maxfx)
        chunks = report_chunks(date_text, picks, fetched, analyzed, skipped, min_grade)
        await status.edit_text(chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(0.25)
    except Exception as e:
        logger.exception("scan failed")
        await status.edit_text(f"❌ Scan failed: {html.escape(type(e).__name__)}: {html.escape(str(e))}", parse_mode=ParseMode.HTML)


def banker_report(date_text: str, picks: List[Pick], fetched: int, analyzed: int, skipped: int) -> str:
    # Ultra-strict money mode: one pick only, and only if it is genuinely top-class.
    candidates = [p for p in best_per_match(picks) if p.grade == "A+" and p.trust >= 94 and p.data_quality >= 84]
    title = f"🏦 Flashscore Banker Pick — {html.escape(date_text)}"
    if not candidates:
        top = best_per_match(picks)[:5]
        msg = (
            f"{title}\n\n"
            + scan_summary(fetched, analyzed, skipped)
            + odds_policy(date_text)
            + "No banker pick passed the ultra-strict filter. <b>No bet is better than forced bet.</b>\n"
        )
        if top:
            msg += "\nClosest candidates:\n"
            for i, p in enumerate(top, 1):
                msg += f"{i}. {html.escape(p.fixture.home)} vs {html.escape(p.fixture.away)} — {html.escape(p.selection)} — Trust {p.trust}/100, Data {p.data_quality}/100, Grade {p.grade}\n"
                msg += format_odds(p)
        return msg
    p = candidates[0]
    return (
        f"{title}\n\n"
        + scan_summary(fetched, analyzed, skipped)
        + odds_policy(date_text)
        + "Ultra-strict filter: <b>A+ only, Trust 94+, Data 84+</b>.\n\n"
        + format_pick(p, 1)
        + "\n⚠️ Still not 100%. Keep stake disciplined."
    )

def banker_chunks(report: str) -> List[str]:
    """Split long fallback reports at complete pick boundaries (balanced HTML)."""
    if len(report) <= 3600:
        return [report]
    parts = re.split(r"(?m)(?=^\d+\. )", report)
    chunks, current = [], ""
    for part in parts:
        if current and len(current) + len(part) > 3600:
            chunks.append(current.rstrip())
            current = "🏦 Banker report continued...\n"
        current += part
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def banker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /banker 05-09-2026")
        return
    date_text = context.args[0]
    try:
        parse_date(date_text)
    except Exception:
        await update.message.reply_text("Invalid date. Use dd-mm-yyyy. Example: /banker 05-09-2026")
        return
    _min_grade, maxfx = DB.get(update.effective_chat.id)
    status = await update.message.reply_text(
        f"🏦 Searching for ONE banker pick on {html.escape(date_text)}...\n"
        f"Scanning {'ALL' if maxfx <= 0 else maxfx} fixtures. If nothing is clean enough, I will say no bet."
    )
    try:
        picks, fetched, analyzed, skipped = await scan(date_text, maxfx)
        chunks = banker_chunks(banker_report(date_text, picks, fetched, analyzed, skipped))
        await status.edit_text(chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(0.25)
    except Exception as e:
        logger.exception("banker scan failed")
        await status.edit_text(f"❌ Banker scan failed: {html.escape(type(e).__name__)}: {html.escape(str(e))}", parse_mode=ParseMode.HTML)

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if len(context.args) < 3: raise ValueError
        stake = float(context.args[0].replace(",", ""))
        odds = [float(x.replace(",", "")) for x in context.args[1:]]
        if stake <= 0 or len(odds) < 2 or any(o <= 1 for o in odds): raise ValueError
    except Exception:
        await update.message.reply_text("Usage: /calc 10000 2.40 3.80 4.50")
        return
    await update.message.reply_text(calc_arb(stake, odds), parse_mode=ParseMode.HTML)

# =========================
# CLI / MAIN
# =========================

def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))

async def cli_safe(date_text: str) -> None:
    picks, fetched, analyzed, skipped = await scan(date_text, MAX_FIXTURES)
    print(strip_tags("\n\n".join(report_chunks(date_text, picks, fetched, analyzed, skipped, DEFAULT_MIN_GRADE))))

def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1].lower() == "safe":
        asyncio.run(cli_safe(sys.argv[2]))
        return
    if len(sys.argv) >= 3 and sys.argv[1].lower() == "banker":
        async def _cli_banker():
            picks, fetched, analyzed, skipped = await scan(sys.argv[2], MAX_FIXTURES)
            print(strip_tags(banker_report(sys.argv[2], picks, fetched, analyzed, skipped)))
        asyncio.run(_cli_banker())
        return
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "calc":
        if len(sys.argv) < 5:
            print("Usage: python flashscore_trust_bot.py calc 10000 2.40 3.80 4.50")
            return
        print(strip_tags(calc_arb(float(sys.argv[2]), [float(x) for x in sys.argv[3:]])))
        return
    if Application is None:
        print("Install: pip install python-telegram-bot==21.6 requests")
        sys.exit(1)
    if not TELEGRAM_BOT_TOKEN:
        print("Set TELEGRAM_BOT_TOKEN first.")
        sys.exit(1)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("setgrade", setgrade_cmd))
    app.add_handler(CommandHandler("setmax", setmax_cmd))
    app.add_handler(CommandHandler("safe", safe_cmd))
    app.add_handler(CommandHandler("banker", banker_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    logger.info("Flashscore Trust Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
    raise SystemExit
#!/usr/bin/env python3
"""
Flashscore Trust Bot - single-file football picks with Flashscore odds

Built for one purpose:
    Use ONLY Flashscore data and return only the matches where the stats are strong,
    clean, and aligned enough to be worth trusting more than normal predictions.

Very important truth:
    No football bot is 100%. This bot is designed to be STRICT, not noisy.
    It will PASS most matches. That is intentional.

Data source:
    Flashscore football feed + Flashscore H2H/recent-form feed.
    Flashscore's odds provider (ds.lsapp.eu), via the regional odds menu.
    No paid API. No hardcoded bookmaker dependency. ODDS_ONLY=1 by default.
    Odds filter/display only: the original scoring and thresholds are unchanged.
    Trust is a heuristic score, NOT a calibrated win probability.
    Prices are snapshots; historical scans are not leakage-free backtests.

Install:
    pip install python-telegram-bot==21.6 requests

Run:
    export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    python flashscore_trust_bot.py

Telegram commands:
    /start
    /banker 05-09-2026  # one ultra-strict pick or no bet
    /safe 05-09-2026
    /settings
    /setgrade A        # A, B, or C. A is strictest.
    /setmax all        # scan every fixture Flashscore provides for the day
    /calc 10000 2.40 3.80 4.50  # optional real arb calculator

CLI test:
    python flashscore_trust_bot.py safe 06-09-2026

Odds environment (optional):
    ODDS_ONLY=1 ODDS_GEO=NG ODDS_GEO_SUB=NGLA
    ODDS_MAX_BOOKMAKERS=3 ODDS_DEPTH=all ODDS_DELAY=0.02
    ODDS_DEPTH=1x2 shows 1/X/2 context only, not a selected-market price.
    Prices for total goals require the EXACT line; no nearest-line substitution.
    Double Chance is priced separately from DNB; BTTS No is a watch alternative,
    not an equivalent team-total-under-1.5 bet.
"""

# This duplicated section is unreachable; the active copy imports annotations above.

import asyncio
import html
import logging
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

try:
    from telegram import Update
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, ContextTypes
except ModuleNotFoundError:
    Update = object
    ContextTypes = object
    Application = None
    CommandHandler = None
    class ParseMode:
        HTML = "HTML"

# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Africa/Lagos"))
DB_PATH = os.getenv("TRUST_BOT_DB", ":memory:")  # no disk storage by default
MAX_FIXTURES = int(os.getenv("MAX_FIXTURES", "0"))  # 0 means scan ALL fixtures for the date
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "5"))
SCAN_CONCURRENCY = int(os.getenv("SCAN_CONCURRENCY", "8"))
DEFAULT_MIN_GRADE = os.getenv("DEFAULT_MIN_GRADE", "A").upper().strip()
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "35"))
DETAIL_DELAY = float(os.getenv("DETAIL_DELAY", "0.05"))

# ---- Flashscore odds: filter + display, never an input to scoring ----
ODDS_API = "https://global.ds.lsapp.eu/odds/pq_graphql"
ODDS_GEO = os.getenv("ODDS_GEO", "NG").strip().upper() or "NG"
ODDS_GEO_SUB = os.getenv("ODDS_GEO_SUB", "NGLA").strip() or "NGLA"
ODDS_ONLY = os.getenv("ODDS_ONLY", "1").strip() != "0"
ODDS_MAX_BOOKMAKERS = max(1, int(os.getenv("ODDS_MAX_BOOKMAKERS", "3")))
ODDS_DEPTH = os.getenv("ODDS_DEPTH", "all").lower().strip()
ODDS_DELAY = max(0.0, float(os.getenv("ODDS_DELAY", "0.02")))
if ODDS_DEPTH not in {"all", "1x2"}:
    raise ValueError("ODDS_DEPTH must be 'all' or '1x2'")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("flashscore_trust_bot")

# =========================
# MODELS
# =========================

@dataclass
class Fixture:
    match_id: str
    date: str
    time: str
    country: str
    league: str
    home: str
    away: str
    home_id: str = ""  # JA: eventParticipantId, not the URL/slug id
    away_id: str = ""  # JB

@dataclass
class ResultRow:
    section: str
    home: str
    away: str
    hg: int
    ag: int
    marker: str
    league: str = ""

@dataclass
class TeamForm:
    team: str
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    gf: int = 0
    ga: int = 0
    clean_sheets: int = 0
    failed_to_score: int = 0
    over15: int = 0
    over25: int = 0
    btts: int = 0
    results: List[str] = field(default_factory=list)

    @property
    def points(self) -> int: return self.wins * 3 + self.draws
    @property
    def ppg(self) -> float: return self.points / self.games if self.games else 0.0
    @property
    def gf_avg(self) -> float: return self.gf / self.games if self.games else 0.0
    @property
    def ga_avg(self) -> float: return self.ga / self.games if self.games else 0.0
    @property
    def tg_avg(self) -> float: return (self.gf + self.ga) / self.games if self.games else 0.0
    @property
    def win_rate(self) -> float: return self.wins / self.games if self.games else 0.0
    @property
    def draw_rate(self) -> float: return self.draws / self.games if self.games else 0.0
    @property
    def loss_rate(self) -> float: return self.losses / self.games if self.games else 0.0
    @property
    def cs_rate(self) -> float: return self.clean_sheets / self.games if self.games else 0.0
    @property
    def fts_rate(self) -> float: return self.failed_to_score / self.games if self.games else 0.0
    @property
    def over15_rate(self) -> float: return self.over15 / self.games if self.games else 0.0
    @property
    def over25_rate(self) -> float: return self.over25 / self.games if self.games else 0.0
    @property
    def btts_rate(self) -> float: return self.btts / self.games if self.games else 0.0

@dataclass
class PickOdds:
    bookmaker: str = ""
    market_label: str = ""
    selection: str = ""
    value: float = 0.0
    opening: float = 0.0
    move: str = ""
    line: str = ""
    all_prices: str = ""  # Same market/line only, among checked bookmakers

@dataclass
class Pick:
    fixture: Fixture
    market: str
    selection: str
    grade: str
    trust: int
    data_quality: int
    reasons: List[str]
    home_form: TeamForm
    away_form: TeamForm
    odds: Optional[PickOdds] = None
    selection_side: str = ""  # Display metadata: home/away, never fuzzy name matching
    odds_1x2: str = ""  # Context only, never a DC/DNB/goals quote
    odds_note: str = ""

# =========================
# SETTINGS DB
# =========================

class SettingsDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS settings(
                chat_id TEXT PRIMARY KEY,
                min_grade TEXT NOT NULL,
                max_fixtures INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def get(self, chat_id: int) -> Tuple[str, int]:
        row = self.conn.execute("SELECT min_grade,max_fixtures FROM settings WHERE chat_id=?", (str(chat_id),)).fetchone()
        if not row:
            return DEFAULT_MIN_GRADE, MAX_FIXTURES
        return str(row["min_grade"]), int(row["max_fixtures"])

    def save(self, chat_id: int, grade: str, max_fixtures: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO settings(chat_id,min_grade,max_fixtures) VALUES(?,?,?)",
            (str(chat_id), grade, max_fixtures),
        )
        self.conn.commit()

DB = SettingsDB(DB_PATH)

# =========================
# HELPERS
# =========================

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def norm_team(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", clean(name)).strip()

def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm_team(text).lower())

def contains(a: str, b: str) -> bool:
    ka, kb = key(a), key(b)
    return bool(ka and kb and (ka in kb or kb in ka))

def parse_date(date_text: str) -> Tuple[str, datetime.date]:
    d = datetime.strptime(date_text.strip(), "%d-%m-%Y").date()
    return d.strftime("%d-%m-%Y"), d

def grade_rank(g: str) -> int:
    return {"A+": 4, "A": 3, "B": 2, "C": 1}.get(g.upper(), 3)

def grade_from_score(score: int) -> str:
    if score >= 92: return "A+"
    if score >= 84: return "A"
    if score >= 76: return "B"
    return "C"

def is_low_coverage_competition(f: Fixture) -> bool:
    # No league blacklist. A match is allowed if Flashscore provides enough
    # complete stats for the prediction engine, regardless of league/country.
    return False

def fixture_priority(f: Fixture) -> int:
    # No league blacklist or preference. Keep Flashscore order.
    # The real filter happens after detail scraping: both teams must have enough
    # recent-match stats for the bot's prediction engine.
    return 0

# =========================
# FLASHSCORE FEED CLIENT
# =========================

class OddsAPIError(RuntimeError):
    """Malformed/failed GraphQL response; fail closed when odds-only is on."""


class Flashscore:
    def __init__(self):
        self.session = requests.Session()
        # A client belongs to one scan worker. No cross-thread shared sessions,
        # no disk odds cache, and no stale prices retained across scan commands.
        # Cache both successes and failures, once per menu or book/market/scope.
        self._odds_cache: Dict[Tuple[Any, ...], Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.session.close()

    def odds_headers(self, match_id: str) -> Dict[str, str]:
        return self.headers(f"https://www.flashscore.com/match/{match_id}/")

    def _odds_query(self, cache_key: Tuple[Any, ...], match_id: str,
                    params: Dict[str, Any], operation: str) -> Dict[str, Any]:
        if cache_key in self._odds_cache:
            cached = self._odds_cache[cache_key]
            if isinstance(cached, Exception):
                raise cached
            return cached
        # This entire client is used inside asyncio.to_thread, including sleep.
        if ODDS_DELAY:
            time.sleep(ODDS_DELAY)
        try:
            r = self.session.get(ODDS_API, params=params,
                                 headers=self.odds_headers(match_id), timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, dict) or payload.get("errors"):
                raise OddsAPIError(f"{operation}: invalid response or GraphQL errors")
            data = payload.get("data")
            if not isinstance(data, dict) or operation not in data:
                raise OddsAPIError(f"{operation}: missing GraphQL operation data")
            node = data[operation]
            if node is None:
                node = {}
            if not isinstance(node, dict):
                raise OddsAPIError(f"{operation}: expected an object")
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            self._odds_cache[cache_key] = e
            raise
        self._odds_cache[cache_key] = node
        return node

    def get_odds_menu(self, match_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return bookmakers and ACTIVE markets; an empty menu fails the gate."""
        params = {"_hash": "pobtm", "eventId": match_id, "projectId": "2",
                  "geoIpCode": ODDS_GEO, "geoIpSubdivisionCode": ODDS_GEO_SUB}
        data = self._odds_query(("menu", match_id, ODDS_GEO, ODDS_GEO_SUB), match_id,
                                params, "getPrematchOddsBettingTypeMenu")
        settings = data.get("settings") or {}
        if not isinstance(settings, dict):
            raise OddsAPIError("Invalid bookmaker settings")
        raw_books, raw_markets = settings.get("bookmakers") or [], data.get("items") or []
        if not isinstance(raw_books, list) or not isinstance(raw_markets, list):
            raise OddsAPIError("Invalid bookmaker/market lists")
        books: List[Dict[str, Any]] = []
        seen_books = set()
        for entry in raw_books:
            b = entry.get("bookmaker") if isinstance(entry, dict) else None
            if not isinstance(b, dict):
                continue
            try:
                bid = int(b["id"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if bid <= 0 or bid in seen_books or not isinstance(b.get("name"), str):
                continue
            name = clean(b["name"])
            if not name:
                continue
            seen_books.add(bid)
            books.append({"id": bid, "name": name})
        markets: List[Dict[str, Any]] = []
        for item in raw_markets:
            if not isinstance(item, dict) or item.get("isActive") is not True:
                continue
            kind, scope = item.get("bettingType"), item.get("bettingScope")
            if not isinstance(kind, str) or not isinstance(scope, str) or not kind or not scope:
                continue
            market: Dict[str, Any] = {"type": kind, "scope": scope}
            # Some menus identify the exact books offering each market.
            ids = item.get("bookmakerIds")
            if isinstance(ids, list):
                market["bookmaker_ids"] = [str(bid) for bid in ids]
            markets.append(market)
        return books, markets

    def get_odds_for(self, match_id: str, bookmaker_id: int,
                     bet_type: str, bet_scope: str = "FULL_TIME") -> Dict[str, Any]:
        params = {"_hash": "ope2", "eventId": match_id, "bookmakerId": bookmaker_id,
                  "betType": bet_type, "betScope": bet_scope}
        data = self._odds_query(("price", match_id, bookmaker_id, bet_type, bet_scope),
                                match_id, params, "findPrematchOddsForBookmaker")
        # Never accept a different market/book in a malformed response.
        if data.get("type") not in (None, bet_type):
            raise OddsAPIError("Returned odds market does not match the request")
        if data.get("bookmakerId") is not None and str(data["bookmakerId"]) != str(bookmaker_id):
            raise OddsAPIError("Returned bookmaker does not match the request")
        return data

    def headers(self, referer: str = "https://www.flashscore.com/football/") -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "*/*",
            "Referer": referer,
            "x-fsign": "SW9D1eZo",
        }

    def records(self, text: str) -> List[Dict[str, str]]:
        out = []
        if not text or text.strip() == "0": return out
        for rec in text.split("¬~"):
            item = {}
            for part in rec.split("¬"):
                if "÷" in part:
                    k, v = part.split("÷", 1)
                    item[k] = v
            if item: out.append(item)
        return out

    def get_fixtures(self, date_text: str) -> List[Fixture]:
        _, target = parse_date(date_text)
        today = datetime.now(LOCAL_TZ).date()
        offset = (target - today).days
        url = f"https://www.flashscore.com/x/feed/f_1_{offset}_2_en_1"
        logger.info("Fetching fixtures offset=%s url=%s", offset, url)
        r = self.session.get(url, headers=self.headers(), timeout=HTTP_TIMEOUT)
        r.raise_for_status()

        country = league = ""
        fixtures: List[Fixture] = []
        for rec in self.records(r.text):
            if "ZA" in rec:
                title = clean(rec.get("ZA", ""))
                if ":" in title:
                    country, league = [clean(x) for x in title.split(":", 1)]
                else:
                    league = title
                continue
            if "AA" not in rec: continue
            mid = rec.get("AA", "")
            home = norm_team(rec.get("AE") or rec.get("CX") or "")
            away = norm_team(rec.get("AF") or "")
            if not mid or not home or not away: continue
            ts = rec.get("AD") or rec.get("ADE") or ""
            try:
                dt = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(LOCAL_TZ)
                if dt.date() != target: continue
                t = dt.strftime("%H:%M")
            except Exception:
                t = ""
            fixtures.append(Fixture(mid, target.strftime("%d-%m-%Y"), t, country, league, home, away,
                                    home_id=rec.get("JA", ""), away_id=rec.get("JB", "")))
        fixtures.sort(key=fixture_priority, reverse=True)
        logger.info("Fixtures found exact date: %d", len(fixtures))
        return fixtures

    def get_rows(self, fixture: Fixture) -> List[ResultRow]:
        url = f"https://www.flashscore.com/x/feed/df_hh_1_{fixture.match_id}"
        ref = f"https://www.flashscore.com/match/{fixture.match_id}/"
        try:
            r = self.session.get(url, headers=self.headers(ref), timeout=HTTP_TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            logger.warning("detail failed %s: %s", fixture.match_id, e)
            return []

        rows: List[ResultRow] = []
        section = ""
        for rec in self.records(r.text):
            if "KB" in rec:
                section = clean(rec.get("KB", ""))
                continue
            if "KC" not in rec: continue
            score = rec.get("KL", "")
            m = re.search(r"(\d{1,2})\s*[:\-]\s*(\d{1,2})", score)
            if not m: continue
            home = norm_team((rec.get("KJ") or rec.get("FH") or "").replace("*", ""))
            away = norm_team((rec.get("KK") or rec.get("FK") or "").replace("*", ""))
            marker = clean(rec.get("WIS", "")).upper()[:1]
            if marker not in {"W", "D", "L"}: marker = ""
            rows.append(ResultRow(section, home, away, int(m.group(1)), int(m.group(2)), marker, rec.get("KF", "")))
        return rows

# =========================
# FORM BUILDING
# =========================

def add_result(form: TeamForm, res: str, gf: int, ga: int) -> None:
    form.games += 1
    form.gf += gf
    form.ga += ga
    form.results.append(res)
    if res == "W": form.wins += 1
    elif res == "D": form.draws += 1
    else: form.losses += 1
    if ga == 0: form.clean_sheets += 1
    if gf == 0: form.failed_to_score += 1
    if gf + ga >= 2: form.over15 += 1
    if gf + ga >= 3: form.over25 += 1
    if gf > 0 and ga > 0: form.btts += 1

def build_forms(fixture: Fixture, rows: List[ResultRow]) -> Tuple[TeamForm, TeamForm, int]:
    hf, af = TeamForm(fixture.home), TeamForm(fixture.away)
    h2h_count = 0
    for r in rows:
        sec = r.section.lower()
        if contains(r.home, fixture.home) and contains(r.away, fixture.away) or contains(r.home, fixture.away) and contains(r.away, fixture.home):
            h2h_count += 1

        target = None
        if contains(r.section, fixture.home): target = hf
        elif contains(r.section, fixture.away): target = af
        elif "last matches" in sec:
            if contains(r.home, fixture.home) or contains(r.away, fixture.home): target = hf
            elif contains(r.home, fixture.away) or contains(r.away, fixture.away): target = af
        else:
            continue

        if target.games >= 10: continue
        if r.marker in {"W", "D", "L"}:
            if r.marker == "W": gf, ga = max(r.hg, r.ag), min(r.hg, r.ag)
            elif r.marker == "L": gf, ga = min(r.hg, r.ag), max(r.hg, r.ag)
            else: gf, ga = r.hg, r.ag
            add_result(target, r.marker, gf, ga)
        else:
            tk = key(target.team)
            if tk == key(r.home):
                res = "W" if r.hg > r.ag else "D" if r.hg == r.ag else "L"
                add_result(target, res, r.hg, r.ag)
            elif tk == key(r.away):
                res = "W" if r.ag > r.hg else "D" if r.hg == r.ag else "L"
                add_result(target, res, r.ag, r.hg)
    return hf, af, h2h_count

# =========================
# PICK ENGINE - STRICT BY DESIGN
# =========================

def data_quality(hf: TeamForm, af: TeamForm, h2h_count: int, fixture: Fixture) -> int:
    # Completeness-first scoring. No league blacklist.
    # If both teams have 10 recent matches, that alone is strong enough data.
    q = 0
    for f in [hf, af]:
        if f.games >= 10: q += 42
        elif f.games >= 8: q += 34
        elif f.games >= 6: q += 24
        elif f.games >= 4: q += 12
    # H2H is useful but not mandatory, because many valid fixtures have little/no H2H.
    if h2h_count >= 5: q += 16
    elif h2h_count >= 3: q += 10
    return max(0, min(100, q))

def power(f: TeamForm) -> float:
    if not f.games: return 0.0
    return f.ppg * 25 + f.win_rate * 18 - f.loss_rate * 12 + (f.gf_avg - f.ga_avg) * 12 + f.cs_rate * 6 - f.fts_rate * 6

def make_picks(fixture: Fixture, hf: TeamForm, af: TeamForm, h2h_count: int) -> List[Pick]:
    q = data_quality(hf, af, h2h_count, fixture)
    # Main completeness gate: both teams must have enough Flashscore recent-form data.
    # This is the only hard filter. No league/country blacklist.
    if q < 68 or hf.games < 8 or af.games < 8:
        return []

    picks: List[Pick] = []
    hp, ap = power(hf), power(af)
    fav_name, dog_name = (fixture.home, fixture.away) if hp >= ap else (fixture.away, fixture.home)
    fav, dog = (hf, af) if hp >= ap else (af, hf)
    gap = abs(hp - ap)

    # Safer-side pick: Draw No Bet / Double Chance, not blind win.
    trust = 0; reasons = []
    if gap >= 34: trust += 28; reasons.append("major strength gap")
    elif gap >= 24: trust += 20; reasons.append("clear strength gap")
    if fav.ppg >= 2.0 and dog.ppg <= 1.1: trust += 20; reasons.append("form points strongly favor selection")
    if fav.win_rate >= 0.65 and dog.loss_rate >= 0.45: trust += 18; reasons.append("win/loss profile aligned")
    if fav.ga_avg <= 1.1 and dog.gf_avg <= 1.2: trust += 12; reasons.append("defence vs weak attack supports safer side")
    if fav_name == fixture.home: trust += 7; reasons.append("home edge")
    trust += int(q * 0.15)
    if trust >= 76:
        market = "Safer side"
        selection = f"{fav_name} Draw No Bet / Double Chance"
        picks.append(Pick(fixture, market, selection, grade_from_score(trust), min(99, trust), q, reasons, hf, af,
                          selection_side="home" if hp >= ap else "away"))

    # Over 1.5 goals: conservative goal pick.
    trust = 0; reasons = []
    if hf.over15_rate >= 0.75 and af.over15_rate >= 0.75: trust += 28; reasons.append("both teams trend over 1.5")
    elif (hf.over15_rate + af.over15_rate) / 2 >= 0.70: trust += 20; reasons.append("combined over 1.5 trend is strong")
    if hf.tg_avg >= 2.5 and af.tg_avg >= 2.5: trust += 18; reasons.append("match goal averages support goals")
    if hf.gf_avg + af.gf_avg >= 2.2: trust += 12; reasons.append("both attacks contribute enough")
    if hf.fts_rate <= 0.25 and af.fts_rate <= 0.25: trust += 10; reasons.append("low failed-to-score rates")
    trust += int(q * 0.18)
    if trust >= 78:
        picks.append(Pick(fixture, "Goals", "Over 1.5 goals", grade_from_score(trust), min(99, trust), q, reasons, hf, af))

    # Under 4.5 goals: often safer if both teams are not crazy high scoring.
    trust = 0; reasons = []
    if hf.tg_avg <= 3.2 and af.tg_avg <= 3.2: trust += 24; reasons.append("both teams stay under extreme goal totals")
    if hf.ga_avg <= 1.7 and af.ga_avg <= 1.7: trust += 14; reasons.append("conceding rates not too high")
    if hf.gf_avg <= 2.4 and af.gf_avg <= 2.4: trust += 12; reasons.append("scoring rates not explosive")
    if hf.games >= 8 and af.games >= 8: trust += 15; reasons.append("enough recent matches")
    trust += int(q * 0.15)
    if trust >= 76:
        picks.append(Pick(fixture, "Goals", "Under 4.5 goals", grade_from_score(trust), min(99, trust), q, reasons, hf, af))

    # Underdog team total under 1.5 / BTTS No angle.
    trust = 0; reasons = []
    if fav.ga_avg <= 0.9 and dog.gf_avg <= 1.0: trust += 24; reasons.append("strong defence vs weak attack")
    if fav.cs_rate >= 0.40 and dog.fts_rate >= 0.30: trust += 20; reasons.append("clean-sheet/failed-score pattern aligned")
    if dog.loss_rate >= 0.45: trust += 10; reasons.append("underdog loss trend")
    trust += int(q * 0.16)
    if trust >= 78:
        picks.append(Pick(fixture, "Team goals", f"{dog_name} under 1.5 team goals / BTTS No watch", grade_from_score(trust), min(99, trust), q, reasons, hf, af))

    return picks

# =========================
# ODDS MAPPING - DISPLAY ONLY
# =========================

def _to_f(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _active_price(item: Any) -> float:
    if not isinstance(item, dict) or item.get("active") is not True:
        return 0.0
    price = _to_f(item.get("value"))
    return price if price > 1.0 else 0.0


def _participant_item(node: Dict[str, Any], fx: Fixture, side: str,
                      home_field: str, away_field: str) -> Optional[Dict[str, Any]]:
    """Prefer JA/JB participant IDs; use semantic API keys only without IDs."""
    if side not in {"home", "away"}:
        return None
    expected_id = fx.home_id if side == "home" else fx.away_id
    items = [node.get(home_field), node.get(away_field)]
    items = [item for item in items if isinstance(item, dict)]
    if expected_id and any(item.get("eventParticipantId") for item in items):
        matches = [item for item in items if str(item.get("eventParticipantId")) == expected_id]
        return matches[0] if len(matches) == 1 else None
    item = node.get(home_field if side == "home" else away_field)
    return item if isinstance(item, dict) else None


def exact_ou(node: Dict[str, Any], target: float, side: str) -> Optional[Dict[str, Any]]:
    """Keep the complete ACTIVE item on the EXACT goal line; never use nearest."""
    if side not in {"over", "under"}:
        return None
    opportunities = node.get("opportunities") or []
    if not isinstance(opportunities, list):
        return None
    best = None
    for opp in opportunities:
        if not isinstance(opp, dict):
            continue
        handicap = opp.get("handicap") or {}
        if not isinstance(handicap, dict) or _to_f(handicap.get("value")) != target:
            continue
        item = opp.get(side)
        if _active_price(item) and (best is None or _active_price(item) > _active_price(best)):
            best = item
    return best


def _market_books(books: List[Dict[str, Any]], markets: List[Dict[str, Any]],
                  bet_type: str) -> List[Dict[str, Any]]:
    active = [m for m in markets if m["type"] == bet_type and m["scope"] == "FULL_TIME"]
    if not active:
        return []
    unrestricted = any("bookmaker_ids" not in m for m in active)
    allowed = {str(bid) for m in active for bid in m.get("bookmaker_ids", [])}
    return [b for b in books if unrestricted or str(b["id"]) in allowed][:ODDS_MAX_BOOKMAKERS]


def attach_1x2_context(pick: Pick, fx: Fixture, books: List[Dict[str, Any]],
                       markets: List[Dict[str, Any]], client: Flashscore) -> None:
    summaries = []
    for book in _market_books(books, markets, "HOME_DRAW_AWAY"):
        try:
            node = client.get_odds_for(fx.match_id, book["id"], "HOME_DRAW_AWAY")
            items = [_participant_item(node, fx, "home", "home", "away"), node.get("draw"),
                     _participant_item(node, fx, "away", "home", "away")]
            prices = [f"{_active_price(item):.2f}" if _active_price(item) else "—" for item in items]
            if any(_active_price(item) for item in items):
                summaries.append(f"{book['name']} {' / '.join(prices)}")
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            logger.warning("1X2 odds failed %s book=%s: %s", fx.match_id, book["id"], e)
    pick.odds_1x2 = " | ".join(summaries)
    pick.odds_note = "Selected-market quote not requested (ODDS_DEPTH=1x2)."
    if not summaries:
        pick.odds_note += " No active full-time 1X2 prices returned."


def attach_odds(pick: Pick, fx: Fixture, books: List[Dict[str, Any]],
                client: Flashscore, markets: Optional[List[Dict[str, Any]]] = None) -> None:
    """Attach best checked price without changing a selection, grade or score.

    Called in the fixture worker thread. DC quotes are not DNB quotes. The
    original Team goals selection includes a 'BTTS No watch' alternative; that
    price is labelled explicitly, never passed off as the team-total price.
    """
    pick.odds, pick.odds_1x2, pick.odds_note = None, "", ""
    if markets is None:
        _, markets = client.get_odds_menu(fx.match_id)  # cached
    if ODDS_DEPTH == "1x2":
        attach_1x2_context(pick, fx, books, markets, client)
        return

    line = ""
    side = ""
    note = ""
    if pick.market == "Safer side" and pick.selection_side in {"home", "away"}:
        bet_type, label = "DOUBLE_CHANCE", "Double Chance"
        selection = "1X" if pick.selection_side == "home" else "X2"
        note = "Double Chance quote only; not Draw No Bet."
    elif pick.market == "Goals" and pick.selection == "Over 1.5 goals":
        bet_type, label, selection, line, side = "OVER_UNDER", "Goals", "Over", "1.5", "over"
    elif pick.market == "Goals" and pick.selection == "Under 4.5 goals":
        bet_type, label, selection, line, side = "OVER_UNDER", "Goals", "Under", "4.5", "under"
    elif pick.market == "Team goals" and pick.selection.endswith(" / BTTS No watch"):
        bet_type, label, selection = "BOTH_TEAMS_TO_SCORE", "BTTS", "No"
        note = "BTTS No watch alternative only; NOT a team-total-under-1.5 quote."
    else:
        pick.odds_note = "No supported exact-market odds mapping for this selection."
        return

    candidates = []
    for book in _market_books(books, markets, bet_type):
        try:
            node = client.get_odds_for(fx.match_id, book["id"], bet_type, "FULL_TIME")
            if bet_type == "DOUBLE_CHANCE":
                item = _participant_item(node, fx, pick.selection_side, "homeOrDraw", "awayOrDraw")
            elif bet_type == "OVER_UNDER":
                item = exact_ou(node, float(line), side)
            else:
                item = node.get("no")
            value = _active_price(item)
            if not value:
                continue
            opening = _to_f(item.get("opening"))
            change = item.get("change") or {}
            move = change.get("type", "") if isinstance(change, dict) else ""
            candidates.append(PickOdds(
                bookmaker=book["name"], market_label=label, selection=selection, value=value,
                opening=opening if opening > 1.0 else 0.0,
                move=move if move in {"UP", "DOWN"} else "", line=line,
            ))
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            logger.warning("odds price failed %s book=%s market=%s: %s", fx.match_id, book["id"], bet_type, e)
    if not candidates:
        pick.odds_note = "No active quote returned for this market/exact line among checked bookmakers."
        if note:
            pick.odds_note += " " + note
        return
    best = max(candidates, key=lambda quote: quote.value)
    if len(candidates) > 1:
        best.all_prices = " | ".join(f"{quote.bookmaker} {quote.value:.2f}" for quote in candidates)
    pick.odds, pick.odds_note = best, note

# =========================
# SCAN + FORMAT
# =========================

def _analyze_fixture(fx: Fixture) -> Tuple[List[Pick], bool]:
    """All requests (including attaching odds) stay off the asyncio event loop."""
    with Flashscore() as client:
        try:
            books, markets = client.get_odds_menu(fx.match_id)
        except (requests.RequestException, ValueError, OddsAPIError) as e:
            logger.warning("odds menu failed %s: %s", fx.match_id, e)
            books, markets = [], []
        if ODDS_ONLY and not markets:
            logger.info("no active Flashscore odds / lookup unavailable -> skip: %s %s vs %s",
                        fx.match_id, fx.home, fx.away)
            return [], False
        rows = client.get_rows(fx)
        hf, af, h2h = build_forms(fx, rows)
        picks = make_picks(fx, hf, af, h2h)
        for pick in picks:
            attach_odds(pick, fx, books, client, markets)
        return picks, True


async def scan(date_text: str, max_fixtures: int) -> Tuple[List[Pick], int, int, int]:
    with Flashscore() as fs:
        all_fixtures = await asyncio.to_thread(fs.get_fixtures, date_text)
    # Preserve feed order, but never request a duplicated match id twice per run.
    all_fixtures = list({fx.match_id: fx for fx in all_fixtures}.values())
    fixtures = all_fixtures[:max_fixtures] if max_fixtures > 0 else all_fixtures
    sem = asyncio.Semaphore(max(1, SCAN_CONCURRENCY))

    async def analyze_one(i: int, fx: Fixture) -> Tuple[List[Pick], bool]:
        async with sem:
            logger.info("%d/%d %s vs %s", i, len(fixtures), fx.home, fx.away)
            result = await asyncio.to_thread(_analyze_fixture, fx)
            if DETAIL_DELAY > 0:
                await asyncio.sleep(DETAIL_DELAY)
            return result

    results = await asyncio.gather(*(analyze_one(i, fx) for i, fx in enumerate(fixtures, 1)))
    all_picks = [pick for picks, _ in results for pick in picks]
    skipped = sum(1 for _, analyzed in results if not analyzed)
    analyzed = len(fixtures) - skipped  # Actual form analyses, not attempted menus
    all_picks.sort(key=lambda p: (grade_rank(p.grade), p.trust, p.data_quality), reverse=True)
    logger.info("Scan complete: fetched=%d checked=%d analyzed=%d skipped_odds=%d picks=%d",
                len(all_fixtures), len(fixtures), analyzed, skipped, len(all_picks))
    return all_picks, len(all_fixtures), analyzed, skipped

def form_line(f: TeamForm) -> str:
    seq = "".join(f.results[:10]) or "-"
    return f"{f.points}/{f.games}pts, GF {f.gf_avg:.1f}, GA {f.ga_avg:.1f}, {seq}"

def format_odds(p: Pick) -> str:
    lines = []
    if p.odds:
        odds = p.odds
        label = f"{odds.market_label} {odds.selection}" + (f" {odds.line}" if odds.line else "")
        arrow = {"UP": "↑", "DOWN": "↓"}.get(odds.move, "")
        opening = f" (open {odds.opening:.2f})" if odds.opening else ""
        movement = f" {arrow}" if arrow else ""
        lines.append(f"💰 FT {html.escape(label)}: <b>{odds.value:.2f}</b> @ "
                     f"{html.escape(odds.bookmaker)}{movement}{opening}")
        if odds.all_prices:
            lines.append("Checked prices: " + html.escape(odds.all_prices))
    if p.odds_1x2:
        lines.append("💰 FT 1X2 context ONLY (1 / X / 2): " + html.escape(p.odds_1x2))
    if p.odds_note:
        lines.append(html.escape(p.odds_note))
    elif not p.odds and not p.odds_1x2:
        lines.append("Selected-market quote unavailable.")
    return "\n".join(lines) + "\n"


def format_pick(p: Pick, idx: int) -> str:
    fx = p.fixture
    return (
        f"<b>{idx}. {html.escape(fx.home)} vs {html.escape(fx.away)}</b>\n"
        f"{html.escape((fx.country + ' ' + fx.league).strip())} | {html.escape(fx.time or 'N/A')}\n"
        f"✅ <b>{html.escape(p.selection)}</b>\n"
        f"Grade: <b>{p.grade}</b> | Trust: <b>{p.trust}/100</b> | Data: <b>{p.data_quality}/100</b>\n"
        f"H: {html.escape(form_line(p.home_form))}\n"
        f"A: {html.escape(form_line(p.away_form))}\n"
        + format_odds(p)
    )


def best_per_match(picks: List[Pick]) -> List[Pick]:
    """Keep only the single strongest pick per fixture, so one match doesn't flood output."""
    best: Dict[str, Pick] = {}
    for p in picks:
        old = best.get(p.fixture.match_id)
        if old is None or (grade_rank(p.grade), p.trust, p.data_quality) > (grade_rank(old.grade), old.trust, old.data_quality):
            best[p.fixture.match_id] = p
    out = list(best.values())
    out.sort(key=lambda p: (grade_rank(p.grade), p.trust, p.data_quality), reverse=True)
    return out

def scan_summary(fetched: int, analyzed: int, skipped: int) -> str:
    return (f"Fetched/analyzed: <b>{fetched}/{analyzed}</b> fixtures. "
            f"Checked: <b>{analyzed + skipped}</b>.\n"
            f"Skipped: <b>{skipped}</b> — no active Flashscore odds or lookup failed.\n")


def odds_policy(date_text: str) -> str:
    if ODDS_ONLY:
        text = "Odds filter ON: only matches with an active Flashscore odds menu qualify.\n"
    else:
        text = "Odds filter OFF: matches without Flashscore odds may be included.\n"
    text += ("Full-time prices are snapshots, not guaranteed offers. "
             "An active menu does not guarantee a quote for every selection.\n"
             "Trust is a heuristic score, not a win probability. No guarantees.\n")
    if parse_date(date_text)[1] < datetime.now(LOCAL_TZ).date():
        text += "⚠️ Past-date scan uses current form; not a pre-match backtest.\n"
    return text


def report_chunks(date_text: str, picks: List[Pick], fetched: int, analyzed: int,
                  skipped: int, min_grade: str) -> List[str]:
    min_rank = grade_rank(min_grade)
    selected = [p for p in best_per_match(picks) if grade_rank(p.grade) >= min_rank]
    selected = selected[:MAX_RESULTS]
    title = f"🛡 Flashscore Trust Picks — {html.escape(date_text)}"
    if not selected:
        return [
            f"{title}\n\n"
            + scan_summary(fetched, analyzed, skipped)
            + odds_policy(date_text)
            + f"No Grade {html.escape(min_grade)}+ picks found. That is intentional: this bot is strict.\n"
            "Try /setgrade B if you want more picks."
        ]
    header = (
        f"{title}\n\n"
        + scan_summary(fetched, analyzed, skipped)
        + f"Showing Grade <b>{html.escape(min_grade)}+</b>.\n"
        + odds_policy(date_text)
    )
    chunks = []
    current = header
    for i, p in enumerate(selected, 1):
        item = "\n" + format_pick(p, i)
        if len(current) + len(item) > 3600:
            chunks.append(current)
            current = f"{title} continued...\n" + item
        else:
            current += item
    if current.strip(): chunks.append(current)
    return chunks

# =========================
# ARB CALC - OPTIONAL
# =========================

def calc_arb(stake: float, odds: List[float]) -> str:
    inv = sum(1 / o for o in odds)
    if inv >= 1:
        return f"❌ No arbitrage. Implied probability: <b>{inv*100:.2f}%</b>. It must be below 100%."
    ret = stake / inv
    stakes = [stake * (1 / o) / inv for o in odds]
    lines = [f"✅ Arbitrage found. Profit: <b>{((ret-stake)/stake)*100:.2f}%</b>", f"Return: <b>₦{ret:,.2f}</b>", ""]
    for i, (o, s) in enumerate(zip(odds, stakes), 1):
        lines.append(f"Outcome {i} @ {o}: stake <b>₦{s:,.2f}</b>")
    return "\n".join(lines)

# =========================
# TELEGRAM COMMANDS
# =========================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "🛡 <b>Flashscore Trust Bot</b>\n\n"
        "This bot uses Flashscore recent-form/H2H data. No league is blacklisted. "
        "By default, only games with an active Flashscore odds menu qualify; bookmaker "
        "prices are displayed where the corresponding market is available. "
        "Odds do not change the original scoring.\n\n"
        "Commands:\n"
        "• <code>/banker 05-09-2026</code> — one ultra-strict pick or no bet\n"
        "• <code>/safe 05-09-2026</code> — short list, max 5 by default\n"
        "• <code>/setgrade A</code> — A is strict, B gives more, C gives many\n"
        "• <code>/setmax all</code> — scan every match Flashscore provides for the day\n"
        "• <code>/settings</code>\n"
        "• <code>/calc 10000 2.40 3.80 4.50</code>\n\n"
        "Trust is a heuristic score, not a win probability. No guaranteed wins. "
        "Prices can change or be historical. Past-date scans are not pre-match backtests."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    g, m = DB.get(update.effective_chat.id)
    max_text = "ALL" if m <= 0 else str(m)

    await update.message.reply_text(
        f"⚙️ Grade: <b>{html.escape(g)}</b>\n"
        f"Max fixtures: <b>{max_text}</b>\n"
        f"Concurrency: <b>{SCAN_CONCURRENCY}</b>\n"
        f"DB: <b>{'in-memory/no stored runs' if DB_PATH == ':memory:' else 'persistent chat settings/no stored runs'}</b>\n"
        f"Odds-only: <b>{'ON' if ODDS_ONLY else 'OFF'}</b>\n"
        f"Odds region: <b>{html.escape(ODDS_GEO)}/{html.escape(ODDS_GEO_SUB)}</b>\n"
        f"Odds depth: <b>{html.escape(ODDS_DEPTH)}</b>\n"
        f"Max bookmakers/market: <b>{ODDS_MAX_BOOKMAKERS}</b>",
        parse_mode=ParseMode.HTML,
    )

async def setgrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or context.args[0].upper() not in {"A+", "A", "B", "C"}:
        await update.message.reply_text("Usage: /setgrade A  — allowed: A+, A, B, C")
        return
    _, m = DB.get(update.effective_chat.id)
    DB.save(update.effective_chat.id, context.args[0].upper(), m)
    await update.message.reply_text(f"✅ Minimum grade set to {context.args[0].upper()}")

async def setmax_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /setmax all  or  /setmax 1500")
        return
    try:
        arg = context.args[0].lower().strip()
        if arg in {"all", "0", "full", "everything"}:
            m = 0
        else:
            m = int(arg)
            if m < 20 or m > 5000:
                raise ValueError
    except Exception:
        await update.message.reply_text("Max fixtures must be 'all' or a number from 20-5000. Example: /setmax all")
        return
    g, _ = DB.get(update.effective_chat.id)
    DB.save(update.effective_chat.id, g, m)
    await update.message.reply_text(f"✅ Max fixtures set to {'ALL' if m <= 0 else m}")

async def safe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /safe 05-09-2026")
        return
    date_text = context.args[0]
    try:
        parse_date(date_text)
    except Exception:
        await update.message.reply_text("Invalid date. Use dd-mm-yyyy. Example: /safe 05-09-2026")
        return
    min_grade, maxfx = DB.get(update.effective_chat.id)
    status = await update.message.reply_text(f"🛡 Scanning Flashscore strictly for {html.escape(date_text)}...\nGrade {min_grade}+ only. Weak games will be passed.")
    try:
        picks, fetched, analyzed, skipped = await scan(date_text, maxfx)
        chunks = report_chunks(date_text, picks, fetched, analyzed, skipped, min_grade)
        await status.edit_text(chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(0.25)
    except Exception as e:
        logger.exception("scan failed")
        await status.edit_text(f"❌ Scan failed: {html.escape(type(e).__name__)}: {html.escape(str(e))}", parse_mode=ParseMode.HTML)


def banker_report(date_text: str, picks: List[Pick], fetched: int, analyzed: int, skipped: int) -> str:
    # Ultra-strict money mode: one pick only, and only if it is genuinely top-class.
    candidates = [p for p in best_per_match(picks) if p.grade == "A+" and p.trust >= 94 and p.data_quality >= 84]
    title = f"🏦 Flashscore Banker Pick — {html.escape(date_text)}"
    if not candidates:
        top = best_per_match(picks)[:5]
        msg = (
            f"{title}\n\n"
            + scan_summary(fetched, analyzed, skipped)
            + odds_policy(date_text)
            + "No banker pick passed the ultra-strict filter. <b>No bet is better than forced bet.</b>\n"
        )
        if top:
            msg += "\nClosest candidates:\n"
            for i, p in enumerate(top, 1):
                msg += f"{i}. {html.escape(p.fixture.home)} vs {html.escape(p.fixture.away)} — {html.escape(p.selection)} — Trust {p.trust}/100, Data {p.data_quality}/100, Grade {p.grade}\n"
                msg += format_odds(p)
        return msg
    p = candidates[0]
    return (
        f"{title}\n\n"
        + scan_summary(fetched, analyzed, skipped)
        + odds_policy(date_text)
        + "Ultra-strict filter: <b>A+ only, Trust 94+, Data 84+</b>.\n\n"
        + format_pick(p, 1)
        + "\n⚠️ Still not 100%. Keep stake disciplined."
    )

def banker_chunks(report: str) -> List[str]:
    """Split long fallback reports at complete pick boundaries (balanced HTML)."""
    if len(report) <= 3600:
        return [report]
    parts = re.split(r"(?m)(?=^\d+\. )", report)
    chunks, current = [], ""
    for part in parts:
        if current and len(current) + len(part) > 3600:
            chunks.append(current.rstrip())
            current = "🏦 Banker report continued...\n"
        current += part
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def banker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /banker 05-09-2026")
        return
    date_text = context.args[0]
    try:
        parse_date(date_text)
    except Exception:
        await update.message.reply_text("Invalid date. Use dd-mm-yyyy. Example: /banker 05-09-2026")
        return
    _min_grade, maxfx = DB.get(update.effective_chat.id)
    status = await update.message.reply_text(
        f"🏦 Searching for ONE banker pick on {html.escape(date_text)}...\n"
        f"Scanning {'ALL' if maxfx <= 0 else maxfx} fixtures. If nothing is clean enough, I will say no bet."
    )
    try:
        picks, fetched, analyzed, skipped = await scan(date_text, maxfx)
        chunks = banker_chunks(banker_report(date_text, picks, fetched, analyzed, skipped))
        await status.edit_text(chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(0.25)
    except Exception as e:
        logger.exception("banker scan failed")
        await status.edit_text(f"❌ Banker scan failed: {html.escape(type(e).__name__)}: {html.escape(str(e))}", parse_mode=ParseMode.HTML)

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if len(context.args) < 3: raise ValueError
        stake = float(context.args[0].replace(",", ""))
        odds = [float(x.replace(",", "")) for x in context.args[1:]]
        if stake <= 0 or len(odds) < 2 or any(o <= 1 for o in odds): raise ValueError
    except Exception:
        await update.message.reply_text("Usage: /calc 10000 2.40 3.80 4.50")
        return
    await update.message.reply_text(calc_arb(stake, odds), parse_mode=ParseMode.HTML)

# =========================
# CLI / MAIN
# =========================

def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))

async def cli_safe(date_text: str) -> None:
    picks, fetched, analyzed, skipped = await scan(date_text, MAX_FIXTURES)
    print(strip_tags("\n\n".join(report_chunks(date_text, picks, fetched, analyzed, skipped, DEFAULT_MIN_GRADE))))

def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1].lower() == "safe":
        asyncio.run(cli_safe(sys.argv[2]))
        return
    if len(sys.argv) >= 3 and sys.argv[1].lower() == "banker":
        async def _cli_banker():
            picks, fetched, analyzed, skipped = await scan(sys.argv[2], MAX_FIXTURES)
            print(strip_tags(banker_report(sys.argv[2], picks, fetched, analyzed, skipped)))
        asyncio.run(_cli_banker())
        return
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "calc":
        if len(sys.argv) < 5:
            print("Usage: python flashscore_trust_bot.py calc 10000 2.40 3.80 4.50")
            return
        print(strip_tags(calc_arb(float(sys.argv[2]), [float(x) for x in sys.argv[3:]])))
        return
    if Application is None:
        print("Install: pip install python-telegram-bot==21.6 requests")
        sys.exit(1)
    if not TELEGRAM_BOT_TOKEN:
        print("Set TELEGRAM_BOT_TOKEN first.")
        sys.exit(1)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("setgrade", setgrade_cmd))
    app.add_handler(CommandHandler("setmax", setmax_cmd))
    app.add_handler(CommandHandler("safe", safe_cmd))
    app.add_handler(CommandHandler("banker", banker_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    logger.info("Flashscore Trust Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
