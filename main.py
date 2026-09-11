#!/usr/bin/env python3
"""
main.py -- Flashscore straight-win Telegram bot.  SINGLE FILE, no package.

Send the bot a date and it scans every Flashscore match on that day, runs the
full model, and replies with the straight wins it trusts.

    you:  09-09-2026
    bot:  scans that day's whole card, replies with ranked picks

    /start /help              usage
    dd-mm-yyyy                scan that date        <-- the main one
    /today /tomorrow /yesterday
    /id <match_id>            deep dive on one match
    /leagues <word>           restrict next scan; /leagues off to clear
    /status                   bot + data-source health

Install and run
---------------
    pip install requests numpy scipy
    export TELEGRAM_BOT_TOKEN=123456:ABC...
    python main.py

No token yet? Print a real scan to stdout:
    python main.py --dry-run 09-09-2026

Everything below is merged from the modular source (fsx/client.py,
fsx/model.py, fsx/engine.py, fsx/scan_day.py, flashscore_trust_bot.py) so
this file has zero package dependencies. Regenerate with build_single.py.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import logging
import math
import os
import queue
import re
import signal
import sys
import threading
import time
import traceback
import types as _types
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

try:
    import requests
    import numpy as np
    from scipy.optimize import brentq, minimize_scalar
    from scipy.stats import poisson, nbinom
except ImportError as _exc:  # fail with a fix, not a traceback
    sys.exit(
        "missing dependency: %s\n\n    pip install requests numpy scipy\n" % _exc
    )

# ==========================================================================
# FLASHSCORE CLIENT  --  from fsx/client.py
# ==========================================================================

"""
fsx.client -- Complete, reverse-engineered Flashscore data client.

EVERY endpoint in this file was verified live against Flashscore on 2026-09-09.
Nothing here is guessed. The feed names were extracted from Flashscore's own
production JS bundle (static.flashscore.com/res/_fs/build/detail.<hash>.js),
which contains the authoritative query-name composer:

    getH2hTab()                 -> "df_hh_"  + projectTypeId + "_" + eventId
    getLineupsTab()             -> "df_li_"  + projectTypeId + "_" + eventId
    getStatisticsFeed()         -> "df_st_"  + projectTypeId + "_" + eventId
    getPrematchOddsFeed(suffix) -> "df_dos_" + projectTypeId + "_" + eventId + "_" + suffix
    getCommonFeed()             -> "dc_"     + projectTypeId + "_" + eventId
    ...

...and the GraphQL persisted-query hash table (module 13274 of the same bundle):

    PREMATCH_ODDS_BETTING_MENU = "pobtm"
    LIVE_ODDS_BETTING_MENU     = "lobtm"
    ODDS_EVENT_PREMATCH        = "ope"
    ODDS_EVENT_PREMATCH_EXT    = "ope2"
    ODDS_EVENT_COMPARISON      = "oce"     <-- ALL bookmakers, ALL markets, ALL lines
    ODDS_EVENT_LIVE_2          = "ole2"
    DETAIL_SUMMARY_ODDS_FORM   = "dsof"    <-- last 5 + league position
    DETAIL_PREDICTED_LINEUPS   = "dplie"   <-- predicted XI
    DETAIL_LINEUPS_ENRICHED_2  = "dlie2"   <-- lineups + average ratings
    DETAIL_MISSING_PLAYERS_2   = "dmpe2"   <-- injuries / suspensions
    DETAIL_SUMMARY_ODDS_TOP_ST = "dsos2"
    LEAGUE_WINNER_ODDS         = "lwo"

The two GraphQL hosts come from Flashscore's runtime config (core.js):

    fsds.client_urls.default   = https://2.ds.lsapp.eu/pq_graphql
    fsds.client_urls.odds      = https://global.ds.lsapp.eu/odds/pq_graphql

Auth is a single static header, "x-fsign: SW9D1eZo", on EVERY request
(classic feeds return HTTP 401 without it). No cookies, no browser, no API key.
"""


import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

log = logging.getLogger("fsx.client")

# --------------------------------------------------------------------------- #
# Constants verified live                                                      #
# --------------------------------------------------------------------------- #

FSIGN = "SW9D1eZo"                 # static feed signature; has been stable for years
FEED_BASE = "https://www.flashscore.com/x/feed"
GQL_DETAIL = "https://2.ds.lsapp.eu/pq_graphql"
GQL_ODDS = "https://global.ds.lsapp.eu/odds/pq_graphql"

PROJECT_ID = "2"                   # GraphQL projectId for football (feed projectTypeId = 1)
PROJECT_TYPE_ID = 1                # used in the classic /x/feed/ names

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Match status codes (verified empirically against kickoff timestamps)
STATUS_UPCOMING, STATUS_LIVE, STATUS_FINISHED = "1", "2", "3"

# The 34 statistics Flashscore exposes in df_st_1 (id -> human name).
# Captured live from a UEFA Champions League match.
STAT_IDS: Dict[int, str] = {
    12: "Ball possession", 13: "Shots on target", 14: "Shots off target",
    15: "Free kicks", 16: "Corner kicks", 17: "Offsides", 18: "Throw ins",
    19: "Goalkeeper saves", 20: "Goal kicks", 21: "Fouls", 23: "Yellow cards",
    34: "Total shots", 158: "Blocked shots", 342: "Passes",
    432: "Expected goals (xG)", 433: "Crosses", 434: "Interceptions",
    457: "Hit the woodwork", 459: "Big chances", 461: "Shots inside the box",
    463: "Shots outside the box", 467: "Passes in final third",
    471: "Touches in opposition box", 475: "Tackles", 479: "Clearances",
    499: "xG on target (xGOT)", 501: "xGOT faced", 503: "Expected assists (xA)",
    507: "Errors leading to shot", 509: "Errors leading to goal",
    511: "Goals prevented", 513: "Duels won", 517: "Long passes",
    521: "Accurate through passes",
}

# Markets returned by the odds-comparison query (verified)
MARKETS = (
    "HOME_DRAW_AWAY", "OVER_UNDER", "ASIAN_HANDICAP", "DOUBLE_CHANCE",
    "BOTH_TEAMS_TO_SCORE", "DRAW_NO_BET", "HALF_FULL_TIME", "ODD_OR_EVEN",
)
SCOPES = ("FULL_TIME", "FIRST_HALF", "SECOND_HALF")


# --------------------------------------------------------------------------- #
# Low-level parsing                                                            #
# --------------------------------------------------------------------------- #

def parse_records(text: str) -> List[Dict[str, str]]:
    """Parse a Flashscore feed body.

    Wire format (escaped):  record separator  '¬~'   field separator '¬'
                            key/value split   '÷'
    (Unescaped these are 0x01 / 0x02 / 0x03 control bytes.)
    """
    out: List[Dict[str, str]] = []
    for chunk in (text or "").split("¬~"):
        rec: Dict[str, str] = {}
        for fld in chunk.split("¬"):
            if "÷" not in fld:
                continue
            k, _, v = fld.partition("÷")
            k = k.lstrip("~")
            if k:
                rec[k] = v
        if rec:
            out.append(rec)
    return out


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).strip().rstrip("%"))
    except Exception:
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Rate-limited session                                                         #
# --------------------------------------------------------------------------- #

class Client:
    """Thread-safe-ish HTTP client with a shared in-memory cache + throttle.

    Every network call goes through here so we can (a) never fetch the same
    thing twice in a run, (b) stay polite, (c) survive 4xx/5xx without killing
    a whole scan.
    """

    def __init__(
        self,
        geo: str = "NG",
        geo_sub: str = "NGLA",
        delay: float = 0.05,
        timeout: float = 20.0,
        max_retries: int = 2,
    ) -> None:
        self.geo = geo                 # ISO country -> which bookmakers you get
        self.geo_sub = geo_sub         # REQUIRED param for the odds API; any non-empty value works
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache: Dict[str, Tuple[int, str]] = {}
        self.stats = {"feed": 0, "gql": 0, "errors": 0, "cache_hits": 0}
        self._s = requests.Session()
        self._s.headers.update({
            "User-Agent": UA,
            "x-fsign": FSIGN,
            "Referer": "https://www.flashscore.com/",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._gql = requests.Session()
        self._gql.headers.update({
            "User-Agent": UA,
            "x-fsign": FSIGN,
            "Referer": "https://www.flashscore.com/",
            "Accept": "*/*",
        })
        self._last = 0.0

    # -- plumbing ----------------------------------------------------------- #

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def feed(self, path: str, use_cache: bool = True) -> Tuple[int, str]:
        url = f"{FEED_BASE}/{path}"
        if use_cache and url in self.cache:
            self.stats["cache_hits"] += 1
            return self.cache[url]
        for attempt in range(self.max_retries + 1):
            try:
                self._throttle()
                r = self._s.get(url, timeout=self.timeout)
                res = (r.status_code, r.text)
                self.stats["feed"] += 1
                if use_cache:
                    self.cache[url] = res
                return res
            except Exception as e:                      # noqa: BLE001
                self.stats["errors"] += 1
                log.debug("feed error %s (%s): %s", path, attempt, e)
                time.sleep(0.4 * (attempt + 1))
        self.cache[url] = (0, "")
        return (0, "")

    def gql(self, base: str, **params: Any) -> Dict[str, Any]:
        key = base + "?" + repr(sorted(params.items()))
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return json.loads(self.cache[key][1])
        for attempt in range(self.max_retries + 1):
            try:
                self._throttle()
                r = self._gql.get(base, params=params, timeout=self.timeout)
                self.stats["gql"] += 1
                if r.status_code != 200:
                    log.debug("gql %s -> %s", params.get("_hash"), r.status_code)
                    return {}
                body = r.text
                if not body or body.strip() in ("0", ""):
                    return {}
                data = r.json()
                self.cache[key] = (200, body)
                return data
            except Exception as e:                       # noqa: BLE001
                self.stats["errors"] += 1
                log.debug("gql error %s (%s): %s", params.get("_hash"), attempt, e)
                time.sleep(0.4 * (attempt + 1))
        return {}

    # -- 1. fixtures -------------------------------------------------------- #

    def fixtures(self, day_offset: int = 0) -> List[Dict[str, str]]:
        """All football matches for a day.

        day_offset: -1 yesterday, 0 today, 1 tomorrow, 2 .. n days ahead.
        Verified: the 2nd component of f_1_{offset}_3_en_1 is the day offset.
        """
        code, body = self.feed(f"f_1_{day_offset}_3_en_1")
        if code != 200:
            return []
        out: List[Dict[str, str]] = []
        league = None
        for rec in parse_records(body):
            if rec.get("ZA"):
                league = rec["ZA"]
            elif rec.get("AA"):
                rec["league"] = league or ""
                rec["tournament_id"] = rec.get("ZEE", "")
                out.append(rec)
        log.info("fixtures(+%d): %d matches", day_offset, len(out))
        return out

    # -- 2. classic per-match feeds ---------------------------------------- #

    def h2h(self, mid: str) -> Dict[str, List[Dict[str, Any]]]:
        """Head-to-head tab -- lossless parse of all three Flashscore sub-tabs.

        Flashscore serves the H2H tab three times, filtered differently:

            KA÷Overall                    (everything)
            KA÷{HomeTeam} - Home          (only matches where the home team was at home)
            KA÷{AwayTeam} - Away          (only matches where the away team was away)

        Inside each, blocks are announced by KB:
            KB÷Last matches: {team}       x1 or x2
            KB÷Head-to-head matches

        Returns {section_name: [{"label": <KB text>, "matches": [rec, ...]}, ...]}.
        Keeping the raw label avoids guessing which block belongs to which team;
        the model layer matches on the team name it already knows.
        """
        code, body = self.feed(f"df_hh_{PROJECT_TYPE_ID}_{mid}")
        if code != 200 or not body.strip():
            return {}
        sections: Dict[str, List[Dict[str, Any]]] = {}
        section = "Overall"
        block: Optional[Dict[str, Any]] = None
        for rec in parse_records(body):
            if rec.get("KA"):
                section = rec["KA"]
                sections.setdefault(section, [])
                block = None
                continue
            if rec.get("KB"):
                block = {"label": rec["KB"], "matches": []}
                sections.setdefault(section, []).append(block)
                continue
            if rec.get("KP"):
                if block is None:
                    block = {"label": "", "matches": []}
                    sections.setdefault(section, []).append(block)
                block["matches"].append(rec)
        return sections

    def statistics(self, mid: str) -> Dict[str, Dict[str, Tuple[str, str]]]:
        """Statistics feed -> {"Match": {name: (home, away)}, "1st Half": {...}, ...}

        Contains 34 metrics including Expected goals (xG), xGOT, xA, big chances.
        """
        code, body = self.feed(f"df_st_{PROJECT_TYPE_ID}_{mid}")
        if code != 200 or not body.strip():
            return {}
        out: Dict[str, Dict[str, Tuple[str, str]]] = {}
        period = "Match"
        for rec in parse_records(body):
            if rec.get("SE"):
                period = rec["SE"]
                out.setdefault(period, {})
            elif rec.get("SG"):
                out.setdefault(period, {})[rec["SG"]] = (rec.get("SH", ""), rec.get("SI", ""))
        return out

    def lineups(self, mid: str) -> Dict[str, Any]:
        """Confirmed lineups (+ player ratings when the match has been played)."""
        code, body = self.feed(f"df_li_{PROJECT_TYPE_ID}_{mid}")
        if code != 200 or not body.strip():
            return {"home": [], "away": [], "formation_home": None, "formation_away": None}
        home: List[Dict[str, str]] = []
        away: List[Dict[str, str]] = []
        side = home
        formation = {"home": None, "away": None}
        started = False
        for rec in parse_records(body):
            if rec.get("LB") and "Starting" in rec["LB"]:
                started = True
            if rec.get("LC"):
                side = away if rec["LC"] == "2" else home
                continue
            if rec.get("LD"):
                if not formation["home"]:
                    formation["home"] = rec["LD"]
                elif not formation["away"]:
                    formation["away"] = rec["LD"]
                continue
            if rec.get("LP"):
                side.append(rec)
        return {"home": home, "away": away,
                "formation_home": formation["home"], "formation_away": formation["away"]}

    def standings(self, mid: str, table_id: int = 1) -> List[Dict[str, str]]:
        """League table for the match's competition.

        Fields: TR rank, TN team, TI team id, TM played, TW/TDR/TL won/drawn/lost,
        TG goals "f:a", TP points, LMS last-match form char.
        """
        code, body = self.feed(f"df_to_{PROJECT_TYPE_ID}_{mid}_{table_id}")
        if code != 200 or not body.strip():
            return []
        return [r for r in parse_records(body) if r.get("TR")]

    def common(self, mid: str) -> Dict[str, str]:
        """Match 'common' data: status, timestamps, which tabs are available."""
        code, body = self.feed(f"dc_{PROJECT_TYPE_ID}_{mid}")
        if code != 200:
            return {}
        recs = parse_records(body)
        return recs[0] if recs else {}

    def summary(self, mid: str) -> List[Dict[str, str]]:
        """Score by period (1st Half / 2nd Half / ET / penalties)."""
        code, body = self.feed(f"df_sui_{PROJECT_TYPE_ID}_{mid}")
        if code != 200:
            return []
        return [r for r in parse_records(body) if r.get("AC")]

    # -- 3. GraphQL: odds --------------------------------------------------- #

    def odds_menu(self, mid: str) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
        """_hash=pobtm -> (bookmakers, active_markets).

        Empty active markets == Flashscore has NO playable odds for this match.
        """
        d = self.gql(GQL_ODDS, _hash="pobtm", eventId=mid, projectId=PROJECT_ID,
                     geoIpCode=self.geo, geoIpSubdivisionCode=self.geo_sub)
        node = ((d.get("data") or {}).get("getPrematchOddsBettingTypeMenu") or {})
        books = [{"id": b["bookmaker"]["id"], "name": b["bookmaker"]["name"]}
                 for b in (node.get("settings") or {}).get("bookmakers", [])
                 if b.get("bookmaker")]
        markets = [(i["bettingType"], i["bettingScope"])
                   for i in node.get("items", []) if i.get("isActive")]
        return books, markets

    def odds_one(self, mid: str, bookmaker_id: int, bet_type: str,
                 bet_scope: str = "FULL_TIME") -> Dict[str, Any]:
        """_hash=ope2 -> odds for exactly one bookmaker/market."""
        d = self.gql(GQL_ODDS, _hash="ope2", eventId=mid, bookmakerId=bookmaker_id,
                     betType=bet_type, betScope=bet_scope)
        return ((d.get("data") or {}).get("findPrematchOddsForBookmaker") or {})

    def odds_comparison(self, mid: str) -> Dict[str, Any]:
        """_hash=oce -> THE BIG ONE.

        Every bookmaker x every market x every line, current AND opening price.
        Verified market types: HOME_DRAW_AWAY, OVER_UNDER, ASIAN_HANDICAP,
        DOUBLE_CHANCE, BOTH_TEAMS_TO_SCORE, DRAW_NO_BET, HALF_FULL_TIME,
        ODD_OR_EVEN -- each across FULL_TIME / FIRST_HALF / SECOND_HALF.
        """
        return self.gql(GQL_ODDS, _hash="oce", eventId=mid, projectId=PROJECT_ID,
                        geoIpCode=self.geo, geoIpSubdivisionCode=self.geo_sub)

    # -- 4. GraphQL: detail -------------------------------------------------- #

    def _detail(self, mid: str, hash_: str) -> Dict[str, Any]:
        d = self.gql(GQL_DETAIL, _hash=hash_, eventId=mid, projectId=PROJECT_ID)
        return ((d.get("data") or {}).get("findEventById") or {})

    def summary_form(self, mid: str) -> Dict[str, Any]:
        """_hash=dsof -> each team's last 5 results, next fixtures, league position."""
        return self._detail(mid, "dsof")

    def missing_players(self, mid: str) -> Dict[str, List[Dict[str, Any]]]:
        """_hash=dmpe2 -> injuries / suspensions, with the stated reason."""
        node = self._detail(mid, "dmpe2")
        out: Dict[str, List[Dict[str, Any]]] = {"HOME": [], "AWAY": []}
        for ep in node.get("eventParticipants", []) or []:
            side = ((ep.get("type") or {}).get("side") or "").upper()
            for mp in ((ep.get("lineup") or {}).get("missingPlayers") or []):
                out.setdefault(side, []).append({
                    "name": ((mp.get("player") or {}).get("name") or "").strip(),
                    "reason": mp.get("reason") or "",
                })
        return out

    def predicted_lineups(self, mid: str) -> Dict[str, List[Dict[str, Any]]]:
        """_hash=dplie -> predicted starting XI (pre-match)."""
        node = self._detail(mid, "dplie")
        out: Dict[str, List[Dict[str, Any]]] = {"HOME": [], "AWAY": []}
        for ep in node.get("eventParticipants", []) or []:
            side = ((ep.get("type") or {}).get("side") or "").upper()
            pl = ((ep.get("predictedLineup") or {}).get("players") or [])
            out[side] = [{"name": (p.get("name") or "").strip()} for p in pl]
        return out

    def lineups_enriched(self, mid: str) -> Dict[str, Any]:
        """_hash=dlie2 -> confirmed lineups + per-team average player rating."""
        node = self._detail(mid, "dlie2")
        out: Dict[str, Any] = {"HOME": {}, "AWAY": {}}
        for ep in node.get("eventParticipants", []) or []:
            side = ((ep.get("type") or {}).get("side") or "").upper()
            out[side] = {
                "average_rating": ep.get("averageRating"),
                "formation": ((ep.get("lineup") or {}).get("formation") or ""),
                "players": [(p.get("name") or "").strip()
                            for p in ((ep.get("lineup") or {}).get("players") or [])],
            }
        return out

    # -- 5. one-call aggregate ---------------------------------------------- #

    def match_bundle(self, mid: str, depth: str = "full") -> Dict[str, Any]:
        """Fetch everything we know about one match.

        depth:
          "lite"  -> H2H + odds comparison            (2 requests)
          "full"  -> + dsof + dmpe2 + dplie           (5 requests)
          "max"   -> + df_li + df_st + standings      (8 requests)
        """
        b: Dict[str, Any] = {"match_id": mid}
        b["h2h"] = self.h2h(mid)
        b["odds_raw"] = self.odds_comparison(mid)
        if depth in ("full", "max"):
            b["summary_form"] = self.summary_form(mid)
            b["missing"] = self.missing_players(mid)
            b["predicted"] = self.predicted_lineups(mid)
        if depth == "max":
            b["lineups"] = self.lineups(mid)
            b["stats"] = self.statistics(mid)
            b["standings"] = self.standings(mid)
        return b


# ==========================================================================
# MODEL  --  from fsx/model.py
# ==========================================================================

"""
fsx.model -- probability engine.

Three independent probability sources, deliberately kept separate so the engine
can measure how much they AGREE (that agreement is the real signal):

  1. MARKET   -- devigged bookmaker odds (Shin + naive), plus a full
                 goal-distribution fitted from the Over/Under ladder and the
                 Asian-handicap lines.
  2. POISSON  -- Dixon-Coles bivariate Poisson fitted on the team's own
                 scraped match history, time-decayed, venue-split, and
                 xG-blended when xG is available.
  3. BLEND    -- a shrinkage-weighted combination of the two.

Why three: the market is the single best single predictor of football that
exists and is very hard to beat. A model that ignores it is worse than useless.
A model that only copies it can never find value. The edge -- if there is any --
lives in the *disagreement*.
"""


import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from scipy.stats import poisson, nbinom

# --------------------------------------------------------------------------- #
# 1. Overround removal                                                         #
# --------------------------------------------------------------------------- #

def implied(odds: Sequence[float]) -> np.ndarray:
    o = np.asarray([o for o in odds if o and o > 1.0], dtype=float)
    if o.size == 0:
        return np.array([])
    return 1.0 / o


def overround(odds: Sequence[float]) -> float:
    return float(np.sum(implied(odds)))


def devig_naive(odds: Sequence[float]) -> np.ndarray:
    """Proportional (multiplicative) normalisation. Simple, slightly biased
    toward longshots."""
    pi = implied(odds)
    if pi.size == 0 or pi.sum() <= 0:
        return np.array([])
    return pi / pi.sum()


def devig_shin(odds: Sequence[float], tol: float = 1e-12) -> np.ndarray:
    """Shin (1993) / Jullien-Salanie normalisation.

    Models the bookmaker as facing a fraction z of insiders who know the
    outcome; solves for z such that the corrected probabilities sum to 1.
    Consistently the best-performing single-match devigging method in the
    literature -- it corrects the favourite-longshot bias that the naive
    method leaves in.
    """
    pi = implied(odds)
    n = pi.size
    if n == 0 or pi.sum() <= 0:
        return np.array([])
    if n < 2:
        return np.ones(n)

    def f(z: float) -> float:
        # Shin probabilities
        num = np.sqrt(z ** 2 + 4.0 * (1.0 - z) * pi ** 2) - z
        den = 2.0 * (1.0 - z)
        return float(np.sum(num / den) - 1.0)

    lo, hi = 0.0, 0.99
    try:
        if f(lo) > 0:          # already sums to <=1 -> no vig
            return pi / pi.sum()
        z = brentq(f, lo, hi, xtol=tol, maxiter=200)
    except Exception:
        z = 0.0
    num = np.sqrt(z ** 2 + 4.0 * (1.0 - z) * pi ** 2) - z
    den = 2.0 * (1.0 - z)
    p = num / den
    s = p.sum()
    return p / s if s > 0 else np.ones(n) / n


def devig(odds: Sequence[float], method: str = "shin") -> np.ndarray:
    if method == "naive":
        return devig_naive(odds)
    if method == "blend":
        a, b = devig_shin(odds), devig_naive(odds)
        return (a + b) / 2.0 if a.size and b.size else a
    return devig_shin(odds)


# --------------------------------------------------------------------------- #
# 2. Score matrix: Dixon-Coles bivariate Poisson                               #
# --------------------------------------------------------------------------- #

MAX_GOALS = 12


def dixon_coles_tau(lh: float, la: float, rho: float) -> np.ndarray:
    tau = np.ones((MAX_GOALS, MAX_GOALS))
    if abs(rho) < 1e-9:
        return tau
    tau[0, 0] = 1.0 - lh * la * rho
    tau[1, 0] = 1.0 + la * rho
    tau[0, 1] = 1.0 + lh * rho
    tau[1, 1] = 1.0 - rho
    return tau


def score_matrix(lh: float, la: float, rho: float = -0.05) -> np.ndarray:
    """P[home=i, away=j] with the Dixon-Coles low-score correction."""
    lh = min(max(lh, 0.02), 8.0)
    la = min(max(la, 0.02), 8.0)
    i = np.arange(MAX_GOALS)
    ph = poisson.pmf(i, lh)[:, None]
    pa = poisson.pmf(i, la)[None, :]
    m = ph * pa
    m = m * dixon_coles_tau(lh, la, rho)
    m = np.clip(m, 0.0, None)
    s = m.sum()
    return m / s if s > 0 else m


def rho_mle(results: Sequence[Tuple[int, int]], lh: float, la: float) -> float:
    """Estimate the Dixon-Coles rho from observed scorelines.

    Only the four low-score cells (0-0, 1-0, 0-1, 1-1) carry information.
    """
    if len(results) < 40:
        return -0.05
    obs = np.zeros((2, 2))
    for h, a in results:
        if h <= 1 and a <= 1:
            obs[h, a] += 1.0
    n = float(len(results))
    if n == 0:
        return -0.05

    def negll(rho: float) -> float:
        tau = dixon_coles_tau(lh, la, rho)[:2, :2]
        tau = np.clip(tau, 1e-6, None)
        i = np.arange(2)
        base = poisson.pmf(i, lh)[:, None] * poisson.pmf(i, la)[None, :]
        exp_ = base * tau
        exp_ = exp_ / exp_.sum() * n
        exp_ = np.clip(exp_, 1e-6, None)
        return float(-np.sum(obs * np.log(exp_)))

    try:
        r = minimize_scalar(negll, bounds=(-0.45, 0.25), method="bounded")
        return float(r.x) if r.success else -0.05
    except Exception:
        return -0.05


def outcomes(m: np.ndarray) -> Dict[str, float]:
    """m[i, j] = P(home scores i, away scores j).

    Rows are the HOME team, columns the AWAY team, so:
        i > j  ->  home win   (lower triangle, k=-1)
        i == j ->  draw       (trace)
        i < j  ->  away win   (upper triangle, k=1)
    """
    home_win = np.tril_indices(MAX_GOALS, k=-1)
    away_win = np.triu_indices(MAX_GOALS, k=1)
    return {
        "home": float(m[home_win].sum()),
        "draw": float(np.trace(m)),
        "away": float(m[away_win].sum()),
    }


def totals_probs(m: np.ndarray) -> np.ndarray:
    """Distribution of total goals."""
    out = np.zeros(2 * MAX_GOALS - 1)
    for i in range(MAX_GOALS):
        for j in range(MAX_GOALS):
            out[i + j] += m[i, j]
    return out


def p_over_line(m: np.ndarray, line: float) -> float:
    """P(total goals > line), asian lines included.

    Asian quarter lines split the stake across the two neighbouring half lines:
        2.25 -> half on 2.0, half on 2.5
        2.75 -> half on 2.5, half on 3.0
    Half the stake winning and half pushing returns half the winnings, which is
    exactly what the average of the two half-line probabilities represents.
    """
    tot = totals_probs(m)
    n = np.arange(len(tot))
    base = math.floor(line * 2) / 2.0
    frac = line - base
    if abs(frac) < 1e-9:                       # whole or half line
        return float(tot[n > line].sum())
    a, b = base, base + 0.5                    # quarter line -> asian split
    return float(0.5 * tot[n > a].sum() + 0.5 * tot[n > b].sum())


def btts_prob(m: np.ndarray) -> float:
    home_zero = m[0, :].sum()
    away_zero = m[:, 0].sum()
    return float(1.0 - home_zero - away_zero + m[0, 0])


# --- three-outcome market pricing -------------------------------------------
# Whole lines (Over 2.0, AH -1.0) can PUSH: the stake is returned. Ignoring
# that produces enormous phantom edges, so every totals/handicap price is
# returned as a (p_win, p_push) pair. The remainder is p_lose.
#
#   EV per unit staked = p_win * odds + p_push * 1 - 1
#
# Quarter asian lines are two half-stakes on neighbouring half lines, so they
# are just the average of the two component (p_win, p_push) pairs.

def tot_probs(m: np.ndarray, line: float, over: bool):
    """(p_win, p_push) for Over/Under `line`."""
    tot = totals_probs(m)
    n = np.arange(len(tot))
    base = math.floor(line * 2) / 2.0
    frac = line - base
    if abs(frac) < 1e-9:
        return _tot_half(tot, n, line, over)
    a, b = base, base + 0.5
    pa = _tot_half(tot, n, a, over)
    pb = _tot_half(tot, n, b, over)
    return (0.5 * (pa[0] + pb[0]), 0.5 * (pa[1] + pb[1]))


def _tot_half(tot, n, line, over):
    if abs((line % 1.0) - 0.5) < 1e-9:          # half line -> no push
        p = float(tot[n > line].sum())
        return (p, 0.0) if over else (1.0 - p, 0.0)
    p_over = float(tot[n > line].sum())         # whole line -> push at exactly `line`
    p_push = float(tot[n == line].sum())
    if over:
        return (p_over, p_push)
    p_under = float(tot[n < line].sum())
    return (p_under, p_push)


def ah_probs(m: np.ndarray, handicap: float, side_home: bool = True):
    """(p_win, p_push) for an asian handicap quoted from the HOME perspective.

    handicap -0.75 -> half stake on -0.5, half on -1.0.
    """
    base = math.floor(handicap * 2) / 2.0
    frac = handicap - base
    if abs(frac) < 1e-9:
        return _ah_half(m, handicap, side_home)
    a, b = base, base + 0.5
    pa = _ah_half(m, a, side_home)
    pb = _ah_half(m, b, side_home)
    return (0.5 * (pa[0] + pb[0]), 0.5 * (pa[1] + pb[1]))


def _ah_half(m, h, side_home):
    pw = pp = 0.0
    for i in range(MAX_GOALS):
        for j in range(MAX_GOALS):
            d = (i - j) + h                    # >0 home covers, ==0 push
            if side_home:
                if d > 1e-9:
                    pw += m[i, j]
                elif abs(d) < 1e-9:
                    pp += m[i, j]
            else:
                if d < -1e-9:
                    pw += m[i, j]
                elif abs(d) < 1e-9:
                    pp += m[i, j]
    return (float(pw), float(pp))


def p_over_line(m: np.ndarray, line: float) -> float:
    """Kept for the ladder: P(total > line) on a half line."""
    return tot_probs(m, line, True)[0]


# --------------------------------------------------------------------------- #
# 3. Team strength from scraped history                                        #
# --------------------------------------------------------------------------- #

@dataclass
class TeamRates:
    """Time-decayed, venue-split scoring / conceding rates for one team."""
    name: str
    gf_home: float = 0.0
    ga_home: float = 0.0
    gf_away: float = 0.0
    ga_away: float = 0.0
    n_home: float = 0.0
    n_away: float = 0.0
    xgf: Optional[float] = None
    xga: Optional[float] = None
    n_xg: int = 0
    form5: float = 0.0          # weighted points per game, last 5
    last_date: int = 0
    complete: float = 0.0       # 0..1 data completeness


@dataclass
class HistoryRow:
    date: int
    comp: str
    home: str
    away: str
    gh: int
    ga: int
    gh_ht: Optional[int] = None
    ga_ht: Optional[int] = None
    ref_side: str = ""          # "home" | "away" | ""
    result: str = ""            # "w" | "l" | "d"
    xgh: Optional[float] = None
    xga: Optional[float] = None


def _decay(age_days: float, half_life: float) -> float:
    if half_life <= 0:
        return 1.0
    return 0.5 ** (max(age_days, 0.0) / half_life)


def build_rates(
    team: str,
    rows: Sequence[HistoryRow],
    now: int,
    half_life: float = 120.0,
    xg_weight: float = 0.5,
    shrink_k: float = 9.0,
) -> TeamRates:
    """Time-decayed venue-split attack/defence for `team` from its own history.

    `xg_weight` blends expected goals into the rate. xG is a much better
    predictor of future goals than past goals are, but it is only available
    for a subset of matches, so we blend rather than replace.
    """
    r = TeamRates(name=team)
    if not rows:
        return r

    sh = sw = shn = swn = 0.0      # weighted sums
    ch = cw = 0.0                  # conceded
    xf = xa = xw = 0.0
    nx = 0

    for row in rows:
        age_days = max(0.0, (now - row.date) / 86400.0)
        w = _decay(age_days, half_life)
        if row.ref_side == "home":
            gf, ga = float(row.gh), float(row.ga)
        elif row.ref_side == "away":
            gf, ga = float(row.ga), float(row.gh)
        else:
            continue

        if row.xgh is not None and row.xga is not None:
            if row.ref_side == "home":
                xgf, xga = row.xgh, row.xga
            else:
                xgf, xga = row.xga, row.xgh
            gf = (1 - xg_weight) * gf + xg_weight * xgf
            ga = (1 - xg_weight) * ga + xg_weight * xga
            xf += w * gf
            xa += w * ga
            xw += w
            nx += 1

        if row.ref_side == "home":
            sh += w * gf
            ch += w * ga
            shn += w
            r.n_home += 1
        else:
            sw += w * gf
            cw += w * ga
            swn += w
            r.n_away += 1

    r.gf_home = sh / shn if shn > 0 else 0.0
    r.ga_home = ch / shn if shn > 0 else 0.0
    r.gf_away = sw / swn if swn > 0 else 0.0
    r.ga_away = cw / swn if swn > 0 else 0.0
    r.n_home, r.n_away = shn, swn
    r.xgf = xf / xw if xw > 0 else None
    r.xga = xa / xw if xw > 0 else None
    r.n_xg = nx

    # form: last 5 chronologically, 3/w 1/d, decayed
    ordered = sorted(rows, key=lambda x: x.date, reverse=True)[:5]
    pts = 0.0
    wsum = 0.0
    for k, row in enumerate(ordered):
        w = 0.5 ** (k / 2.5)
        p = 3.0 if row.result == "w" else (1.0 if row.result == "d" else 0.0)
        pts += w * p
        wsum += w
    r.form5 = (pts / wsum / 3.0) if wsum else 0.0
    r.last_date = max((x.date for x in rows), default=0)

    need = 20.0
    r.complete = float(min(1.0, ((r.n_home + r.n_away) / 2.0) / need))
    return r


@dataclass
class PoissonForecast:
    lh: float
    la: float
    rho: float
    matrix: np.ndarray
    p_home: float
    p_draw: float
    p_away: float
    p_btts: float
    p_over25: float
    mu_total: float
    shrinkage: float


def forecast(
    home: TeamRates,
    away: TeamRates,
    league_mu: float,
    rho: float = -0.05,
    shrink_k: float = 9.0,
) -> PoissonForecast:
    """Multiplicative Poisson with shrinkage.

        lam_home = attack_home(at home) * defence_away(away) * league_mu
    where attack/defence are multipliers relative to `league_mu`, shrunk
    toward 1.0 by an effective-sample-size prior.
    """
    mu = league_mu if league_mu and league_mu > 0.2 else 1.35

    def mult(value: float, n: float) -> float:
        if n <= 0 or value <= 0:
            return 1.0
        raw = value / mu
        return (n * raw + shrink_k * 1.0) / (n + shrink_k)

    a_home = mult(home.gf_home, home.n_home)
    d_away = mult(away.ga_away, away.n_away)
    a_away = mult(away.gf_away, away.n_away)
    d_home = mult(home.ga_home, home.n_home)

    lh = a_home * d_away * mu
    la = a_away * d_home * mu

    # if a team has no venue-specific data, fall back to its overall rate
    if home.n_home <= 0 and home.n_away > 0:
        lh = mult(home.gf_away, home.n_away) * d_away * mu
    if away.n_away <= 0 and away.n_home > 0:
        la = mult(away.gf_home, away.n_home) * d_home * mu

    m = score_matrix(lh, la, rho)
    oc = outcomes(m)
    return PoissonForecast(
        lh=float(lh), la=float(la), rho=float(rho), matrix=m,
        p_home=oc["home"], p_draw=oc["draw"], p_away=oc["away"],
        p_btts=btts_prob(m), p_over25=p_over_line(m, 2.5),
        mu_total=float(lh + la),
        shrinkage=float(shrink_k),
    )


def league_mean(rows: Sequence[HistoryRow], now: Optional[int] = None,
                half_life: float = 120.0) -> float:
    """Average goals per team per match across a pooled history sample.

    Time-decayed by default. Scoring rates drift (rules, tactics, squad
    quality), and an undecayed mean pulled across several seasons biases every
    lambda toward a stale environment -- verified to under-project totals by
    roughly a goal per game on recently high-scoring samples.
    """
    if not rows:
        return 1.35
    if now is None:
        tot = float(sum((r.gh + r.ga) for r in rows))
        return max(0.4, tot / (2.0 * len(rows)))
    num = den = 0.0
    for r in rows:
        w = _decay(max(0.0, (now - r.date) / 86400.0), half_life)
        num += w * (r.gh + r.ga)
        den += w
    if den <= 0:
        return 1.35
    return max(0.4, num / (2.0 * den))


# --------------------------------------------------------------------------- #
# 4. Reading the market: fit a goal distribution to the odds                   #
# --------------------------------------------------------------------------- #

@dataclass
class MarketRead:
    p_home: float
    p_draw: float
    p_away: float
    overround: float
    n_books: int
    best_home: float
    best_draw: float
    best_away: float
    opening_home: Optional[float] = None
    opening_draw: Optional[float] = None
    opening_away: Optional[float] = None
    lam_total: Optional[float] = None       # from the O/U ladder
    lam_home: Optional[float] = None
    lam_away: Optional[float] = None
    ladder: Dict[float, float] = None       # line -> devigged P(over)


def _fit_total_lambda(ladder: Dict[float, float], rho: float = -0.05) -> Optional[float]:
    """Fit a total-goals Poisson intensity to the devigged O/U ladder."""
    if not ladder:
        return None
    items = sorted(ladder.items())

    def loss(mu: float) -> float:
        if mu <= 0.05:
            return 1e6
        # use a bivariate split of 50/50 only to get the total distribution
        m = score_matrix(mu / 2.0, mu / 2.0, rho)
        tot = totals_probs(m)
        n = np.arange(len(tot))
        err = 0.0
        for line, target in items:
            if line % 0.5 != 0:
                continue
            pred = float(tot[n > line].sum())
            err += (pred - target) ** 2
        return err

    try:
        r = minimize_scalar(loss, bounds=(0.3, 6.0), method="bounded")
        return float(r.x) if r.success else None
    except Exception:
        return None


def _split_lambdas(lam_total: float, p_home: float, p_away: float,
                   rho: float = -0.05) -> Tuple[float, float]:
    """Find (lh, la) with lh+la fixed that reproduces the market's 1X2 split."""
    if lam_total is None or lam_total <= 0:
        return (lam_total or 1.35) / 2, (lam_total or 1.35) / 2
    target = p_home / max(p_home + p_away, 1e-9)

    def loss(frac: float) -> float:
        lh = lam_total * frac
        la = lam_total * (1 - frac)
        m = score_matrix(lh, la, rho)
        oc = outcomes(m)
        ph = oc["home"] / max(oc["home"] + oc["away"], 1e-9)
        return (ph - target) ** 2

    try:
        r = minimize_scalar(loss, bounds=(0.05, 0.95), method="bounded")
        frac = float(r.x) if r.success else 0.5
    except Exception:
        frac = 0.5
    return lam_total * frac, lam_total * (1 - frac)


def read_market(odds_raw: Dict, method: str = "shin") -> Optional[MarketRead]:
    """Turn the raw `oce` payload into a clean, devigged market read."""
    node = ((odds_raw or {}).get("data") or {}).get("findOddsByEventId")
    if not node:
        return None

    entries = node.get("odds") or []
    hda: Dict[str, List[Tuple[float, Optional[float], Optional[float]]]] = {"home": [], "draw": [], "away": []}
    ou: Dict[float, List[Tuple[float, Optional[float]]]] = {}
    ah: Dict[Tuple[float, str], List[float]] = {}
    btts: Dict[bool, List[Tuple[float, Optional[float]]]] = {}
    ou_scope: Dict[float, str] = {}

    for e in entries:
        bt, bs = e.get("bettingType"), e.get("bettingScope")
        items = e.get("odds") or []
        if bt == "HOME_DRAW_AWAY" and bs == "FULL_TIME":
            # order is home, away, draw -- identified by eventParticipantId
            hid = None
            for it in items:
                if it.get("eventParticipantId"):
                    hid = it["eventParticipantId"]
                    break
            for it in items:
                if not it.get("active"):
                    continue
                v = float(it["value"]) if it.get("value") else 0.0
                op = float(it["opening"]) if it.get("opening") else None
                if not v:
                    continue
                pid = it.get("eventParticipantId")
                if pid is None:
                    hda["draw"].append((v, op, v))
                elif hid and pid == hid:
                    hda["home"].append((v, op, v))
                else:
                    hda["away"].append((v, op, v))
        elif bt == "OVER_UNDER" and bs == "FULL_TIME":
            for it in items:
                if not it.get("active"):
                    continue
                hc = (it.get("handicap") or {}).get("value")
                if hc is None:
                    continue
                try:
                    line = float(hc)
                except Exception:
                    continue
                v = float(it["value"]) if it.get("value") else 0.0
                op = float(it["opening"]) if it.get("opening") else None
                sel = it.get("selection")
                if sel == "OVER" and v:
                    ou.setdefault(line, []).append((v, op))
                    ou_scope[line] = bs
        elif bt == "BOTH_TEAMS_TO_SCORE" and bs == "FULL_TIME":
            for it in items:
                if not it.get("active"):
                    continue
                v = float(it["value"]) if it.get("value") else 0.0
                op = float(it["opening"]) if it.get("opening") else None
                flag = it.get("bothTeamsToScore")
                if v and flag is not None:
                    btts.setdefault(bool(flag), []).append((v, op))

    if not (hda["home"] and hda["draw"] and hda["away"]):
        return None

    def mean_price(lst):
        arr = np.array([x[0] for x in lst], dtype=float)
        return float(arr.mean()) if arr.size else 0.0

    def best_price(lst):
        arr = np.array([x[0] for x in lst], dtype=float)
        return float(arr.max()) if arr.size else 0.0

    def mean_open(lst):
        vals = [x[1] for x in lst if x[1]]
        return float(np.mean(vals)) if vals else None

    mean_odds = [mean_price(hda["home"]), mean_price(hda["draw"]), mean_price(hda["away"])]
    p = devig(mean_odds, method)
    if p.size != 3:
        return None

    # opening line (for movement) -- first book's opening, averaged
    op = [mean_open(hda["home"]), mean_open(hda["draw"]), mean_open(hda["away"])]
    p_open = devig([o for o in op if o], method) if all(op) else None

    ladder: Dict[float, float] = {}
    for line, lst in ou.items():
        if line % 0.5 != 0:
            continue
        v = mean_price(lst)
        if v > 1.0:
            # devig the over/under pair
            under = None
            for line2, l2 in ou.items():
                if abs(line2 - line) < 1e-9:
                    continue
            # we only stored OVER prices; approximate devig with the O/U symmetry
            # by requesting the pair from the raw payload
            pass
    # proper pair devigging: re-walk raw for over/under pairs
    ladder = _devig_ladder(entries, method)

    lam_total = _fit_total_lambda(ladder)
    lh = la = None
    if lam_total:
        lh, la = _split_lambdas(lam_total, float(p[0]), float(p[2]))

    return MarketRead(
        p_home=float(p[0]), p_draw=float(p[1]), p_away=float(p[2]),
        overround=overround(mean_odds),
        n_books=len(hda["home"]),
        best_home=best_price(hda["home"]),
        best_draw=best_price(hda["draw"]),
        best_away=best_price(hda["away"]),
        opening_home=op[0], opening_draw=op[1], opening_away=op[2],
        lam_total=lam_total, lam_home=lh, lam_away=la,
        ladder=ladder,
    )


def _devig_ladder(entries: List[Dict], method: str) -> Dict[float, float]:
    """Devig each Over/Under line using its true over/under price pair."""
    pairs: Dict[float, Dict[str, List[float]]] = {}
    for e in entries:
        if e.get("bettingType") != "OVER_UNDER" or e.get("bettingScope") != "FULL_TIME":
            continue
        for it in e.get("odds") or []:
            if not it.get("active"):
                continue
            hc = (it.get("handicap") or {}).get("value")
            if hc is None:
                continue
            try:
                line = float(hc)
            except Exception:
                continue
            # ONLY half lines: on a whole line (2.0) the stake is refunded at
            # exactly 2 goals, so over+under do NOT partition the probability
            # space and a 2-way devig would inflate both. Half lines are clean.
            if abs((line % 1.0) - 0.5) > 1e-9:
                continue
            v = float(it["value"]) if it.get("value") else 0.0
            if not v:
                continue
            sel = (it.get("selection") or "").upper()
            pairs.setdefault(line, {"OVER": [], "UNDER": []})
            if sel in pairs[line]:
                pairs[line][sel].append(v)
    out: Dict[float, float] = {}
    for line, d in pairs.items():
        if d["OVER"] and d["UNDER"]:
            o = float(np.mean(d["OVER"]))
            u = float(np.mean(d["UNDER"]))
            p = devig([o, u], method)
            if p.size == 2:
                out[line] = float(p[0])
    return out


# freeze the MODEL namespace so `M.name` keeps working
M = _types.SimpleNamespace(
    **{k: v for k, v in list(globals().items()) if not k.startswith('_')}
)


# ==========================================================================
# ENGINE  --  from fsx/engine.py
# ==========================================================================

"""
fsx.engine -- turn raw scraped data into calibrated probabilities, EV and a grade.

Design principle
----------------
Nothing here claims certainty. Every pick is emitted with:

  * a point-estimate probability,
  * an honest uncertainty band (from model-vs-market disagreement),
  * the expected value at the best available price,
  * the number of independent signals that agree,
  * and an explicit list of reasons it can still lose.

The "sure win" the brief asks for does not exist. What DOES exist, and what
this engine computes, is the highest-confidence, highest-agreement,
positive-expectation subset of the day's board -- plus the arithmetic that
shows exactly how often it will still fail.
"""


import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np



# --------------------------------------------------------------------------- #
# Parsing helpers                                                              #
# --------------------------------------------------------------------------- #

# Competition codes seen in the H2H feed (KI field). Friendlies and
# youth/reserve fixtures carry much less predictive weight.
COMP_WEIGHT = {
    "CF": 0.35,          # Club Friendly
    "CL": 1.00, "UEL": 0.95, "UECL": 0.90, "CWC": 0.85,
    "CUP": 0.85, "SC": 0.80,
}
DEFAULT_COMP_WEIGHT = 1.0


def _comp_weight(code: str, name: str) -> float:
    c = (code or "").upper()
    if c in COMP_WEIGHT:
        return COMP_WEIGHT[c]
    n = (name or "").lower()
    if "friendly" in n or "club friendly" in n:
        return 0.35
    if " u17" in n or " u18" in n or " u19" in n or " u20" in n or " u21" in n:
        return 0.45
    if "reserve" in n or " 2" == n[-2:]:
        return 0.60
    return DEFAULT_COMP_WEIGHT


def _clean(name: str) -> str:
    return (name or "").lstrip("*").strip()


def rows_from_block(block: Dict[str, Any], ref_team: str) -> List[HistoryRow]:
    """Convert one 'Last matches: X' block into HistoryRows for team X."""
    out: List[HistoryRow] = []
    for r in block.get("matches", []):
        try:
            date = int(r.get("KC", 0))
        except Exception:
            continue
        gh = _i(r.get("KU"))
        ga = _i(r.get("KT"))
        side = (r.get("KS") or "").lower()
        if side not in ("home", "away"):
            home_n, away_n = _clean(r.get("KJ", "")), _clean(r.get("KK", ""))
            if home_n and ref_team and home_n.lower().startswith(ref_team.lower()[:8]):
                side = "home"
            elif away_n and ref_team:
                side = "away"
            else:
                continue
        wis = (r.get("WIS") or "").lower()
        if wis.startswith("w"):
            res = "w"
        elif wis.startswith("l"):
            res = "l"
        elif wis.startswith("d"):
            res = "d"
        else:
            res = "w" if (gh > ga) == (side == "home") else ("l" if gh != ga else "d")
        gh_ht = ga_ht = None
        try:
            if r.get("KX") is not None and r.get("KY") is not None:
                gh_ht, ga_ht = int(r["KX"]), int(r["KY"])
        except Exception:
            pass
        out.append(HistoryRow(
            date=date, comp=r.get("KF", ""), home=_clean(r.get("KJ", "")),
            away=_clean(r.get("KK", "")), gh=gh, ga=ga, gh_ht=gh_ht, ga_ht=ga_ht,
            ref_side=side, result=res,
        ))
    return out


def h2h_rows(sections: Dict[str, List[Dict[str, Any]]], home: str, away: str) -> List[HistoryRow]:
    """Mutual meetings, always expressed from the CURRENT home team's perspective."""
    out: List[HistoryRow] = []
    for sec in sections.values():
        for blk in sec:
            if not (blk.get("label") or "").lower().startswith("head"):
                continue
            for r in blk.get("matches", []):
                hn, an = _clean(r.get("KJ", "")), _clean(r.get("KK", ""))
                gh, ga = _i(r.get("KU")), _i(r.get("KT"))
                try:
                    date = int(r.get("KC", 0))
                except Exception:
                    continue
                if hn.lower()[:9] == home.lower()[:9]:
                    side = "home"
                elif an.lower()[:9] == home.lower()[:9]:
                    gh, ga = ga, gh
                    side = "away"
                else:
                    continue
                out.append(HistoryRow(
                    date=date, comp=r.get("KF", ""), home=hn, away=an,
                    gh=gh, ga=ga, ref_side=side,
                    result="w" if gh > ga else ("l" if gh < ga else "d"),
                ))
    # de-duplicate across the three Flashscore sub-tabs
    seen, uniq = set(), []
    for r in sorted(out, key=lambda x: x.date, reverse=True):
        k = (r.date, r.home, r.away, r.gh, r.ga)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def team_rows(sections: Dict[str, List[Dict[str, Any]]], team: str) -> List[HistoryRow]:
    """All 'Last matches: {team}' rows across the Overall / Home / Away sub-tabs."""
    out: List[HistoryRow] = []
    for sec in sections.values():
        for blk in sec:
            label = (blk.get("label") or "")
            if not label.lower().startswith("last matches"):
                continue
            if _clean(label.split(":", 1)[-1]).lower()[:9] != team.lower()[:9]:
                continue
            out.extend(rows_from_block(blk, team))
    seen, uniq = set(), []
    for r in sorted(out, key=lambda x: x.date, reverse=True):
        k = (r.date, r.home, r.away)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


# --------------------------------------------------------------------------- #
# Market prices for every market                                               #
# --------------------------------------------------------------------------- #

@dataclass
class Price:
    label: str
    selection: str
    odds: float
    opening: Optional[float]
    book: str
    line: Optional[float] = None


def all_prices(odds_raw: Dict, book_name: str = "") -> List[Price]:
    """Flatten the `oce` payload into one Price per market/selection (best price)."""
    node = ((odds_raw or {}).get("data") or {}).get("findOddsByEventId")
    if not node:
        return []
    entries = node.get("odds") or []
    best: Dict[Tuple[str, str, Optional[float]], Price] = {}

    hid = aid = None
    for e in entries:
        if e.get("bettingType") == "HOME_DRAW_AWAY" and e.get("bettingScope") == "FULL_TIME":
            for it in e.get("odds") or []:
                if it.get("eventParticipantId"):
                    if hid is None:
                        hid = it["eventParticipantId"]
                    elif aid is None and it["eventParticipantId"] != hid:
                        aid = it["eventParticipantId"]
    for e in entries:
        if e.get("bettingScope") != "FULL_TIME":
            continue
        bt = e.get("bettingType")
        for it in e.get("odds") or []:
            if not it.get("active"):
                continue
            v = float(it["value"]) if it.get("value") else 0.0
            if not v or v <= 1.0:
                continue
            op = float(it["opening"]) if it.get("opening") else None
            pid = it.get("eventParticipantId")
            hc = (it.get("handicap") or {}).get("value")
            line = float(hc) if hc not in (None, "") else None
            sel = it.get("selection")
            key = None
            if bt == "HOME_DRAW_AWAY":
                s = "Home" if (pid and pid == hid) else ("Away" if pid else "Draw")
                key = ("1X2", s, None)
            elif bt == "DOUBLE_CHANCE":
                s = "1X" if (pid and pid == hid) else ("X2" if pid else "12")
                key = ("Double Chance", s, None)
            elif bt == "DRAW_NO_BET":
                s = "Home" if (pid and pid == hid) else "Away"
                key = ("Draw No Bet", s, None)
            elif bt == "BOTH_TEAMS_TO_SCORE":
                key = ("BTTS", "Yes" if it.get("bothTeamsToScore") else "No", None)
            elif bt == "OVER_UNDER":
                if not sel or line is None:
                    continue
                key = ("Over/Under", "Over" if sel == "OVER" else "Under", line)
            elif bt == "ASIAN_HANDICAP":
                if pid is None or line is None:
                    continue
                s = "Home" if pid == hid else "Away"
                key = ("Asian Handicap", s, line)
            elif bt == "ODD_OR_EVEN":
                if not sel:
                    continue
                key = ("Odd/Even", sel.capitalize(), None)
            if key and (key not in best or v > best[key].odds):
                best[key] = Price(label=key[0], selection=key[1], odds=v,
                                  opening=op, book=book_name, line=key[2])
    return list(best.values())


# --------------------------------------------------------------------------- #
# Signals                                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class Signal:
    name: str
    vote: str                 # "home" | "draw" | "away" | "abstain"
    weight: float
    detail: str


@dataclass
class Assessment:
    match_id: str
    home: str
    away: str
    league: str
    kickoff: int
    market: Optional[M.MarketRead]
    forecast: Optional[M.PoissonForecast]
    p_blend: Dict[str, float]
    signals: List[Signal]
    agreement: float
    picks: List[Dict[str, Any]]
    red_flags: List[str]
    completeness: float
    best_grade: str
    all_priced: List[Dict[str, Any]] = field(default_factory=list)
    notes: Dict[str, Any] = field(default_factory=dict)


def _side_from_probs(p: Dict[str, float]) -> str:
    return max(("home", "draw", "away"), key=lambda k: p[k])


# --------------------------------------------------------------------------- #
# Self-correction                                                              #
# --------------------------------------------------------------------------- #

_CAL: Optional[Dict[str, float]] = None
CAL_PATH = "/home/user/fsx_calibration.json"


def load_calibration(path: str = CAL_PATH) -> Optional[Dict[str, float]]:
    """Load the fitted confidence correction produced by calibrate.py.

    The fit is only written when it improves HELD-OUT log-loss; if it does not,
    calibrate.py stores the identity (gamma=1.0). So loading it is always safe.
    """
    global _CAL
    try:
        with open(path) as fh:
            import json as _json
            _CAL = _json.load(fh)
    except Exception:
        _CAL = None
    return _CAL


def apply_calibration(p: Dict[str, float]) -> Dict[str, float]:
    """Sharpen/flatten the probability vector by the fitted temperature."""
    if not _CAL or not _CAL.get("accepted", True):
        return p
    g = float(_CAL.get("gamma", 1.0))
    db = float(_CAL.get("draw_boost", 1.0))
    if abs(g - 1.0) < 1e-6 and abs(db - 1.0) < 1e-6:
        return p
    import numpy as np
    v = np.array([max(p.get(s, 1e-9), 1e-9) for s in ("home", "draw", "away")])
    v[1] *= db
    v = v ** g
    tot = v.sum()
    return dict(zip(("home", "draw", "away"), v / tot)) if tot > 0 else p


load_calibration()


def league_positions(summary_form: Dict[str, Any]) -> Dict[str, Optional[int]]:
    out: Dict[str, Optional[int]] = {"HOME": None, "AWAY": None}
    for ep in (summary_form or {}).get("eventParticipants") or []:
        side = ((ep.get("type") or {}).get("side") or "").upper()
        try:
            lab = ep["table"]["rows"][0]["values"][0]["label"]
            out[side] = int(str(lab).strip().rstrip("."))
        except Exception:
            pass
    return out


def assess(
    mid: str,
    home: str,
    away: str,
    league: str,
    kickoff: int,
    bundle: Dict[str, Any],
    now: Optional[int] = None,
    half_life: float = 120.0,
    xg_weight: float = 0.5,
) -> Optional[Assessment]:
    now = now or int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    sections = bundle.get("h2h") or {}
    if not sections:
        return None

    h_rows = team_rows(sections, home)
    a_rows = team_rows(sections, away)
    if not h_rows or not a_rows:
        return None

    hr = M.build_rates(home, h_rows, now, half_life=half_life, xg_weight=xg_weight)
    ar = M.build_rates(away, a_rows, now, half_life=half_life, xg_weight=xg_weight)

    pooled = list(h_rows) + list(a_rows)
    mu = M.league_mean(pooled, now=now, half_life=half_life)
    scorelines = [(r.gh, r.ga) for r in pooled]
    rho = M.rho_mle(scorelines, mu, mu)

    fc = M.forecast(hr, ar, mu, rho=rho)
    mk = M.read_market(bundle.get("odds_raw") or {}, method="blend")

    # ---- blend market and model ------------------------------------------- #
    p_model = {"home": fc.p_home, "draw": fc.p_draw, "away": fc.p_away}
    if mk:
        p_mkt = {"home": mk.p_home, "draw": mk.p_draw, "away": mk.p_away}
    else:
        p_mkt = p_model

    # ---- blend weight ------------------------------------------------------#
    # EMPIRICALLY FITTED, not guessed. A held-out sweep over 194 finished
    # matches (`calibrate.py` methodology) scored log-loss by model weight:
    #
    #     w_model  0.00    0.05    0.10    0.30    0.60    1.00
    #     logloss  0.9331  0.9328  0.9331  0.9387  0.9585  1.0055
    #     brier    0.5506  0.5505  0.5507  0.5544  0.5684  0.6028
    #
    # The optimum is ~0.05-0.10 and the curve is flat to ~0.20. Translated:
    # the bookmaker price already contains nearly all publicly available
    # information, and a from-scratch Poisson model on public data adds
    # essentially nothing to a 1X2 forecast. Pretending otherwise is how
    # people lose money. We keep the model at a small weight because it DOES
    # add value when the market is thin or illiquid.
    w_model = 0.10
    if hr.n_xg >= 5 and ar.n_xg >= 5:
        w_model += 0.05                          # real shot-quality data
    if mk and mk.overround > 1.12:               # soft/illiquid market
        w_model += 0.05
    if mk and mk.n_books < 2:                    # no consensus price to trust
        w_model += 0.05
    w_model = float(min(max(w_model, 0.05), 0.35))
    w_mkt = 1.0 - w_model
    p_blend = {k: w_mkt * p_mkt[k] + w_model * p_model[k] for k in p_model}
    s = sum(p_blend.values()) or 1.0
    p_blend = {k: v / s for k, v in p_blend.items()}
    p_blend = apply_calibration(p_blend)

    # ---- signals ----------------------------------------------------------- #
    signals: List[Signal] = []

    # 1. model
    v = _side_from_probs(p_model)
    signals.append(Signal("Poisson/xG model", v, 1.60,
                          f"λ {fc.lh:.2f}-{fc.la:.2f} | {p_model['home']:.0%}/"
                          f"{p_model['draw']:.0%}/{p_model['away']:.0%}"))

    # 2. market
    if mk:
        v = _side_from_probs(p_mkt)
        signals.append(Signal("Market (devigged)", v, 1.90,
                              f"{p_mkt['home']:.0%}/{p_mkt['draw']:.0%}/{p_mkt['away']:.0%} "
                              f"({mk.n_books} books, vig {mk.overround-1:+.1%})"))

    # 3. form (last 5, decayed)
    d_form = hr.form5 - ar.form5
    v = "home" if d_form > 0.05 else ("away" if d_form < -0.05 else "abstain")
    signals.append(Signal("Recent form (last 5)", v, 0.85,
                          f"{hr.form5:.2f} vs {ar.form5:.2f} weighted PPG"))

    # 4. head-to-head
    hh = h2h_rows(sections, home, away)
    if hh:
        pts = w = 0.0
        for k, r in enumerate(sorted(hh, key=lambda x: x.date, reverse=True)[:8]):
            ww = 0.5 ** (k / 3.0)
            pts += ww * (3.0 if r.result == "w" else 1.0 if r.result == "d" else 0.0)
            w += ww
        h2h_ppg = (pts / w / 3.0) if w else 0.5
        v = "home" if h2h_ppg > 0.55 else ("away" if h2h_ppg < 0.45 else "abstain")
        signals.append(Signal("Head-to-head", v, 0.55 if len(hh) >= 4 else 0.25,
                              f"{len(hh)} meetings, home PPG {h2h_ppg:.2f}"))
    else:
        h2h_ppg, hh = 0.5, []

    # 5. league position
    pos = league_positions(bundle.get("summary_form") or {})
    hp, ap = pos.get("HOME"), pos.get("AWAY")
    if hp and ap:
        d_p = ap - hp                      # positive => home team higher
        v = "home" if d_p >= 3 else ("away" if d_p <= -3 else "abstain")
        signals.append(Signal("League position", v, 0.80,
                              f"home {hp} vs away {ap}"))
    else:
        d_p = 0

    # 6. underlying performance (goals vs xG regression candidates)
    gh_rate = (hr.gf_home * hr.n_home + hr.gf_away * hr.n_away) / max(hr.n_home + hr.n_away, 1e-9)
    ga_rate = (hr.ga_home * hr.n_home + hr.ga_away * hr.n_away) / max(hr.n_home + hr.n_away, 1e-9)
    aa_rate = (ar.gf_home * ar.n_home + ar.gf_away * ar.n_away) / max(ar.n_home + ar.n_away, 1e-9)
    ad_rate = (ar.ga_home * ar.n_home + ar.ga_away * ar.n_away) / max(ar.n_home + ar.n_away, 1e-9)
    gd_home = gh_rate - ga_rate
    gd_away = aa_rate - ad_rate
    v = "home" if gd_home - gd_away > 0.25 else ("away" if gd_home - gd_away < -0.25 else "abstain")
    signals.append(Signal("Goal difference rate", v, 1.00,
                          f"home {gd_home:+.2f}/gm, away {gd_away:+.2f}/gm"))

    # 7. venue splits
    home_adv = (hr.gf_home - hr.gf_away) - (ar.gf_home - ar.gf_away)
    v = "home" if home_adv > 0.3 else ("away" if home_adv < -0.3 else "abstain")
    signals.append(Signal("Home/away split", v, 0.45,
                          f"home {hr.gf_home:.2f}H/{hr.gf_away:.2f}A, away {ar.gf_home:.2f}H/{ar.gf_away:.2f}A"))

    # 8. line movement
    if mk and mk.opening_home and mk.opening_draw and mk.opening_away:
        p_o = M.devig([mk.opening_home, mk.opening_draw, mk.opening_away], "blend")
        if p_o.size == 3:
            move_home = mk.p_home - float(p_o[0])
            move_away = mk.p_away - float(p_o[2])
            pick = "home" if move_home >= move_away else "away"
            mag = abs(move_home) if pick == "home" else abs(move_away)
            v = pick if mag >= 0.015 else "abstain"
            signals.append(Signal("Line movement", v, 0.70,
                                  f"home {move_home:+.1%}, away {move_away:+.1%} since open"))
    # 9. team news
    miss = bundle.get("missing") or {}
    nm_h, nm_a = len(miss.get("HOME") or []), len(miss.get("AWAY") or [])
    if nm_h or nm_a:
        diff = nm_a - nm_h
        v = "home" if diff >= 2 else ("away" if diff <= -2 else "abstain")
        signals.append(Signal("Availability", v, 0.50,
                              f"missing: home {nm_h}, away {nm_a}"))

    # 10. rest
    if hr.last_date and ar.last_date:
        rh = (now - hr.last_date) / 86400.0
        ra = (now - ar.last_date) / 86400.0
        diff = ra - rh
        v = "home" if diff >= 2 else ("away" if diff <= -2 else "abstain")
        signals.append(Signal("Rest days", v, 0.35,
                              f"home {rh:.0f}d, away {ra:.0f}d"))

    # ---- agreement ---------------------------------------------------------- #
    tot_w = sum(s.weight for s in signals if s.vote != "abstain") or 1.0
    pick_side = _side_from_probs(p_blend)
    agree_w = sum(s.weight for s in signals if s.vote == pick_side)
    agreement = agree_w / tot_w
    contra = [s for s in signals if s.vote not in ("abstain", pick_side)]

    # ---- red flags ---------------------------------------------------------- #
    flags: List[str] = []
    if not mk:
        flags.append("No bookmaker odds -> unpriceable, skipped")
    if mk and mk.overround > 1.16:
        flags.append(f"Very high overround ({mk.overround-1:.1%}) -> illiquid market")
    if mk and mk.n_books < 2:
        flags.append("Only one bookmaker -> unreliable price")
    if min(hr.complete, ar.complete) < 0.50:
        flags.append("Thin historical sample")
    if len(hh) < 3:
        flags.append("Few/no head-to-head meetings")
    lname = (league or "").lower()
    if any(k in lname for k in ("reserve", " u17", " u18", " u19", " u20", " u21", "youth", "women")):
        flags.append("Reserve/youth/women's fixture -> very weak market, low model reliability")
    if any(k in lname for k in ("friendly",)):
        flags.append("Friendly -> rotation risk, weak motivation signal")
    if abs((now - kickoff) / 3600.0) < 1.5:
        flags.append("Kickoff < 90 min away -> late team-news risk")
    if nm_h >= 4 or nm_a >= 4:
        flags.append("Heavy absentee list")

    # ---- consensus goal matrix ------------------------------------------------
    # Derived markets (O/U, BTTS, asian handicap, odd/even) live and die by the
    # total-goals intensity. The market's own O/U ladder is a far better
    # estimate of that intensity than any model, so we blend the two rather
    # than trusting the model's tails (which is where phantom edges come from).
    fcx = fc
    if mk and mk.lam_home and mk.lam_away:
        lh_b = w_model * fc.lh + w_mkt * mk.lam_home
        la_b = w_model * fc.la + w_mkt * mk.lam_away
        m_b = M.score_matrix(lh_b, la_b, fc.rho)
        oc = M.outcomes(m_b)
        fcx = M.PoissonForecast(
            lh=float(lh_b), la=float(la_b), rho=fc.rho, matrix=m_b,
            p_home=oc["home"], p_draw=oc["draw"], p_away=oc["away"],
            p_btts=M.btts_prob(m_b), p_over25=M.p_over_line(m_b, 2.5),
            mu_total=float(lh_b + la_b), shrinkage=fc.shrinkage,
        )

    # ---- price every market -------------------------------------------------- #
    prices = all_prices(bundle.get("odds_raw") or {})
    picks: List[Dict[str, Any]] = []
    all_priced: List[Dict[str, Any]] = []
    for pr in prices:
        prob = _prob_for(pr, p_blend, fcx, mk)
        if prob is None:
            continue
        p_win, p_push = prob
        p_lose = 1.0 - p_win - p_push
        if p_win <= 0 or p_lose < -1e-9:
            continue
        # stake 1 -> return `odds` on a win, 1 on a push, 0 on a loss
        ev = p_win * pr.odds + p_push * 1.0 - 1.0
        # EV measured against a VIG-FREE market. In a market with a 10%
        # overround every price is roughly -9% EV by construction, so a raw
        # EV gate can never pass and tells you nothing. Adding the overround
        # back asks the useful question: is this price better or worse than
        # the house average on this match?
        vig = (mk.overround - 1.0) if mk and mk.overround else 0.0
        ev_vig = ev + vig
        # NOTE: deliberately no early `continue` on ev <= 0 any more. Every
        # priced selection is recorded in `all_priced`; only `picks` is
        # filtered to positive EV. The lean tier needs the favourite even when
        # its price is fair-or-worse.
        # Kelly conditioned on a decisive result (a push just repeats the bet)
        decisive = p_win + p_lose
        q = (p_win / decisive) if decisive > 1e-9 else 0.0
        kelly = ((q * pr.odds - 1.0) / (pr.odds - 1.0)) if pr.odds > 1.0001 else 0.0
        kelly_q = max(0.0, kelly) * 0.25
        grade = _grade(p_win + 0.5 * p_push, ev, agreement,
                       min(hr.complete, ar.complete), flags, contra)
        side = _price_side(pr)
        aligned = (side == pick_side) or side == "neutral"
        entry = {
            "label": pr.label, "selection": pr.selection, "line": pr.line,
            "odds": pr.odds, "opening": pr.opening,
            "p_win": p_win, "p_push": p_push,
            "p_model_prob": p_win + 0.5 * p_push,   # "effective" probability
            "ev": ev, "ev_vig": ev_vig, "vig": vig,
            "kelly_full": kelly, "kelly_quarter": kelly_q,
            "grade": grade, "aligned": aligned, "side": side,
        }
        # every priced selection, EV or not. The value tier reads `picks`
        # (EV > 0 only); the lean tier needs the favourite regardless of EV,
        # because on an efficient card the favourite is almost never +EV.
        all_priced.append(entry)
        if ev > 0:
            picks.append(entry)
    picks.sort(key=lambda x: (x["aligned"], x["ev"]), reverse=True)

    best_grade = "SKIP"
    for g in ("ELITE", "STRONG", "FAIR", "MARGINAL"):
        if any(p["grade"] == g and p["aligned"] for p in picks):
            best_grade = g
            break

    return Assessment(
        match_id=mid, home=home, away=away, league=league or "", kickoff=kickoff,
        market=mk, forecast=fc, p_blend=p_blend, signals=signals,
        agreement=agreement, picks=picks, red_flags=flags,
        all_priced=all_priced,
        completeness=float(min(hr.complete, ar.complete)), best_grade=best_grade,
        notes={
            "rates_home": hr, "rates_away": ar, "h2h": hh, "league_mean": mu,
            "rho": rho, "w_model": w_model, "positions": pos,
            "h2h_ppg": h2h_ppg, "n_hist_home": len(h_rows), "n_hist_away": len(a_rows),
        },
    )


def _price_side(pr: Price) -> str:
    if pr.label in ("1X2", "Draw No Bet", "Asian Handicap"):
        return pr.selection.lower()
    if pr.label == "Double Chance":
        return {"1X": "home", "X2": "away", "12": "neutral"}[pr.selection]
    return "neutral"


def _prob_for(pr: Price, p_blend: Dict[str, float], fc: M.PoissonForecast,
              mk: Optional[M.MarketRead]) -> Optional[Tuple[float, float]]:
    """Fair (p_win, p_push) for one price, from the model matrix where possible.

    Markets that cannot push (1X2, DC, DNB, BTTS, Odd/Even) return p_push=0.
    Totals and asian handicaps return a real push probability on whole lines.
    """
    if pr.label == "1X2":
        return (p_blend[pr.selection.lower()], 0.0)
    if pr.label == "Double Chance":
        h, d, a = p_blend["home"], p_blend["draw"], p_blend["away"]
        return ({"1X": h + d, "X2": a + d, "12": h + a}[pr.selection], 0.0)
    if pr.label == "Draw No Bet":
        h, a = p_blend["home"], p_blend["away"]
        tot = h + a
        if tot <= 0:
            return None
        return ((h if pr.selection == "Home" else a) / tot, 0.0)
    if pr.label == "BTTS":
        p = fc.p_btts
        return (p if pr.selection == "Yes" else 1 - p, 0.0)
    if pr.label == "Over/Under" and pr.line is not None:
        return M.tot_probs(fc.matrix, pr.line, over=(pr.selection == "Over"))
    if pr.label == "Asian Handicap" and pr.line is not None:
        # each AH item carries the handicap from ITS OWN team's perspective
        # (home -0.75 pairs with away +0.75); normalise to home perspective.
        hcap = pr.line if pr.selection == "Home" else -pr.line
        return M.ah_probs(fc.matrix, hcap, side_home=(pr.selection == "Home"))
    if pr.label == "Odd/Even":
        tot = M.totals_probs(fc.matrix)
        odd = float(tot[1::2].sum())
        return (odd if pr.selection == "Odd" else 1 - odd, 0.0)
    return None


def _grade(p: float, ev: float, agreement: float, completeness: float,
           flags: List[str], contra: List[Signal]) -> str:
    hard = [f for f in flags if f.startswith(("No bookmaker", "Only one", "Thin historical",
                                              "Reserve/youth", "Friendly", "Very high"))]
    if p >= 0.66 and ev >= 0.05 and agreement >= 0.80 and completeness >= 0.70 and not hard:
        return "ELITE"
    if p >= 0.58 and ev >= 0.025 and agreement >= 0.70 and completeness >= 0.55 and not hard:
        return "STRONG"
    if p >= 0.50 and ev >= 0.01 and agreement >= 0.60:
        return "FAIR"
    return "MARGINAL"


# freeze the ENGINE namespace so `E.name` keeps working
E = _types.SimpleNamespace(
    **{k: v for k, v in list(globals().items()) if not k.startswith('_')}
)


# ==========================================================================
# SCAN DAY  --  from fsx/scan_day.py
# ==========================================================================

"""
fsx.scan_day -- "give me the trusted straight wins for dd-mm-yyyy".

This is the layer your Telegram bot talks to. It takes a date, pulls every
Flashscore match on that date, runs the full engine, and returns ranked
straight-win candidates with a safer (double-chance / draw-no-bet) fallback
and a goals-market alternative.

Three things this file handles that a naive `fixtures(0)` does not:

1. LOCAL-DATE FILTERING
   Flashscore's day feed is offset-based in UTC and each response SPILLS into
   the neighbouring day (offset 0 for 09-09 also returned 17 matches stamped
   09-08). A 23:30 UTC kickoff is already tomorrow in Lagos. So we pull the
   offsets either side and keep only matches whose *local* date matches.

2. THE FEED WINDOW IS -7..+7 DAYS
   Verified by probing every offset: -8 and +8 return zero matches. Anything
   outside the window raises OutOfWindow so the bot can say so instead of
   silently returning nothing.

3. BIG-CARD COST CONTROL
   A Saturday card can be 1,800 matches. A zero-request pre-filter on the
   fixture `MW` field (the bookmaker-id list) drops the no-odds matches before
   a single HTTP call is spent -- verified 0 false negatives in testing.
"""


import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


log = logging.getLogger("fsx.scan_day")

FEED_MIN_OFFSET = -7
FEED_MAX_OFFSET = 7

# Home/away participant ids -> 1xBet, used as a fast "is this match priced"
# sanity hint. Never used to select a bookmaker.
KNOWN_BOOKS = (417, 849)

DATE_FMT = "%d-%m-%Y"


class OutOfWindow(ValueError):
    """Requested date is outside the window Flashscore's day feed serves."""


class BadDate(ValueError):
    """Could not parse the date string."""


# --------------------------------------------------------------------------- #

def parse_date(text: str) -> _dt.date:
    """Parse dd-mm-yyyy (also tolerates dd/mm/yyyy and dd.mm.yyyy)."""
    t = (text or "").strip()
    for sep in ("-", "/", "."):
        if sep in t:
            parts = t.split(sep)
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                d, m, y = (int(p) for p in parts)
                try:
                    return _dt.date(y, m, d)
                except ValueError:
                    raise BadDate(f"{t} is not a real date")
    raise BadDate(f"could not read '{t}' -- expected dd-mm-yyyy, e.g. 09-09-2026")


def day_offset(target: _dt.date, today: Optional[_dt.date] = None,
               tz: str = "Africa/Lagos") -> int:
    if today is None:
        today = _dt.datetime.now(ZoneInfo(tz)).date()
    off = (target - today).days
    if not (FEED_MIN_OFFSET <= off <= FEED_MAX_OFFSET):
        raise OutOfWindow(
            f"Flashscore's day feed only serves {FEED_MIN_OFFSET:+d}..{FEED_MAX_OFFSET:+d} "
            f"days from today ({today.strftime(DATE_FMT)}); "
            f"{target.strftime(DATE_FMT)} is {off:+d} days out.")
    return off


def _has_odds_hint(rec: Dict[str, str]) -> bool:
    """Zero-request pre-filter: an empty MW means no bookmaker is attached."""
    mw = (rec.get("MW") or "").strip()
    return bool(mw)


def fixtures_on(client: Client, target: _dt.date, tz: str = "Africa/Lagos",
                upcoming_only: bool = True) -> List[Dict[str, str]]:
    """Every match whose LOCAL date is `target`.

    Pulls the day offset and its two neighbours, then filters on local date,
    because each feed response leaks into adjacent days.
    """
    zone = ZoneInfo(tz)
    today = _dt.datetime.now(zone).date()
    off = day_offset(target, today, tz)
    offsets = sorted({o for o in (off - 1, off, off + 1)
                      if FEED_MIN_OFFSET <= o <= FEED_MAX_OFFSET})

    seen, out = set(), []
    for o in offsets:
        for rec in client.fixtures(o):
            mid = rec.get("AA")
            if not mid or mid in seen:
                continue
            ts = rec.get("AD")
            if not ts:
                continue
            local = _dt.datetime.fromtimestamp(int(ts), zone).date()
            if local != target:
                continue
            if upcoming_only and rec.get("AB") != STATUS_UPCOMING:
                continue
            seen.add(mid)
            rec["local_kickoff"] = _dt.datetime.fromtimestamp(int(ts), zone)
            out.append(rec)
    out.sort(key=lambda r: int(r.get("AD") or 0))
    log.info("date %s -> %d matches (offsets %s)", target, len(out), offsets)
    return out


# --------------------------------------------------------------------------- #

@dataclass
class DayPick:
    """One qualifying match, with a straight win and its safer alternatives."""
    match_id: str
    home: str
    away: str
    league: str
    kickoff: _dt.datetime
    grade: str
    agreement: float
    completeness: float
    p_home: float
    p_draw: float
    p_away: float
    lam_home: float
    lam_away: float
    straight: Optional[Dict[str, Any]] = None
    safer: Optional[Dict[str, Any]] = None
    goals: Optional[Dict[str, Any]] = None
    lean: bool = False          # True = high-agreement favourite, no +EV
    lean_tier: str = ""         # display tier for leans (they bypass grade)
    red_flags: List[str] = field(default_factory=list)
    signals: List[E.Signal] = field(default_factory=list)
    market_overround: Optional[float] = None
    n_books: int = 0

    @property
    def side(self) -> str:
        return self.straight["selection"].lower() if self.straight else ""

    @property
    def team(self) -> str:
        if not self.straight:
            return ""
        return self.home if self.side == "home" else self.away


@dataclass
class DayScan:
    date: _dt.date
    n_fixtures: int
    n_prefiltered: int
    n_priced: int
    n_qualifying: int
    picks: List[DayPick]
    elapsed: float = 0.0
    leans: List[DayPick] = field(default_factory=list)
    capped: bool = False
    errors: int = 0

    def summary(self) -> str:
        return (f"{self.n_qualifying} value + {len(self.leans)} lean of "
                f"{self.n_priced} priced ({self.n_fixtures} on the card)")


# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Selection rules                                                              #
# --------------------------------------------------------------------------- #
# These exist because the backtest proved the naive rule is a trap. Ranking by
# expected value surfaces the LONGest shots, because that is where the model
# and the market disagree most -- and where the model is least reliable:
#
#     value filter EV >= +5%   ->  -21.6% ROI  (37 bets)
#     value filter EV >= +10%  ->  -45.7% ROI  (22 bets)
#
# A live example of what this produced before the guard: "Moreirense to win
# @ 17.00, p 8%, EV +32.8%". That is not a trusted straight win, it is a
# rounding error in the tail dressed up as an edge.
#
# So a straight win must ALSO be a team that is genuinely likely to win.

MIN_STRAIGHT_P = 0.42      # below this it is a flyer, not a straight win
MAX_STRAIGHT_ODDS = 3.50   # above this the model's tail is not trustworthy
MAX_SIDE_ODDS = 6.00       # cap for the safer/goals alternatives

# ---- tier 2: "lean" --------------------------------------------------------
# Measured fact from the backtest: on a liquid market with a 7-10% overround,
# positive EV on a FAVOURITE almost never exists. Every +EV 1X2 candidate on
# two full days of live scanning was a 3.5-18.0 longshot. Gating the bot on
# EV alone therefore guarantees it only ever recommends flyers.
#
# The one filter the backtest actually validated was signal agreement
# (65.2% hit rate at agreement>=0.90 vs 52.4% below 0.60). So the second tier
# selects on probability + agreement and reports EV honestly instead of
# requiring it. It is labelled as having no mathematical edge, because it does
# not have one.
LEAN_MIN_P = 0.50
LEAN_MIN_AGREEMENT = 0.75
LEAN_MAX_ODDS = 3.00
# Gate on VIG-ADJUSTED EV, not raw EV. With a 7-10% overround every raw EV is
# negative by construction; requiring ev > -0.05 simply returns nothing, ever.
# ev_vig >= 0 means "this price is at least as good as the house average on
# this match", which is the only meaningful question on an efficient card.
LEAN_MIN_EV_VIG = 0.0


def _clean_cands(picks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop lottery tickets: any price past MAX_SIDE_ODDS."""
    return [p for p in picks if p["ev"] > 0 and p["odds"] <= MAX_SIDE_ODDS]


def _best(picks: Sequence[Dict[str, Any]], labels: Sequence[str],
          side: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cands = [p for p in _clean_cands(picks) if p["label"] in labels]
    if side and cands:
        aligned = [p for p in cands if _side_of(p) == side]
        cands = aligned or cands
    if not cands:
        return None
    # prefer the highest-probability option, not the highest EV
    return max(cands, key=lambda p: (p["p_model_prob"], p["ev"]))


def _side_of(p: Dict[str, Any]) -> str:
    if p["label"] in ("1X2", "Draw No Bet", "Asian Handicap"):
        return p["selection"].lower()
    if p["label"] == "Double Chance":
        return {"1X": "home", "X2": "away", "12": ""}[p["selection"]]
    return ""


def _straight_pick(a: E.Assessment, min_p: float = MIN_STRAIGHT_P,
                   max_odds: float = MAX_STRAIGHT_ODDS) -> Optional[Dict[str, Any]]:
    """A straight win = back a team to win in 90 minutes.

    The draw is never a candidate. Beyond positive EV the selection must also
    be genuinely likely (>= min_p) and not a longshot (<= max_odds), and among
    qualifiers we take the one most likely to land rather than the biggest EV.
    """
    cands = [p for p in a.picks
             if p["label"] == "1X2" and p["selection"] in ("Home", "Away")
             and p["ev"] > 0
             and p["p_model_prob"] >= min_p
             and p["odds"] <= max_odds]
    if not cands:
        return None
    return max(cands, key=lambda p: (p["p_model_prob"], p["ev"]))


def _lean_tier(sel: Dict[str, Any], a: E.Assessment) -> str:
    """Leans bypass the value grading (which is EV-gated), so they need their
    own displayed confidence tier built from probability + agreement -- the two
    things the backtest actually validated."""
    p, ag = sel["p_model_prob"], a.agreement
    if p >= 0.65 and ag >= 0.88:
        return "STRONG LEAN"
    if p >= 0.55 and ag >= 0.82:
        return "LEAN"
    return "WEAK LEAN"


def _lean_pick(a: E.Assessment) -> Optional[Dict[str, Any]]:
    """Strongest favourite, used for the no-edge 'lean' tier.

    Selection is on probability + agreement, NOT expected value. EV is
    reported so the user can see the price is fair-or-worse.
    """
    # NOTE: reads all_priced, not picks. picks is EV>0 only, and on an
    # efficient card the favourite is essentially never positive EV -- so a
    # lean tier built on picks would always come back empty.
    cands = [p for p in (a.all_priced or a.picks)
             if p["label"] == "1X2" and p["selection"] in ("Home", "Away")
             and p["p_model_prob"] >= LEAN_MIN_P
             and p["odds"] <= LEAN_MAX_ODDS
             and p.get("ev_vig", p["ev"]) >= LEAN_MIN_EV_VIG]
    if not cands:
        return None
    return max(cands, key=lambda p: (p.get("ev_vig", p["ev"]), p["p_model_prob"]))


def to_day_pick(a: E.Assessment, tz: str = "Africa/Lagos",
                min_p: float = MIN_STRAIGHT_P,
                max_odds: float = MAX_STRAIGHT_ODDS,
                allow_lean: bool = False,
                min_agreement: float = LEAN_MIN_AGREEMENT) -> Optional[DayPick]:
    straight = _straight_pick(a, min_p, max_odds)
    lean = False
    if not straight and allow_lean:
        straight = _lean_pick(a)
        if not straight:
            return None
        if a.agreement < min_agreement:
            return None
        lean = True
    if not straight:
        return None
    side = straight["selection"].lower()
    zone = ZoneInfo(tz)
    ko = _dt.datetime.fromtimestamp(a.kickoff, zone)
    return DayPick(
        match_id=a.match_id, home=a.home, away=a.away, league=a.league,
        kickoff=ko, grade=a.best_grade, agreement=a.agreement,
        completeness=a.completeness,
        p_home=a.p_blend["home"], p_draw=a.p_blend["draw"], p_away=a.p_blend["away"],
        lam_home=a.forecast.lh if a.forecast else 0.0,
        lam_away=a.forecast.la if a.forecast else 0.0,
        straight=straight, lean=lean, lean_tier=(_lean_tier(straight, a) if lean else ""),
        safer=_best(a.picks, ("Double Chance", "Draw No Bet"), side=side),
        goals=_best(a.picks, ("Over/Under", "Both Teams to Score")),
        red_flags=list(a.red_flags), signals=list(a.signals),
        market_overround=(a.market.overround if a.market else None),
        n_books=(a.market.n_books if a.market else 0),
    )


def scan_date(
    client: Client,
    target: _dt.date,
    tz: str = "Africa/Lagos",
    max_matches: int = 120,
    workers: int = 8,
    depth: str = "full",
    league_filter: Optional[Sequence[str]] = None,
    min_grade: str = "MARGINAL",
    half_life: float = 120.0,
    xg_weight: float = 0.5,
    min_p: float = MIN_STRAIGHT_P,
    max_odds: float = MAX_STRAIGHT_ODDS,
    allow_lean: bool = True,
    min_agreement: float = LEAN_MIN_AGREEMENT,
    progress=None,
) -> DayScan:
    """Scan one calendar date end to end.

    max_matches caps how many fixtures get the expensive deep fetch; the
    pre-filter means the cheap ones cost nothing.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    t0 = time.time()
    fx = fixtures_on(client, target, tz)

    if league_filter:
        keys = [k.lower() for k in league_filter]
        fx = [f for f in fx if any(k in (f.get("league") or "").lower() for k in keys)]

    n_fixtures = len(fx)

    # zero-request pre-filter
    priced_candidates = [f for f in fx if _has_odds_hint(f)]
    n_prefiltered = n_fixtures - len(priced_candidates)

    capped = False
    if max_matches and len(priced_candidates) > max_matches:
        priced_candidates = priced_candidates[:max_matches]
        capped = True

    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.depth, cfg.half_life, cfg.xg_weight = depth, half_life, xg_weight

    results: List[E.Assessment] = []
    errors = 0
    done = 0

    def work(rec: Dict[str, str]) -> Optional[E.Assessment]:
        try:
            books, markets = client.odds_menu(rec["AA"])
            if not markets:
                return None
            bundle = client.match_bundle(rec["AA"], depth=depth)
            return E.assess(rec["AA"], rec.get("CX", ""), rec.get("AF", ""),
                            rec.get("league", ""), int(rec.get("AD") or 0),
                            bundle, half_life=half_life, xg_weight=xg_weight)
        except Exception as e:                                  # noqa: BLE001
            log.debug("match %s failed: %s", rec.get("AA"), e)
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, f) for f in priced_candidates]
        for fu in as_completed(futs):
            done += 1
            try:
                r = fu.result()
            except Exception:                                   # noqa: BLE001
                r = None
                errors += 1
            if r:
                results.append(r)
            if progress and (done % 20 == 0 or done == len(futs)):
                try:
                    progress(done, len(futs))
                except Exception:                               # noqa: BLE001
                    pass

    grade_rank = {"ELITE": 4, "STRONG": 3, "FAIR": 2, "MARGINAL": 1, "SKIP": 0}
    min_rank = grade_rank.get(min_grade, 1)
    picks: List[DayPick] = []
    leans: List[DayPick] = []
    for a in results:
        # The grade gate is a *value* gate (best_grade is SKIP unless some
        # selection has positive EV). Lean candidates have no +EV selection by
        # definition, so applying it to them would empty the tier. Their gate
        # is agreement + probability instead.
        dp = to_day_pick(a, tz, min_p=min_p, max_odds=max_odds,
                         allow_lean=allow_lean, min_agreement=min_agreement)
        if not dp or not dp.straight:
            continue
        if not dp.lean and grade_rank.get(a.best_grade, 0) < min_rank:
            continue
        (leans if dp.lean else picks).append(dp)
    # Most likely to land first. Deliberately NOT sorted by EV: the backtest
    # showed big EV correlates with LOSING, so EV is a filter, never a ranking.
    key = lambda p: (p.straight["p_model_prob"], p.agreement)
    picks.sort(key=key, reverse=True)
    leans.sort(key=key, reverse=True)

    return DayScan(
        date=target, n_fixtures=n_fixtures, n_prefiltered=n_prefiltered,
        n_priced=len(results), n_qualifying=len(picks), picks=picks,
        leans=leans, elapsed=time.time() - t0, capped=capped, errors=errors,
    )


def scan_date_text(text: str, client: Optional[Client] = None, **kw) -> DayScan:
    """Convenience: parse 'dd-mm-yyyy' and scan it."""
    client = client or Client()
    return scan_date(client, parse_date(text), **kw)


# ==========================================================================
# TELEGRAM BOT  --  from flashscore_trust_bot.py
# ==========================================================================

#!/usr/bin/env python3
"""
flashscore_trust_bot.py -- Flashscore straight-win bot.

Send it a date and it scans every Flashscore match on that day, runs the full
model, and replies with the qualifying straight wins.

    you:  09-09-2026
    bot:  scans that day's whole card, replies with ranked picks

Commands
--------
    /start  /help            -- usage
    dd-mm-yyyy               -- scan that date   <-- the main one
    /today  /tomorrow  /yesterday
    /id <match_id>           -- deep dive on one match
    /leagues <word>          -- restrict the next scan, /leagues off to clear
    /status                  -- bot + data-source health

Setup
-----
    export TELEGRAM_BOT_TOKEN=123456:ABC...
    python flashscore_trust_bot.py

    # or test without a token at all:
    python flashscore_trust_bot.py --dry-run 09-09-2026

Dependencies: requests only. No python-telegram-bot, no webhook, no framework.
Handlers run in a worker pool so a 40-second scan never blocks polling.
"""


import argparse
import datetime as _dt
import html
import logging
import os
import queue
import re
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


log = logging.getLogger("bot")

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MSG = 4096
TZ = "Africa/Lagos"
GRADE_RANK = {"ELITE": 4, "STRONG": 3, "FAIR": 2, "MARGINAL": 1, "SKIP": 0}
GRADE_ICON = {"ELITE": "🟡", "STRONG": "🟢", "FAIR": "🔵", "MARGINAL": "⚪"}

DATE_RE = re.compile(r"^\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})\s*$")

FOOTER = (
    "\n<i>Not guarantees. A 70% pick still loses 3 times in 10. "
    "Stake a fixed small fraction; never money you need.</i>"
)


# --------------------------------------------------------------------------- #
# Telegram transport                                                           #
# --------------------------------------------------------------------------- #

class Telegram:
    def __init__(self, token: str, timeout: float = 40.0) -> None:
        self.token = token
        self.s = requests.Session()
        self.s.timeout = timeout
        self._last_send = 0.0
        self._lock = threading.Lock()

    def call(self, method: str, **params) -> Optional[dict]:
        url = API.format(token=self.token, method=method)
        for attempt in range(3):
            try:
                r = self.s.post(url, json={k: v for k, v in params.items()
                                           if v is not None}, timeout=45)
                if r.status_code == 429:
                    wait = float((r.json().get("parameters") or {}).get("retry_after", 2))
                    time.sleep(min(wait, 30))
                    continue
                data = r.json()
                if data.get("ok"):
                    return data["result"]
                log.warning("tg %s -> %s", method, data)
                return None
            except Exception as e:                               # noqa: BLE001
                log.warning("tg %s error (%s): %s", method, attempt, e)
                time.sleep(1 + attempt)
        return None

    def get_updates(self, offset: int, timeout: int = 30) -> List[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout,
                         allowed_updates=["message", "edited_message"]) or []

    def send(self, chat_id: int, text: str,
             reply_to: Optional[int] = None,
             disable_preview: bool = True) -> Optional[int]:
        # ~30 msg/sec global limit; be polite
        with self._lock:
            gap = time.time() - self._last_send
            if gap < 0.05:
                time.sleep(0.05 - gap)
            self._last_send = time.time()
        if len(text) > MAX_MSG:
            text = text[: MAX_MSG - 20] + "\n… (truncated)"
        r = self.call("sendMessage", chat_id=chat_id, text=text,
                      parse_mode="HTML", reply_to_message_id=reply_to,
                      disable_web_page_preview=disable_preview)
        return (r or {}).get("message_id")

    def edit(self, chat_id: int, message_id: int, text: str) -> None:
        if len(text) > MAX_MSG:
            text = text[: MAX_MSG - 20] + "\n… (truncated)"
        self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                  text=text, parse_mode="HTML", disable_web_page_preview=True)

    def action(self, chat_id: int, act: str = "typing") -> None:
        self.call("sendChatAction", chat_id=chat_id, action=act)


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def send_chunked(tg: Telegram, chat_id: int, blocks: Sequence[str],
                 footer: Optional[str] = None) -> None:
    """Pack blocks into as few messages as possible under Telegram's 4096."""
    buf = ""
    for b in blocks:
        if buf and len(buf) + len(b) + 2 > MAX_MSG - 200:
            tg.send(chat_id, buf)
            buf = ""
        buf += ("\n\n" if buf else "") + b
    if footer:
        if len(buf) + len(footer) > MAX_MSG - 50:
            tg.send(chat_id, buf)
            buf = footer
        else:
            buf += "\n" + footer
    if buf.strip():
        tg.send(chat_id, buf)


# --------------------------------------------------------------------------- #
# Formatting                                                                   #
# --------------------------------------------------------------------------- #

def fmt_pick(i: int, p: DayPick) -> str:
    if p.lean:
        g = p.lean_tier or "LEAN"
        icon = {"STRONG LEAN": "🟢", "LEAN": "🔵", "WEAK LEAN": "⚪"}.get(g, "🔵")
    else:
        g = p.grade
        icon = GRADE_ICON.get(g, "⚪")
    ko = p.kickoff.strftime("%H:%M")
    lines = [f"{icon} <b>[{esc(g)}]</b>  <b>{esc(p.home)} vs {esc(p.away)}</b>"]
    lines.append(f"🏆 {esc(p.league)} · {ko} WAT")

    s = p.straight
    if s:
        team = p.home if s["selection"] == "Home" else p.away
        pp = s["p_model_prob"]
        ev = s["ev"]
        if p.lean:
            evv = s.get("ev_vig", ev)
            vig = s.get("vig", 0.0)
            lines.append(f"🎯 <b>{esc(team)} to win @ {s['odds']:.2f}</b>  "
                         f"(p {pp:.0%})")
            lines.append(f"💵 price {ev:+.1%} vs a {vig:.0%} overround → "
                         f"<b>{evv:+.1%} better than the house average</b>")
        else:
            lines.append(f"🎯 <b>{esc(team)} to win @ {s['odds']:.2f}</b>  "
                         f"(p {pp:.0%} · EV {ev:+.1%})")
    lines.append(f"📊 agreement {p.agreement:.0%} · data {p.completeness:.0%} · "
                 f"λ {p.lam_home:.2f}–{p.lam_away:.2f} · {p.n_books} book(s)")
    if p.p_draw >= max(p.p_home, p.p_away):
        lines.append(f"⚠️ draw is the single most likely outcome "
                     f"({p.p_draw:.0%}) — this is a lean, not a lock")
    if p.safer:
        q = p.safer
        ln = f" {q['line']}" if q.get("line") else ""
        lines.append(f"🔒 safer: {esc(q['label'])} {esc(q['selection'])}{ln} "
                     f"@ {q['odds']:.2f} (p {q['p_model_prob']:.0%})")
    if p.goals:
        q = p.goals
        ln = f" {q['line']}" if q.get("line") else ""
        lines.append(f"⚽ also +EV: {esc(q['label'])} {esc(q['selection'])}{ln} "
                     f"@ {q['odds']:.2f} (EV {q['ev']:+.1%})")
    if p.red_flags:
        lines.append("🚩 " + esc("; ".join(p.red_flags)))
    lines.append(f"<code>/id {esc(p.match_id)}</code>")
    return "\n".join(lines)


def fmt_scan(scan: DayScan, requested: str, show_leans: bool = True) -> List[str]:
    d = scan.date
    head = (f"📅 <b>Straight wins — {esc(d.strftime('%a %d %b %Y'))}</b>\n"
            f"<i>requested: {esc(requested)}</i>\n"
            f"card {scan.n_fixtures} · skipped {scan.n_prefiltered} (no odds) · "
            f"priced {scan.n_priced}\n"
            f"⏱ {scan.elapsed:.0f}s"
            + (" · ⚠️ match cap hit, raise --max to cover the rest" if scan.capped else ""))
    blocks = [head]

    if scan.picks:
        blocks.append(f"✅ <b>VALUE — positive expected value ({scan.n_qualifying})</b>")
        for i, p in enumerate(scan.picks, 1):
            blocks.append(f"<b>{i}.</b> " + fmt_pick(i, p))
    else:
        blocks.append(
            "✅ <b>VALUE — none</b>\n"
            "<i>Nothing on this card is priced below its true probability. That is "
            "normal: the overround is 7–10% and every positive-EV opportunity on a "
            "favourite was already taken. The picks that DO show positive EV on days "
            "like this are longshots, and the backtest measured those losing 45.7%.</i>")

    if show_leans and scan.leans:
        blocks.append(
            f"🔎 <b>LEAN — high agreement, no edge ({len(scan.leans)})</b>\n"
            f"<i>These are the teams most likely to win where every independent "
            f"signal agrees. Agreement is the one filter the backtest validated "
            f"(65.2% vs 52.4%). But the price is fair-or-worse, so there is no "
            f"mathematical edge. Treat as information, not as a bet tip.</i>")
        for i, p in enumerate(scan.leans, 1):
            blocks.append(f"<b>{i}.</b> " + fmt_pick(i, p))
    return blocks


def fmt_one(a: E.Assessment) -> List[str]:
    ko = _dt.datetime.fromtimestamp(a.kickoff, ZoneInfo(TZ)).strftime("%a %d %b %H:%M WAT")
    head = (f"🔍 <b>{esc(a.home)} vs {esc(a.away)}</b>\n"
            f"🏆 {esc(a.league)} · {ko}\n"
            f"grade <b>{esc(a.best_grade)}</b> · agreement {a.agreement:.0%} · "
            f"data {a.completeness:.0%}")
    rows = []
    if a.market and a.forecast:
        m, f = a.market, a.forecast
        rows.append("<b>Source</b>            <b>H</b>     <b>D</b>     <b>A</b>")
        rows.append(f"market    {m.p_home:>6.1%} {m.p_draw:>6.1%} {m.p_away:>6.1%}")
        rows.append(f"model     {f.p_home:>6.1%} {f.p_draw:>6.1%} {f.p_away:>6.1%}")
        rows.append(f"<b>blend</b>     {a.p_blend['home']:>6.1%} "
                    f"{a.p_blend['draw']:>6.1%} {a.p_blend['away']:>6.1%}")
        rows.append(f"λ {f.lh:.2f}–{f.la:.2f} · ρ {f.rho:+.3f} · "
                    f"vig {m.overround-1:.1%} · {m.n_books} book(s)")
    sig = []
    for s in a.signals:
        ch = {"home": "H", "away": "A", "draw": "X", "abstain": "·"}[s.vote]
        sig.append(f"{ch} {esc(s.name)} — {esc(s.detail)}")
    body = "\n".join(rows)
    blocks = [head, "<pre>" + body + "</pre>", "<b>Signals</b>\n" + "\n".join(sig)]
    if a.red_flags:
        blocks.append("🚩 " + esc("; ".join(a.red_flags)))
    top = [p for p in a.picks if p["ev"] > 0][:10]
    if top:
        t = ["<b>Positive-EV prices</b>"]
        for p in top:
            ln = f" {p['line']}" if p["line"] is not None else ""
            t.append(f"· {esc(p['label'])} {esc(p['selection'])}{ln} @ {p['odds']:.2f} "
                     f"p {p['p_model_prob']:.0%} EV {p['ev']:+.1%} "
                     f"¼K {p['kelly_quarter']:.2%}")
        blocks.append("\n".join(t))
    return blocks


HELP = f"""<b>Flashscore straight-win bot</b>

Send a date and it scans every Flashscore match that day.

<code>09-09-2026</code>   scan that date  (dd-mm-yyyy)
<code>/today</code> <code>/tomorrow</code> <code>/yesterday</code>
<code>/id &lt;match_id&gt;</code>        deep dive on one match
<code>/leagues premier</code>  filter the next scan
<code>/leagues off</code>       clear the filter
<code>/status</code>            health check
<code>/help</code>              this message

Dates are read in your local timezone ({TZ}).
Flashscore's day feed only covers <b>±7 days</b> from today.

<b>What a pick means</b>
🟡 ELITE  p≥66%, EV≥+5%, ≥80% signal agreement
🟢 STRONG p≥58%, EV≥+2.5%, ≥70% agreement
🔵 FAIR   p≥50%, EV≥+1%,  ≥60% agreement
⚪ MARGINAL any positive EV

<b>Two sections in every reply</b>
✅ VALUE — genuine positive expected value
🔎 LEAN — high signal agreement but fair-or-worse price, so NO edge

<i>Grades are confidence tiers, not certainties. The backtest behind this
bot measured the market beating its own model by six accuracy points —
read the footer on every scan.</i>"""


# --------------------------------------------------------------------------- #
# Bot                                                                          #
# --------------------------------------------------------------------------- #

class Bot:
    def __init__(self, token: str, tz: str = TZ, max_matches: int = 120,
                 workers: int = 8, geo: str = "NG", geo_sub: str = "NGLA",
                 min_grade: str = "MARGINAL", max_picks: int = 12,
                 min_p: float = 0.42, max_odds: float = 3.50,
                 show_leans: bool = True) -> None:
        self.tg = Telegram(token)
        self.tz = tz
        self.max_matches = max_matches
        self.workers = workers
        self.min_grade = min_grade
        self.max_picks = max_picks
        self.min_p, self.max_odds = min_p, max_odds
        self.show_leans = show_leans
        self.geo, self.geo_sub = geo, geo_sub
        self.client = Client(geo=geo, geo_sub=geo_sub, delay=0.04)
        self.league_filter: Dict[int, Optional[List[str]]] = {}
        self._busy: Dict[int, bool] = {}
        self._q: "queue.Queue[Tuple[dict, dict]]" = queue.Queue()
        self._stop = threading.Event()
        for i in range(4):
            threading.Thread(target=self._worker, daemon=True,
                             name=f"h{i}").start()

    # -- worker pool -------------------------------------------------------- #

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                chat, msg = self._q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.handle(chat, msg)
            except Exception:                                    # noqa: BLE001
                log.exception("handler crashed")
                try:
                    self.tg.send(chat["id"], "⚠️ something broke handling that. "
                                             "It's logged; try again.")
                except Exception:                                # noqa: BLE001
                    pass
            finally:
                self._busy.pop(chat["id"], None)
                self._q.task_done()

    def dispatch(self, chat: dict, msg: dict) -> None:
        cid = chat["id"]
        if self._busy.get(cid):
            self.tg.send(cid, "⏳ still working on your last one — one scan at a time.")
            return
        self._busy[cid] = True
        self._q.put((chat, msg))

    # -- handlers ----------------------------------------------------------- #

    def handle(self, chat: dict, msg: dict) -> None:
        cid = chat["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            rest = rest.strip()
        else:
            m = DATE_RE.match(text)
            if m:
                cmd, rest = "/date", m.group(1)
            else:
                self.tg.send(cid, "Send a date as <code>dd-mm-yyyy</code> "
                                  "(e.g. 09-09-2026), or /help")
                return

        if cmd in ("/start", "/help"):
            self.tg.send(cid, HELP)
        elif cmd in ("/date", "/scan"):
            self.do_date(cid, rest)
        elif cmd == "/today":
            self.do_date(cid, _dt.datetime.now(ZoneInfo(self.tz)).strftime(DATE_FMT))
        elif cmd == "/tomorrow":
            self.do_date(cid, (_dt.datetime.now(ZoneInfo(self.tz)).date()
                               + _dt.timedelta(days=1)).strftime(DATE_FMT))
        elif cmd == "/yesterday":
            self.do_date(cid, (_dt.datetime.now(ZoneInfo(self.tz)).date()
                               - _dt.timedelta(days=1)).strftime(DATE_FMT))
        elif cmd == "/id":
            self.do_id(cid, rest)
        elif cmd == "/leagues":
            self.do_leagues(cid, rest)
        elif cmd in ("/status", "/ping"):
            self.do_status(cid)
        else:
            self.tg.send(cid, "Unknown command. /help")

    # -- actions ------------------------------------------------------------ #

    def do_leagues(self, cid: int, rest: str) -> None:
        if not rest or rest.lower() in ("off", "clear", "none", "all"):
            self.league_filter.pop(cid, None)
            self.tg.send(cid, "League filter cleared.")
            return
        keys = [k.strip() for k in rest.split(",") if k.strip()]
        self.league_filter[cid] = keys
        self.tg.send(cid, f"Filter set: <code>{esc(', '.join(keys))}</code>\n"
                          f"Send a date to scan. <code>/leagues off</code> to clear.")

    def do_status(self, cid: int) -> None:
        t0 = time.time()
        try:
            fx = self.client.fixtures(0)
            ok, n = "✅", len(fx)
        except Exception as e:                                   # noqa: BLE001
            ok, n = f"❌ {e}", 0
        lat = (time.time() - t0) * 1000
        now = _dt.datetime.now(ZoneInfo(self.tz)).strftime("%a %d %b %Y %H:%M:%S")
        self.tg.send(cid,
                     f"🩺 <b>status</b>\n"
                     f"time  {now} ({self.tz})\n"
                     f"feed  {ok} {n} matches today ({lat:.0f} ms)\n"
                     f"odds  {esc(self.geo)} / {esc(self.geo_sub)}\n"
                     f"cap   {self.max_matches} matches · {self.workers} workers\n"
                     f"grade ≥ {esc(self.min_grade)} · max {self.max_picks} picks\n"
                     f"gates p≥{self.min_p:.2f} · odds≤{self.max_odds:.2f} · "
                     f"leans {'on' if self.show_leans else 'off'}")

    def do_date(self, cid: int, text: str) -> None:
        try:
            target = parse_date(text)
        except BadDate as e:
            self.tg.send(cid, f"❌ {esc(e)}\nFormat is <code>dd-mm-yyyy</code>.")
            return

        status_id = self.tg.send(cid, f"🔎 scanning <b>{esc(target.strftime('%a %d %b %Y'))}</b>…")
        try:
            self.tg.action(cid)

            def progress(done: int, total: int) -> None:
                if status_id:
                    self.tg.edit(cid, status_id,
                                 f"🔎 scanning <b>{esc(target.strftime('%a %d %b %Y'))}</b>…\n"
                                 f"<i>priced {done}/{total}</i>")

            scan = scan_date(
                self.client, target, tz=self.tz,
                max_matches=self.max_matches, workers=self.workers,
                league_filter=self.league_filter.get(cid),
                min_grade=self.min_grade, progress=progress,
                min_p=self.min_p, max_odds=self.max_odds,
            )
            blocks = fmt_scan(scan, text, show_leans=self.show_leans)
            total = scan.n_qualifying + (len(scan.leans) if self.show_leans else 0)
            if total > self.max_picks:
                keep, n = [], 0
                for b in blocks[1:]:
                    if b.startswith(("✅", "🔎")):
                        keep.append(b)
                        continue
                    if n < self.max_picks:
                        keep.append(b)
                        n += 1
                blocks[0] += (f"\n\n<i>showing top {self.max_picks} of {total}</i>")
                blocks = [blocks[0]] + keep
            if status_id:
                try:
                    self.tg.edit(cid, status_id, "✅ done — sending picks…")
                except Exception:                                # noqa: BLE001
                    pass
            send_chunked(self.tg, cid, blocks, footer=FOOTER)
            if status_id:
                try:
                    self.tg.edit(cid, status_id,
                                 f"✅ {scan.summary()} · {scan.elapsed:.0f}s")
                except Exception:                                # noqa: BLE001
                    pass
        except OutOfWindow as e:
            self.tg.send(cid, f"📆 {esc(e)}")
        except Exception as e:                                   # noqa: BLE001
            log.exception("scan failed")
            self.tg.send(cid, f"⚠️ scan failed: <code>{esc(e)}</code>")

    def do_id(self, cid: int, rest: str) -> None:
        mid = rest.strip()
        if not mid:
            self.tg.send(cid, "Usage: <code>/id &lt;match_id&gt;</code>")
            return
        mid = re.sub(r"[^A-Za-z0-9]", "", mid)
        st = self.tg.send(cid, f"🔍 loading <code>{esc(mid)}</code>…")
        try:
            rec = None
            for off in range(-7, 3):
                for r in self.client.fixtures(off):
                    if r.get("AA") == mid:
                        rec = r
                        break
                if rec:
                    break
            if not rec:
                self.tg.send(cid, f"❌ no match <code>{esc(mid)}</code> in the "
                                  f"last week or the next 3 days.")
                return
            bundle = self.client.match_bundle(mid, depth="max")
            a = E.assess(mid, rec.get("CX", ""), rec.get("AF", ""),
                         rec.get("league", ""), int(rec.get("AD") or 0), bundle)
            if not a:
                self.tg.send(cid, "❌ not enough history to model that match.")
                return
            send_chunked(self.tg, cid, fmt_one(a), footer=FOOTER)
            if st:
                try:
                    self.tg.edit(cid, st, "✅ done")
                except Exception:                                # noqa: BLE001
                    pass
        except Exception as e:                                   # noqa: BLE001
            log.exception("id failed")
            self.tg.send(cid, f"⚠️ failed: <code>{esc(e)}</code>")

    # -- loop ---------------------------------------------------------------- #

    def run(self) -> None:
        offset = 0
        me = self.tg.call("getMe") or {}
        log.info("bot online: @%s (%s)", me.get("username"), me.get("first_name"))
        print(f"bot online: @{me.get('username')} — send it a date (dd-mm-yyyy)")
        while not self._stop.is_set():
            try:
                ups = self.tg.get_updates(offset)
            except Exception as e:                               # noqa: BLE001
                log.warning("poll error: %s", e)
                time.sleep(3)
                continue
            for u in ups:
                offset = max(offset, int(u.get("update_id", 0)) + 1)
                msg = u.get("message") or u.get("edited_message")
                if not msg or not msg.get("text"):
                    continue
                chat = msg.get("chat") or {}
                if chat.get("type") not in ("private", "group", "supergroup"):
                    continue
                log.info("[%s] %s", chat.get("id"), msg["text"][:80])
                self.dispatch(chat, msg)

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------- #

def dry_run(date_text: str, tz: str = TZ, max_matches: int = 40,
            workers: int = 8, geo: str = "NG", geo_sub: str = "NGLA",
            min_grade: str = "MARGINAL", min_p: float = 0.42,
            max_odds: float = 3.50) -> int:
    """Print exactly what the bot would send, no token required."""
    client = Client(geo=geo, geo_sub=geo_sub, delay=0.04)
    try:
        target = parse_date(date_text)
    except BadDate as e:
        print("bad date:", e)
        return 1
    print(f"scanning {target} (cap {max_matches})…", flush=True)
    try:
        scan = scan_date(client, target, tz=tz, max_matches=max_matches,
                         workers=workers, min_grade=min_grade,
                         min_p=min_p, max_odds=max_odds,
                         progress=lambda d, t: print(f"  {d}/{t}", flush=True))
    except OutOfWindow as e:
        print("out of window:", e)
        return 1
    blocks = fmt_scan(scan, date_text)
    print("\n" + "=" * 70)
    for b in blocks:
        print(re.sub(r"<[^>]+>", "", b))
        print("-" * 70)
    print(FOOTER)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.getenv("TELEGRAM_BOT_TOKEN"))
    ap.add_argument("--tz", default=TZ)
    ap.add_argument("--max", dest="max_matches", type=int, default=120,
                    help="cap on matches given the deep fetch (0 = no cap)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-grade", default="MARGINAL",
                    choices=["ELITE", "STRONG", "FAIR", "MARGINAL"])
    ap.add_argument("--min-p", type=float, default=0.42,
                    help="minimum fair probability for a straight win (default 0.42)")
    ap.add_argument("--max-odds", type=float, default=3.50,
                    help="maximum odds for a straight win (default 3.50)")
    ap.add_argument("--no-leans", action="store_true",
                    help="only send picks with genuine positive EV")
    ap.add_argument("--max-picks", type=int, default=12)
    ap.add_argument("--geo", default="NG")
    ap.add_argument("--geo-sub", default="NGLA")
    ap.add_argument("--dry-run", metavar="DD-MM-YYYY", default=None,
                    help="print what would be sent, no token needed")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.dry_run:
        return dry_run(args.dry_run, tz=args.tz, max_matches=args.max_matches or 40,
                       workers=args.workers, geo=args.geo, geo_sub=args.geo_sub,
                       min_grade=args.min_grade, min_p=args.min_p,
                       max_odds=args.max_odds)

    if not args.token:
        print("set TELEGRAM_BOT_TOKEN or pass --token", file=sys.stderr)
        print("(or run `python flashscore_trust_bot.py --dry-run 09-09-2026` to test offline)",
              file=sys.stderr)
        return 2

    bot = Bot(args.token, tz=args.tz, max_matches=args.max_matches or 10 ** 9,
              workers=args.workers, geo=args.geo, geo_sub=args.geo_sub,
              min_grade=args.min_grade, max_picks=args.max_picks,
              min_p=args.min_p, max_odds=args.max_odds,
              show_leans=not args.no_leans)

    def bye(*_):
        print("\nshutting down")
        bot.stop()
    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())


