import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import ast
import numpy as np
from scipy.stats import poisson

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Replace with your actual bot token
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

class MatchPredictor:
    def __init__(self, data):
        self.data = data
        self.xg_home, self.xg_away = self._calculate_xg()

    def _calculate_xg(self):
        """Calculate expected goals"""
        home_attack = self.data["home_goals_scored"] / max(1, self.data["home_matches"])
        away_defense = self.data["away_goals_conceded"] / max(1, self.data["away_matches"])
        away_attack = self.data["away_goals_scored"] / max(1, self.data["away_matches"])
        home_defense = self.data["home_goals_conceded"] / max(1, self.data["home_matches"])

        xg_home = (home_attack * away_defense) * 1.5
        xg_away = (away_attack * home_defense) * 1.5

        return round(xg_home, 2), round(xg_away, 2)

    def predict_1x2(self):
        """Predict match outcome using Poisson distribution"""
        home_win = 0
        draw = 0
        away_win = 0

        for i in range(0, 10):  # Home goals
            for j in range(0, 10):  # Away goals
                prob = poisson.pmf(i, self.xg_home) * poisson.pmf(j, self.xg_away)
                if i > j:
                    home_win += prob
                elif i == j:
                    draw += prob
                else:
                    away_win += prob

        prediction = "Home Win" if home_win > away_win and home_win > draw else \
                     "Away Win" if away_win > home_win and away_win > draw else "Draw"

        return {
            "prediction": prediction,
            "probabilities": {
                "Home": round(home_win, 2),
                "Draw": round(draw, 2),
                "Away": round(away_win, 2)
            }
        }

    def predict_btts(self):
        """Predict Both Teams to Score"""
        btts_prob = 1 - (np.exp(-self.xg_home) * np.exp(-self.xg_away))
        return {
            "prediction": "Yes" if btts_prob > 0.5 else "No",
            "probability": round(btts_prob, 2)
        }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Football Prediction Bot ⚽\n\n"
        "Send match data in this format:\n"
        '{"home_team": "TeamA", "away_team": "TeamB", '
        '"home_goals_scored": 2.1, "home_goals_conceded": 0.8, "home_matches": 10, '
        '"away_goals_scored": 1.9, "away_goals_conceded": 1.2, "away_matches": 10}'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = ast.literal_eval(update.message.text)

        predictor = MatchPredictor(data)

        # Get predictions
        outcome = predictor.predict_1x2()
        btts = predictor.predict_btts()

        # Format response
        response = (
            f"⚽ {data['home_team']} vs {data['away_team']} Predictions:\n\n"
            f"🔹 Expected Goals:\n"
            f"   Home: {predictor.xg_home} | Away: {predictor.xg_away}\n\n"
            f"🔹 1X2 Prediction:\n"
            f"   {outcome['prediction']}\n"
            f"   Probabilities: Home {outcome['probabilities']['Home']:.0%} | "
            f"Draw {outcome['probabilities']['Draw']:.0%} | Away {outcome['probabilities']['Away']:.0%}\n\n"
            f"🔹 BTTS:\n"
            f"   {btts['prediction']} (Probability: {btts['probability']:.0%})"
        )

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}\n\nUse /start to see the correct format.")

def main():
    app = ApplicationBuilder().token(8186199634:AAEitjNjLOUafYoEzKNsIErn5Rclf5RymSE).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()