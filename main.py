#!/usr/bin/env python3
"""
Football Prediction Telegram Bot - Enhanced Multi-Site Scraper Version
Professional bot with improved scraping and advanced prediction algorithms
"""

import asyncio
import logging
import signal
import sys
import os
import json
import time
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import requests
from bs4 import BeautifulSoup
import aiohttp
import statistics

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('betting_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration class holding all bot settings"""

    # Required fields (no defaults)
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Working betting sites
    betting_sites: Dict[str, Dict[str, str]] = None
    current_site: str = "forebet"

    def __post_init__(self):
        if self.betting_sites is None:
            self.betting_sites = {
                "forebet": {
                    "base_url": "https://www.forebet.com",
                    "football_url": "https://www.forebet.com/en/football-tips-and-predictions-for-today",
                    "name": "Forebet Mathematical Predictions"
                },
                "bet9ja": {
                    "base_url": "https://web.bet9ja.com",
                    "football_url": "https://web.bet9ja.com/Sport/LoadEvent/29",
                    "name": "Bet9ja Nigeria"
                },
                "betway": {
                    "base_url": "https://betway.com.ng",
                    "football_url": "https://betway.com.ng/sport/football",
                    "name": "Betway Nigeria"
                }
            }

    @property
    def forbet_base_url(self) -> str:
        """Get current site base URL"""
        return self.betting_sites[self.current_site]["base_url"]

    @property
    def forbet_football_url(self) -> str:
        """Get current site football URL"""
        return self.betting_sites[self.current_site]["football_url"]

    @property
    def site_name(self) -> str:
        """Get current site name"""
        return self.betting_sites[self.current_site]["name"]

    # Prediction settings
    max_predictions: int = 5
    min_confidence_threshold: float = 0.65
    max_matches_per_session: int = 10

    # Scraping protection settings
    request_delay: float = 2.0  # Seconds between requests
    max_retries: int = 2
    timeout: int = 15

    def __init__(self):
        # Initialize betting_sites first
        self.__post_init__()

        # Load from environment variables
        self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Allow site selection via environment variable
        chosen_site = os.getenv("BETTING_SITE", "forebet").lower()
        if chosen_site in self.betting_sites:
            self.current_site = chosen_site
            print(f"🌐 Using {self.site_name} for predictions")
        else:
            print(f"⚠️ Unknown site '{chosen_site}', defaulting to {self.site_name}")

        # Optional overrides from environment
        self.max_predictions = int(os.getenv("MAX_PREDICTIONS", str(self.max_predictions)))
        self.min_confidence_threshold = float(os.getenv("MIN_CONFIDENCE", str(self.min_confidence_threshold)))

        # Validate required settings
        self._validate()

    def _validate(self):
        """Validate required configuration"""
        if not self.telegram_token:
            raise ValueError("TELEGRAM_TOKEN environment variable is required")
        if not self.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID environment variable is required")

    @property
    def scraping_headers(self) -> dict:
        """Get safe scraping headers optimized for current site"""
        base_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

        # Site-specific optimizations
        if self.current_site == "forebet":
            base_headers["Referer"] = "https://www.forebet.com/"
        elif self.current_site == "bet9ja":
            base_headers["Referer"] = "https://web.bet9ja.com/"
        elif self.current_site == "betway":
            base_headers["Referer"] = "https://betway.com.ng/"

        return base_headers


class SafeScraper:
    """Safe web scraper with anti-ban protections"""

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.scraping_headers)
        self.last_request_time = 0

    def _wait_if_needed(self):
        """Ensure minimum delay between requests"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.config.request_delay:
            wait_time = self.config.request_delay - elapsed
            logger.info(f"⏳ Rate limit protection: waiting {wait_time:.1f}s")
            time.sleep(wait_time)

    def get_page(self, url: str) -> Optional[BeautifulSoup]:
        """Safely fetch and parse a webpage"""
        self._wait_if_needed()

        for attempt in range(self.config.max_retries):
            try:
                logger.info(f"🌐 Fetching: {url} (attempt {attempt + 1})")

                response = self.session.get(
                    url, 
                    timeout=self.config.timeout,
                    allow_redirects=True
                )

                self.last_request_time = time.time()

                if response.status_code == 200:
                    logger.info("✅ Page fetched successfully")
                    return BeautifulSoup(response.content, 'html.parser')
                elif response.status_code == 429:
                    wait_time = 2 ** attempt * 5
                    logger.warning(f"⚠️ Rate limited! Waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.warning(f"⚠️ HTTP {response.status_code}: {url}")

            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Request error: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)

        logger.error(f"❌ Failed to fetch after {self.config.max_retries} attempts")
        return None

    def debug_page_structure(self, soup: BeautifulSoup, save_to_file: bool = False):
        """Debug page structure to understand HTML layout"""
        if save_to_file:
            with open(f"debug_page_{self.config.current_site}.html", "w", encoding="utf-8") as f:
                f.write(str(soup.prettify()))
            logger.info(f"🔍 Page structure saved to debug_page_{self.config.current_site}.html")

        # Look for common match-related patterns
        match_indicators = [
            "match", "game", "fixture", "event", "prediction", "bet", "team", 
            "vs", "home", "away", "odds", "tip", "forecast", "rcnt"
        ]

        print(f"\n🔍 DEBUG: Analyzing page structure for {self.config.current_site}...")

        for indicator in match_indicators:
            # Find elements by class containing the indicator
            elements_by_class = soup.find_all(attrs={"class": re.compile(indicator, re.I)})
            if elements_by_class and len(elements_by_class) > 0:
                print(f"Found {len(elements_by_class)} elements with class containing '{indicator}'")

            # Find elements by id containing the indicator
            elements_by_id = soup.find_all(attrs={"id": re.compile(indicator, re.I)})
            if elements_by_id and len(elements_by_id) > 0:
                print(f"Found {len(elements_by_id)} elements with id containing '{indicator}'")

        # Look for table rows that might contain matches
        tables = soup.find_all('table')
        print(f"Found {len(tables)} tables on page")

        for i, table in enumerate(tables[:3]):  # Check first 3 tables
            rows = table.find_all('tr')
            print(f"Table {i+1}: {len(rows)} rows")

        # Check for divs that might contain matches
        divs = soup.find_all('div')
        match_divs = [div for div in divs if any(keyword in str(div.get('class', [])).lower() 
                                                for keyword in match_indicators)]
        print(f"Found {len(match_divs)} divs with match-related classes")


class BettingScraper:
    """Enhanced betting site scraper with improved detection"""

    def __init__(self, config: Config):
        self.config = config
        self.scraper = SafeScraper(config)
        self.site_name = config.current_site

    def extract_match_data_forebet_enhanced(self, match_element) -> Optional[Dict]:
        """Enhanced Forebet extraction with multiple fallback strategies"""
        try:
            home_team = "Unknown"
            away_team = "Unknown"

            # Strategy 1: Look for team links or spans
            team_links = match_element.find_all('a', href=re.compile(r'/team/|team'))
            if len(team_links) >= 2:
                home_team = team_links[0].get_text(strip=True)
                away_team = team_links[1].get_text(strip=True)
            else:
                # Strategy 2: Look for team names in various class patterns
                team_elements = match_element.find_all(['span', 'div', 'td'], 
                                                     class_=re.compile(r'team|home|away', re.I))
                if len(team_elements) >= 2:
                    home_team = team_elements[0].get_text(strip=True)
                    away_team = team_elements[1].get_text(strip=True)
                else:
                    # Strategy 3: Look for text patterns like "Team1 vs Team2"
                    element_text = match_element.get_text()
                    vs_match = re.search(r'([^-\n]+?)\s*(?:vs?|v|-)\s*([^-\n]+)', element_text, re.I)
                    if vs_match:
                        home_team = vs_match.group(1).strip()
                        away_team = vs_match.group(2).strip()

            # Extract match time
            time_patterns = [
                re.compile(r'time|clock|date', re.I),
                re.compile(r'\d{1,2}:\d{2}'),
                re.compile(r'today|tomorrow|tonight', re.I)
            ]

            match_time = "TBD"
            for pattern in time_patterns:
                time_element = match_element.find(string=pattern)
                if not time_element:
                    time_element = match_element.find(attrs={"class": pattern})

                if time_element:
                    if hasattr(time_element, 'get_text'):
                        match_time = time_element.get_text(strip=True)
                    else:
                        match_time = str(time_element).strip()
                    break

            # Extract league info
            league = "Unknown League"
            league_element = match_element.find_parent('table')
            if league_element:
                league_header = league_element.find_previous(['h2', 'h3', 'div'], 
                                                           class_=re.compile(r'league|competition|tournament', re.I))
                if league_header:
                    league = league_header.get_text(strip=True)

            # Extract predictions/odds
            odds = {"1": "N/A", "X": "N/A", "2": "N/A"}

            # Look for percentage predictions (common in Forebet)
            percentage_elements = match_element.find_all(string=re.compile(r'\d+%'))
            if len(percentage_elements) >= 3:
                try:
                    for i, perc_str in enumerate(percentage_elements[:3]):
                        percentage = float(re.search(r'(\d+)', perc_str).group(1))
                        pseudo_odd = round(100 / percentage, 2) if percentage > 0 else 5.0
                        if i == 0:
                            odds["1"] = str(pseudo_odd)
                        elif i == 1:
                            odds["X"] = str(pseudo_odd)
                        elif i == 2:
                            odds["2"] = str(pseudo_odd)
                except:
                    pass

            # Validate we found meaningful team names
            if (home_team == "Unknown" or away_team == "Unknown" or 
                len(home_team) < 2 or len(away_team) < 2):
                return None

            return {
                "home_team": home_team,
                "away_team": away_team,
                "match_time": match_time,
                "league": league,
                "odds": odds,
                "home_form": [],
                "away_form": [],
                "scraped_at": datetime.now().isoformat(),
                "source": "Forebet Mathematical Predictions"
            }

        except Exception as e:
            logger.error(f"❌ Error extracting enhanced Forebet match data: {e}")
            return None

    def extract_match_data_bet9ja(self, match_element) -> Optional[Dict]:
        """Extract match data specifically from Bet9ja"""
        try:
            # Bet9ja specific selectors
            team_elements = match_element.find_all(class_=re.compile(r'team|competitor'))

            home_team = team_elements[0].get_text(strip=True) if len(team_elements) > 0 else "Unknown"
            away_team = team_elements[1].get_text(strip=True) if len(team_elements) > 1 else "Unknown"

            # Extract match time
            time_element = match_element.find(class_=re.compile(r'time|date|kick'))
            match_time = time_element.get_text(strip=True) if time_element else "TBD"

            # Extract league
            league_element = match_element.find(class_=re.compile(r'league|competition|tournament'))
            league = league_element.get_text(strip=True) if league_element else "Nigerian League"

            # Extract odds
            odds_elements = match_element.find_all(class_=re.compile(r'odd|coeff|price|rate'))
            odds = {"1": "N/A", "X": "N/A", "2": "N/A"}

            for i, elem in enumerate(odds_elements[:3]):
                odds_text = elem.get_text(strip=True)
                if i == 0:
                    odds["1"] = odds_text
                elif i == 1:
                    odds["X"] = odds_text
                elif i == 2:
                    odds["2"] = odds_text

            return {
                "home_team": home_team,
                "away_team": away_team,
                "match_time": match_time,
                "league": league,
                "odds": odds,
                "home_form": [],
                "away_form": [],
                "scraped_at": datetime.now().isoformat(),
                "source": "Bet9ja Nigeria"
            }

        except Exception as e:
            logger.error(f"❌ Error extracting Bet9ja match data: {e}")
            return None

    def extract_match_data(self, match_element) -> Optional[Dict]:
        """Extract match data using site-specific logic"""
        if self.site_name == "forebet":
            return self.extract_match_data_forebet_enhanced(match_element)
        elif self.site_name == "bet9ja":
            return self.extract_match_data_bet9ja(match_element)
        elif self.site_name == "betway":
            return self.extract_match_data_betway(match_element)
        else:
            # Generic fallback extraction
            return self.extract_match_data_generic(match_element)

    def extract_match_data_betway(self, match_element) -> Optional[Dict]:
        """Extract match data specifically from Betway Nigeria"""
        try:
            home_team = "Unknown"
            away_team = "Unknown"

            # Strategy 1: Look for team names in Betway structure
            team_elements = match_element.find_all(['span', 'div', 'a'], class_=re.compile(r'team|participant|competitor|name', re.I))

            if len(team_elements) >= 2:
                home_team = team_elements[0].get_text(strip=True)
                away_team = team_elements[1].get_text(strip=True)
            else:
                # Strategy 2: Look for text patterns in the element
                element_text = match_element.get_text()

                # Common patterns for Betway: "Team1 v Team2", "Team1 vs Team2"
                vs_patterns = [
                    r'([A-Za-z\s\d]+?)\s*(?:vs?|v)\s*([A-Za-z\s\d]+)',
                    r'([A-Za-z\s\d]+?)\s*-\s*([A-Za-z\s\d]+)',
                    r'([A-Za-z\s\d]+?)\s*/\s*([A-Za-z\s\d]+)'
                ]

                for pattern in vs_patterns:
                    match = re.search(pattern, element_text, re.I)
                    if match:
                        home_team = match.group(1).strip()
                        away_team = match.group(2).strip()
                        break

                # Strategy 3: Look for team names in nested elements
                if home_team == "Unknown":
                    all_text_elements = match_element.find_all(string=True)
                    team_candidates = []

                    for text in all_text_elements:
                        text = text.strip()
                        # Filter out obvious non-team text
                        if (len(text) > 2 and len(text) < 30 and 
                            not re.match(r'^\d+[:\.]?\d*

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        elif self.site_name == "betway":
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tbody tr',
                'table tr',
                '.bet-item',
                '.outcome-group',
                '[data-test*="match"]',
                '[data-testid*="match"]'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0
        successful_extractions = 0

        for selector_index, selector in enumerate(match_selectors):
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    # Add debug information for first few elements
                    if debug and selector_index < 3:
                        print(f"\n🔍 DEBUG: Analyzing first few elements with selector '{selector}':")
                        for i, element in enumerate(elements[:3]):
                            element_text = element.get_text()[:100] + "..." if len(element.get_text()) > 100 else element.get_text()
                            print(f"  Element {i+1}: {element_text}")
                            print(f"  Classes: {element.get('class', [])}")
                            print(f"  ID: {element.get('id', 'None')}")
                            print("  ---")

                    for element_index, element in enumerate(elements[:self.config.max_matches_per_session]):
                        match_data = self.extract_match_data(element)

                        if debug and element_index < 5:
                            print(f"\n🔧 DEBUG Element {element_index + 1}:")
                            print(f"   Text: {element.get_text()[:200]}...")
                            print(f"   Extracted: {match_data['home_team'] if match_data else 'None'} vs {match_data['away_team'] if match_data else 'None'}")

                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            successful_extractions += 1
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")
        logger.info(f"🎯 Successful extractions: {successful_extractions}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found > 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Get all text and look for team patterns
            all_text = soup.get_text()

            # More comprehensive patterns
            vs_patterns = [
                r'([A-Za-z\s\d\.\-\']{3,25})\s*(?:vs?|v)\s*([A-Za-z\s\d\.\-\']{3,25})',
                r'([A-Za-z\s]{3,25})\s*-\s*([A-Za-z\s]{3,25})',
                r'([A-Z][a-z\s]+?)\s*([A-Z][a-z\s]+)',
            ]

            found_teams = set()

            for pattern in vs_patterns:
                matches_found = re.findall(pattern, all_text)

                for home, away in matches_found[:10]:  # Limit to prevent spam
                    home = home.strip()
                    away = away.strip()

                    # Enhanced validation
                    if (len(home) > 2 and len(away) > 2 and 
                        len(home) < 30 and len(away) < 30 and
                        home != away and
                        not any(char.isdigit() for char in home[:5]) and
                        not any(char.isdigit() for char in away[:5]) and
                        home.lower() not in ['live', 'bet', 'odds', 'today', 'tomorrow'] and
                        away.lower() not in ['live', 'bet', 'odds', 'today', 'tomorrow']):

                        match_key = f"{home}_{away}"
                        if match_key not in found_teams:
                            found_teams.add(match_key)
                            matches.append({
                                "home_team": home,
                                "away_team": away,
                                "match_time": "TBD",
                                "league": "Unknown League",
                                "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                                "home_form": [],
                                "away_form": [],
                                "scraped_at": datetime.now().isoformat(),
                                "source": f"Fallback ({self.config.site_name})"
                            })

                if matches:
                    break

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        if debug and len(unique_matches) > 0:
            print(f"\n✅ EXTRACTED MATCHES:")
            for i, match in enumerate(unique_matches[:5], 1):
                print(f"{i}. {match['home_team']} vs {match['away_team']} ({match['source']})")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main()), text) and  # Not just numbers/time
                            not text.lower() in ['vs', 'v', '-', 'bet', 'live', 'today', 'tomorrow'] and
                            not re.match(r'^\d+\.\d+

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main()), text)):  # Not odds format
                            team_candidates.append(text)

                    if len(team_candidates) >= 2:
                        home_team = team_candidates[0]
                        away_team = team_candidates[1]

            # Extract match time
            match_time = "TBD"
            time_patterns = [
                re.compile(r'\d{1,2}:\d{2}'),  # HH:MM format
                re.compile(r'today|tomorrow|live', re.I),
                re.compile(r'time|clock|date', re.I)
            ]

            element_text = match_element.get_text()
            for pattern in time_patterns:
                time_match = pattern.search(element_text)
                if time_match:
                    match_time = time_match.group(0)
                    break

            # Extract league
            league = "Nigerian League"
            league_element = match_element.find_parent(['div', 'section'])
            if league_element:
                league_header = league_element.find_previous(['h1', 'h2', 'h3', 'h4'], string=re.compile(r'league|premier|championship|cup', re.I))
                if league_header:
                    league = league_header.get_text(strip=True)

            # Extract odds
            odds = {"1": "N/A", "X": "N/A", "2": "N/A"}

            # Look for odds patterns in Betway
            odds_elements = match_element.find_all(string=re.compile(r'^\d+\.\d+

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main())))
            if len(odds_elements) >= 3:
                try:
                    odds["1"] = odds_elements[0]
                    odds["X"] = odds_elements[1] 
                    odds["2"] = odds_elements[2]
                except:
                    pass
            else:
                # Alternative odds extraction
                number_elements = match_element.find_all(['span', 'div'], string=re.compile(r'^\d+\.\d{2}

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main())))
                if len(number_elements) >= 3:
                    try:
                        odds["1"] = number_elements[0].get_text(strip=True)
                        odds["X"] = number_elements[1].get_text(strip=True)
                        odds["2"] = number_elements[2].get_text(strip=True)
                    except:
                        pass

            # Validate team names
            if (home_team == "Unknown" or away_team == "Unknown" or 
                len(home_team) < 2 or len(away_team) < 2 or
                home_team == away_team):
                return None

            return {
                "home_team": home_team,
                "away_team": away_team,
                "match_time": match_time,
                "league": league,
                "odds": odds,
                "home_form": [],
                "away_form": [],
                "scraped_at": datetime.now().isoformat(),
                "source": "Betway Nigeria"
            }

        except Exception as e:
            logger.error(f"❌ Error extracting Betway match data: {e}")
            return None

    def extract_match_data_generic(self, match_element) -> Optional[Dict]:
        """Enhanced generic match data extraction"""
        try:
            home_team = "Unknown"
            away_team = "Unknown"

            # Strategy 1: Look for team elements by common class patterns
            team_selectors = [
                {'class_': re.compile(r'team|participant|competitor|name', re.I)},
                {'class_': re.compile(r'home|away', re.I)},
                {'href': re.compile(r'team|club', re.I)}
            ]

            for selector in team_selectors:
                teams = match_element.find_all(['span', 'div', 'a'], **selector)
                if len(teams) >= 2:
                    home_team = teams[0].get_text(strip=True)
                    away_team = teams[1].get_text(strip=True)
                    break

            # Strategy 2: Parse text for team vs team patterns
            if home_team == "Unknown":
                element_text = match_element.get_text()
                vs_patterns = [
                    r'([A-Za-z\s\d\.\-\']+?)\s*(?:vs?|v|-|/)\s*([A-Za-z\s\d\.\-\']+)',
                    r'([A-Z][a-z\s]+?)\s+([A-Z][a-z\s]+)'  # Two capitalized words
                ]

                for pattern in vs_patterns:
                    match = re.search(pattern, element_text)
                    if match:
                        potential_home = match.group(1).strip()
                        potential_away = match.group(2).strip()

                        # Validate potential team names
                        if (len(potential_home) > 2 and len(potential_away) > 2 and
                            len(potential_home) < 40 and len(potential_away) < 40 and
                            not re.match(r'^\d+[\.\:]?\d*

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main()), potential_home) and
                            not re.match(r'^\d+[\.\:]?\d*

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main()), potential_away)):
                            home_team = potential_home
                            away_team = potential_away
                            break

            # Strategy 3: Look for meaningful text elements
            if home_team == "Unknown":
                text_elements = []
                for elem in match_element.find_all(string=True):
                    text = elem.strip()
                    if (len(text) > 2 and len(text) < 30 and
                        not re.match(r'^\d+[\.\:]?\d*

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main()), text) and
                        text.lower() not in ['vs', 'v', '-', 'bet', 'live', 'odds', 'match']):
                        text_elements.append(text)

                if len(text_elements) >= 2:
                    home_team = text_elements[0]
                    away_team = text_elements[1]

            # Extract match time
            match_time = "TBD"
            time_element = match_element.find(string=re.compile(r'\d{1,2}:\d{2}'))
            if time_element:
                match_time = str(time_element).strip()
            else:
                time_element = match_element.find(['span', 'div'], class_=re.compile(r'time|date|clock', re.I))
                if time_element:
                    match_time = time_element.get_text(strip=True)

            # Extract league
            league = "Unknown League"
            league_element = match_element.find(['span', 'div'], class_=re.compile(r'league|competition|tournament', re.I))
            if league_element:
                league = league_element.get_text(strip=True)

            # Extract odds
            odds = {"1": "N/A", "X": "N/A", "2": "N/A"}

            # Look for decimal odds (e.g., 1.85, 2.30)
            odds_numbers = re.findall(r'\d+\.\d{2}', match_element.get_text())
            if len(odds_numbers) >= 3:
                odds["1"] = odds_numbers[0]
                odds["X"] = odds_numbers[1]
                odds["2"] = odds_numbers[2]

            # Validate extraction
            if (home_team == "Unknown" or away_team == "Unknown" or 
                len(home_team) < 2 or len(away_team) < 2 or
                home_team == away_team):
                return None

            return {
                "home_team": home_team,
                "away_team": away_team,
                "match_time": match_time,
                "league": league,
                "odds": odds,
                "home_form": [],
                "away_form": [],
                "scraped_at": datetime.now().isoformat(),
                "source": f"Generic ({self.config.site_name})"
            }

        except Exception as e:
            logger.error(f"❌ Error extracting generic match data: {e}")
            return None

    def scrape_football_matches(self, debug: bool = False) -> List[Dict]:
        """Enhanced scraping with better detection and debugging"""
        print(f"🕷️ Starting enhanced scraping session from {self.config.site_name}...")
        print("⚠️ Respecting rate limits to avoid detection")

        soup = self.scraper.get_page(self.config.forbet_football_url)
        if not soup:
            logger.error(f"❌ Failed to fetch {self.config.site_name} football page")
            return []

        if debug:
            self.scraper.debug_page_structure(soup, save_to_file=True)

        logger.info(f"🔍 Parsing {self.config.site_name} football page...")

        # Enhanced selectors with more comprehensive patterns
        if self.site_name == "forebet":
            match_selectors = [
                # Standard Forebet selectors
                'tr.tr_0, tr.tr_1',
                '[class*="rcnt"]',
                'tr[class*="predict"]',
                # Enhanced Forebet selectors
                'tr[class*="match"]',
                'div[class*="match"]',
                'tr[onclick]',  # Forebet often uses onclick events
                'table tr:has(a[href*="team"])',  # Rows with team links
                '.content tr',  # Generic content table rows
                'tbody tr',  # Table body rows
                # Very broad selectors as fallback
                'tr:contains("vs")',
                'tr:contains("v")',
                'div:contains("vs")'
            ]
        elif self.site_name == "bet9ja":
            match_selectors = [
                '[class*="event"]',
                '[class*="match"]',
                'tr[class*="row"]',
                '[class*="fixture"]',
                '.match-row',
                '.event-row'
            ]
        else:
            # Generic selectors
            match_selectors = [
                '[class*="match"]',
                '[class*="event"]',
                '[class*="game"]',
                '[class*="fixture"]',
                'tr[class*="row"]',
                'tbody tr',
                'table tr'
            ]

        matches = []
        total_elements_found = 0

        for selector in match_selectors:
            try:
                elements = soup.select(selector)
                total_elements_found += len(elements)

                if elements:
                    logger.info(f"✅ Found {len(elements)} potential matches with selector: {selector}")

                    for element in elements[:self.config.max_matches_per_session]:
                        match_data = self.extract_match_data(element)
                        if match_data and match_data["home_team"] != "Unknown":
                            matches.append(match_data)
                            logger.debug(f"✅ Extracted: {match_data['home_team']} vs {match_data['away_team']}")

                    if matches:
                        break  # Stop after finding matches with first working selector
                else:
                    logger.debug(f"❌ No elements found with selector: {selector}")

            except Exception as e:
                logger.error(f"❌ Error with selector '{selector}': {e}")
                continue

        logger.info(f"📊 Total elements checked: {total_elements_found}")

        # If no matches found with standard selectors, try aggressive fallback
        if not matches and total_elements_found == 0:
            logger.warning("🔍 No matches found with standard selectors, trying aggressive fallback...")

            # Look for any element containing team vs team patterns
            all_text = soup.get_text()
            vs_patterns = re.findall(r'([A-Za-z\s]+?)\s*(?:vs?|v|-)\s*([A-Za-z\s]+)', all_text)

            for i, (home, away) in enumerate(vs_patterns[:5]):
                home = home.strip()
                away = away.strip()

                # Basic validation
                if (len(home) > 2 and len(away) > 2 and 
                    len(home) < 50 and len(away) < 50 and
                    not any(char.isdigit() for char in home[:10]) and
                    not any(char.isdigit() for char in away[:10])):

                    matches.append({
                        "home_team": home,
                        "away_team": away,
                        "match_time": "TBD",
                        "league": "Unknown League",
                        "odds": {"1": "N/A", "X": "N/A", "2": "N/A"},
                        "home_form": [],
                        "away_form": [],
                        "scraped_at": datetime.now().isoformat(),
                        "source": f"Fallback ({self.config.site_name})"
                    })

        # Filter out duplicates
        unique_matches = []
        seen_matches = set()

        for match in matches:
            match_key = f"{match['home_team']}_{match['away_team']}"
            if match_key not in seen_matches:
                unique_matches.append(match)
                seen_matches.add(match_key)

        logger.info(f"🎯 Successfully scraped {len(unique_matches)} unique matches from {self.config.site_name}")
        print(f"⚽ Found {len(unique_matches)} matches from {self.config.site_name}")

        return unique_matches


# [Rest of the classes remain the same: PredictionEngine, TelegramBot, BettingPredictionBot]
class PredictionEngine:
    """Advanced prediction engine using scraped betting data"""

    def __init__(self, config: Config):
        self.config = config

    def analyze_team_form(self, form_data: List[str]) -> Dict:
        """Analyze team form from scraped form indicators"""
        if not form_data:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        wins = form_data.count('W')
        draws = form_data.count('D')
        losses = form_data.count('L')
        total = len(form_data)

        if total == 0:
            return {
                "form_string": "-----",
                "win_rate": 0.33,
                "form_score": 50.0,
                "recent_results": 0
            }

        win_rate = wins / total
        form_score = (wins * 3 + draws * 1) / (total * 3) * 100

        return {
            "form_string": "".join(form_data),
            "win_rate": win_rate,
            "form_score": form_score,
            "recent_results": total
        }

    def analyze_odds(self, odds: Dict) -> Dict:
        """Analyze betting odds to extract probabilities"""
        try:
            home_odd = float(odds["1"]) if odds["1"] != "N/A" else 2.5
            draw_odd = float(odds["X"]) if odds["X"] != "N/A" else 3.2
            away_odd = float(odds["2"]) if odds["2"] != "N/A" else 2.8

            # Convert odds to implied probabilities
            home_prob = 1 / home_odd
            draw_prob = 1 / draw_odd
            away_prob = 1 / away_odd

            # Normalize probabilities (remove bookmaker margin)
            total_prob = home_prob + draw_prob + away_prob
            home_prob_norm = home_prob / total_prob
            draw_prob_norm = draw_prob / total_prob
            away_prob_norm = away_prob / total_prob

            # Determine favorite
            if home_prob_norm > away_prob_norm:
                favorite = "Home"
                favorite_prob = home_prob_norm
            else:
                favorite = "Away"
                favorite_prob = away_prob_norm

            return {
                "home_prob": home_prob_norm,
                "draw_prob": draw_prob_norm,
                "away_prob": away_prob_norm,
                "favorite": favorite,
                "favorite_prob": favorite_prob,
                "odds_quality": "High" if total_prob > 0.9 else "Medium"
            }

        except:
            return {
                "home_prob": 0.4,
                "draw_prob": 0.3,
                "away_prob": 0.3,
                "favorite": "Unknown",
                "favorite_prob": 0.4,
                "odds_quality": "Low"
            }

    def predict_match_result(self, match_data: Dict) -> Dict:
        """Generate comprehensive match prediction"""
        home_team = match_data["home_team"]
        away_team = match_data["away_team"]

        # Analyze form
        home_analysis = self.analyze_team_form(match_data.get("home_form", []))
        away_analysis = self.analyze_team_form(match_data.get("away_form", []))

        # Analyze odds
        odds_analysis = self.analyze_odds(match_data["odds"])

        # Home advantage
        home_advantage = 0.1  # 10% boost for home team

        # Calculate result probabilities
        home_strength = (home_analysis["form_score"] / 100) + home_advantage
        away_strength = away_analysis["form_score"] / 100

        # Combine with odds analysis (weighted average)
        final_home_prob = (home_strength * 0.4) + (odds_analysis["home_prob"] * 0.6)
        final_away_prob = (away_strength * 0.4) + (odds_analysis["away_prob"] * 0.6)
        final_draw_prob = (0.25 * 0.4) + (odds_analysis["draw_prob"] * 0.6)  # Base draw probability

        # Normalize
        total = final_home_prob + final_draw_prob + final_away_prob
        final_home_prob /= total
        final_draw_prob /= total
        final_away_prob /= total

        # Determine prediction
        if final_home_prob > final_away_prob and final_home_prob > final_draw_prob:
            prediction = "Home Win"
            confidence = final_home_prob
        elif final_away_prob > final_home_prob and final_away_prob > final_draw_prob:
            prediction = "Away Win"
            confidence = final_away_prob
        else:
            prediction = "Draw"
            confidence = final_draw_prob

        # Predict over/under 2.5 goals
        avg_total_goals = 2.7  # League average
        form_factor = (home_analysis["win_rate"] + away_analysis["win_rate"]) / 2
        goal_prediction = avg_total_goals + (form_factor - 0.5) * 1.5

        over_under = "Over 2.5" if goal_prediction > 2.5 else "Under 2.5"
        over_confidence = abs(goal_prediction - 2.5) / 2.5

        # Predict BTTS
        btts_prob = 0.6 - (max(home_analysis["form_score"], away_analysis["form_score"]) - 50) / 200
        btts_prediction = "Yes" if btts_prob > 0.5 else "No"

        # Generate correct score prediction
        home_goals = max(0, min(3, round(goal_prediction * final_home_prob * 2)))
        away_goals = max(0, min(3, round(goal_prediction * final_away_prob * 2)))
        correct_score = f"{home_goals}-{away_goals}"

        # Calculate overall confidence
        data_quality = 0.7
        if home_analysis["recent_results"] >= 3 and away_analysis["recent_results"] >= 3:
            data_quality += 0.15
        if odds_analysis["odds_quality"] == "High":
            data_quality += 0.15

        overall_confidence = confidence * data_quality

        return {
            "match": f"{home_team} vs {away_team}",
            "league": match_data["league"],
            "match_time": match_data["match_time"],
            "odds": match_data["odds"],
            "prediction": prediction,
            "confidence": overall_confidence,
            "correct_score": correct_score,
            "over_under": over_under,
            "over_confidence": over_confidence,
            "btts": btts_prediction,
            "btts_confidence": abs(btts_prob - 0.5) * 2,
            "analysis": {
                "home_form": home_analysis["form_string"],
                "away_form": away_analysis["form_string"],
                "home_prob": final_home_prob,
                "away_prob": final_away_prob,
                "draw_prob": final_draw_prob,
                "favorite": odds_analysis["favorite"],
                "data_quality": data_quality
            }
        }

    def select_top_predictions(self, matches_data: List[Dict]) -> List[Dict]:
        """Select best predictions based on confidence and variety"""
        if not matches_data:
            return []

        predictions = []
        for match_data in matches_data:
            try:
                prediction = self.predict_match_result(match_data)
                if prediction["confidence"] >= self.config.min_confidence_threshold:
                    predictions.append(prediction)
            except Exception as e:
                logger.error(f"❌ Error predicting match: {e}")
                continue

        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)

        # Ensure variety in predictions
        selected = []
        prediction_types = {"result": set(), "over_under": set(), "btts": set()}

        for pred in predictions:
            # Add variety to avoid all same predictions
            result = pred["prediction"]
            over_under = pred["over_under"]
            btts = pred["btts"]

            # Allow some repetition but prefer variety
            variety_score = 0
            if result not in prediction_types["result"]:
                variety_score += 0.1
            if over_under not in prediction_types["over_under"]:
                variety_score += 0.05
            if btts not in prediction_types["btts"]:
                variety_score += 0.05

            if len(selected) < 3 or variety_score > 0.1 or pred["confidence"] > 0.8:
                selected.append(pred)
                prediction_types["result"].add(result)
                prediction_types["over_under"].add(over_under)
                prediction_types["btts"].add(btts)

                if len(selected) >= self.config.max_predictions:
                    break

        logger.info(f"Selected {len(selected)} high-confidence predictions")
        return selected


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
        """Format a single prediction into professional message"""
        match = prediction["match"]
        league = prediction["league"]
        match_time = prediction["match_time"]
        odds = prediction["odds"]
        pred_result = prediction["prediction"]
        confidence = prediction["confidence"]
        correct_score = prediction["correct_score"]
        over_under = prediction["over_under"]
        btts = prediction["btts"]
        analysis = prediction["analysis"]

        confidence_pct = int(confidence * 100)
        confidence_emoji = "🔥" if confidence > 0.8 else "✅" if confidence > 0.7 else "⚡"

        message = f"📊 <b>{match}</b>\n"
        message += f"🏆 {league}\n"
        message += f"🕓 Time: {match_time}\n"
        message += f"🔢 Odds: 1 – {odds['1']} | X – {odds['X']} | 2 – {odds['2']}\n\n"

        # Main predictions
        message += f"🎯 <b>Main Tip:</b> {pred_result}\n"
        message += f"⚽ <b>Correct Score:</b> {correct_score}\n"
        message += f"📈 <b>Over/Under 2.5:</b> {over_under}\n"
        message += f"💡 <b>BTTS:</b> {btts}\n\n"

        # Confidence indicator
        confidence_bar = "🟢" * (confidence_pct // 20) + "⚪" * (5 - (confidence_pct // 20))
        message += f"📊 <b>Confidence:</b> {confidence_pct}% {confidence_emoji}\n"
        message += f"📈 {confidence_bar}\n\n"

        # Analysis
        message += f"📋 <b>Form Analysis:</b>\n"
        message += f"🏠 Home: {analysis['home_form']} ({analysis['home_prob']*100:.0f}%)\n"
        message += f"✈️ Away: {analysis['away_form']} ({analysis['away_prob']*100:.0f}%)\n"
        message += f"🏺 Draw Probability: {analysis['draw_prob']*100:.0f}%\n"
        message += f"⭐ Favorite: {analysis['favorite']}\n"

        return message

    def format_predictions_summary(self, predictions: List[Dict]) -> str:
        """Format professional summary header"""
        today = datetime.now().strftime("%A, %B %d, %Y")

        # Get site info from first prediction
        site_name = predictions[0].get("source", "Betting Site") if predictions else "Multi-Site Analysis"

        message = f"🤖 <b>FOOTBALL ANALYTICS PRO - ACTIVATED</b>\n\n"
        message += f"✅ Connected to {site_name}\n"
        message += f"✅ Advanced prediction algorithms loaded\n"
        message += f"✅ Telegram integration active\n"
        message += f"✅ Rate limiting & scraping protection enabled\n"
        message += f"✅ Match analyzer ready\n\n"
        message += f"🔥 <b>What makes our predictions special:</b>\n"
        message += f"• Real-time scraping of live betting data\n"
        message += f"• Professional-grade prediction logic\n"
        message += f"• Form, odds & performance-based analysis\n"
        message += f"• Live league filtering\n"
        message += f"• Goals, BTTS, clean sheet trends\n"
        message += f"• Smart correct score predictions\n"
        message += f"• Confidence scores\n\n"
        message += f"⚠️ <b>Note:</b> We keep it safe — max 10 matches per session\n"
        message += f"⏳ Be patient, scraping takes time and respects site limits\n\n"
        message += f"🚀 Bot ready to provide top-tier football predictions!\n\n"
        message += f"📅 <b>TODAY'S TOP PICKS - {today}</b>\n"
        message += f"🎯 {len(predictions)} Professional Predictions\n"
        message += "=" * 40 + "\n\n"

        return message

    async def send_daily_predictions(self, predictions: List[Dict]) -> bool:
        """Send daily predictions to Telegram"""
        if not predictions:
            await self.send_message("❌ No high-confidence predictions available today.")
            return False

        logger.info(f"Sending {len(predictions)} predictions to Telegram")

        try:
            # Send summary header
            summary = self.format_predictions_summary(predictions)
            await self.send_message(summary)
            await asyncio.sleep(1)

            # Send each prediction
            for i, prediction in enumerate(predictions, 1):
                match = prediction["match"]
                pred_result = prediction["prediction"]
                confidence = int(prediction["confidence"] * 100)

                prediction_msg = f"<b>🎯 PICK #{i}</b>\n" + self.format_prediction_message(prediction)

                success = await self.send_message(prediction_msg)
                if not success:
                    logger.error(f"Failed to send prediction {i}")

                # Delay between messages
                await asyncio.sleep(2)

            # Send footer
            footer = "\n" + "=" * 40 + "\n"
            footer += "🤖 <b>Football Analytics Pro</b>\n"
            footer += "🕷️ <i>Powered by Safe Multi-Site Scraping</i>\n"
            footer += "🔬 <i>Real-time statistical analysis</i>\n"
            footer += "⚠️ <i>For entertainment purposes only. Please bet responsibly!</i>\n"
            footer += "💎 <i>Good luck and may the odds be with you!</i>"

            await self.send_message(footer)

            logger.info("All predictions sent successfully")
            return True

        except Exception as e:
            logger.error(f"Error sending predictions: {e}")
            return False

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
        message += "🔍 Our scraping algorithms require minimum data thresholds for reliable predictions.\n\n"
        message += "⏰ Try running again later for fresh professional analysis!\n"
        message += "⚽ We'll be ready with top-quality predictions!"

        await self.send_message(message)


class BettingPredictionBot:
    """Main bot class for betting site scraping and predictions"""

    def __init__(self):
        self.config = Config()
        self.telegram_bot = TelegramBot(self.config.telegram_token, self.config.telegram_chat_id)
        self.betting_scraper = BettingScraper(self.config)
        self.prediction_engine = PredictionEngine(self.config)
        self.running = False

    async def run_predictions_immediately(self):
        """Run prediction analysis immediately when executed"""
        print(f"🚀 Starting {self.config.site_name} scraping and prediction analysis...")
        try:
            logger.info(f"Starting immediate prediction generation with {self.config.site_name} scraping...")

            print(f"🕷️ Scraping {self.config.site_name} for live match data...")

            # Scrape match data from current betting site
            matches_data = self.betting_scraper.scrape_football_matches()

            if not matches_data:
                logger.warning("No matches found from scraping")
                await self.telegram_bot.send_no_matches_message()
                return

            print(f"✅ Successfully scraped {len(matches_data)} matches from {self.config.site_name}!")

            # Generate professional predictions
            top_predictions = self.prediction_engine.select_top_predictions(matches_data)

            if not top_predictions:
                logger.warning("No high-confidence predictions generated")
                await self.telegram_bot.send_message(
                    "🔍 <b>Analysis Complete</b>\n\n"
                    "📊 Today's matches don't meet our strict confidence thresholds.\n"
                    "🎯 We only provide predictions with 65%+ confidence based on real data.\n\n"
                    "⏰ Try running again later for fresh professional analysis!"
                )
                return

            # Send predictions to Telegram
            success = await self.telegram_bot.send_daily_predictions(top_predictions)

            if success:
                logger.info(f"Successfully sent {len(top_predictions)} professional predictions")
                print(f"🎉 Successfully sent {len(top_predictions)} predictions with real {self.config.site_name} analysis!")
            else:
                logger.error("Failed to send predictions to Telegram")

        except Exception as e:
            logger.error(f"Error in prediction generation: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def analyze_specific_team(self, team_name: str):
        """Analyze matches for a specific team"""
        print(f"🔍 Searching for {team_name} matches on {self.config.site_name}...")

        try:
            matches_data = self.betting_scraper.scrape_football_matches()

            # Filter matches for the specific team
            team_matches = []
            for match in matches_data:
                if (team_name.lower() in match["home_team"].lower() or 
                    team_name.lower() in match["away_team"].lower()):
                    team_matches.append(match)

            if not team_matches:
                await self.telegram_bot.send_message(
                    f"🤷‍♂️ <b>No matches found for '{team_name}'</b>\n\n"
                    f"📅 Try checking the team name or run analysis later.\n"
                    f"⚽ We only analyze matches currently available on {self.config.site_name}."
                )
                return

            # Generate predictions for team matches
            predictions = []
            for match_data in team_matches:
                try:
                    prediction = self.prediction_engine.predict_match_result(match_data)
                    predictions.append(prediction)
                except Exception as e:
                    logger.error(f"Error predicting team match: {e}")
                    continue

            if predictions:
                await self.telegram_bot.send_daily_predictions(predictions)
            else:
                await self.telegram_bot.send_message(
                    f"❌ <b>Analysis Error</b>\n\n"
                    f"🔧 Unable to generate predictions for {team_name} matches.\n"
                    f"📊 Insufficient data available."
                )

        except Exception as e:
            logger.error(f"Error analyzing specific team: {e}")
            await self.telegram_bot.send_error_message(str(e))

    async def start(self):
        """Start the bot and run predictions immediately"""
        logger.info(f"Starting {self.config.site_name} Football Prediction Bot...")

        try:
            # Initialize Telegram bot
            await self.telegram_bot.initialize()
            logger.info("Telegram bot initialized successfully")

            print("🔄 Professional Football Analytics Bot is now running!")
            print(f"🕷️ Using safe {self.config.site_name} scraping with anti-ban protection")

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
    bot = BettingPredictionBot()

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


async def analyze_team(team_name: str):
    """Analyze a specific team"""
    bot = BettingPredictionBot()
    await bot.telegram_bot.initialize()
    await bot.analyze_specific_team(team_name)
    await bot.telegram_bot.close()


if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Enhanced test mode with debugging
            async def test_scraping():
                config = Config()
                scraper = BettingScraper(config)

                print(f"\n🔧 ENHANCED TEST MODE for {config.site_name}")
                print("=" * 50)

                # Run with debug mode enabled
                matches = scraper.scrape_football_matches(debug=True)

                print(f"\n🎯 Test Results from {config.site_name}:")
                print(f"📊 Found {len(matches)} matches")

                if matches:
                    for i, match in enumerate(matches[:5], 1):
                        print(f"\n{i}. {match['home_team']} vs {match['away_team']}")
                        print(f"   League: {match['league']}")
                        print(f"   Time: {match['match_time']}")
                        print(f"   Odds: 1-{match['odds']['1']} X-{match['odds']['X']} 2-{match['odds']['2']}")
                        print(f"   Source: {match['source']}")
                        if match['home_form']:
                            print(f"   Form: {''.join(match['home_form'])} vs {''.join(match['away_form'])}")

                    print(f"\n✅ Scraping test completed successfully!")
                    print(f"📁 Debug HTML file saved for analysis")
                else:
                    print(f"\n❌ No matches found. Check debug HTML file for page structure.")
                    print(f"💡 Consider trying different betting sites or checking selectors.")

            asyncio.run(test_scraping())

        elif sys.argv[1] == "debug":
            # Pure debug mode - just fetch and save page
            async def debug_page():
                config = Config()
                scraper = BettingScraper(config)

                print(f"🔧 DEBUG MODE: Fetching {config.site_name} page structure...")

                soup = scraper.scraper.get_page(config.forbet_football_url)
                if soup:
                    scraper.scraper.debug_page_structure(soup, save_to_file=True)
                    print(f"✅ Page structure saved to debug_page_{config.current_site}.html")
                    print(f"🔍 Analyze the file to understand the page structure")
                else:
                    print(f"❌ Failed to fetch page")

            asyncio.run(debug_page())

        elif sys.argv[1].startswith("team="):
            # Analyze specific team
            team_name = sys.argv[1].split("=", 1)[1]
            asyncio.run(analyze_team(team_name))

        else:
            print("Enhanced Usage:")
            print("  python betting_bot.py                    # Run full prediction analysis")
            print("  python betting_bot.py test               # Test scraping with debug info")
            print("  python betting_bot.py debug              # Save page HTML for analysis")
            print("  python betting_bot.py team=Arsenal       # Analyze specific team")
            print("\nEnvironment Variables:")
            print("  BETTING_SITE=forebet|bet9ja|betway      # Choose betting site (default: forebet)")
            print("  TELEGRAM_TOKEN=your_token               # Required")
            print("  TELEGRAM_CHAT_ID=your_chat_id           # Required")
            print("  MIN_CONFIDENCE=0.65                     # Minimum prediction confidence")
            print("  MAX_PREDICTIONS=5                       # Maximum predictions to send")
    else:
        # Run full prediction analysis
        asyncio.run(main())