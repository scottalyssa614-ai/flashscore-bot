#!/usr/bin/env python3
"""
Football Prediction Telegram Bot - Football-Data.org Version
Complete bot with all functionality in one file - NO SCHEDULER VERSION
"""

import asyncio
import logging
import signal
import sys
import os
import json
import threading
import aiohttp
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import statistics

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration class holding all bot settings"""

    # Required fields (no defaults)
    football_data_key: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Football-Data.org configuration
    football_data_base_url: str = "https://api.football-data.org/v4"

    # Prediction settings
    max_predictions: int = 5
    min_confidence_threshold: float = 0.6

    # Available competitions from Football-Data.org
    available_competitions: List[str] = None

    # Rate limiting
    api_rate_limit_delay: float = 1.0  # Seconds between API calls
    max_retries: int = 3

    def __init__(self):
        # Load from environment variables
        self.football_data_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
        self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Available competitions
        self.available_competitions = ['WC', 'CL', 'BL1', 'DED', 'BSA', 'PD', 'FL1', 'ELC', 'PPL', 'EC', 'SA', 'PL']

        # Optional overrides from environment
        self.max_predictions = int(os.getenv("MAX_PREDICTIONS", str(self.max_predictions)))
        self.min_confidence_threshold = float(os.getenv("MIN_CONFIDENCE", str(self.min_confidence_threshold)))

        # Validate required settings
        self._validate()

    def _validate(self):
        """Validate required configuration"""
        if not self.football_data_key:
            raise ValueError("FOOTBALL_DATA_API_KEY environment variable is required")
        if not self.telegram_token:
            raise ValueError("TELEGRAM_TOKEN environment variable is required")
        if not self.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID environment variable is required")

    @property
    def api_headers(self) -> dict:
        """Get Football-Data.org headers"""
        return {
            "X-Auth-Token": self.football_data_key
        }

class RateLimiter:
    """Rate limiter to ensure API calls don't exceed limits"""

    def __init__(self, max_calls_per_minute: int = 10):
        self.max_calls_per_minute = max_calls_per_minute
        self.calls_made = []
        self.lock = threading.Lock()

    def wait_if_needed(self):
        """Wait if necessary to respect rate limits"""
        with self.lock:
            now = time.time()

            # Remove calls older than 1 minute
            self.calls_made = [call_time for call_time in self.calls_made if now - call_time < 60]

            # If we're at the limit, wait until we can make another call
            if len(self.calls_made) >= self.max_calls_per_minute:
                oldest_call = min(self.calls_made)
                wait_time = 60 - (now - oldest_call) + 1  # Add 1 second buffer

                if wait_time > 0:
                    logger.info(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
                    print(f"⏳ Rate limit protection: Waiting {wait_time:.1f}s to respect 10 calls/minute limit")
                    time.sleep(wait_time)

            # Record this call
            self.calls_made.append(time.time())

            # Also add a small buffer between calls
            time.sleep(6.5)  # 6.5 seconds = ~9 calls per minute (safe margin)

class FootballDataClient:
    """Client for Football-Data.org API"""

    def __init__(self, config):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limiter = RateLimiter(max_calls_per_minute=10)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers=self.config.api_headers,
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_request(self, endpoint: str, params: Dict[str, Any] = None) -> Optional[Dict]:
        """Make a request to Football-Data.org with comprehensive rate limiting"""
        if not self.session:
            raise RuntimeError("FootballDataClient must be used as async context manager")

        url = f"{self.config.football_data_base_url}/{endpoint}"

        for attempt in range(self.config.max_retries):
            try:
                # CRITICAL: Apply rate limiting before every API call
                logger.debug(f"Applying rate limit protection before {endpoint} (attempt {attempt + 1})")
                self.rate_limiter.wait_if_needed()

                logger.info(f"Making API call to {endpoint} (attempt {attempt + 1})")

                async with self.session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.debug(f"✅ API call successful: {endpoint}")
                        return data
                    elif response.status == 429:  # Rate limited
                        wait_time = 2 ** attempt * 10  # Longer wait for rate limits
                        logger.warning(f"⚠️ Rate limited by API! Waiting {wait_time} seconds...")
                        print(f"🚨 API Rate limited! Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                    elif response.status == 403:
                        logger.error("❌ API key invalid or suspended!")
                        print("🚨 CRITICAL: API key issue - check your FOOTBALL_DATA_API_KEY!")
                        return None
                    else:
                        logger.error(f"❌ API request failed with status {response.status}")
                        error_text = await response.text()
                        logger.error(f"Error details: {error_text}")

            except Exception as e:
                logger.error(f"Error making API request: {e}")
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        logger.error(f"❌ All retry attempts failed for {endpoint}")
        return None

    async def get_daily_matches(self, date: str = None) -> List[Dict]:
        """Get matches for a specific date (default: today)"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"🔍 Fetching matches for {date} with rate limit protection...")
        print(f"📅 Fetching today's matches from Football-Data.org...")

        # Join competitions with comma
        competitions = ','.join(self.config.available_competitions)

        params = {
            "dateFrom": date,
            "dateTo": date,
            "status": "SCHEDULED",
            "competitions": competitions
        }

        response = await self._make_request("matches", params)
        if response and "matches" in response:
            matches = response["matches"]
            logger.info(f"✅ Found {len(matches)} matches for {date}")
            print(f"⚽ Found {len(matches)} scheduled matches today")
            return matches

        logger.warning(f"⚠️ No matches found for {date}")
        print(f"🤷 No matches found for today")
        return []

    async def get_team_matches(self, team_id: int, limit: int = 10) -> List[Dict]:
        """Get recent matches for a team with rate limiting"""
        logger.debug(f"📊 Fetching recent matches for team {team_id} (rate limited)")

        # Get matches from last 30 days
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        date_to = datetime.now().strftime("%Y-%m-%d")

        params = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "status": "FINISHED"
        }

        response = await self._make_request(f"teams/{team_id}/matches", params)
        if response and "matches" in response:
            matches = response["matches"][:limit]
            logger.debug(f"✅ Got {len(matches)} recent matches for team {team_id}")
            return matches

        logger.debug(f"⚠️ No recent matches found for team {team_id}")
        return []

    async def get_head_to_head(self, team1_id: int, team2_id: int) -> List[Dict]:
        """Get head-to-head matches between two teams with rate limiting"""
        logger.debug(f"🔄 Fetching H2H for teams {team1_id} vs {team2_id} (rate limited)")

        # Get H2H from team1 perspective
        params = {
            "limit": 10,
            "status": "FINISHED"
        }

        response = await self._make_request(f"teams/{team1_id}/matches", params)
        if response and "matches" in response:
            # Filter for matches where both teams played against each other
            h2h_matches = []
            for match in response["matches"]:
                home_team_id = match["homeTeam"]["id"]
                away_team_id = match["awayTeam"]["id"]

                if (home_team_id == team1_id and away_team_id == team2_id) or \
                   (home_team_id == team2_id and away_team_id == team1_id):
                    h2h_matches.append(match)

            logger.debug(f"✅ Found {len(h2h_matches)} H2H matches")
            return h2h_matches[:10]  # Last 10 H2H matches

        logger.debug("⚠️ No H2H matches found")
        return []

class FootballDataProcessor:
    """Process and analyze football data from Football-Data.org"""

    def __init__(self, api_client: FootballDataClient):
        self.api_client = api_client

    async def get_comprehensive_match_data(self, match: Dict) -> Dict:
        """Get comprehensive data for a match including teams and recent form"""
        home_team = match["homeTeam"]
        away_team = match["awayTeam"]

        logger.info(f"🔍 Processing match: {home_team['name']} vs {away_team['name']} (with rate limiting)")
        print(f"📊 Analyzing: {home_team['name']} vs {away_team['name']}")

        # Gather team data with controlled rate limiting
        # We need to be very careful here as this is where most API calls happen
        try:
            logger.info("📊 Fetching home team recent form...")
            home_form = await self.api_client.get_team_matches(home_team["id"], 5)

            logger.info("📊 Fetching away team recent form...")
            away_form = await self.api_client.get_team_matches(away_team["id"], 5)

            logger.info("🔄 Fetching head-to-head history...")
            h2h = await self.api_client.get_head_to_head(home_team["id"], away_team["id"])

            logger.info(f"✅ Data collected: {len(home_form)} home, {len(away_form)} away, {len(h2h)} H2H matches")

            return {
                "match": match,
                "home_form": home_form,
                "away_form": away_form,
                "head_to_head": h2h
            }

        except Exception as e:
            logger.error(f"❌ Error processing match data: {e}")
            return {
                "match": match,
                "home_form": [],
                "away_form": [],
                "head_to_head": []
            }

    async def get_daily_matches_data(self, date: str = None) -> List[Dict]:
        """Get comprehensive data for all matches on a given date with strict rate limiting"""
        print("🚀 Starting comprehensive match analysis with rate limit protection...")

        matches = await self.api_client.get_daily_matches(date)

        if not matches:
            print("❌ No matches found for today")
            return []

        print(f"⚽ Found {len(matches)} matches. Starting detailed analysis...")
        print(f"⏳ This will take approximately {len(matches) * 3 * 7}+ seconds due to rate limiting (10 calls/min max)")

        # Process matches ONE BY ONE (not concurrently) to respect rate limits
        # Each match needs 3 API calls: home_form + away_form + h2h
        # With 6.5s between calls, we're safely under 10 calls/minute

        valid_matches = []

        for i, match in enumerate(matches, 1):
            try:
                home_team = match["homeTeam"]["name"]
                away_team = match["awayTeam"]["name"]

                print(f"🔍 [{i}/{len(matches)}] Analyzing: {home_team} vs {away_team}")
                logger.info(f"Processing match {i}/{len(matches)}: {home_team} vs {away_team}")

                match_data = await self.get_comprehensive_match_data(match)

                if match_data and match_data["match"]:
                    valid_matches.append(match_data)
                    print(f"✅ [{i}/{len(matches)}] Analysis complete")
                else:
                    print(f"⚠️ [{i}/{len(matches)}] Insufficient data")

            except Exception as e:
                logger.error(f"❌ Error processing match {i}: {e}")
                print(f"❌ [{i}/{len(matches)}] Error occurred")
                continue

        logger.info(f"✅ Successfully processed {len(valid_matches)} out of {len(matches)} matches")
        print(f"🎯 Completed analysis: {len(valid_matches)}/{len(matches)} matches have sufficient data")

        return valid_matches

class PredictionEngine:
    """Professional-grade engine for analyzing matches and generating predictions using Football-Data.org"""

    def __init__(self, config):
        self.config = config

    def analyze_team_form(self, form_matches: List[Dict], team_id: int) -> Dict:
        """Deep analysis of team form using Football-Data.org match data"""
        if not form_matches:
            return {
                "form_string": "-----",
                "win_rate": 0.0,
                "goals_per_game": 0.0,
                "goals_conceded_per_game": 0.0,
                "clean_sheet_rate": 0.0,
                "btts_rate": 0.0,
                "over_2_5_rate": 0.0,
                "strength_rating": 50.0
            }

        wins = losses = draws = 0
        goals_scored = goals_conceded = 0
        clean_sheets = btts_count = over_2_5_count = 0
        form_string = ""

        for match in form_matches:
            if match["status"] != "FINISHED":
                continue

            home_team = match["homeTeam"]
            away_team = match["awayTeam"]

            # Get scores
            home_score = match["score"]["fullTime"]["home"] or 0
            away_score = match["score"]["fullTime"]["away"] or 0

            # Determine if team was home or away
            is_home = home_team["id"] == team_id
            team_goals = home_score if is_home else away_score
            opponent_goals = away_score if is_home else home_score

            goals_scored += team_goals
            goals_conceded += opponent_goals

            # Form string and result tracking
            if team_goals > opponent_goals:
                wins += 1
                form_string = "W" + form_string
            elif team_goals == opponent_goals:
                draws += 1
                form_string = "D" + form_string
            else:
                losses += 1
                form_string = "L" + form_string

            # Clean sheets
            if opponent_goals == 0:
                clean_sheets += 1

            # BTTS (Both Teams to Score)
            if home_score > 0 and away_score > 0:
                btts_count += 1

            # Over 2.5 goals
            if (home_score + away_score) > 2.5:
                over_2_5_count += 1

        total_matches = len([m for m in form_matches if m["status"] == "FINISHED"])
        if total_matches == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.0,
                "goals_per_game": 0.0,
                "goals_conceded_per_game": 0.0,
                "clean_sheet_rate": 0.0,
                "btts_rate": 0.0,
                "over_2_5_rate": 0.0,
                "strength_rating": 50.0
            }

        # Calculate strength rating (0-100 scale)
        win_points = wins * 3 + draws * 1
        max_points = total_matches * 3
        strength_rating = (win_points / max_points * 100) if max_points > 0 else 50.0

        return {
            "form_string": form_string[:5],  # Last 5 matches
            "win_rate": wins / total_matches,
            "goals_per_game": goals_scored / total_matches,
            "goals_conceded_per_game": goals_conceded / total_matches,
            "clean_sheet_rate": clean_sheets / total_matches,
            "btts_rate": btts_count / total_matches,
            "over_2_5_rate": over_2_5_count / total_matches,
            "strength_rating": strength_rating,
            "total_matches": total_matches
        }

    def analyze_head_to_head(self, h2h_matches: List[Dict], home_team_id: int, away_team_id: int) -> Dict:
        """Comprehensive H2H analysis using Football-Data.org format"""
        if not h2h_matches:
            return {
                "home_wins": 0,
                "away_wins": 0,
                "draws": 0,
                "home_win_rate": 0.33,
                "away_win_rate": 0.33,
                "avg_goals": 2.5,
                "btts_rate": 0.5,
                "over_2_5_rate": 0.5,
                "dominance_factor": 0.0
            }

        home_wins = away_wins = draws = 0
        total_goals = btts_count = over_2_5_count = 0

        for match in h2h_matches:
            if match["status"] != "FINISHED":
                continue

            home_score = match["score"]["fullTime"]["home"] or 0
            away_score = match["score"]["fullTime"]["away"] or 0
            match_home_id = match["homeTeam"]["id"]

            total_goals += (home_score + away_score)

            # BTTS and Over 2.5 tracking
            if home_score > 0 and away_score > 0:
                btts_count += 1
            if (home_score + away_score) > 2.5:
                over_2_5_count += 1

            # Result tracking (adjusted for which team was actually home)
            if match_home_id == home_team_id:
                if home_score > away_score:
                    home_wins += 1
                elif away_score > home_score:
                    away_wins += 1
                else:
                    draws += 1
            else:  # away_team was home in H2H match
                if away_score > home_score:
                    away_wins += 1
                elif home_score > away_score:
                    home_wins += 1
                else:
                    draws += 1

        total_matches = len([m for m in h2h_matches if m["status"] == "FINISHED"])
        if total_matches == 0:
            return {
                "home_wins": 0,
                "away_wins": 0,
                "draws": 0,
                "home_win_rate": 0.33,
                "away_win_rate": 0.33,
                "avg_goals": 2.5,
                "btts_rate": 0.5,
                "over_2_5_rate": 0.5,
                "dominance_factor": 0.0
            }

        avg_goals = total_goals / total_matches

        # Dominance factor: how much one team dominates the fixture
        dominance_factor = abs(home_wins - away_wins) / total_matches

        return {
            "home_wins": home_wins,
            "away_wins": away_wins,
            "draws": draws,
            "home_win_rate": home_wins / total_matches,
            "away_win_rate": away_wins / total_matches,
            "avg_goals": avg_goals,
            "btts_rate": btts_count / total_matches,
            "over_2_5_rate": over_2_5_count / total_matches,
            "dominance_factor": dominance_factor,
            "total_matches": total_matches
        }

    def predict_correct_score(self, home_analysis: Dict, away_analysis: Dict, h2h_analysis: Dict) -> Dict:
        """Predict most likely correct score using statistical analysis"""

        # Calculate expected goals using multiple factors
        home_attack = home_analysis["goals_per_game"]
        home_defense = 2.0 - home_analysis["goals_conceded_per_game"]  # Defensive strength
        away_attack = away_analysis["goals_per_game"]
        away_defense = 2.0 - away_analysis["goals_conceded_per_game"]

        # Home advantage factor
        home_boost = 0.3  # Typical home advantage

        # Calculate expected goals
        home_expected = (home_attack + (2.0 - away_defense)) / 2 + home_boost
        away_expected = (away_attack + (2.0 - home_defense)) / 2

        # Adjust based on H2H average if available
        if h2h_analysis["total_matches"] >= 3:
            h2h_weight = 0.2
            home_expected = home_expected * (1 - h2h_weight) + (h2h_analysis["avg_goals"] / 2) * h2h_weight
            away_expected = away_expected * (1 - h2h_weight) + (h2h_analysis["avg_goals"] / 2) * h2h_weight

        # Round to realistic score range
        home_goals = max(0, min(4, round(home_expected)))
        away_goals = max(0, min(4, round(away_expected)))

        # Calculate confidence based on data quality
        confidence = 0.6  # Base confidence
        if home_analysis["total_matches"] >= 5 and away_analysis["total_matches"] >= 5:
            confidence += 0.1
        if h2h_analysis["total_matches"] >= 5:
            confidence += 0.1
        if abs(home_analysis["strength_rating"] - away_analysis["strength_rating"]) > 20:
            confidence += 0.1  # Clear favorite

        return {
            "home_goals": int(home_goals),
            "away_goals": int(away_goals),
            "confidence": min(confidence, 0.9)
        }

    def predict_match_result(self, home_analysis: Dict, away_analysis: Dict, h2h_analysis: Dict, score_prediction: Dict) -> Dict:
        """Predict 1X2 result with confidence"""

        home_goals = score_prediction["home_goals"]
        away_goals = score_prediction["away_goals"]

        # Base prediction from score
        if home_goals > away_goals:
            result = "Home Win"
            result_code = "1"
        elif away_goals > home_goals:
            result = "Away Win"
            result_code = "2"
        else:
            result = "Draw"
            result_code = "X"

        # Calculate confidence using multiple factors
        strength_diff = abs(home_analysis["strength_rating"] - away_analysis["strength_rating"])
        form_diff = abs(home_analysis["win_rate"] - away_analysis["win_rate"])

        # Base confidence from strength difference
        confidence = 0.5 + (strength_diff / 200)  # Max 0.75 from strength

        # Form factor
        confidence += form_diff * 0.3

        # H2H factor
        if h2h_analysis["total_matches"] >= 3:
            if result == "Home Win" and h2h_analysis["home_win_rate"] > 0.6:
                confidence += 0.1
            elif result == "Away Win" and h2h_analysis["away_win_rate"] > 0.6:
                confidence += 0.1
            elif result == "Draw" and h2h_analysis["dominance_factor"] < 0.3:
                confidence += 0.1

        return {
            "result": result,
            "result_code": result_code,
            "confidence": min(confidence, 0.95)
        }

    def predict_over_under(self, home_analysis: Dict, away_analysis: Dict, h2h_analysis: Dict, score_prediction: Dict) -> Dict:
        """Predict Over/Under 2.5 goals with varied outcomes"""

        predicted_total = score_prediction["home_goals"] + score_prediction["away_goals"]

        # Historical over 2.5 rates
        home_over_rate = home_analysis["over_2_5_rate"]
        away_over_rate = away_analysis["over_2_5_rate"]
        h2h_over_rate = h2h_analysis["over_2_5_rate"]

        # Combined probability
        combined_rate = (home_over_rate + away_over_rate) / 2
        if h2h_analysis["total_matches"] >= 3:
            combined_rate = (combined_rate * 0.7) + (h2h_over_rate * 0.3)

        # Attack vs Defense analysis
        total_attack = home_analysis["goals_per_game"] + away_analysis["goals_per_game"]
        total_defense = home_analysis["goals_conceded_per_game"] + away_analysis["goals_conceded_per_game"]

        # Clean sheet factor - if both teams have good clean sheet rates, lean Under
        clean_sheet_factor = (home_analysis["clean_sheet_rate"] + away_analysis["clean_sheet_rate"]) / 2

        # Make varied prediction
        if predicted_total > 2.5 and combined_rate > 0.55 and total_attack > 2.8:
            prediction = "Over 2.5"
            confidence = 0.6 + min(combined_rate * 0.3, 0.3)
        elif predicted_total <= 1.5 or clean_sheet_factor > 0.4 or total_attack < 2.0:
            prediction = "Under 2.5"
            confidence = 0.6 + min((1 - combined_rate) * 0.3, 0.3)
        else:
            # More nuanced decision based on multiple factors
            if combined_rate > 0.5 and total_defense > 2.5:  # High scoring but leaky defenses
                prediction = "Over 2.5"
                confidence = 0.55 + (combined_rate - 0.5) * 0.6
            else:
                prediction = "No"
                confidence = 0.55 + (0.5 - combined_btts_rate) * 0.7

        return {
            "prediction": prediction,
            "confidence": min(confidence, 0.9)
        }

    def generate_match_prediction(self, match_data: Dict) -> Dict:
        """Generate comprehensive prediction for a single match"""
        match = match_data["match"]
        home_team = match["homeTeam"]["name"]
        away_team = match["awayTeam"]["name"]

        home_team_id = match["homeTeam"]["id"]
        away_team_id = match["awayTeam"]["id"]

        # Analyze team data
        home_analysis = self.analyze_team_form(match_data.get("home_form", []), home_team_id)
        away_analysis = self.analyze_team_form(match_data.get("away_form", []), away_team_id)

        h2h_analysis = self.analyze_head_to_head(
            match_data.get("head_to_head", []), 
            home_team_id, 
            away_team_id
        )

        # Generate predictions
        score_pred = self.predict_correct_score(home_analysis, away_analysis, h2h_analysis)
        result_pred = self.predict_match_result(home_analysis, away_analysis, h2h_analysis, score_pred)
        over_under_pred = self.predict_over_under(home_analysis, away_analysis, h2h_analysis, score_pred)
        btts_pred = self.predict_btts(home_analysis, away_analysis, h2h_analysis)

        # Calculate overall confidence (weighted average)
        overall_confidence = (
            score_pred["confidence"] * 0.3 +
            result_pred["confidence"] * 0.3 +
            over_under_pred["confidence"] * 0.2 +
            btts_pred["confidence"] * 0.2
        )

        # Generate detailed analysis explanation
        explanations = []

        # Form analysis
        home_form_str = home_analysis["form_string"] or "-----"
        away_form_str = away_analysis["form_string"] or "-----"
        explanations.append(f"Form: {home_team[:15]} ({home_form_str}) vs {away_team[:15]} ({away_form_str})")

        # Goals analysis
        home_gpg = home_analysis["goals_per_game"]
        away_gpg = away_analysis["goals_per_game"]
        explanations.append(f"Attack: {home_gpg:.1f} vs {away_gpg:.1f} goals/game")

        # Defense analysis
        home_concede = home_analysis["goals_conceded_per_game"]
        away_concede = away_analysis["goals_conceded_per_game"]
        explanations.append(f"Defense: {home_concede:.1f} vs {away_concede:.1f} conceded/game")

        # H2H insight
        if h2h_analysis["total_matches"] >= 3:
            explanations.append(f"H2H: {h2h_analysis['home_wins']}-{h2h_analysis['draws']}-{h2h_analysis['away_wins']} (last {h2h_analysis['total_matches']})")

        # Strength comparison
        home_strength = home_analysis["strength_rating"]
        away_strength = away_analysis["strength_rating"]
        explanations.append(f"Strength: {home_strength:.0f}% vs {away_strength:.0f}%")

        return {
            "match": f"{home_team} vs {away_team}",
            "kickoff": match["utcDate"],
            "league": match["competition"]["name"],
            "predicted_score": f"{score_pred['home_goals']}-{score_pred['away_goals']}",
            "result": result_pred["result"],
            "over_under": over_under_pred["prediction"],
            "btts": btts_pred["prediction"],
            "confidence": overall_confidence,
            "individual_confidences": {
                "score": score_pred["confidence"],
                "result": result_pred["confidence"],
                "over_under": over_under_pred["confidence"],
                "btts": btts_pred["confidence"]
            },
            "analysis": {
                "home_form": home_form_str,
                "away_form": away_form_str,
                "home_goals_avg": home_gpg,
                "away_goals_avg": away_gpg,
                "home_strength": home_strength,
                "away_strength": away_strength,
                "h2h_matches": h2h_analysis["total_matches"]
            },
            "explanation": " • ".join(explanations)
        }

    def calculate_match_score(self, match_data: Dict) -> float:
        """Calculate match quality score for ranking predictions"""
        match = match_data["match"]
        home_form = match_data.get("home_form", [])
        away_form = match_data.get("away_form", [])
        h2h = match_data.get("head_to_head", [])

        # Data availability score
        data_score = 0.0
        if len(home_form) >= 3:
            data_score += 0.25
        if len(away_form) >= 3:
            data_score += 0.25
        if len(h2h) >= 3:
            data_score += 0.2

        # Competition quality bonus
        competition_name = match["competition"]["name"].lower()
        major_competitions = [
            "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "primeira liga",
            "champions league", "europa league", "world cup", "european championship"
        ]

        if any(comp in competition_name for comp in major_competitions):
            data_score += 0.3
        elif "championship" in competition_name or any(word in competition_name for word in ["division", "league"]):
            data_score += 0.2

        # Predictability score (teams with clear form differences are easier to predict)
        if home_form and away_form:
            home_team_id = match["homeTeam"]["id"]
            away_team_id = match["awayTeam"]["id"]

            home_analysis = self.analyze_team_form(home_form, home_team_id)
            away_analysis = self.analyze_team_form(away_form, away_team_id)

            strength_diff = abs(home_analysis["strength_rating"] - away_analysis["strength_rating"])
            predictability_score = min(strength_diff / 150, 0.25)  # Reduced threshold for more variety
            data_score += predictability_score

        return min(data_score, 1.0)

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select top matches for predictions based on data quality and varied outcomes"""
        if not matches_data:
            return []

        scored_matches = []

        for match_data in matches_data:
            try:
                # Generate prediction first
                prediction = self.generate_match_prediction(match_data)

                # Only include matches with decent confidence
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    score = self.calculate_match_score(match_data)

                    scored_matches.append({
                        "score": score,
                        "prediction": prediction,
                        "match_data": match_data
                    })

            except Exception as e:
                logger.error(f"Error processing match: {e}")
                continue

        # Sort by score (descending) and ensure variety in predictions
        scored_matches.sort(key=lambda x: x["score"], reverse=True)

        # Select predictions with variety
        selected_predictions = []
        prediction_types = {"over_under": set(), "btts": set(), "result": set()}

        for match in scored_matches:
            pred = match["prediction"]

            # Add some variety in predictions to avoid all being the same
            over_under = pred["over_under"]
            btts = pred["btts"]
            result = pred["result"]

            # Limit similar predictions to create variety
            if (len(prediction_types["over_under"]) < 2 or over_under not in prediction_types["over_under"] or
                len(selected_predictions) < 3):

                selected_predictions.append(pred)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)
                prediction_types["result"].add(result)

                if len(selected_predictions) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected_predictions)} varied predictions from {len(matches_data)} matches")
        return selected_predictions

class TelegramBot:
    """Telegram bot for sending predictions"""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """Initialize the bot session"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )

        # Test the bot token
        try:
            await self.get_me()
            logger.info("Telegram bot initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
            raise

    async def close(self):
        """Close the bot session"""
        if self.session:
            await self.session.close()

    async def get_me(self) -> Dict:
        """Get bot information"""
        url = f"{self.base_url}/getMe"
        async with self.session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data
            else:
                raise Exception(f"Failed to get bot info: {response.status}")

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat"""
        url = f"{self.base_url}/sendMessage"

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            async with self.session.post(url, json=payload) as response:
                if response.status == 200:
                    logger.debug("Message sent successfully")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to send message: {response.status} - {error_text}")
                    return False

        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def format_prediction_message(self, prediction: Dict) -> str:
        """Format a single prediction into a professional message"""
        match = prediction["match"]
        league = prediction["league"]
        kickoff = prediction["kickoff"]
        predicted_score = prediction["predicted_score"]
        result = prediction["result"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        confidence = prediction["confidence"]
        analysis = prediction["analysis"]
        explanation = prediction["explanation"]

        # Parse kickoff time
        try:
            kickoff_dt = datetime.fromisoformat(kickoff.replace('Z', '+00:00'))
            kickoff_str = kickoff_dt.strftime("%H:%M UTC")
        except:
            kickoff_str = "Time TBD"

        # Build professional message
        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"⏰ {kickoff_str}\n\n"

        # Core predictions with confidence indicators
        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡" if confidence > 0.6 else "📈"

        message += f"🔮 <b>Predicted Score:</b> {predicted_score}\n"
        message += f"✅ <b>Result:</b> {result}\n"
        message += f"⚽ <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"❓ <b>BTTS:</b> {btts}\n\n"

        # Overall confidence with visual indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📈 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📊 {confidence_bar}\n\n"

        # Team analysis
        message += f"📋 <b>Team Analysis:</b>\n"
        message += f"🏠 Form: {analysis['home_form']} | ⚽ {analysis['home_goals_avg']:.1f} GPG | 💪 {analysis['home_strength']:.0f}%\n"
        message += f"✈️ Form: {analysis['away_form']} | ⚽ {analysis['away_goals_avg']:.1f} GPG | 💪 {analysis['away_strength']:.0f}%\n"

        if analysis['h2h_matches'] > 0:
            message += f"🔄 H2H: {analysis['h2h_matches']} recent meetings analyzed\n"

        message += f"\n💡 <b>Key Insights:</b>\n{explanation}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header for daily predictions"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        message = f"🏆 <b>DAILY FOOTBALL PREDICTIONS</b>\n"
        message += f"📅 {today}\n"
        message += f"🎯 Top {len(predictions)} Professional Picks\n"
        message += f"🤖 Powered by Football-Data.org Analytics\n"
        message += "=" * 40 + "\n\n"
        message += "📊 Each prediction uses:\n"
        message += "• Recent form (W/D/L)\n"
        message += "• Goals scored & conceded\n"
        message += "• Head-to-head records\n"
        message += "• BTTS & Over 2.5 rates\n"
        message += "• Clean sheet statistics\n"
        message += "• Strength ratings\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram with professional formatting"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available for today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match_teams = prediction["match"]
                predicted_score = prediction["predicted_score"]
                result = prediction["result"]
                confidence = int(prediction["confidence"] * 100)

                print(f"✅ Sending prediction #{i}: {match_teams} | Score: {predicted_score} | Result: {result} | Confidence: {confidence}%")

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages to avoid rate limiting
                await asyncio.sleep(2)

            # Send professional footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "📈 <i>Powered by Football-Data.org</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

    async def send_startup_message(self):
        """Send professional bot startup message"""
        message = "🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += "✅ Connected to Football-Data.org database\n"
        message += "✅ Advanced prediction algorithms loaded\n"
        message += "✅ Telegram integration active\n"
        message += "✅ Rate limiting protection enabled (10 calls/min max)\n"
        message += "✅ Analysis system ready\n\n"
        message += "🔥 <b>What makes our predictions special:</b>\n"
        message += "• Real match statistics analysis\n"
        message += "• Professional-grade algorithms\n"
        message += "• Live form and performance tracking\n"
        message += "• Head-to-head historical analysis\n"
        message += "• Goals, BTTS, and clean sheet rates\n"
        message += "• Strength ratings and confidence scoring\n\n"
        message += "⏳ <b>Note:</b> Analysis takes time due to API rate limits\n"
        message += "🎯 Running immediate analysis and predictions now!"

        await self.send_message(message)

    async def send_error_message(self, error: str):
        """Send error notification"""
        message = f"❌ <b>SYSTEM ALERT</b>\n\n"
        message += f"⚠️ Technical issue detected: {error[:200]}...\n\n"
        message += "🔧 Our systems are working to resolve this automatically.\n"
        message += "📞 Professional analysis will resume shortly."

        await self.send_message(message)

    async def send_no_matches_message(self):
        """Send message when no matches are available"""
        message = "🤷‍♂️ <b>NO MATCHES AVAILABLE</b>\n\n"
        message += "📅 No suitable matches found for analysis today.\n"
        message += "🔍 Our algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again tomorrow for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)

class FootballPredictionBot:
    """Main bot class - runs prediction analysis immediately"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self, date: str = None):
        """Run prediction analysis immediately when executed"""
        print("🧪 Running professional prediction analysis with rate limit protection...")
        try:
            logger.info("Starting immediate prediction generation with Football-Data.org stats analysis...")

            # Use today's date if none provided
            if not date:
                date = datetime.now().strftime("%Y-%m-%d")

            print(f"📅 Analyzing matches for {date}")
            print("⏳ Please be patient - rate limiting ensures we stay within API limits")

            # Fetch and process match data
            async with FootballDataClient(self.config) as api_client:
                processor = FootballDataProcessor(api_client)
                matches_data = await processor.get_daily_matches_data(date)

            print("✅ Real match data analysis completed!")

            if not matches_data:
                logger.warning("No matches found for prediction")
                await self.telegram_bot.send_no_matches_message()
                return

            # Generate professional predictions using real analysis
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 60%+ confidence based on real data.\n\n"
                    "⏰ Try running again tomorrow for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real Football-Data.org analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info("Starting Football Prediction Bot with Football-Data.org Analytics...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            # Send startup message
            await self.telegram_bot.send_startup_message()
            print("🔄 Professional Football Analytics Bot is now running!")

            # Run predictions immediately (no scheduling)
            await self.run_predictions_immediately()

            logger.info("Bot predictions completed successfully")
            print("🎯 Prediction analysis complete!")

        except Exception as e:
            logger.error(f"Error running bot: {e}")
            await self.telegram_bot.send_error_message(str(e))
        finally:
            # Close telegram session
            await self.telegram_bot.close()

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

async def main():
    """Main function - runs predictions immediately"""
    bot = FootballPredictionBot()

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, bot.signal_handler)
    signal.signal(signal.SIGTERM, bot.signal_handler)

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Bot execution completed")

if __name__ == "__main__":
    # Run predictions immediately when executed
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        async def test_predictions():
            config = Config()
            telegram_bot = TelegramBot(config.telegram_token, config.telegram_chat_id)
            prediction_engine = PredictionEngine(config)
            await telegram_bot.initialize()

            bot = FootballPredictionBot()
            await bot.run_predictions_immediately()
            await telegram_bot.close()

        asyncio.run(test_predictions())
    else:
        asyncio.run(main())Under 2.5"
                confidence = 0.55 + (0.5 - combined_rate) * 0.6

        return {
            "prediction": prediction,
            "confidence": min(confidence, 0.9)
        }

    def predict_btts(self, home_analysis: Dict, away_analysis: Dict, h2h_analysis: Dict) -> Dict:
        """Predict Both Teams to Score with varied outcomes"""

        # BTTS rates from recent form
        home_btts_rate = home_analysis["btts_rate"]
        away_btts_rate = away_analysis["btts_rate"]
        h2h_btts_rate = h2h_analysis["btts_rate"]

        # Attack strength vs defense weakness
        home_attack = home_analysis["goals_per_game"]
        away_attack = away_analysis["goals_per_game"]
        home_defense = home_analysis["goals_conceded_per_game"]
        away_defense = away_analysis["goals_conceded_per_game"]

        # Both teams scoring likelihood
        home_scores_prob = (home_attack > 0.8 and away_defense > 0.6)
        away_scores_prob = (away_attack > 0.8 and home_defense > 0.6)

        # Combined BTTS probability
        combined_btts_rate = (home_btts_rate + away_btts_rate) / 2
        if h2h_analysis["total_matches"] >= 3:
            combined_btts_rate = (combined_btts_rate * 0.7) + (h2h_btts_rate * 0.3)

        # Clean sheet factor - if either team keeps many clean sheets, lean No
        clean_sheet_factor = max(home_analysis["clean_sheet_rate"], away_analysis["clean_sheet_rate"])

        # Make varied prediction
        if combined_btts_rate > 0.6 and home_scores_prob and away_scores_prob and clean_sheet_factor < 0.3:
            prediction = "Yes"
            confidence = 0.6 + min(combined_btts_rate * 0.3, 0.3)
        elif clean_sheet_factor > 0.5 or min(home_attack, away_attack) < 0.5:
            prediction = "No"
            confidence = 0.6 + min((1 - combined_btts_rate) * 0.3, 0.3)
        else:
            # More balanced approach
            if combined_btts_rate > 0.5 and min(home_attack, away_attack) > 0.7:
                prediction = "Yes"
                confidence = 0.55 + (combined_btts_rate - 0.5) * 0.7
            else:
                prediction = "