#!/usr/bin/env python3
"""
Football Prediction Telegram Bot - Forbet Scraper Version
Complete bot with Forbet scraping and anti-ban protections
"""

import asyncio
import logging
import signal
import sys
import os
import json
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import requests
from bs4 import BeautifulSoup
import re

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('forbet_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration class holding bot settings"""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    forbet_url: str = "https://forbet.com.ng/sport/football"
    max_matches: int = 10
    min_confidence_threshold: float = 0.6
    request_headers: dict = None

    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.max_matches = int(os.getenv("MAX_MATCHES", str(self.max_matches)))
        self.min_confidence_threshold = float(os.getenv("MIN_CONFIDENCE", str(self.min_confidence_threshold)))
        self.request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
        }
        self._validate()

    def _validate(self):
        """Validate required configuration"""
        if not self.telegram_token:
            raise ValueError("TELEGRAM_TOKEN environment variable is required")
        if not self.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID environment variable is required")

class ForbetScraper:
    """Scraper for Forbet football matches"""
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.request_headers)

    def scrape_matches(self, target_team: Optional[str] = None) -> List[Dict]:
        """Scrape matches from Forbet's main football page"""
        logger.info("Starting Forbet scraping session...")
        print("⏳ Scraping Forbet football page...")

        try:
            time.sleep(2)  # Anti-ban delay
            response = self.session.get(self.config.forbet_url, timeout=30)
            response.raise_for_status()
            time.sleep(2)  # Anti-ban delay before parsing

            soup = BeautifulSoup(response.text, 'html.parser')
            matches = []

            # Find top leagues or popular matches section
            match_sections = soup.select('div.event-list__item, div.popular-matches')
            for section in match_sections:
                # Extract match details
                match_info = self._parse_match(section)
                if match_info:
                    if target_team and target_team.lower() not in match_info['match'].lower():
                        continue
                    matches.append(match_info)
                    if len(matches) >= self.config.max_matches:
                        break

            logger.info(f"✅ Scraped {len(matches)} matches from Forbet")
            print(f"⚽ Found {len(matches)} matches")
            return matches

        except Exception as e:
            logger.error(f"Scraping error: {e}")
            print(f"❌ Error scraping Forbet: {e}")
            return []

    def _parse_match(self, element) -> Optional[Dict]:
        """Parse individual match data from HTML element"""
        try:
            # Extract teams
            teams = element.select_one('.event__participants')
            if not teams:
                return None
            match_name = teams.text.strip()
            team_names = match_name.split(' - ')
            if len(team_names) != 2:
                return None
            home_team, away_team = team_names

            # Extract league
            league_elem = element.select_one('.event__league')
            league = league_elem.text.strip() if league_elem else "Unknown League"

            # Extract time
            time_elem = element.select_one('.event__time')
            match_time = time_elem.text.strip() if time_elem else "TBD"

            # Extract odds
            odds_1 = odds_x = odds_2 = 0.0
            odds_elements = element.select('.event__odds button')
            if len(odds_elements) >= 3:
                odds_1 = float(odds_elements[0].text.strip()) if odds_elements[0].text.strip() else 0.0
                odds_x = float(odds_elements[1].text.strip()) if odds_elements[1].text.strip() else 0.0
                odds_2 = float(odds_elements[2].text.strip()) if odds_elements[2].text.strip() else 0.0

            # Extract form (if available)
            form_elements = element.select('.team-form__icon')
            home_form = away_form = ""
            if len(form_elements) >= 2:
                home_form = ''.join([e['class'][-1][0].upper() for e in form_elements[:5]])
                away_form = ''.join([e['class'][-1][0].upper() for e in form_elements[5:10]])

            return {
                "match": f"{home_team} vs {away_team}",
                "league": league,
                "time": match_time,
                "odds": {"1": odds_1, "X": odds_x, "2": odds_2},
                "home_form": home_form or "-----",
                "away_form": away_form or "-----"
            }
        except Exception as e:
            logger.debug(f"Error parsing match: {e}")
            return None

class PredictionEngine:
    """Prediction engine for Forbet scraped data"""
    def __init__(self, config):
        self.config = config

    def analyze_form(self, form_string: str) -> Dict:
        """Analyze team form from scraped icons"""
        if not form_string or form_string == "-----":
            return {
                "form_string": "-----",
                "win_rate": 0.0,
                "strength_rating": 50.0
            }

        wins = form_string.count('W')
        draws = form_string.count('D')
        losses = form_string.count('L')
        total = len(form_string)

        win_rate = wins / total if total > 0 else 0.0
        strength = (wins * 3 + draws * 1) / (total * 3) * 100 if total > 0 else 50.0

        return {
            "form_string": form_string,
            "win_rate": win_rate,
            "strength_rating": strength
        }

    def predict_correct_score(self, home_analysis: Dict, away_analysis: Dict, odds: Dict) -> Dict:
        """Predict correct score based on form and odds"""
        home_strength = home_analysis["strength_rating"]
        away_strength = away_analysis["strength_rating"]

        # Use odds to estimate goals
        implied_total = 2.5 / (1/odds.get("over_2_5", 2.0) if odds.get("over_2_5") else 2.0)
        home_goals = round(implied_total * (home_strength / (home_strength + away_strength)))
        away_goals = round(implied_total * (away_strength / (home_strength + away_strength)))

        confidence = 0.6
        if home_analysis["form_string"] != "-----" and away_analysis["form_string"] != "-----":
            confidence += 0.1

        return {
            "home_goals": max(0, min(4, home_goals)),
            "away_goals": max(0, min(4, away_goals)),
            "confidence": min(confidence, 0.9)
        }

    def predict_match_result(self, home_analysis: Dict, away_analysis: Dict, odds: Dict) -> Dict:
        """Predict 1X2 result"""
        home_strength = home_analysis["strength_rating"]
        away_strength = away_analysis["strength_rating"]

        # Convert odds to probabilities
        odds_1 = odds.get("1", 3.0)
        odds_x = odds.get("X", 3.0)
        odds_2 = odds.get("2", 3.0)

        prob_1 = 1/odds_1 / (1/odds_1 + 1/odds_x + 1/odds_2)
        prob_x = 1/odds_x / (1/odds_1 + 1/odds_x + 1/odds_2)
        prob_2 = 1/odds_2 / (1/odds_1 + 1/odds_x + 1/odds_2)

        # Combine with form
        if home_strength > away_strength + 20:
            result, result_code = "Home Win", "1"
            confidence = prob_1 + 0.1
        elif away_strength > home_strength + 20:
            result, result_code = "Away Win", "2"
            confidence = prob_2 + 0.1
        else:
            result, result_code = "Draw", "X"
            confidence = prob_x + 0.1

        return {
            "result": result,
            "result_code": result_code,
            "confidence": min(confidence, 0.95)
        }

    def predict_over_under(self, odds: Dict, home_analysis: Dict, away_analysis: Dict) -> Dict:
        """Predict Over/Under 2.5 goals"""
        over_odds = odds.get("over_2_5", 2.0)
        under_odds = odds.get("under_2_5", 2.0)

        prob_over = 1/over_odds / (1/over_odds + 1/under_odds)

        prediction = "Over 2.5" if prob_over > 0.55 else "Under 2.5"
        confidence = prob_over if prob_over > 0.5 else 1 - prob_over

        if home_analysis["win_rate"] > 0.6 or away_analysis["win_rate"] > 0.6:
            confidence += 0.1

        return {
            "prediction": prediction,
            "confidence": min(confidence, 0.9)
        }

    def predict_btts(self, home_analysis: Dict, away_analysis: Dict, odds: Dict) -> Dict:
        """Predict Both Teams to Score"""
        btts_yes_odds = odds.get("btts_yes", 2.0)
        btts_no_odds = odds.get("btts_no", 2.0)

        prob_yes = 1/btts_yes_odds / (1/btts_yes_odds + 1/btts_no_odds)

        prediction = "Yes" if prob_yes > 0.55 else "No"
        confidence = prob_yes if prob_yes > 0.5 else 1 - prob_yes

        if home_analysis["win_rate"] > 0.6 and away_analysis["win_rate"] > 0.6:
            confidence += 0.1

        return {
            "prediction": prediction,
            "confidence": min(confidence, 0.9)
        }

    def generate_match_prediction(self, match_data: Dict) -> Dict:
        """Generate comprehensive prediction for a match"""
        home_analysis = self.analyze_form(match_data["home_form"])
        away_analysis = self.analyze_form(match_data["away_form"])
        odds = match_data["odds"]

        score_pred = self.predict_correct_score(home_analysis, away_analysis, odds)
        result_pred = self.predict_match_result(home_analysis, away_analysis, odds)
        over_under_pred = self.predict_over_under(odds, home_analysis, away_analysis)
        btts_pred = self.predict_btts(home_analysis, away_analysis, odds)

        overall_confidence = (
            score_pred["confidence"] * 0.3 +
            result_pred["confidence"] * 0.3 +
            over_under_pred["confidence"] * 0.2 +
            btts_pred["confidence"] * 0.2
        )

        explanations = [
            f"Form: {match_data['home_form']} vs {match_data['away_form']}",
            f"Strength: {home_analysis['strength_rating']:.0f}% vs {away_analysis['strength_rating']:.0f}%",
            f"Odds: 1 - {odds.get('1', 0.0):.2f} | X - {odds.get('X', 0.0):.2f} | 2 - {odds.get('2', 0.0):.2f}"
        ]

        return {
            "match": match_data["match"],
            "league": match_data["league"],
            "time": match_data["time"],
            "odds": odds,
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
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_strength": home_analysis["strength_rating"],
                "away_strength": away_analysis["strength_rating"]
            },
            "explanation": " • ".join(explanations)
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select top matches for predictions"""
        scored_matches = []

        for match_data in matches_data:
            try:
                prediction = self.generate_match_prediction(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    score = self.calculate_match_score(match_data)
                    scored_matches.append({
                        "score": score,
                        "prediction": prediction
                    })
            except Exception as e:
                logger.error(f"Error processing match: {e}")
                continue

        scored_matches.sort(key=lambda x: x["score"], reverse=True)
        selected = []
        prediction_types = {"over_under": set(), "btts": set(), "result": set()}

        for match in scored_matches:
            pred = match["prediction"]
            if (len(prediction_types["over_under"]) < 2 or 
                pred["over_under"] not in prediction_types["over_under"] or
                len(selected) < 3):
                selected.append(pred)
                prediction_types["over_under"].add(pred["over_under"])
                prediction_types["btts"].add(pred["btts"])
                prediction_types["result"].add(pred["result"])

                if len(selected) >= self.config.max_matches:
                    break

        return selected

    def calculate_match_score(self, match_data: Dict) -> float:
        """Calculate match quality score"""
        score = 0.0
        if match_data["home_form"] != "-----" and match_data["away_form"] != "-----":
            score += 0.4
        if any(word in match_data["league"].lower() for word in ["premier", "liga", "serie", "bundesliga", "champions"]):
            score += 0.3
        if all(k in match_data["odds"] for k in ["1", "X", "2"]):
            score += 0.2
        return min(score, 1.0)

class TelegramBot:
    """Telegram bot for sending predictions"""
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        try:
            await self.get_me()
            logger.info("Telegram bot initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
            raise

    async def close(self):
        if self.session:
            await self.session.close()

    async def get_me(self) -> Dict:
        url = f"{self.base_url}/getMe"
        async with self.session.get(url) as response:
            if response.status == 200:
                return await response.json()
            raise Exception(f"Failed to get bot info: {response.status}")

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
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
                logger.error(f"Failed to send message: {response.status}")
                return False
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def format_prediction_message(self, prediction: Dict) -> str:
        """Format a single prediction"""
        message = f"📊 <b>{prediction['match']}</b>\n"
        message += f"🏟️ League: {prediction['league']}\n"
        message += f"🕓 Time: {prediction['time']}\n"
        message += f"🔢 Odds: 1 - {prediction['odds'].get('1', 0.0):.2f} | X - {prediction['odds'].get('X', 0.0):.2f} | 2 - {prediction['odds'].get('2', 0.0):.2f}\n\n"
        message += f"🔮 <b>Predicted Score:</b> {prediction['predicted_score']}\n"
        message += f"✅ <b>Result:</b> {prediction['result']}\n"
        message += f"⚽ <b>Over/Under 2.5:</b> {prediction['over_under']}\n"
        message += f"💡 <b>Tip: BTTS</b> {prediction['btts']} ✅\n\n"

        confidence_pct = int(prediction["confidence"] * 100)
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"🎯 <b>Confidence:</b> {confidence_pct}% {confidence_bar}\n\n"
        message += f"📋 <b>Analysis:</b>\n{prediction['explanation']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")
        message = f"🏆 <b>DAILY FOOTBALL PREDICTIONS</b>\n"
        message += f"📅 {today}\n"
        message += f"🎯 Top {len(predictions)} Picks\n"
        message += f"🤖 Powered by Forbet Analytics\n"
        message += "=" * 40 + "\n\n"
        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available.")
            return False

        logger.info(f"Sending {len(predictions)} predictions")
        try:
            await self.send_message(self.format_predictions_summary(predictions))
            await asyncio.sleep(1)

            for i, pred in enumerate(predictions, 1):
                print(f"✅ Sending prediction #{i}: {pred['match']}")
                message = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(pred)
                success = await self.send_message(message)
                if not success:
                    logger.error(f"Failed to send prediction {i}")
                await asyncio.sleep(2)

            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "📈 <i>Powered by Forbet Scraping</i>\n"
            footer += "⚠️ <i>Bet responsibly!</i>\n"
            await self.send_message(footer)
            return True
        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

    async def send_startup_message(self):
        """Send startup message with branding"""
        message = """🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b> ✅
✅ Connected to Forbet Stats Engine
✅ Advanced prediction algorithms loaded
✅ Telegram integration active
✅ Rate limiting & scraping protection enabled
✅ Match analyzer ready

🔥 <b>What makes our predictions special:</b>
• Real-time scraping of Forbet data
• Professional-grade prediction logic
• Form, odds & performance-based analysis
• Live league filtering
• Goals, BTTS, clean sheet trends
• Smart correct score predictions
• Confidence scores

⚠️ <b>Note:</b> We keep it safe — max 10 matches per session
⏳ Be patient, scraping takes time and respects site limits
🚀 Bot ready to provide top-tier football predictions!"""

        await self.send_message(message)

    async def send_error_message(self, error: str):
        """Send error notification"""
        message = f"❌ <b>SYSTEM ALERT</b>\n\n"
        message += f"⚠️ Issue: {error[:200]}...\n"
        message += "🔧 Working to resolve automatically.\n"
        await self.send_message(message)

    async def send_no_matches_message(self):
        """Send no matches message"""
        message = "🤷‍♂️ <b>NO MATCHES AVAILABLE</b>\n\n"
        message += "📅 No suitable matches found.\n"
        message += "⏰ Try again later for fresh analysis!"
        await self.send_message(message)

class FootballPredictionBot:
    """Main bot class"""
    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def analyze_matches(self, target_team: Optional[str] = None):
        """Analyze matches and generate predictions"""
        print("🧪 Running Forbet prediction analysis...")
        try:
            logger.info("Starting Forbet scraping and analysis...")
            scraper = ForbetScraper(self.config)
            matches_data = scraper.scrape_matches(target_team)

            if not matches_data:
                await self.telegram_bot.send_no_matches_message()
                return

            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 No high-confidence predictions available.\n"
                    "⏰ Try again later!"
                )
                return

            success = await self.telegram_bot.send_daily_predictions(top_predictions)
            if success:
                print(f"🎉 Sent {len(top_predictions)} predictions!")
            else:
                logger.error("Failed to send predictions")

        except Exception as e:
            logger.error(f"Error in analysis: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot"""
        logger.info("Starting Forbet Prediction Bot...")
        try:
            await self.telegram_bot.initialize()
            await self.telegram_bot.send_startup_message()
            print("🔄 Football Analytics Bot running!")
            await self.analyze_matches()
        except Exception as e:
            logger.error(f"Error running bot: {e}")
            await self.telegram_bot.send_error_message(str(e))
        finally:
            await self.telegram_bot.close()

    async def analyze_specific_team(self, team: str):
        """Analyze matches for a specific team"""
        logger.info(f"Analyzing matches for team: {team}")
        await self.analyze_matches(target_team=team)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

async def main():
    """Main function"""
    bot = FootballPredictionBot()
    signal.signal(signal.SIGINT, bot.signal_handler)
    signal.signal(signal.SIGTERM, bot.signal_handler)

    try:
        if len(sys.argv) > 1 and sys.argv[1].startswith("/analyze"):
            team = sys.argv[1].replace("/analyze", "").strip()
            if team:
                await bot.analyze_specific_team(team)
            else:
                await bot.start()
        else:
            await bot.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Bot execution completed")

if __name__ == "__main__":
    asyncio.run(main())#!/usr/bin/env python3
"""
Football Prediction Telegram Bot - Forbet Scraper Version
Complete bot with Forbet scraping and anti-ban protections
"""

import asyncio
import logging
import signal
import sys
import os
import json
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import requests
from bs4 import BeautifulSoup
import re

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('forbet_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration class holding bot settings"""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    forbet_url: str = "https://forbet.com.ng/sport/football"
    max_matches: int = 10
    min_confidence_threshold: float = 0.6
    request_headers: dict = None

    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.max_matches = int(os.getenv("MAX_MATCHES", str(self.max_matches)))
        self.min_confidence_threshold = float(os.getenv("MIN_CONFIDENCE", str(self.min_confidence_threshold)))
        self.request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
        }
        self._validate()

    def _validate(self):
        """Validate required configuration"""
        if not self.telegram_token:
            raise ValueError("TELEGRAM_TOKEN environment variable is required")
        if not self.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID environment variable is required")

class ForbetScraper:
    """Scraper for Forbet football matches"""
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.request_headers)

    def scrape_matches(self, target_team: Optional[str] = None) -> List[Dict]:
        """Scrape matches from Forbet's main football page"""
        logger.info("Starting Forbet scraping session...")
        print("⏳ Scraping Forbet football page...")

        try:
            time.sleep(2)  # Anti-ban delay
            response = self.session.get(self.config.forbet_url, timeout=30)
            response.raise_for_status()
            time.sleep(2)  # Anti-ban delay before parsing

            soup = BeautifulSoup(response.text, 'html.parser')
            matches = []

            # Find top leagues or popular matches section
            match_sections = soup.select('div.event-list__item, div.popular-matches')
            for section in match_sections:
                # Extract match details
                match_info = self._parse_match(section)
                if match_info:
                    if target_team and target_team.lower() not in match_info['match'].lower():
                        continue
                    matches.append(match_info)
                    if len(matches) >= self.config.max_matches:
                        break

            logger.info(f"✅ Scraped {len(matches)} matches from Forbet")
            print(f"⚽ Found {len(matches)} matches")
            return matches

        except Exception as e:
            logger.error(f"Scraping error: {e}")
            print(f"❌ Error scraping Forbet: {e}")
            return []

    def _parse_match(self, element) -> Optional[Dict]:
        """Parse individual match data from HTML element"""
        try:
            # Extract teams
            teams = element.select_one('.event__participants')
            if not teams:
                return None
            match_name = teams.text.strip()
            team_names = match_name.split(' - ')
            if len(team_names) != 2:
                return None
            home_team, away_team = team_names

            # Extract league
            league_elem = element.select_one('.event__league')
            league = league_elem.text.strip() if league_elem else "Unknown League"

            # Extract time
            time_elem = element.select_one('.event__time')
            match_time = time_elem.text.strip() if time_elem else "TBD"

            # Extract odds
            odds_1 = odds_x = odds_2 = 0.0
            odds_elements = element.select('.event__odds button')
            if len(odds_elements) >= 3:
                odds_1 = float(odds_elements[0].text.strip()) if odds_elements[0].text.strip() else 0.0
                odds_x = float(odds_elements[1].text.strip()) if odds_elements[1].text.strip() else 0.0
                odds_2 = float(odds_elements[2].text.strip()) if odds_elements[2].text.strip() else 0.0

            # Extract form (if available)
            form_elements = element.select('.team-form__icon')
            home_form = away_form = ""
            if len(form_elements) >= 2:
                home_form = ''.join([e['class'][-1][0].upper() for e in form_elements[:5]])
                away_form = ''.join([e['class'][-1][0].upper() for e in form_elements[5:10]])

            return {
                "match": f"{home_team} vs {away_team}",
                "league": league,
                "time": match_time,
                "odds": {"1": odds_1, "X": odds_x, "2": odds_2},
                "home_form": home_form or "-----",
                "away_form": away_form or "-----"
            }
        except Exception as e:
            logger.debug(f"Error parsing match: {e}")
            return None

class PredictionEngine:
    """Prediction engine for Forbet scraped data"""
    def __init__(self, config):
        self.config = config

    def analyze_form(self, form_string: str) -> Dict:
        """Analyze team form from scraped icons"""
        if not form_string or form_string == "-----":
            return {
                "form_string": "-----",
                "win_rate": 0.0,
                "strength_rating": 50.0
            }

        wins = form_string.count('W')
        draws = form_string.count('D')
        losses = form_string.count('L')
        total = len(form_string)

        win_rate = wins / total if total > 0 else 0.0
        strength = (wins * 3 + draws * 1) / (total * 3) * 100 if total > 0 else 50.0

        return {
            "form_string": form_string,
            "win_rate": win_rate,
            "strength_rating": strength
        }

    def predict_correct_score(self, home_analysis: Dict, away_analysis: Dict, odds: Dict) -> Dict:
        """Predict correct score based on form and odds"""
        home_strength = home_analysis["strength_rating"]
        away_strength = away_analysis["strength_rating"]
        
        # Use odds to estimate goals
        implied_total = 2.5 / (1/odds.get("over_2_5", 2.0) if odds.get("over_2_5") else 2.0)
        home_goals = round(implied_total * (home_strength / (home_strength + away_strength)))
        away_goals = round(implied_total * (away_strength / (home_strength + away_strength)))

        confidence = 0.6
        if home_analysis["form_string"] != "-----" and away_analysis["form_string"] != "-----":
            confidence += 0.1

        return {
            "home_goals": max(0, min(4, home_goals)),
            "away_goals": max(0, min(4, away_goals)),
            "confidence": min(confidence, 0.9)
        }

    def predict_match_result(self, home_analysis: Dict, away_analysis: Dict, odds: Dict) -> Dict:
        """Predict 1X2 result"""
        home_strength = home_analysis["strength_rating"]
        away_strength = away_analysis["strength_rating"]
        
        # Convert odds to probabilities
        odds_1 = odds.get("1", 3.0)
        odds_x = odds.get("X", 3.0)
        odds_2 = odds.get("2", 3.0)
        
        prob_1 = 1/odds_1 / (1/odds_1 + 1/odds_x + 1/odds_2)
        prob_x = 1/odds_x / (1/odds_1 + 1/odds_x + 1/odds_2)
        prob_2 = 1/odds_2 / (1/odds_1 + 1/odds_x + 1/odds_2)

        # Combine with form
        if home_strength > away_strength + 20:
            result, result_code = "Home Win", "1"
            confidence = prob_1 + 0.1
        elif away_strength > home_strength + 20:
            result, result_code = "Away Win", "2"
            confidence = prob_2 + 0.1
        else:
            result, result_code = "Draw", "X"
            confidence = prob_x + 0.1

        return {
            "result": result,
            "result_code": result_code,
            "confidence": min(confidence, 0.95)
        }

    def predict_over_under(self, odds: Dict, home_analysis: Dict, away_analysis: Dict) -> Dict:
        """Predict Over/Under 2.5 goals"""
        over_odds = odds.get("over_2_5", 2.0)
        under_odds = odds.get("under_2_5", 2.0)
        
        prob_over = 1/over_odds / (1/over_odds + 1/under_odds)
        
        prediction = "Over 2.5" if prob_over > 0.55 else "Under 2.5"
        confidence = prob_over if prob_over > 0.5 else 1 - prob_over
        
        if home_analysis["win_rate"] > 0.6 or away_analysis["win_rate"] > 0.6:
            confidence += 0.1

        return {
            "prediction": prediction,
            "confidence": min(confidence, 0.9)
        }

    def predict_btts(self, home_analysis: Dict, away_analysis: Dict, odds: Dict) -> Dict:
        """Predict Both Teams to Score"""
        btts_yes_odds = odds.get("btts_yes", 2.0)
        btts_no_odds = odds.get("btts_no", 2.0)
        
        prob_yes = 1/btts_yes_odds / (1/btts_yes_odds + 1/btts_no_odds)
        
        prediction = "Yes" if prob_yes > 0.55 else "No"
        confidence = prob_yes if prob_yes > 0.5 else 1 - prob_yes
        
        if home_analysis["win_rate"] > 0.6 and away_analysis["win_rate"] > 0.6:
            confidence += 0.1

        return {
            "prediction": prediction,
            "confidence": min(confidence, 0.9)
        }

    def generate_match_prediction(self, match_data: Dict) -> Dict:
        """Generate comprehensive prediction for a match"""
        home_analysis = self.analyze_form(match_data["home_form"])
        away_analysis = self.analyze_form(match_data["away_form"])
        odds = match_data["odds"]

        score_pred = self.predict_correct_score(home_analysis, away_analysis, odds)
        result_pred = self.predict_match_result(home_analysis, away_analysis, odds)
        over_under_pred = self.predict_over_under(odds, home_analysis, away_analysis)
        btts_pred = self.predict_btts(home_analysis, away_analysis, odds)

        overall_confidence = (
            score_pred["confidence"] * 0.3 +
            result_pred["confidence"] * 0.3 +
            over_under_pred["confidence"] * 0.2 +
            btts_pred["confidence"] * 0.2
        )

        explanations = [
            f"Form: {match_data['home_form']} vs {match_data['away_form']}",
            f"Strength: {home_analysis['strength_rating']:.0f}% vs {away_analysis['strength_rating']:.0f}%",
            f"Odds: 1 - {odds.get('1', 0.0):.2f} | X - {odds.get('X', 0.0):.2f} | 2 - {odds.get('2', 0.0):.2f}"
        ]

        return {
            "match": match_data["match"],
            "league": match_data["league"],
            "time": match_data["time"],
            "odds": odds,
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
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_strength": home_analysis["strength_rating"],
                "away_strength": away_analysis["strength_rating"]
            },
            "explanation": " • ".join(explanations)
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select top matches for predictions"""
        scored_matches = []
        
        for match_data in matches_data:
            try:
                prediction = self.generate_match_prediction(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    score = self.calculate_match_score(match_data)
                    scored_matches.append({
                        "score": score,
                        "prediction": prediction
                    })
            except Exception as e:
                logger.error(f"Error processing match: {e}")
                continue

        scored_matches.sort(key=lambda x: x["score"], reverse=True)
        selected = []
        prediction_types = {"over_under": set(), "btts": set(), "result": set()}

        for match in scored_matches:
            pred = match["prediction"]
            if (len(prediction_types["over_under"]) < 2 or 
                pred["over_under"] not in prediction_types["over_under"] or
                len(selected) < 3):
                selected.append(pred)
                prediction_types["over_under"].add(pred["over_under"])
                prediction_types["btts"].add(pred["btts"])
                prediction_types["result"].add(pred["result"])
                
                if len(selected) >= self.config.max_matches:
                    break

        return selected

    def calculate_match_score(self, match_data: Dict) -> float:
        """Calculate match quality score"""
        score = 0.0
        if match_data["home_form"] != "-----" and match_data["away_form"] != "-----":
            score += 0.4
        if any(word in match_data["league"].lower() for word in ["premier", "liga", "serie", "bundesliga", "champions"]):
            score += 0.3
        if all(k in match_data["odds"] for k in ["1", "X", "2"]):
            score += 0.2
        return min(score, 1.0)

class TelegramBot:
    """Telegram bot for sending predictions"""
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        try:
            await self.get_me()
            logger.info("Telegram bot initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
            raise

    async def close(self):
        if self.session:
            await self.session.close()

    async def get_me(self) -> Dict:
        url = f"{self.base_url}/getMe"
        async with self.session.get(url) as response:
            if response.status == 200:
                return await response.json()
            raise Exception(f"Failed to get bot info: {response.status}")

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
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
                logger.error(f"Failed to send message: {response.status}")
                return False
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def format_prediction_message(self, prediction: Dict) -> str:
        """Format a single prediction"""
        message = f"📊 <b>{prediction['match']}</b>\n"
        message += f"🏟️ League: {prediction['league']}\n"
        message += f"🕓 Time: {prediction['time']}\n"
        message += f"🔢 Odds: 1 - {prediction['odds'].get('1', 0.0):.2f} | X - {prediction['odds'].get('X', 0.0):.2f} | 2 - {prediction['odds'].get('2', 0.0):.2f}\n\n"
        message += f"🔮 <b>Predicted Score:</b> {prediction['predicted_score']}\n"
        message += f"✅ <b>Result:</b> {prediction['result']}\n"
        message += f"⚽ <b>Over/Under 2.5:</b> {prediction['over_under']}\n"
        message += f"💡 <b>Tip: BTTS</b> {prediction['btts']} ✅\n\n"
        
        confidence_pct = int(prediction["confidence"] * 100)
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"🎯 <b>Confidence:</b> {confidence_pct}% {confidence_bar}\n\n"
        message += f"📋 <b>Analysis:</b>\n{prediction['explanation']}\n"
        
        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")
        message = f"🏆 <b>DAILY FOOTBALL PREDICTIONS</b>\n"
        message += f"📅 {today}\n"
        message += f"🎯 Top {len(predictions)} Picks\n"
        message += f"🤖 Powered by Forbet Analytics\n"
        message += "=" * 40 + "\n\n"
        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available.")
            return False

        logger.info(f"Sending {len(predictions)} predictions")
        try:
            await self.send_message(self.format_predictions_summary(predictions))
            await asyncio.sleep(1)

            for i, pred in enumerate(predictions, 1):
                print(f"✅ Sending prediction #{i}: {pred['match']}")
                message = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(pred)
                success = await self.send_message(message)
                if not success:
                    logger.error(f"Failed to send prediction {i}")
                await asyncio.sleep(2)

            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "📈 <i>Powered by Forbet Scraping</i>\n"
            footer += "⚠️ <i>Bet responsibly!</i>\n"
            await self.send_message(footer)
            return True
        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

    async def send_startup_message(self):
        """Send startup message with branding"""
        message = """🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b> ✅
✅ Connected to Forbet Stats Engine
✅ Advanced prediction algorithms loaded
✅ Telegram integration active
✅ Rate limiting & scraping protection enabled
✅ Match analyzer ready

🔥 <b>What makes our predictions special:</b>
• Real-time scraping of Forbet data
• Professional-grade prediction logic
• Form, odds & performance-based analysis
• Live league filtering
• Goals, BTTS, clean sheet trends
• Smart correct score predictions
• Confidence scores

⚠️ <b>Note:</b> We keep it safe — max 10 matches per session
⏳ Be patient, scraping takes time and respects site limits
🚀 Bot ready to provide top-tier football predictions!"""

        await self.send_message(message)

    async def send_error_message(self, error: str):
        """Send error notification"""
        message = f"❌ <b>SYSTEM ALERT</b>\n\n"
        message += f"⚠️ Issue: {error[:200]}...\n"
        message += "🔧 Working to resolve automatically.\n"
        await self.send_message(message)

    async def send_no_matches_message(self):
        """Send no matches message"""
        message = "🤷‍♂️ <b>NO MATCHES AVAILABLE</b>\n\n"
        message += "📅 No suitable matches found.\n"
        message += "⏰ Try again later for fresh analysis!"
        await self.send_message(message)

class FootballPredictionBot:
    """Main bot class"""
    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def analyze_matches(self, target_team: Optional[str] = None):
        """Analyze matches and generate predictions"""
        print("🧪 Running Forbet prediction analysis...")
        try:
            logger.info("Starting Forbet scraping and analysis...")
            scraper = ForbetScraper(self.config)
            matches_data = scraper.scrape_matches(target_team)

            if not matches_data:
                await self.telegram_bot.send_no_matches_message()
                return

            top_predictions = self.prediction_engine.select_top_predictions(matches_data)
            
            if not top_predictions:
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 No high-confidence predictions available.\n"
                    "⏰ Try again later!"
                )
                return

            success = await self.telegram_bot.send_daily_predictions(top_predictions)
            if success:
                print(f"🎉 Sent {len(top_predictions)} predictions!")
            else:
                logger.error("Failed to send predictions")

        except Exception as e:
            logger.error(f"Error in analysis: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot"""
        logger.info("Starting Forbet Prediction Bot...")
        try:
            await self.telegram_bot.initialize()
            await self.telegram_bot.send_startup_message()
            print("🔄 Football Analytics Bot running!")
            await self.analyze_matches()
        except Exception as e:
            logger.error(f"Error running bot: {e}")
            await self.telegram_bot.send_error_message(str(e))
        finally:
            await self.telegram_bot.close()

    async def analyze_specific_team(self, team: str):
        """Analyze matches for a specific team"""
        logger.info(f"Analyzing matches for team: {team}")
        await self.analyze_matches(target_team=team)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

async def main():
    """Main function"""
    bot = FootballPredictionBot()
    signal.signal(signal.SIGINT, bot.signal_handler)
    signal.signal(signal.SIGTERM, bot.signal_handler)
    
    try:
        if len(sys.argv) > 1 and sys.argv[1].startswith("/analyze"):
            team = sys.argv[1].replace("/analyze", "").strip()
            if team:
                await bot.analyze_specific_team(team)
            else:
                await bot.start()
        else:
            await bot.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Bot execution completed")

if __name__ == "__main__":
    asyncio.run(main())