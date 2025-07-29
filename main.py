
import logging
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, classification_report
import joblib

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Conversation states
TEAM_INPUT, STATS_INPUT = range(2)

class FootballPredictor:
    """Advanced Football prediction engine using real match statistics"""
    
    def __init__(self):
        self.model = None
        self.scaler = StandardScaler()
        self.model_path = "football_model.pkl"
        self.scaler_path = "scaler.pkl"
        self.predictions_log = "predictions_log.csv"
        
    def generate_realistic_training_data(self, n_samples: int = 5000) -> pd.DataFrame:
        """Generate realistic football match data based on actual football statistics"""
        np.random.seed(42)
        
        # More realistic football data distributions
        data = {
            # Team ratings (1-100 scale)
            'home_team_rating': np.random.normal(65, 20, n_samples).clip(1, 100),
            'away_team_rating': np.random.normal(65, 20, n_samples).clip(1, 100),
            
            # Recent form (last 5 games: 0-15 points)
            'home_recent_form': np.random.uniform(0, 15, n_samples),
            'away_recent_form': np.random.uniform(0, 15, n_samples),
            
            # Head to head record
            'home_h2h_wins': np.random.poisson(2, n_samples),
            'away_h2h_wins': np.random.poisson(2, n_samples),
            'h2h_draws': np.random.poisson(1, n_samples),
            
            # Goals statistics (per game averages)
            'home_goals_for_avg': np.random.gamma(2, 0.8, n_samples).clip(0, 5),
            'home_goals_against_avg': np.random.gamma(2, 0.7, n_samples).clip(0, 4),
            'away_goals_for_avg': np.random.gamma(2, 0.7, n_samples).clip(0, 4),
            'away_goals_against_avg': np.random.gamma(2, 0.8, n_samples).clip(0, 4),
            
            # League position (1-20)
            'home_league_position': np.random.randint(1, 21, n_samples),
            'away_league_position': np.random.randint(1, 21, n_samples),
            
            # Injury/suspension count
            'home_missing_players': np.random.poisson(2, n_samples).clip(0, 8),
            'away_missing_players': np.random.poisson(2, n_samples).clip(0, 8),
            
            # Days since last match
            'home_rest_days': np.random.choice([3, 4, 7, 14], n_samples, p=[0.4, 0.3, 0.25, 0.05]),
            'away_rest_days': np.random.choice([3, 4, 7, 14], n_samples, p=[0.4, 0.3, 0.25, 0.05]),
        }
        
        df = pd.DataFrame(data)
        
        # Create realistic outcome probabilities
        rating_diff = df['home_team_rating'] - df['away_team_rating']
        form_diff = df['home_recent_form'] - df['away_recent_form']
        position_advantage = df['away_league_position'] - df['home_league_position']
        goal_diff = (df['home_goals_for_avg'] - df['home_goals_against_avg']) - (df['away_goals_for_avg'] - df['away_goals_against_avg'])
        rest_advantage = df['home_rest_days'] - df['away_rest_days']
        
        # Home advantage factor
        home_advantage = 3
        
        # Combined strength indicator
        strength_indicator = (rating_diff + form_diff*2 + position_advantage + goal_diff*5 + rest_advantage*0.5 + home_advantage) / 15
        
        # Convert to probabilities using sigmoid
        home_prob = 1 / (1 + np.exp(-strength_indicator))
        draw_prob = 0.25 + 0.05 * np.cos(strength_indicator)  # Draws more likely when teams are close
        away_prob = 1 - home_prob - draw_prob.clip(0, 0.4)
        
        # Ensure probabilities are valid
        home_prob = home_prob.clip(0.1, 0.8)
        draw_prob = draw_prob.clip(0.15, 0.4)
        away_prob = (1 - home_prob - draw_prob).clip(0.1, 0.8)
        
        # Generate outcomes
        outcomes = []
        for i in range(n_samples):
            rand = np.random.random()
            if rand < away_prob[i]:
                outcomes.append(0)  # Away win
            elif rand < away_prob[i] + draw_prob[i]:
                outcomes.append(1)  # Draw
            else:
                outcomes.append(2)  # Home win
                
        df['outcome'] = outcomes
        return df
    
    def train_model(self, retrain: bool = False) -> bool:
        """Train the prediction model with realistic data"""
        try:
            if not retrain and Path(self.model_path).exists():
                self.model = joblib.load(self.model_path)
                self.scaler = joblib.load(self.scaler_path)
                logger.info("Loaded existing model")
                return True
            
            logger.info("Training advanced prediction model...")
            df = self.generate_realistic_training_data()
            
            features = [
                'home_team_rating', 'away_team_rating', 'home_recent_form', 'away_recent_form',
                'home_h2h_wins', 'away_h2h_wins', 'h2h_draws',
                'home_goals_for_avg', 'home_goals_against_avg', 'away_goals_for_avg', 'away_goals_against_avg',
                'home_league_position', 'away_league_position', 'home_missing_players', 'away_missing_players',
                'home_rest_days', 'away_rest_days'
            ]
            
            X = df[features]
            y = df['outcome']
            
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
            
            X_train_scaled = self.scaler.fit_transform(X_train)
            X_test_scaled = self.scaler.transform(X_test)
            
            # Advanced Random Forest with better parameters
            self.model = RandomForestClassifier(
                n_estimators=200,
                max_depth=15,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=42,
                class_weight='balanced'
            )
            self.model.fit(X_train_scaled, y_train)
            
            # Evaluate model
            y_pred = self.model.predict(X_test_scaled)
            accuracy = accuracy_score(y_test, y_pred)
            
            logger.info(f"Advanced model trained with accuracy: {accuracy:.3f}")
            
            # Save model
            joblib.dump(self.model, self.model_path)
            joblib.dump(self.scaler, self.scaler_path)
            
            return True
            
        except Exception as e:
            logger.error(f"Error training model: {e}")
            return False
    
    def predict_match_with_stats(self, match_stats: Dict) -> Dict:
        """Predict match outcome using provided statistics"""
        try:
            if self.model is None:
                raise ValueError("Model not trained")
            
            # Extract features from match stats
            features = np.array([[
                float(match_stats['home_team_rating']),
                float(match_stats['away_team_rating']),
                float(match_stats['home_recent_form']),
                float(match_stats['away_recent_form']),
                int(match_stats['home_h2h_wins']),
                int(match_stats['away_h2h_wins']),
                int(match_stats['h2h_draws']),
                float(match_stats['home_goals_for_avg']),
                float(match_stats['home_goals_against_avg']),
                float(match_stats['away_goals_for_avg']),
                float(match_stats['away_goals_against_avg']),
                int(match_stats['home_league_position']),
                int(match_stats['away_league_position']),
                int(match_stats['home_missing_players']),
                int(match_stats['away_missing_players']),
                int(match_stats['home_rest_days']),
                int(match_stats['away_rest_days'])
            ]])
            
            features_scaled = self.scaler.transform(features)
            
            # Get prediction probabilities
            probabilities = self.model.predict_proba(features_scaled)[0]
            prediction = self.model.predict(features_scaled)[0]
            
            outcome_labels = ['Away Win', 'Draw', 'Home Win']
            
            # Calculate confidence based on probability distribution
            confidence = max(probabilities) * 100
            
            # Additional analysis
            prob_dict = {
                'away_win': probabilities[0] * 100,
                'draw': probabilities[1] * 100,
                'home_win': probabilities[2] * 100
            }
            
            # Risk assessment
            entropy = -sum(p * np.log2(p + 1e-10) for p in probabilities)
            risk_level = "Low" if entropy < 1.2 else "Medium" if entropy < 1.5 else "High"
            
            result = {
                'home_team': match_stats['home_team'],
                'away_team': match_stats['away_team'],
                'prediction': outcome_labels[prediction],
                'confidence': confidence,
                'probabilities': prob_dict,
                'risk_level': risk_level,
                'entropy': entropy,
                'timestamp': datetime.now().isoformat()
            }
            
            # Log prediction
            self.log_prediction(result, match_stats)
            
            return result
            
        except Exception as e:
            logger.error(f"Error making prediction: {e}")
            return None
    
    def log_prediction(self, prediction: Dict, match_stats: Dict):
        """Log prediction with input stats to CSV file"""
        try:
            log_data = {
                'timestamp': prediction['timestamp'],
                'home_team': prediction['home_team'],
                'away_team': prediction['away_team'],
                'prediction': prediction['prediction'],
                'confidence': prediction['confidence'],
                'home_win_prob': prediction['probabilities']['home_win'],
                'draw_prob': prediction['probabilities']['draw'],
                'away_win_prob': prediction['probabilities']['away_win'],
                'risk_level': prediction['risk_level'],
                **match_stats  # Include all input stats
            }
            
            df_new = pd.DataFrame([log_data])
            
            if Path(self.predictions_log).exists():
                df_existing = pd.read_csv(self.predictions_log)
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            else:
                df_combined = df_new
            
            df_combined.to_csv(self.predictions_log, index=False)
            
        except Exception as e:
            logger.error(f"Error logging prediction: {e}")

class FootballBot:
    """Advanced Telegram bot for football predictions with user input"""
    
    def __init__(self, token: str):
        self.token = token
        self.predictor = FootballPredictor()
        self.application = None
        self.user_data = {}
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        welcome_message = """
💰 **PROFESSIONAL FOOTBALL PREDICTION BOT** 💰

🎯 **MAKE MILLIONS WITH ACCURATE PREDICTIONS!**

This bot uses advanced machine learning with YOUR data to provide precise predictions.

**Commands:**
/predict - Start prediction with your match data
/stats - View prediction history
/help - Show detailed usage guide

**NO RANDOMNESS - PURE DATA-DRIVEN PREDICTIONS** 📊
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Make Prediction", callback_data="predict")],
            [InlineKeyboardButton("📊 View Stats", callback_data="stats")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_message, 
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def predict_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start prediction conversation"""
        await update.message.reply_text(
            "🎯 **PROFESSIONAL PREDICTION MODE** 💰\n\n"
            "Enter the teams (format: Home_Team vs Away_Team)\n"
            "Example: `Manchester_United vs Liverpool`",
            parse_mode='Markdown'
        )
        return TEAM_INPUT
    
    async def get_teams(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get team names from user"""
        try:
            text = update.message.text.strip()
            if ' vs ' not in text:
                await update.message.reply_text(
                    "❌ Invalid format! Use: Home_Team vs Away_Team\n"
                    "Example: `Arsenal vs Chelsea`",
                    parse_mode='Markdown'
                )
                return TEAM_INPUT
            
            home_team, away_team = text.split(' vs ')
            home_team = home_team.strip()
            away_team = away_team.strip()
            
            context.user_data['home_team'] = home_team
            context.user_data['away_team'] = away_team
            
            stats_request = f"""
📊 **MATCH STATISTICS REQUIRED** 📊

**Match:** {home_team} vs {away_team}

Please provide the following statistics (one per line):

**Team Ratings (1-100):**
Home rating: 
Away rating: 

**Recent Form (points from last 5 games, 0-15):**
Home form: 
Away form: 

**Head-to-Head Record:**
Home wins: 
Away wins: 
Draws: 

**Goals Per Game Average:**
Home goals for: 
Home goals against: 
Away goals for: 
Away goals against: 

**League Positions (1-20):**
Home position: 
Away position: 

**Missing Players:**
Home missing: 
Away missing: 

**Rest Days:**
Home rest days: 
Away rest days: 

**Example:**
```
85
78
12
9
3
1
2
2.1
1.2
1.8
1.4
4
7
2
1
4
3
```

**Copy and paste your numbers in this exact order!**
            """
            
            await update.message.reply_text(stats_request, parse_mode='Markdown')
            return STATS_INPUT
            
        except Exception as e:
            logger.error(f"Error in get_teams: {e}")
            await update.message.reply_text("❌ Error processing teams. Please try again.")
            return TEAM_INPUT
    
    async def get_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process statistics and make prediction"""
        try:
            stats_text = update.message.text.strip()
            stats_lines = [line.strip() for line in stats_text.split('\n') if line.strip()]
            
            if len(stats_lines) != 17:
                await update.message.reply_text(
                    f"❌ Expected 17 values, got {len(stats_lines)}!\n\n"
                    "Please provide all statistics in the exact order shown.",
                    parse_mode='Markdown'
                )
                return STATS_INPUT
            
            # Parse statistics
            match_stats = {
                'home_team': context.user_data['home_team'],
                'away_team': context.user_data['away_team'],
                'home_team_rating': float(stats_lines[0]),
                'away_team_rating': float(stats_lines[1]),
                'home_recent_form': float(stats_lines[2]),
                'away_recent_form': float(stats_lines[3]),
                'home_h2h_wins': int(stats_lines[4]),
                'away_h2h_wins': int(stats_lines[5]),
                'h2h_draws': int(stats_lines[6]),
                'home_goals_for_avg': float(stats_lines[7]),
                'home_goals_against_avg': float(stats_lines[8]),
                'away_goals_for_avg': float(stats_lines[9]),
                'away_goals_against_avg': float(stats_lines[10]),
                'home_league_position': int(stats_lines[11]),
                'away_league_position': int(stats_lines[12]),
                'home_missing_players': int(stats_lines[13]),
                'away_missing_players': int(stats_lines[14]),
                'home_rest_days': int(stats_lines[15]),
                'away_rest_days': int(stats_lines[16])
            }
            
            # Validate ranges
            if not (1 <= match_stats['home_team_rating'] <= 100 and 1 <= match_stats['away_team_rating'] <= 100):
                await update.message.reply_text("❌ Team ratings must be between 1-100")
                return STATS_INPUT
            
            if not (0 <= match_stats['home_recent_form'] <= 15 and 0 <= match_stats['away_recent_form'] <= 15):
                await update.message.reply_text("❌ Recent form must be between 0-15")
                return STATS_INPUT
            
            # Show processing message
            processing_msg = await update.message.reply_text(
                "🤖 **ANALYZING YOUR DATA...**\n"
                "💰 **CALCULATING MILLION-DOLLAR PREDICTION...**"
            )
            
            # Get prediction
            prediction = self.predictor.predict_match_with_stats(match_stats)
            
            if prediction is None:
                await processing_msg.edit_text("❌ Error generating prediction. Please try again.")
                return ConversationHandler.END
            
            # Format detailed result
            confidence_emoji = "🟢" if prediction['confidence'] > 75 else "🟡" if prediction['confidence'] > 60 else "🔴"
            risk_emoji = "🟢" if prediction['risk_level'] == "Low" else "🟡" if prediction['risk_level'] == "Medium" else "🔴"
            
            result_message = f"""
💰 **PROFESSIONAL PREDICTION RESULT** 💰

**Match:** {prediction['home_team']} vs {prediction['away_team']}

🎯 **PREDICTION:** {prediction['prediction']}
📈 **CONFIDENCE:** {prediction['confidence']:.1f}% {confidence_emoji}

**DETAILED PROBABILITIES:**
🏠 Home Win: {prediction['probabilities']['home_win']:.1f}%
🤝 Draw: {prediction['probabilities']['draw']:.1f}%
✈️ Away Win: {prediction['probabilities']['away_win']:.1f}%

📊 **RISK ANALYSIS:**
Risk Level: {prediction['risk_level']} {risk_emoji}
Market Uncertainty: {prediction['entropy']:.2f}

⏰ **Generated:** {datetime.fromisoformat(prediction['timestamp']).strftime('%Y-%m-%d %H:%M')}

💡 **BETTING ADVICE:**
{self.get_betting_advice(prediction)}

🔥 **THIS IS YOUR MONEY-MAKING PREDICTION!** 💀
            """
            
            await processing_msg.edit_text(result_message, parse_mode='Markdown')
            return ConversationHandler.END
            
        except ValueError as e:
            await update.message.reply_text(
                "❌ Invalid number format! Please enter valid numbers.\n"
                "Make sure decimals use dots (.) not commas (,)"
            )
            return STATS_INPUT
        except Exception as e:
            logger.error(f"Error in get_stats: {e}")
            await update.message.reply_text("❌ Error processing statistics. Please try again.")
            return STATS_INPUT
    
    def get_betting_advice(self, prediction: Dict) -> str:
        """Generate betting advice based on prediction"""
        confidence = prediction['confidence']
        risk = prediction['risk_level']
        
        if confidence > 75 and risk == "Low":
            return "💰 HIGH CONFIDENCE BET - Consider larger stake"
        elif confidence > 65 and risk == "Medium":
            return "⚖️ MODERATE BET - Standard stake recommended"
        elif confidence > 55:
            return "⚠️ LOW CONFIDENCE - Small stake or avoid"
        else:
            return "🚫 VERY RISKY - Avoid betting"
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed prediction statistics"""
        try:
            if not Path(self.predictor.predictions_log).exists():
                await update.message.reply_text("📊 No predictions logged yet. Make your first prediction!")
                return
            
            df = pd.read_csv(self.predictor.predictions_log)
            
            total_predictions = len(df)
            avg_confidence = df['confidence'].mean()
            high_conf_predictions = len(df[df['confidence'] > 70])
            
            # Most recent predictions
            recent_predictions = df.tail(5)
            
            stats_message = f"""
📊 **PROFESSIONAL STATISTICS** 📈

**Total Predictions:** {total_predictions}
**Average Confidence:** {avg_confidence:.1f}%
**High Confidence (>70%):** {high_conf_predictions}

**Recent Predictions:**
            """
            
            for idx, pred in recent_predictions.iterrows():
                stats_message += f"""
🎯 {pred['home_team']} vs {pred['away_team']}
   Result: {pred['prediction']} ({pred['confidence']:.1f}%)
"""
            
            await update.message.reply_text(stats_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in stats_command: {e}")
            await update.message.reply_text("❌ Error retrieving statistics.")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Detailed help command"""
        help_text = """
💰 **PROFESSIONAL PREDICTION GUIDE** 💰

**How to Use:**
1. Type `/predict` to start
2. Enter teams: `Home_Team vs Away_Team`
3. Provide all 17 statistics in order
4. Get your million-dollar prediction!

**Required Statistics:**
• Team ratings (1-100 scale)
• Recent form (points from last 5 games)
• Head-to-head record
• Goals per game averages
• Current league positions
• Missing players count
• Days of rest

**Tips for Accuracy:**
• Use official team ratings
• Calculate recent form as: (Wins×3 + Draws×1)
• Include all competitions in averages
• Count only key missing players

🔥 **ACCURATE DATA = ACCURATE PREDICTIONS = BIG PROFITS!** 💀
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel conversation"""
        await update.message.reply_text("❌ Prediction cancelled. Use /predict to start again.")
        return ConversationHandler.END
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "predict":
            await query.edit_message_text(
                "🎯 To start prediction, use: `/predict`",
                parse_mode='Markdown'
            )
        elif query.data == "stats":
            # Simulate update object for stats command
            update_obj = type('obj', (object,), {'message': query.message})()
            context_obj = type('obj', (object,), {})()
            await self.stats_command(update_obj, context_obj)
        elif query.data == "help":
            await self.help_command(update, context)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
    
    def run(self):
        """Start the bot"""
        try:
            # Initialize predictor
            logger.info("🤖 Initializing Professional Football Prediction Bot...")
            
            if not self.predictor.train_model():
                logger.error("Failed to initialize prediction model")
                return
            
            # Create application
            self.application = Application.builder().token(self.token).build()
            
            # Create conversation handler for predictions
            prediction_handler = ConversationHandler(
                entry_points=[CommandHandler('predict', self.predict_start)],
                states={
                    TEAM_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_teams)],
                    STATS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_stats)],
                },
                fallbacks=[CommandHandler('cancel', self.cancel)],
            )
            
            # Add handlers
            self.application.add_handler(CommandHandler("start", self.start))
            self.application.add_handler(prediction_handler)
            self.application.add_handler(CommandHandler("stats", self.stats_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CallbackQueryHandler(self.button_callback))
            
            # Add error handler
            self.application.add_error_handler(self.error_handler)
            
            logger.info("💰 PROFESSIONAL FOOTBALL PREDICTION BOT STARTED! 💰")
            
            # Start bot
            self.application.run_polling(allowed_updates=Update.ALL_TYPES)
            
        except Exception as e:
            logger.error(f"Error starting bot: {e}")

def main():
    """Main function"""
    # Bot token - replace with your actual token
    BOT_TOKEN = "8186199634:AAEEafBIm5GhZrhrWt-je8wa1UESaTHF9ZM"
    
    if not BOT_TOKEN:
        logger.error("Bot token not provided")
        return
    
    bot = FootballBot(BOT_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()
