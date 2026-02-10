#!/usr/bin/env python3
"""
Football Betting Analysis Bot
Based on LSE Paper: "A Profitable Model For Predicting the Over/Under Market in Football"
Manual input version for Telegram
"""

import os
import json
import logging
from typing import Dict, Tuple, List
from flask import Flask, request, jsonify
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# In-memory storage (use database in production)
TEAMS_DATA: Dict[str, Dict] = {}
FIXTURES: List[Dict] = []
USER_STATE: Dict = {}

# ====================
# CORE CALCULATIONS (Based on LSE Paper)
# ====================

def calculate_expected_total(home_team: str, away_team: str) -> float:
    """
    Calculate expected total goals for a match
    Formula: ((Home xG + Away xGA) + (Away xG + Home xGA)) / 2
    """
    if home_team not in TEAMS_DATA or away_team not in TEAMS_DATA:
        raise ValueError(f"Missing team data: {home_team} or {away_team}")

    home_xg = TEAMS_DATA[home_team]["xG"]
    home_xga = TEAMS_DATA[home_team]["xGA"]
    away_xg = TEAMS_DATA[away_team]["xG"]
    away_xga = TEAMS_DATA[away_team]["xGA"]

    # Expected goals if home attacks away's defense
    home_expected = (home_xg + away_xga) / 2
    # Expected goals if away attacks home's defense
    away_expected = (away_xg + home_xga) / 2

    total_expected = home_expected + away_expected
    return round(total_expected, 2)

def calculate_value_probability(expected_total: float) -> Tuple[float, float]:
    """
    Convert expected total to probability of Over/Under
    Simplified logistic function based on paper's approach
    """
    # Convert expected total to probability of Over 2.5
    # Using simplified sigmoid: p = 1 / (1 + exp(-(x - 2.5)*k))
    # Where k is sensitivity factor (paper used ~0.8)
    k = 0.8
    x = expected_total - 2.5
    prob_over = 1 / (1 + 2.71828 ** (-k * x))
    prob_under = 1 - prob_over

    return round(prob_over, 3), round(prob_under, 3)

def find_value_bet(expected_total: float, odds_over: float = None, odds_under: float = None) -> Dict:
    """
    Determine if a bet offers value based on paper's strategy
    Returns recommendation dictionary
    """
    prob_over, prob_under = calculate_value_probability(expected_total)

    recommendation = {
        "expected_total": expected_total,
        "prob_over": prob_over,
        "prob_under": prob_under,
        "decision": "NO_VALUE",
        "market": None,
        "min_odds": None,
        "edge": 0
    }

    # If odds provided, calculate exact value
    if odds_over and odds_under:
        implied_over = 1 / odds_over
        implied_under = 1 / odds_under

        edge_over = prob_over - implied_over
        edge_under = prob_under - implied_under

        if edge_over > 0.02:  # 2% edge threshold
            recommendation["decision"] = "OVER_VALUE"
            recommendation["market"] = "Over 2.5"
            recommendation["min_odds"] = round(1 / prob_over, 2)
            recommendation["edge"] = round(edge_over * 100, 1)
        elif edge_under > 0.02:
            recommendation["decision"] = "UNDER_VALUE"
            recommendation["market"] = "Under 2.5"
            recommendation["min_odds"] = round(1 / prob_under, 2)
            recommendation["edge"] = round(edge_under * 100, 1)

    # If no odds provided, use simplified rules from paper
    else:
        if expected_total > 2.8:
            recommendation["decision"] = "OVER_VALUE"
            recommendation["market"] = "Over 2.5"
            recommendation["min_odds"] = round(1 / max(prob_over, 0.55), 2)
        elif expected_total < 2.2:
            recommendation["decision"] = "UNDER_VALUE"
            recommendation["market"] = "Under 2.5"
            recommendation["min_odds"] = round(1 / max(prob_under, 0.55), 2)

    return recommendation

# ====================
# TELEGRAM COMMAND PROCESSING
# ====================

def process_addteam(command_parts: List[str]) -> str:
    """Process /addteam command"""
    if len(command_parts) < 4:
        return "❌ Format: /addteam 'Team Name' xG xGA\nExample: /addteam Arsenal 2.02 0.77"

    try:
        team_name = command_parts[1]
        # Handle team names with spaces
        if command_parts[1].startswith('"'):
            team_name = " ".join(command_parts[1:3])
            xg = float(command_parts[3])
            xga = float(command_parts[4])
        else:
            xg = float(command_parts[2])
            xga = float(command_parts[3])

        TEAMS_DATA[team_name] = {"xG": xg, "xGA": xga}
        return f"✅ **{team_name}** added\nxG: {xg} | xGA: {xga}"

    except (ValueError, IndexError):
        return "❌ Error: Use format /addteam 'Team Name' xG xGA"

def process_addfixture(command_parts: List[str]) -> str:
    """Process /addfixture command"""
    if len(command_parts) < 3:
        return "❌ Format: /addfixture 'Home Team' 'Away Team'\nExample: /addfixture Chelsea 'Aston Villa'"

    try:
        # Handle quoted team names
        if command_parts[1].startswith('"'):
            home = " ".join(command_parts[1:3]).strip('"')
            away = " ".join(command_parts[3:]).strip('"')
        else:
            home = command_parts[1]
            away = command_parts[2]

        FIXTURES.append({"home": home, "away": away})
        return f"✅ **Fixture added**\n🏠 {home} vs 🛫 {away}"

    except IndexError:
        return "❌ Error: Use format /addfixture 'Home' 'Away'"

def process_analyze() -> str:
    """Process /analyze command - generate predictions"""
    if not FIXTURES:
        return "❌ No fixtures added. Use /addfixture first"

    if not TEAMS_DATA:
        return "❌ No team data added. Use /addteam first"

    results = []
    value_bets = 0

    for i, fixture in enumerate(FIXTURES, 1):
        home = fixture["home"]
        away = fixture["away"]

        if home not in TEAMS_DATA or away not in TEAMS_DATA:
            results.append(f"{i}. ❌ {home} vs {away} - Missing data")
            continue

        try:
            expected_total = calculate_expected_total(home, away)
            recommendation = find_value_bet(expected_total)

            # Format result
            if recommendation["decision"] == "NO_VALUE":
                symbol = "⚪"
                advice = f"Expected: {expected_total} goals\nNo clear value"
            elif recommendation["decision"] == "OVER_VALUE":
                symbol = "🟢"
                advice = f"Expected: {expected_total} goals\n✅ **OVER 2.5**\nOdds needed: <{recommendation['min_odds']}"
                value_bets += 1
            else:  # UNDER_VALUE
                symbol = "🔵"
                advice = f"Expected: {expected_total} goals\n✅ **UNDER 2.5**\nOdds needed: <{recommendation['min_odds']}"
                value_bets += 1

            results.append(f"{i}. {symbol} **{home} vs {away}**\n{advice}")

        except Exception as e:
            results.append(f"{i}. ❌ {home} vs {away} - Error: {str(e)}")

    # Summary
    summary = f"\n📊 **ANALYSIS COMPLETE**\n"
    summary += f"Fixtures: {len(FIXTURES)}\n"
    summary += f"Value bets found: {value_bets}\n"
    summary += f"Teams in database: {len(TEAMS_DATA)}"

    return "\n\n".join(results) + "\n\n" + summary

def process_odds(command_parts: List[str]) -> str:
    """Process /odds command - add specific odds to last fixture"""
    if len(command_parts) < 3:
        return "❌ Format: /odds OverOdds UnderOdds\nExample: /odds 1.85 2.00"

    try:
        odds_over = float(command_parts[1])
        odds_under = float(command_parts[2])

        if not FIXTURES:
            return "❌ No fixtures to add odds to"

        last_fixture = FIXTURES[-1]
        home = last_fixture["home"]
        away = last_fixture["away"]

        if home not in TEAMS_DATA or away not in TEAMS_DATA:
            return "❌ Missing team data for last fixture"

        expected_total = calculate_expected_total(home, away)
        recommendation = find_value_bet(expected_total, odds_over, odds_under)

        # Format detailed analysis
        analysis = f"📊 **Detailed Analysis - {home} vs {away}**\n"
        analysis += f"Expected total: {expected_total} goals\n"
        analysis += f"Probability Over 2.5: {recommendation['prob_over']*100:.1f}%\n"
        analysis += f"Probability Under 2.5: {recommendation['prob_under']*100:.1f}%\n\n"

        if recommendation["decision"] == "NO_VALUE":
            analysis += "❌ **NO VALUE BET**\n"
            analysis += f"Market odds don't offer sufficient edge\n"
            analysis += f"Over 2.5 @ {odds_over} (edge: {recommendation['edge']}%)"
        elif recommendation["decision"] == "OVER_VALUE":
            analysis += "✅ **VALUE BET FOUND**\n"
            analysis += f"**BET: OVER 2.5 GOALS**\n"
            analysis += f"Odds: {odds_over} (Edge: {recommendation['edge']}%)\n"
            analysis += f"Minimum fair odds: {recommendation['min_odds']}"
        else:  # UNDER_VALUE
            analysis += "✅ **VALUE BET FOUND**\n"
            analysis += f"**BET: UNDER 2.5 GOALS**\n"
            analysis += f"Odds: {odds_under} (Edge: {recommendation['edge']}%)\n"
            analysis += f"Minimum fair odds: {recommendation['min_odds']}"

        return analysis

    except (ValueError, IndexError):
        return "❌ Error: Use format /odds OverOdds UnderOdds"

def process_command(text: str) -> str:
    """Main command processor"""
    if not text.startswith('/'):
        return "🤖 Send /help for available commands"

    parts = text.strip().split()
    command = parts[0].lower()

    if command == "/start":
        return welcome_message()

    elif command == "/help":
        return help_message()

    elif command == "/addteam":
        return process_addteam(parts)

    elif command == "/addfixture":
        return process_addfixture(parts)

    elif command == "/odds":
        return process_odds(parts)

    elif command == "/analyze":
        return process_analyze()

    elif command == "/clear":
        TEAMS_DATA.clear()
        FIXTURES.clear()
        return "🗑️ All data cleared"

    elif command == "/teams":
        if not TEAMS_DATA:
            return "📭 No teams in database"
        teams_list = "\n".join([f"{team}: xG={data['xG']}, xGA={data['xGA']}" 
                               for team, data in TEAMS_DATA.items()])
        return f"📋 **Teams in database** ({len(TEAMS_DATA)}):\n{teams_list}"

    elif command == "/fixtures":
        if not FIXTURES:
            return "📭 No fixtures added"
        fixtures_list = "\n".join([f"{i+1}. {f['home']} vs {f['away']}" 
                                  for i, f in enumerate(FIXTURES)])
        return f"📅 **Fixtures** ({len(FIXTURES)}):\n{fixtures_list}"

    else:
        return "❌ Unknown command. Use /help"

# ====================
# MESSAGES
# ====================

def welcome_message() -> str:
    return """⚽ **Football Betting Analyzer** 🤖

Based on LSE Research Paper:
*A Profitable Model For Predicting Over/Under 2.5 Goals*

📊 **How it works:**
1. Add team xG/xGA stats from Understat
2. Add weekend fixtures
3. Get value betting recommendations

🔧 **Quick Start:**
/addteam Arsenal 2.02 0.77
/addfixture Chelsea "Aston Villa"
/analyze

Type /help for all commands"""

def help_message() -> str:
    return """📚 **AVAILABLE COMMANDS**

📊 **Data Input:**
`/addteam "Team Name" xG xGA`
  Add team expected goals data
  Example: /addteam "Man City" 1.98 1.22

`/addfixture "Home Team" "Away Team"`
  Add a match fixture
  Example: /addfixture Chelsea "Aston Villa"

`/odds OverOdds UnderOdds`
  Add market odds to last fixture
  Example: /odds 1.85 2.00

📈 **Analysis:**
`/analyze`
  Analyze all fixtures for value bets

`/teams`
  Show all teams in database

`/fixtures`
  Show all fixtures

🛠️ **Utility:**
`/clear`
  Clear all data

`/help`
  Show this message

💡 **Tip:** Use quotes for team names with spaces
Example: /addteam "Manchester United" 1.96 1.31"""

# ====================
# FLASK ROUTES
# ====================

@app.route('/')
def home():
    return "⚽ Football Betting Analyzer Bot is running! Use Telegram to interact."

@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    try:
        data = request.get_json()
        logger.info(f"Received data: {data}")

        # Extract message
        message = data.get('message', {})
        text = message.get('text', '').strip()
        chat_id = message.get('chat', {}).get('id')

        if not text or not chat_id:
            return jsonify({"status": "error", "message": "Invalid request"}), 400

        # Process command
        response_text = process_command(text)

        # Prepare response for Telegram
        response = {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": response_text,
            "parse_mode": "Markdown"
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/set_webhook', methods=['GET'])
def set_webhook_page():
    """Page to help set up webhook"""
    token = os.environ.get('TELEGRAM_TOKEN', 'YOUR_BOT_TOKEN_HERE')
    repl_url = os.environ.get('REPLIT_URL', 'https://your-repl-url.username.repl.co')

    webhook_url = f"{repl_url}/webhook"
    set_webhook_cmd = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"

    return f"""
    <h1>⚽ Football Betting Bot - Webhook Setup</h1>
    <p>Webhook URL: <code>{webhook_url}</code></p>
    <p>Set webhook by visiting:</p>
    <a href="{set_webhook_cmd}" target="_blank">{set_webhook_cmd}</a>
    <p>Or manually set in Telegram:</p>
    <code>curl "{set_webhook_cmd}"</code>
    """

# ====================
# MAIN
# ====================

if __name__ == '__main__':
    # Get port from environment (for Replit)
    port = int(os.environ.get('PORT', 5000))

    # Run Flask app
    print(f"⚽ Starting Football Betting Analyzer Bot on port {port}")
    print(f"📊 Based on LSE Paper: Predicting Over/Under 2.5 Goals")
    print(f"🌐 Webhook setup: /set_webhook")

    app.run(host='0.0.0.0', port=port, debug=False)