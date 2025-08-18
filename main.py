import re
import json
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

class MatchOutcome(Enum):
    HOME_WIN = "HOME_WIN"
    AWAY_WIN = "AWAY_WIN"
    DRAW = "DRAW"

@dataclass
class TeamStats:
    name: str
    position: int
    points: int
    games_played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    goal_difference: int
    goals_per_game: float
    goals_conceded_per_game: float
    clean_sheets: int
    recent_form_wins: int
    recent_form_draws: int
    recent_form_losses: int
    recent_games_count: int

@dataclass
class HeadToHeadRecord:
    home_team_wins: int
    away_team_wins: int
    draws: int
    total_games: int
    recent_results: List[str]  # Recent match results

@dataclass
class PredictionResult:
    predicted_outcome: MatchOutcome
    confidence_score: float
    predicted_score: str
    reasoning: List[str]
    key_factors: Dict[str, float]

class FootballPredictor:
    def __init__(self):
        self.weights = {
            'league_position': 0.15,
            'recent_form': 0.25,
            'head_to_head': 0.20,
            'attack_strength': 0.15,
            'defense_strength': 0.15,
            'home_advantage': 0.10
        }

    def parse_team_stats(self, stats_text: str) -> Tuple[TeamStats, TeamStats]:
        """Parse team statistics from the provided text format"""

        # Extract league table
        standings_pattern = r'(\d+)\s+([^0-9]+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([+-]?\d+)'
        standings_matches = re.findall(standings_pattern, stats_text)

        teams_data = {}
        for match in standings_matches:
            pos, name, pts, gp, w, d, l, gf, ga, gd = match
            # Clean team name
            name = name.strip()
            if name not in teams_data:
                teams_data[name] = {
                    'position': int(pos),
                    'points': int(pts),
                    'games_played': int(gp),
                    'wins': int(w),
                    'draws': int(d),
                    'losses': int(l),
                    'goals_for': int(gf),
                    'goals_against': int(ga),
                    'goal_difference': int(gd)
                }

        # Find the two main teams (usually the ones with most recent matches)
        team_names = list(teams_data.keys())
        home_team_name = None
        away_team_name = None

        # Try to identify teams from match data
        for name in team_names:
            if "millonarios" in name.lower():
                away_team_name = name
            elif "tolima" in name.lower():
                home_team_name = name

        # If we can't identify specifically, take first two teams
        if not home_team_name or not away_team_name:
            home_team_name = team_names[0] if len(team_names) > 0 else "Team A"
            away_team_name = team_names[1] if len(team_names) > 1 else "Team B"

        # Create team stats objects
        home_stats = self._create_team_stats(home_team_name, teams_data.get(home_team_name, {}), stats_text, True)
        away_stats = self._create_team_stats(away_team_name, teams_data.get(away_team_name, {}), stats_text, False)

        return home_stats, away_stats

    def _create_team_stats(self, name: str, basic_stats: Dict, full_text: str, is_home: bool) -> TeamStats:
        """Create TeamStats object from parsed data"""

        # Extract recent form (wins/draws/losses from last 6)
        form_pattern = r'Win\s+(\d+)\s+\d+%\s+Draw\s+(\d+)\s+\d+%\s+Lost\s+(\d+)\s+\d+%'
        form_matches = re.findall(form_pattern, full_text)

        recent_wins = int(form_matches[0][0]) if form_matches else 0
        recent_draws = int(form_matches[0][1]) if form_matches else 0
        recent_losses = int(form_matches[0][2]) if form_matches else 0
        recent_games = recent_wins + recent_draws + recent_losses

        return TeamStats(
            name=name,
            position=basic_stats.get('position', 10),
            points=basic_stats.get('points', 0),
            games_played=basic_stats.get('games_played', 20),
            wins=basic_stats.get('wins', 0),
            draws=basic_stats.get('draws', 0),
            losses=basic_stats.get('losses', 0),
            goals_for=basic_stats.get('goals_for', 0),
            goals_against=basic_stats.get('goals_against', 0),
            goal_difference=basic_stats.get('goal_difference', 0),
            goals_per_game=basic_stats.get('goals_for', 0) / max(basic_stats.get('games_played', 1), 1),
            goals_conceded_per_game=basic_stats.get('goals_against', 0) / max(basic_stats.get('games_played', 1), 1),
            clean_sheets=0,  # Would need to parse from detailed stats
            recent_form_wins=recent_wins,
            recent_form_draws=recent_draws,
            recent_form_losses=recent_losses,
            recent_games_count=recent_games
        )

    def parse_head_to_head(self, stats_text: str) -> HeadToHeadRecord:
        """Parse head-to-head record from stats text"""

        # Look for H2H summary
        h2h_pattern = r'(\w+)\s+(\d+)\s+(\d+)%\s+Draw\s+(\d+)\s+(\d+)%\s+(\w+)\s+(\d+)\s+(\d+)%'
        h2h_match = re.search(h2h_pattern, stats_text)

        if h2h_match:
            team1_wins = int(h2h_match.group(2))
            draws = int(h2h_match.group(4))
            team2_wins = int(h2h_match.group(7))
            total = team1_wins + draws + team2_wins

            return HeadToHeadRecord(
                home_team_wins=team1_wins,
                away_team_wins=team2_wins,
                draws=draws,
                total_games=total,
                recent_results=[]
            )

        # Default if no H2H found
        return HeadToHeadRecord(0, 0, 0, 0, [])

    def calculate_form_score(self, team: TeamStats) -> float:
        """Calculate recent form score (0-1)"""
        if team.recent_games_count == 0:
            return 0.5

        form_points = (team.recent_form_wins * 3 + team.recent_form_draws * 1)
        max_possible = team.recent_games_count * 3
        return form_points / max_possible

    def calculate_league_strength(self, team: TeamStats, total_teams: int = 20) -> float:
        """Calculate league position strength (0-1, higher is better)"""
        return (total_teams - team.position + 1) / total_teams

    def calculate_attack_strength(self, team: TeamStats) -> float:
        """Calculate attacking strength based on goals per game"""
        # Normalize around 1.5 goals per game as average
        return min(team.goals_per_game / 2.0, 1.0)

    def calculate_defense_strength(self, team: TeamStats) -> float:
        """Calculate defensive strength (lower conceded = higher score)"""
        # Normalize around 1.5 goals conceded per game
        if team.goals_conceded_per_game == 0:
            return 1.0
        return max(0, 1 - (team.goals_conceded_per_game / 2.0))

    def calculate_h2h_advantage(self, h2h: HeadToHeadRecord, is_home: bool) -> float:
        """Calculate head-to-head advantage"""
        if h2h.total_games == 0:
            return 0.5

        if is_home:
            win_rate = h2h.home_team_wins / h2h.total_games
        else:
            win_rate = h2h.away_team_wins / h2h.total_games

        return win_rate

    def predict_match(self, stats_text: str) -> PredictionResult:
        """Main prediction function"""

        # Parse the data
        home_team, away_team = self.parse_team_stats(stats_text)
        h2h = self.parse_head_to_head(stats_text)

        # Calculate component scores
        home_scores = {
            'league_position': self.calculate_league_strength(home_team),
            'recent_form': self.calculate_form_score(home_team),
            'attack_strength': self.calculate_attack_strength(home_team),
            'defense_strength': self.calculate_defense_strength(home_team),
            'head_to_head': self.calculate_h2h_advantage(h2h, True),
            'home_advantage': 0.55  # Standard home advantage
        }

        away_scores = {
            'league_position': self.calculate_league_strength(away_team),
            'recent_form': self.calculate_form_score(away_team),
            'attack_strength': self.calculate_attack_strength(away_team),
            'defense_strength': self.calculate_defense_strength(away_team),
            'head_to_head': self.calculate_h2h_advantage(h2h, False),
            'home_advantage': 0.45  # Away disadvantage
        }

        # Calculate weighted scores
        home_total = sum(home_scores[key] * self.weights[key] for key in self.weights.keys())
        away_total = sum(away_scores[key] * self.weights[key] for key in self.weights.keys())

        # Determine outcome
        score_difference = home_total - away_total

        if abs(score_difference) < 0.05:  # Very close
            outcome = MatchOutcome.DRAW
            confidence = 0.6
            predicted_score = "1-1"
        elif score_difference > 0.1:  # Clear home advantage
            outcome = MatchOutcome.HOME_WIN
            confidence = min(0.8, 0.5 + abs(score_difference))
            predicted_score = "2-1" if score_difference > 0.15 else "1-0"
        elif score_difference < -0.1:  # Clear away advantage
            outcome = MatchOutcome.AWAY_WIN
            confidence = min(0.8, 0.5 + abs(score_difference))
            predicted_score = "1-2" if score_difference < -0.15 else "0-1"
        else:  # Slight advantage
            if score_difference > 0:
                outcome = MatchOutcome.DRAW  # Lean towards draw when close
                predicted_score = "1-1"
            else:
                outcome = MatchOutcome.DRAW
                predicted_score = "1-1"
            confidence = 0.65

        # Generate reasoning
        reasoning = self._generate_reasoning(home_team, away_team, home_scores, away_scores, h2h)

        return PredictionResult(
            predicted_outcome=outcome,
            confidence_score=confidence,
            predicted_score=predicted_score,
            reasoning=reasoning,
            key_factors={'home_total': home_total, 'away_total': away_total, 'difference': score_difference}
        )

    def _generate_reasoning(self, home_team: TeamStats, away_team: TeamStats, 
                          home_scores: Dict, away_scores: Dict, h2h: HeadToHeadRecord) -> List[str]:
        """Generate human-readable reasoning for the prediction"""
        reasoning = []

        # League position comparison
        if home_team.position < away_team.position:
            reasoning.append(f"{home_team.name} has better league position ({home_team.position} vs {away_team.position})")
        elif away_team.position < home_team.position:
            reasoning.append(f"{away_team.name} has better league position ({away_team.position} vs {home_team.position})")
        else:
            reasoning.append("Teams are very close in league standings")

        # Recent form
        home_form_rate = home_team.recent_form_wins / max(home_team.recent_games_count, 1)
        away_form_rate = away_team.recent_form_wins / max(away_team.recent_games_count, 1)

        if home_form_rate > away_form_rate + 0.2:
            reasoning.append(f"{home_team.name} has much better recent form")
        elif away_form_rate > home_form_rate + 0.2:
            reasoning.append(f"{away_team.name} has much better recent form")

        # Goals comparison
        if home_team.goals_per_game > away_team.goals_per_game + 0.2:
            reasoning.append(f"{home_team.name} has stronger attack ({home_team.goals_per_game:.1f} vs {away_team.goals_per_game:.1f} goals/game)")
        elif away_team.goals_per_game > home_team.goals_per_game + 0.2:
            reasoning.append(f"{away_team.name} has stronger attack ({away_team.goals_per_game:.1f} vs {home_team.goals_per_game:.1f} goals/game)")

        # Defense comparison
        if home_team.goals_conceded_per_game < away_team.goals_conceded_per_game - 0.2:
            reasoning.append(f"{home_team.name} has better defense ({home_team.goals_conceded_per_game:.1f} vs {away_team.goals_conceded_per_game:.1f} conceded/game)")
        elif away_team.goals_conceded_per_game < home_team.goals_conceded_per_game - 0.2:
            reasoning.append(f"{away_team.name} has better defense ({away_team.goals_conceded_per_game:.1f} vs {home_team.goals_conceded_per_game:.1f} conceded/game)")

        # Head to head
        if h2h.total_games > 0:
            if h2h.home_team_wins > h2h.away_team_wins:
                reasoning.append(f"{home_team.name} leads head-to-head record")
            elif h2h.away_team_wins > h2h.home_team_wins:
                reasoning.append(f"{away_team.name} leads head-to-head record")
            else:
                reasoning.append("Even head-to-head record")

        return reasoning

# Telegram Bot Integration
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

class TelegramFootballBot:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.predictor = FootballPredictor()
        self.application = Application.builder().token(bot_token).build()
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup bot command and message handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("predict", self.predict_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_stats))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = """
🏆 **Football Prediction Bot**

Send me team statistics and I'll analyze the match for you!

**How to use:**
1. Paste your team statistics (league table, recent form, head-to-head)
2. I'll automatically analyze and predict the outcome
3. Use /predict for manual prediction mode

**Commands:**
/help - Show this help message
/predict - Manual prediction mode

Just paste your stats and let me do the analysis! ⚽
        """
        await update.message.reply_text(welcome_message, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = """
📊 **How to use the Football Prediction Bot:**

1. **Paste team statistics** - Just send me stats in any format:
   - League standings
   - Recent form
   - Head-to-head records
   - Goals scored/conceded

2. **I analyze these factors:**
   - League positions (15%)
   - Recent form (25%)
   - Head-to-head record (20%)
   - Attack strength (15%)
   - Defense strength (15%)
   - Home advantage (10%)

3. **Get prediction with:**
   - Match outcome
   - Predicted score
   - Confidence level
   - Detailed reasoning

**Example:** Just paste league table and team stats, I'll handle the rest!
        """
        await update.message.reply_text(help_message, parse_mode='Markdown')

    async def predict_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /predict command"""
        await update.message.reply_text(
            "🔮 **Prediction Mode Active**\n\nSend me the team statistics and I'll analyze the match!",
            parse_mode='Markdown'
        )

    async def handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming statistics text"""
        try:
            stats_text = update.message.text

            # Check if this looks like football stats
            if not self._is_football_stats(stats_text):
                await update.message.reply_text(
                    "🤔 This doesn't look like football statistics. Please send:\n"
                    "- League table\n"
                    "- Team standings\n"
                    "- Recent form data\n"
                    "- Head-to-head records"
                )
                return

            # Show processing message
            processing_msg = await update.message.reply_text("⚽ Analyzing match data...")

            # Make prediction
            prediction = self.predictor.predict_match(stats_text)

            # Format response
            response = self._format_prediction_response(prediction)

            # Edit the processing message with results
            await processing_msg.edit_text(response, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text(
                f"❌ Error analyzing stats: {str(e)}\n\n"
                "Please check your data format and try again."
            )

    def _is_football_stats(self, text: str) -> bool:
        """Check if text contains football statistics"""
        indicators = [
            'pts', 'gp', 'goals', 'win', 'draw', 'loss',
            'standing', 'position', 'matches', 'scored',
            'millonarios', 'tolima', 'américa', 'nacional'
        ]
        text_lower = text.lower()
        return sum(1 for indicator in indicators if indicator in text_lower) >= 3

    def _format_prediction_response(self, prediction: PredictionResult) -> str:
        """Format prediction result for Telegram"""

        outcome_emoji = {
            MatchOutcome.HOME_WIN: "🏠",
            MatchOutcome.AWAY_WIN: "✈️",
            MatchOutcome.DRAW: "🤝"
        }

        outcome_text = {
            MatchOutcome.HOME_WIN: "Home Win",
            MatchOutcome.AWAY_WIN: "Away Win", 
            MatchOutcome.DRAW: "Draw"
        }

        confidence_bar = "🟩" * int(prediction.confidence_score * 10) + "⬜" * (10 - int(prediction.confidence_score * 10))

        response = f"""
🏆 **MATCH PREDICTION**

{outcome_emoji[prediction.predicted_outcome]} **{outcome_text[prediction.predicted_outcome]}**
⚽ **Predicted Score:** {prediction.predicted_score}
📊 **Confidence:** {prediction.confidence_score:.1%}
{confidence_bar}

**🧠 Analysis:**
"""

        for reason in prediction.reasoning:
            response += f"• {reason}\n"

        response += f"""
**📈 Key Factors:**
• Home Advantage: {prediction.key_factors.get('home_total', 0):.2f}
• Away Strength: {prediction.key_factors.get('away_total', 0):.2f}
• Score Difference: {prediction.key_factors.get('difference', 0):.3f}

*Prediction based on systematic analysis of current form, league position, and historical data.*
        """

        return response

    def run(self):
        """Start the bot"""
        print("🤖 Football Prediction Bot starting...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# Main execution
if __name__ == "__main__":
    # Configuration
    BOT_TOKEN = "8186199634:AAEEafBIm5GhZrhrWt-je8wa1UESaTHF9ZM"  # Replace with your actual bot token

    # For testing without Telegram (you can test the predictor directly)
    def test_predictor():
        predictor = FootballPredictor()

        # Your sample data
        sample_stats = """
STANDINGS UP TO 18/08/2025
CLAUSURA	PTS	GP	W	D	L	GF	GA	+/-
1	América de Cali	39	20	11	6	3	29	12	17
2	Millonarios	38	20	11	5	4	30	17	13
3	Junior Barranquilla	37	20	10	7	3	26	16	10
4	Deportes Tolima	36	20	10	6	4	30	19	11

Deportes Tolima 2
33%
Draw 1
17%
Millonarios 3
50%

Win 3
50%
Draw 1
17%
Lost 2
33%

Win 1
17%
Draw 1
17%
Lost 4
67%
        """

        result = predictor.predict_match(sample_stats)
        print(f"Prediction: {result.predicted_outcome.value}")
        print(f"Score: {result.predicted_score}")
        print(f"Confidence: {result.confidence_score:.1%}")
        print("Reasoning:")
        for reason in result.reasoning:
            print(f"  - {reason}")

    # Uncomment to test without Telegram
    # test_predictor()

    # To run the Telegram bot, uncomment these lines:
    # bot = TelegramFootballBot(BOT_TOKEN)
    # bot.run()

    print("Replace BOT_TOKEN with your actual Telegram bot token and uncomment the bot.run() line to start!")

# Installation requirements for Replit:
"""
Add to requirements.txt:
python-telegram-bot==20.7
aiohttp
asyncio
"""

# Replit setup instructions:
"""
1. Create new Python repl on Replit
2. Copy this code to main.py
3. Add requirements.txt with the dependencies above
4. Get Telegram bot token from @BotFather
5. Replace BOT_TOKEN with your actual token
6. Uncomment the bot.run() line
7. Click Run!

Environment variables (optional, more secure):
Add to Secrets tab in Replit:
- Key: BOT_TOKEN
- Value: your_actual_bot_token

Then use: BOT_TOKEN = os.environ.get('BOT_TOKEN')
"""