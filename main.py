#!/usr/bin/env python3
"""
live_bot.py -- 24/7 live scanner. No commands. You never type anything.

WHAT YOU DO
-----------
    pip install requests
    export TELEGRAM_BOT_TOKEN=123456:ABC...
    python live_bot.py

First run only: open Telegram and send your bot ANY message (even "hi").
That is just so Telegram tells the bot your chat id. It gets saved, and from
then on the bot starts scanning by itself. You never need to send anything
again.

The only two commands that exist:

    /stop     pause scanning
    /start    resume scanning

That is all. Everything else happens by itself.

WHAT YOU SEE
------------
One message that keeps rewriting itself, match by match:

    LIVE SCANNER  * running
    sweep 4  *  uptime 0h 12m

    now scanning 23/75
    > Skenderbeu vs Kukesi

    judged 3  *  firing 0  *  alerts 0
    closest: Van vs BKMA  edge +3.1%

And when it finds something, a separate message that stays:

    BET NOW
    Skenderbeu vs Kukesi  (min 67')  score 0-1
    BET: Skenderbeu to win @ 2.10
    safer: Draw No Bet ~1.62
    why: dominating but behind -- xG 71%, shots on target +4
    model 58% vs market 41%  *  edge +12%

WHEN IT IS QUIET
----------------
Quiet means no edge found. It is still scanning. Watch the dashboard -- the
"now scanning" line keeps moving, which is how you know it is alive.

REQUIREMENT: Python 3.9+ and `requests`. Nothing else.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:                                          # noqa: BLE001
    sys.exit("missing dependency: requests\n\n    pip install requests\n")

if sys.version_info < (3, 9):
    sys.exit("live_bot needs Python 3.9 or newer\n")

log = logging.getLogger("live")

# --------------------------------------------------------------------------- #
# Config                                                                       #
# --------------------------------------------------------------------------- #

FSIGN = "SW9D1eZo"
FEED_BASE = "https://www.flashscore.com/x/feed"
GQL_ODDS = "https://global.ds.lsapp.eu/odds/pq_graphql"
PROJECT_ID = "2"

CHAT_FILE = "live_chat.json"     # remembers your chat + the dashboard message
LEDGER = "live_ledger.jsonl"     # every alert, for /settle-style scoring

# The trigger. Each gate exists because a specific failure was measured.
MIN_EDGE_VIG = 0.05     # must beat the in-play overround by this much
MIN_LEFT_MIN = 12.0     # need time for pressure to convert
MAX_ODDS     = 3.00     # no flyers
MIN_MODEL_P  = 0.40     # no longshots dressed up as edge
XG_SHARE_MIN = 0.62     # dominant side owns this much of the xG
SOT_EDGE_MIN = 3        # ... and leads shots on target by this much

# Data-quality gates: a match we cannot see is a match we must not judge.
MIN_MINUTE = 20.0
MAX_MINUTE = 80.0
MIN_XG     = 0.30

PRIOR_MIN  = 30.0
PRIOR_XG90 = 1.35
MAX_GOALS  = 8

ALERT_COOLDOWN = 1800.0   # same match+side is not re-pinged for 30 min
DASH_MIN_GAP   = 0.9      # seconds between dashboard edits (telegram limits)
SWEEP_GAP      = 5.0      # seconds between full sweeps


# --------------------------------------------------------------------------- #
# Poisson, stdlib only                                                         #
# --------------------------------------------------------------------------- #

def pois(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    if k < 0:
        return 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1.0))


def probs_1x2(sh: int, sa: int, lam_h: float, lam_a: float
              ) -> Tuple[float, float, float]:
    gh = [pois(i, lam_h) for i in range(MAX_GOALS + 1)]
    ga = [pois(j, lam_a) for j in range(MAX_GOALS + 1)]
    ph = pd = pa = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = gh[i] * ga[j]
            if p < 1e-12:
                continue
            fh, fa = sh + i, sa + j
            if fh > fa:
                ph += p
            elif fh == fa:
                pd += p
            else:
                pa += p
    t = ph + pd + pa
    return (ph / t, pd / t, pa / t) if t else (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# Flashscore                                                                   #
# --------------------------------------------------------------------------- #

class FS:
    def __init__(self, geo: str = "NG", geo_sub: str = "NGLA",
                 delay: float = 0.1, timeout: int = 15) -> None:
        self.geo, self.geo_sub = geo, geo_sub
        self.delay, self.timeout = delay, timeout
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({"x-fsign": FSIGN, "user-agent": "Mozilla/5.0"})

    def _wait(self) -> None:
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def feed(self, path: str) -> str:
        self._wait()
        try:
            r = self.s.get(f"{FEED_BASE}/{path}", timeout=self.timeout)
            return r.text if r.status_code == 200 else ""
        except Exception as e:                                # noqa: BLE001
            log.debug("feed %s: %s", path, e)
            return ""

    def gql(self, **params: Any) -> Dict[str, Any]:
        self._wait()
        try:
            r = self.s.get(GQL_ODDS, params=params, timeout=self.timeout)
            return r.json() if r.status_code == 200 else {}
        except Exception as e:                                # noqa: BLE001
            log.debug("gql: %s", e)
            return {}

    def fixtures(self, offset: int = 0) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for chunk in (self.feed(f"f_1_{offset}_3_en_1") or "").split("\u00ac~"):
            rec: Dict[str, str] = {}
            for fld in chunk.split("\u00ac"):
                if "\u00f7" not in fld:
                    continue
                k, _, v = fld.partition("\u00f7")
                k = k.lstrip("~")
                if k:
                    rec[k] = v
            if rec.get("AA"):
                out.append(rec)
        return out

    def live(self) -> List[Dict[str, str]]:
        return [r for r in self.fixtures(0)
                if r.get("AB") == "2" and r.get("MW")]

    def odds(self, mid: str) -> Dict[str, Any]:
        """Live 1X2 from a bookmaker that is actually pricing in-play.

        Array order is [home, away, DRAW] -- a null participantId is the draw.
        """
        d = self.gql(_hash="oce", eventId=mid, projectId=PROJECT_ID,
                     geoIpCode=self.geo, geoIpSubdivisionCode=self.geo_sub)
        groups = (((d.get("data") or {}).get("findOddsByEventId") or {})
                  .get("odds") or [])
        hda = [g for g in groups
               if g.get("bettingType") == "HOME_DRAW_AWAY"
               and g.get("bettingScope") == "FULL_TIME"]
        if not hda:
            return {}
        hda.sort(key=lambda g: (not g.get("hasLiveBettingOffers"),
                                g.get("bookmakerId") or 0))
        best = hda[0]
        if not best.get("hasLiveBettingOffers"):
            return {}
        vals = [it.get("value") for it in (best.get("odds") or [])]
        if len(vals) != 3:
            return {}
        try:
            home, away, draw = (float(v) for v in vals)
        except (TypeError, ValueError):
            return {}
        if min(home, away, draw) <= 1.0:
            return {}
        return {"book": best.get("bookmakerId"),
                "home": home, "away": away, "draw": draw,
                "overround": (1 / home + 1 / away + 1 / draw) - 1.0}


def parse_sui(body: str) -> Dict[str, Any]:
    """Incidents feed -> the only reliable clock (match minute)."""
    periods = re.findall(r"AC\u00f7([^\u00ac~]+)", body or "")
    mins = [int(m) for m in re.findall(r"IB\u00f7(\d+)", body or "")]
    return {"period": periods[-1] if periods else "",
            "max_minute": max(mins) if mins else 0}


def parse_stats(body: str) -> Dict[str, Any]:
    """Statistics feed -> the signalling numbers, per half. SE marks the half."""
    out: Dict[str, Any] = {}
    cur = ""
    for blk in (body or "").split("\u00ac~"):
        kv = dict(f.split("\u00f7", 1) for f in blk.split("\u00ac")
                  if "\u00f7" in f)
        if "SE" in kv:
            cur = kv["SE"]
            continue
        if "SG" not in kv:
            continue
        key = kv["SG"]
        pair = (kv.get("SH", "0"), kv.get("SI", "0"))
        if cur in ("", "Match"):
            out[key] = pair
        elif cur == "2nd Half":
            out["2H|" + key] = pair
    return out


def _f(v: Any) -> float:
    try:
        return float(str(v).strip().rstrip("%"))
    except Exception:                                         # noqa: BLE001
        return 0.0


# --------------------------------------------------------------------------- #
# Evaluate one match                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class State:
    mid: str
    home: str = ""
    away: str = ""
    league: str = ""
    score: Tuple[int, int] = (0, 0)
    minute: float = 0.0
    second_half: bool = False
    xg: Tuple[float, float] = (0.0, 0.0)
    sot: Tuple[float, float] = (0.0, 0.0)
    odds: Dict[str, Any] = field(default_factory=dict)
    model: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    market: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    dominant: str = ""
    xg_share: float = 0.5
    sot_edge: int = 0
    price: float = 0.0
    dnb_price: float = 0.0
    edge_vig: float = 0.0
    firing: bool = False
    skip: str = ""


def evaluate(fs: FS, fx: Dict[str, str]) -> State:
    mid = fx.get("AA", "")
    st = State(mid=mid, home=fx.get("AE", ""), away=fx.get("AF", ""),
               league=fx.get("league", ""))
    try:
        st.score = (int(fx.get("AG") or 0), int(fx.get("AH") or 0))
        sui = parse_sui(fs.feed(f"df_sui_1_{mid}"))
        raw = parse_stats(fs.feed(f"df_st_1_{mid}"))
        odds = fs.odds(mid)

        h2shots = raw.get("2H|Total shots", ("0", "0"))
        h2xg = raw.get("2H|Expected goals (xG)", ("0", "0"))
        st.second_half = (_f(h2shots[0]) + _f(h2shots[1]) > 0
                          or _f(h2xg[0]) + _f(h2xg[1]) > 0)
        est = float(sui["max_minute"])
        if st.second_half:
            est = max(est, 45.0)
        st.minute = est

        st.xg = (_f(raw.get("Expected goals (xG)", (0, 0))[0]),
                 _f(raw.get("Expected goals (xG)", (0, 0))[1]))
        st.sot = (_f(raw.get("Shots on target", (0, 0))[0]),
                  _f(raw.get("Shots on target", (0, 0))[1]))

        if not odds:
            st.skip = "no live odds"
            return st
        if (st.xg[0] + st.xg[1]) < MIN_XG:
            st.skip = "no stats yet"
            return st
        if est < MIN_MINUTE:
            st.skip = "too early"
            return st
        if est > MAX_MINUTE:
            st.skip = "too late"
            return st

        st.odds = odds
        xh, xa = st.xg
        st.xg_share = xh / (xh + xa) if (xh + xa) else 0.5
        st.sot_edge = int(st.sot[0] - st.sot[1])
        dom_h = st.xg_share >= XG_SHARE_MIN and st.sot_edge >= SOT_EDGE_MIN
        dom_a = (1 - st.xg_share) >= XG_SHARE_MIN and -st.sot_edge >= SOT_EDGE_MIN
        st.dominant = "home" if dom_h else ("away" if dom_a else "")

        min_left = max(0.0, 90.0 - est)
        obs = max(est, 5.0)
        rh = (xh + PRIOR_XG90 * PRIOR_MIN / 90.0) / (obs + PRIOR_MIN)
        ra = (xa + PRIOR_XG90 * PRIOR_MIN / 90.0) / (obs + PRIOR_MIN)
        st.model = probs_1x2(st.score[0], st.score[1],
                             rh * min_left, ra * min_left)
        ih, idr, ia = 1 / odds["home"], 1 / odds["draw"], 1 / odds["away"]
        s = ih + idr + ia
        st.market = (ih / s, idr / s, ia / s)

        if st.dominant:
            idx = 0 if st.dominant == "home" else 1
            price = odds["home"] if idx == 0 else odds["away"]
            p_model = st.model[0] if idx == 0 else st.model[1]
            st.price = price
            st.edge_vig = (p_model * price - 1.0) + odds["overround"]
            # draw-no-bet is the same side with the draw removed
            p_side = st.market[0] if idx == 0 else st.market[1]
            p_other = st.market[1] if idx == 0 else st.market[0]
            if p_side + p_other > 0:
                st.dnb_price = 1.0 / ((p_side + p_other) * (1 + odds["overround"]))
            behind = (st.score[0] <= st.score[1]) if idx == 0 else \
                     (st.score[1] <= st.score[0])
            st.firing = bool(
                behind and st.second_half and min_left >= MIN_LEFT_MIN
                and price <= MAX_ODDS and p_model >= MIN_MODEL_P
                and st.edge_vig >= MIN_EDGE_VIG)
    except Exception as e:                                    # noqa: BLE001
        st.skip = f"{type(e).__name__}: {e}"
    return st


# --------------------------------------------------------------------------- #
# Telegram                                                                     #
# --------------------------------------------------------------------------- #

def esc(x: Any) -> str:
    return html.escape(str(x), quote=False)


class Telegram:
    BASE = "https://api.telegram.org/bot{}/{}"

    def __init__(self, token: str, timeout: int = 25) -> None:
        self.token = token
        self.url = self.BASE.format(token, "sendMessage")
        self.edit_url = self.BASE.format(token, "editMessageText")
        self.timeout = timeout
        self.offset = 0

    def send(self, cid: int, text: str) -> Optional[int]:
        try:
            r = requests.post(self.url, timeout=self.timeout,
                              data={"chat_id": cid, "text": text,
                                    "parse_mode": "HTML",
                                    "disable_web_page_preview": True})
            j = r.json() if r.status_code == 200 else {}
            return (j.get("result") or {}).get("message_id")
        except Exception as e:                                # noqa: BLE001
            log.warning("send failed: %s", e)
            return None

    def edit(self, cid: int, msg_id: int, text: str) -> bool:
        """Edit a message.

        Telegram answers HTTP 200 with {"ok": false} when it could not edit --
        "message is not modified" (fine, already showing this) or "message to
        edit not found" / too old (NOT fine: we must re-send). Treating every
        200 as success is how the dashboard silently froze.
        """
        try:
            r = requests.post(self.edit_url, timeout=self.timeout,
                              data={"chat_id": cid, "message_id": msg_id,
                                    "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True})
            if r.status_code != 200:
                return False
            j = r.json()
            if j.get("ok"):
                return True
            desc = str(j.get("description") or "").lower()
            if "not modified" in desc:
                return True          # already displaying exactly this text
            log.debug("edit rejected: %s", j.get("description"))
            return False             # stale or gone -> caller re-sends
        except Exception as e:                                # noqa: BLE001
            log.debug("edit failed: %s", e)
            return False

    def updates(self, timeout: int = 20) -> List[dict]:
        try:
            r = requests.get(self.BASE.format(self.token, "getUpdates"),
                             timeout=timeout + 10,
                             params={"offset": self.offset, "timeout": timeout})
            data = r.json()
        except Exception as e:                                # noqa: BLE001
            log.debug("poll: %s", e)
            return []
        out = data.get("result") or []
        for u in out:
            self.offset = max(self.offset, (u.get("update_id") or 0) + 1)
        return out


# --------------------------------------------------------------------------- #
# Messages                                                                     #
# --------------------------------------------------------------------------- #

BAR_LEN = 14


def _bar(done: int, total: int) -> str:
    if total <= 0:
        return "-" * BAR_LEN
    filled = int(BAR_LEN * done / total)
    return "\u2588" * filled + "\u2591" * (BAR_LEN - filled)


def dash(now_team: str, done: int, total: int, sweeps: int, uptime: float,
         judged: int, firing: int, alerts: int,
         closest: Optional[State], phase: str) -> str:
    h = int(uptime // 3600)
    m = int((uptime % 3600) // 60)
    L = ["\U0001F534 <b>LIVE SCANNER</b>  \u2022  " + esc(phase),
         f"sweep {sweeps}  \u00b7  uptime {h}h {m}m",
         "",
         f"<code>{_bar(done, total)}</code> {done}/{total}",
         "\u25b6 " + esc(now_team),
         ""]
    if judged or firing or alerts:
        L.append(f"judged <b>{judged}</b>  \u00b7  firing <b>{firing}</b>"
                 f"  \u00b7  alerts <b>{alerts}</b>")
    if closest is not None:
        L.append(f"closest: {esc(closest.home)} vs {esc(closest.away)}  "
                 f"{closest.edge_vig:+.1%}")
    else:
        L.append("nothing close yet")
    L.append("")
    if phase == "STOPPED":
        L.append("<i>paused \u2014 send /start to resume</i>")
    else:
        L.append("<i>quiet = no edge found, still scanning</i>")
    return "\n".join(L)


def bet_message(s: State) -> str:
    side = s.home if s.dominant == "home" else s.away
    p_model = s.model[0] if s.dominant == "home" else s.model[1]
    p_market = s.market[0] if s.dominant == "home" else s.market[1]
    L = ["\U0001F6A8 <b>BET NOW</b>",
         f"<b>{esc(s.home)} vs {esc(s.away)}</b>",
         f"{esc(s.league)}",
         "",
         f"minute &gt;= {s.minute:.0f}'  \u00b7  score "
         f"{s.score[0]}-{s.score[1]}",
         "",
         f"\U0001F449 <b>BET: {esc(side)} to win @ {s.price:.2f}</b>"]
    if s.dnb_price > 1.0:
        L.append(f"   safer: {esc(side)} Draw No Bet ~{s.dnb_price:.2f}")
    L += ["",
          f"why: dominating but not ahead",
          f"xG {s.xg_share:.0%}  \u00b7  shots on target {s.sot_edge:+d}",
          f"model {p_model:.0%}  vs  market {p_market:.0%}",
          f"overround {s.odds['overround']:.1%}  \u00b7  "
          f"edge {s.edge_vig:+.1%}",
          "",
          "<i>Paper mode \u2014 not proven. Clock is a lower bound and live "
          "data lags a few minutes, so CHECK THE PRICE before betting.</i>"]
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Bot                                                                          #
# --------------------------------------------------------------------------- #

class Scanner:
    def __init__(self, token: str, geo: str = "NG", geo_sub: str = "NGLA",
                 limit: int = 200) -> None:
        self.tg = Telegram(token)
        self.fs = FS(geo=geo, geo_sub=geo_sub)
        self.limit = limit
        self.cid: Optional[int] = None
        self.msg_id: Optional[int] = None
        self.recent: Dict[str, float] = {}
        self._last_edit = 0.0
        self.stop = threading.Event()
        self.paused = False
        self.st = {"sweeps": 0, "alerts": 0, "live": 0,
                   "judged": 0, "firing": 0, "error": "", "started": 0.0}

    # -- persistence -------------------------------------------------------- #

    def load(self) -> bool:
        try:
            d = json.load(open(CHAT_FILE, encoding="utf8"))
            self.cid = d.get("chat_id")
            self.msg_id = d.get("msg_id")
            return bool(self.cid)
        except Exception:                                     # noqa: BLE001
            return False

    def save(self) -> None:
        try:
            json.dump({"chat_id": self.cid, "msg_id": self.msg_id},
                      open(CHAT_FILE, "w", encoding="utf8"))
        except OSError as e:                                  # noqa: BLE001
            log.warning("could not save chat: %s", e)

    # -- dashboard ---------------------------------------------------------- #

    def push_dash(self, text: str, force: bool = False) -> None:
        if self.cid is None:
            return
        now = time.time()
        if not force and now - self._last_edit < DASH_MIN_GAP:
            return
        self._last_edit = now
        if self.msg_id:
            if self.tg.edit(self.cid, self.msg_id, text):
                return
            self.msg_id = None
        mid = self.tg.send(self.cid, text)
        if mid:
            self.msg_id = mid
            self.save()

    def say(self, text: str) -> None:
        """One-off message. Deliberately does NOT touch the dashboard id."""
        if self.cid is not None:
            if not self.tg.send(self.cid, text):
                log.warning("could not send message to %s", self.cid)
        else:
            print(text)

    # -- first contact ------------------------------------------------------- #

    def wait_for_chat(self) -> None:
        """Block until the user sends anything at all, then remember them."""
        print("waiting for your first message in telegram "
              "(send the bot anything)...", flush=True)
        while not self.stop.is_set() and self.cid is None:
            for u in self.tg.updates(10):
                msg = u.get("message") or {}
                chat = msg.get("chat") or {}
                if chat.get("id"):
                    self.cid = chat["id"]
                    self.save()
                    self.say("\U0001F7E2 <b>connected</b> \u2014 scanning now.\n\n"
                             "This message updates itself as I work.\n"
                             "Only commands: <code>/stop</code> and "
                             "<code>/start</code>")
                    return
            time.sleep(1)

    # -- the only two commands --------------------------------------------- #

    def _on_command(self, text: str) -> None:
        cmd = ""
        if text:
            cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/stop":
            if self.paused:
                self.say("already stopped \u2014 /start to resume")
                return
            self.paused = True
            uptime = time.time() - self.st["started"] if self.st["started"] else 0
            self.push_dash(dash("stopped", 0, 0, self.st["sweeps"], uptime,
                                self.st["judged"], self.st["firing"],
                                self.st["alerts"], None, "STOPPED"),
                           force=True)
            self.say("\u23F8 <b>stopped</b> \u2014 no more scanning.\n"
                     "<code>/start</code> to resume.")
        elif cmd in ("/start", "/resume", "/go"):
            if not self.paused:
                self.say("already scanning")
                return
            self.paused = False
            # drop the old dashboard id so the next push sends a FRESH
            # message at the bottom of the chat -- an edited message stays
            # where it was, and after a long pause it is scrolled far away
            self.msg_id = None
            self.say("\u25B6 <b>scanning again</b>")
        elif cmd:
            self.say("Only two commands: <code>/stop</code> and "
                     "<code>/start</code>")

    def _poll(self) -> None:
        """Background listener for /stop and /start."""
        while not self.stop.is_set():
            try:
                for u in self.tg.updates(5):
                    msg = u.get("message") or {}
                    chat = msg.get("chat") or {}
                    cid = chat.get("id")
                    if not cid:
                        continue
                    if self.cid is None:
                        self.cid = cid
                        self.save()
                        self.say("\U0001F7E2 <b>connected</b> \u2014 scanning "
                                 "now.\n\nOnly commands: <code>/stop</code> "
                                 "and <code>/start</code>")
                        continue
                    self._on_command((msg.get("text") or "").strip())
            except Exception as e:                            # noqa: BLE001
                log.debug("poll: %s", e)
            time.sleep(1)

    # -- the loop ------------------------------------------------------------ #

    def loop(self) -> None:
        if not self.st["started"]:
            self.st["started"] = time.time()
        while not self.stop.is_set():
            if self.paused:
                self.stop.wait(2)
                continue
            if self.cid is None:
                self.stop.wait(2)
                continue
            try:
                self.sweep_once()
            except Exception as e:                            # noqa: BLE001
                self.st["error"] = f"{type(e).__name__}: {e}"
                log.warning("sweep failed: %s", e)
                try:
                    self.push_dash(dash(self.st["error"], 0, 0,
                                        self.st["sweeps"],
                                        time.time() - self.st["started"],
                                        0, 0, self.st["alerts"],
                                        None, "error \u2014 retrying"),
                                   force=True)
                except Exception:                             # noqa: BLE001
                    pass
            self.stop.wait(SWEEP_GAP)

    def sweep_once(self) -> None:
        uptime = time.time() - self.st["started"]
        self.st["sweeps"] += 1
        self.st["error"] = ""

        live = self.fs.live()
        total = min(len(live), self.limit)
        self.st["live"] = len(live)

        judged: List[State] = []
        firing: List[State] = []

        for i, fx in enumerate(live[:self.limit], 1):
            if self.stop.is_set():
                return
            s = evaluate(self.fs, fx)
            if not s.skip:
                judged.append(s)
                if s.firing:
                    firing.append(s)
            self.st["judged"] = len(judged)
            self.st["firing"] = len(firing)

            near = sorted((x for x in judged if x.price),
                          key=lambda x: -x.edge_vig)
            self.push_dash(dash(f"{s.home} vs {s.away}", i, total,
                                self.st["sweeps"], uptime, len(judged),
                                len(firing), self.st["alerts"],
                                near[0] if near else None, "scanning"))

        # fire the alerts
        now = time.time()
        for s in sorted(firing, key=lambda x: -x.edge_vig):
            key = f"{s.mid}:{s.dominant}"
            if now - self.recent.get(key, 0.0) < ALERT_COOLDOWN:
                continue
            self.recent[key] = now
            self._log(s)
            self.st["alerts"] += 1
            self.say(bet_message(s))

        near = sorted((x for x in judged if x.price),
                      key=lambda x: -x.edge_vig)
        self.push_dash(dash("idle \u2014 waiting for next sweep", total, total,
                            self.st["sweeps"], uptime, len(judged),
                            len(firing), self.st["alerts"],
                            near[0] if near else None, "watching"),
                       force=True)

    def _log(self, s: State) -> None:
        try:
            with open(LEDGER, "a", encoding="utf8") as fh:
                fh.write(json.dumps({
                    "ts": time.time(), "mid": s.mid, "home": s.home,
                    "away": s.away, "dominant": s.dominant, "odds": s.price,
                    "edge": s.edge_vig, "minute": s.minute,
                    "score": list(s.score)}, ensure_ascii=False) + "\n")
        except OSError as e:                                  # noqa: BLE001
            log.warning("ledger: %s", e)

    def run(self) -> None:
        threading.Thread(target=self._poll, daemon=True,
                         name="cmds").start()
        if not self.load() or self.cid is None:
            self.wait_for_chat()
        if self.cid is None:
            return
        print(f"chat {self.cid} \u2014 scanning  "
              f"(/stop to pause, /start to resume)", flush=True)
        self.loop()


def settle(fs: FS) -> str:
    if not os.path.exists(LEDGER):
        return "No alerts logged yet."
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf8") if l.strip()]
    finals: Dict[str, Tuple[int, int]] = {}
    for off in (-1, 0):
        for r in fs.fixtures(off):
            if r.get("AB") == "3" and r.get("AA"):
                finals[r["AA"]] = (int(r.get("AG") or 0), int(r.get("AH") or 0))
    seen: set = set()
    staked = wins = 0
    pnl = 0.0
    for r in rows:
        mid = r.get("mid")
        o = float(r.get("odds") or 0)
        if not mid or mid in seen or o <= 1.0:
            continue
        seen.add(mid)
        f = finals.get(mid)
        if not f:
            continue
        staked += 1
        won = (f[0] > f[1]) if r.get("dominant") == "home" else (f[1] > f[0])
        if won:
            wins += 1
            pnl += o - 1.0
        else:
            pnl -= 1.0
    if not staked:
        return f"{len(seen)} alert(s) logged, none finished yet."
    out = [f"settled {staked}  \u00b7  won {wins}  \u00b7  "
           f"hit rate {wins / staked:.0%}",
           f"flat 1u ROI: {pnl:+.2f}u  ({pnl / staked:+.1%} per bet)"]
    if staked < 100:
        out.append(f"NOT ENOUGH DATA ({staked}/100) \u2014 keep paper mode on.")
    return "\n".join(out)


def doctor() -> int:
    issues = 0

    def say(tag: str, good: bool, detail: str = "") -> None:
        nonlocal issues
        if not good:
            issues += 1
        print(f"  [{'OK  ' if good else 'FAIL'}] {tag:<20} {detail}")

    v = sys.version_info
    print("python")
    say("version >= 3.9", v >= (3, 9), f"{v.major}.{v.minor}.{v.micro}")
    print("\npackages")
    try:
        import requests as _r                                 # noqa: F401
        say("requests", True, _r.__version__)
    except Exception as e:                                    # noqa: BLE001
        say("requests", False, "missing \u2014 pip install requests")
    print("\nnetwork")
    fs = FS()
    try:
        fx = fs.fixtures(0)
        say("flashscore", len(fx) > 0, f"{len(fx)} matches today")
        live = [r for r in fx if r.get("AB") == "2"]
        say("in-play", True, f"{len(live)} live now")
        if live:
            say("live odds", bool(fs.odds(live[0]["AA"])),
                f"{live[0].get('AE')} vs {live[0].get('AF')}")
    except Exception as e:                                    # noqa: BLE001
        say("flashscore", False, f"{type(e).__name__}: {e}")
    print("\ntelegram")
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    say("token", bool(tok), "set" if tok else "NOT SET")
    print()
    print("ready \u2014 run:  python live_bot.py" if not issues
          else f"{issues} problem(s)")
    return 1 if issues else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="24/7 live scanner")
    ap.add_argument("--once", action="store_true",
                    help="one sweep, print to terminal (no telegram)")
    ap.add_argument("--settle", action="store_true",
                    help="score logged alerts against final results")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--geo", default="NG")
    ap.add_argument("--geo-sub", default="NGLA")
    ap.add_argument("--token", default=os.getenv("TELEGRAM_BOT_TOKEN"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.doctor:
        return doctor()
    if args.settle:
        print(settle(FS(geo=args.geo, geo_sub=args.geo_sub)))
        return 0
    if args.once:
        fs = FS(geo=args.geo, geo_sub=args.geo_sub)
        live = fs.live()
        rows = [evaluate(fs, f) for f in live[:args.limit]]
        judged = [r for r in rows if not r.skip]
        print(f"{len(live)} in-play, {len(judged)} judgeable")
        for s in sorted((x for x in judged if x.firing),
                        key=lambda x: -x.edge_vig):
            print()
            print(bet_message(s))
        near = sorted((x for x in judged if x.price),
                      key=lambda x: -x.edge_vig)[:5]
        for s in near:
            print(f"  {s.home[:20]:<21} vs {s.away[:20]:<21} "
                  f"edge {s.edge_vig:+6.1%}  min>={s.minute:>3.0f}  "
                  f"xG {s.xg_share:.0%}")
        return 0

    if not args.token:
        print("set TELEGRAM_BOT_TOKEN or pass --token", file=sys.stderr)
        return 2

    sc = Scanner(args.token, geo=args.geo, geo_sub=args.geo_sub,
                 limit=args.limit)

    def bye(*_):
        print("\nstopping")
        sc.stop.set()
    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)
    sc.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
