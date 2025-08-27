#!/usr/bin/env python3
"""
🔥 Ultimate Memecoin Alert Bot for Telegram
Monitor multiple memecoins and get smart trading alerts!
"""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp
import requests
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Get from @BotFather
COINGECKO_API = "https://api.coingecko.com/api/v3"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
CHECK_INTERVAL = 300  # 5 minutes
DATABASE_FILE = "memecoin_alerts.db"

class MemecoinBot:
    def __init__(self, token: str):
        self.token = token
        self.bot = Bot(token=token)
        self.application = Application.builder().token(token).build()
        self.init_database()
        self.volume_history = {}  # Track volume for spike detection

    def init_database(self):
        """Initialize SQLite database for storing alerts"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                coin_id TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                target_price REAL,
                drop_percent REAL,
                volume_percent REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_triggered TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS volume_history (
                coin_id TEXT PRIMARY KEY,
                volume_24h REAL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_msg = """
🔥 **Ultimate Memecoin Alert Bot** 🚀

I'll help you catch the best memecoin opportunities!

**Available Commands:**
• `/setprice <coin> <price>` - Alert when price drops below target
• `/setdrop <coin> <percent>` - Alert on percentage drops
• `/setvolume <coin> <percent>` - Alert on volume spikes
• `/setsmart <coin> <drop%> <volume%>` - Smart alerts (dip + hype)
• `/list` - Show your active alerts
• `/remove <coin>` - Remove alerts for a coin
• `/clear` - Clear all your alerts
• `/help` - Show this message

**Examples:**
• `/setprice dogecoin 0.05` - Alert if DOGE < $0.05
• `/setdrop shiba-inu 15` - Alert if SHIB drops 15%
• `/setsmart pepe 10 50` - Alert if PEPE drops 10% + volume up 50%

Ready to catch those pumps! 🚀
        """
        await update.message.reply_text(welcome_msg, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await self.start_command(update, context)

    async def setprice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setprice command"""
        if len(context.args) != 2:
            await update.message.reply_text(
                "❌ Usage: `/setprice <coin> <target_price>`\n"
                "Example: `/setprice dogecoin 0.05`",
                parse_mode='Markdown'
            )
            return

        coin_id = context.args[0].lower()
        try:
            target_price = float(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Price must be a valid number!")
            return

        # Verify coin exists
        if not await self.verify_coin_exists(coin_id):
            await update.message.reply_text(f"❌ Coin '{coin_id}' not found on CoinGecko!")
            return

        # Save alert
        self.save_alert(
            user_id=update.effective_user.id,
            username=update.effective_user.username,
            coin_id=coin_id,
            alert_type="price",
            target_price=target_price
        )

        await update.message.reply_text(
            f"✅ Price alert set!\n"
            f"🪙 **{coin_id.upper()}**\n"
            f"💰 Target: ${target_price}\n"
            f"📢 I'll notify you when price drops below this level!",
            parse_mode='Markdown'
        )

    async def setdrop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setdrop command"""
        if len(context.args) != 2:
            await update.message.reply_text(
                "❌ Usage: `/setdrop <coin> <drop_percent>`\n"
                "Example: `/setdrop shiba-inu 15`",
                parse_mode='Markdown'
            )
            return

        coin_id = context.args[0].lower()
        try:
            drop_percent = float(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Drop percentage must be a valid number!")
            return

        if not await self.verify_coin_exists(coin_id):
            await update.message.reply_text(f"❌ Coin '{coin_id}' not found on CoinGecko!")
            return

        self.save_alert(
            user_id=update.effective_user.id,
            username=update.effective_user.username,
            coin_id=coin_id,
            alert_type="drop",
            drop_percent=drop_percent
        )

        await update.message.reply_text(
            f"✅ Drop alert set!\n"
            f"🪙 **{coin_id.upper()}**\n"
            f"📉 Trigger: -{drop_percent}% drop\n"
            f"📢 I'll notify you when this coin dips!",
            parse_mode='Markdown'
        )

    async def setvolume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setvolume command"""
        if len(context.args) != 2:
            await update.message.reply_text(
                "❌ Usage: `/setvolume <coin> <volume_spike_percent>`\n"
                "Example: `/setvolume pepe 50`",
                parse_mode='Markdown'
            )
            return

        coin_id = context.args[0].lower()
        try:
            volume_percent = float(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Volume percentage must be a valid number!")
            return

        if not await self.verify_coin_exists(coin_id):
            await update.message.reply_text(f"❌ Coin '{coin_id}' not found on CoinGecko!")
            return

        self.save_alert(
            user_id=update.effective_user.id,
            username=update.effective_user.username,
            coin_id=coin_id,
            alert_type="volume",
            volume_percent=volume_percent
        )

        await update.message.reply_text(
            f"✅ Volume alert set!\n"
            f"🪙 **{coin_id.upper()}**\n"
            f"📊 Trigger: +{volume_percent}% volume spike\n"
            f"📢 I'll notify you when trading heats up!",
            parse_mode='Markdown'
        )

    async def setsmart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setsmart command - the ultimate alert!"""
        if len(context.args) != 3:
            await update.message.reply_text(
                "❌ Usage: `/setsmart <coin> <drop_percent> <volume_spike_percent>`\n"
                "Example: `/setsmart floki 10 40`",
                parse_mode='Markdown'
            )
            return

        coin_id = context.args[0].lower()
        try:
            drop_percent = float(context.args[1])
            volume_percent = float(context.args[2])
        except ValueError:
            await update.message.reply_text("❌ Percentages must be valid numbers!")
            return

        if not await self.verify_coin_exists(coin_id):
            await update.message.reply_text(f"❌ Coin '{coin_id}' not found on CoinGecko!")
            return

        self.save_alert(
            user_id=update.effective_user.id,
            username=update.effective_user.username,
            coin_id=coin_id,
            alert_type="smart",
            drop_percent=drop_percent,
            volume_percent=volume_percent
        )

        await update.message.reply_text(
            f"🧠 **SMART ALERT SET!** 🚀\n"
            f"🪙 **{coin_id.upper()}**\n"
            f"📉 Drop trigger: -{drop_percent}%\n"
            f"📊 Volume trigger: +{volume_percent}%\n"
            f"🎯 **Perfect entry signals = dip + hype!**",
            parse_mode='Markdown'
        )

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list command"""
        user_id = update.effective_user.id
        alerts = self.get_user_alerts(user_id)

        if not alerts:
            await update.message.reply_text("📭 No active alerts set!")
            return

        message = "📋 **Your Active Alerts:**\n\n"
        keyboard = []

        for alert in alerts:
            coin = alert['coin_id'].upper()
            alert_type = alert['alert_type']

            if alert_type == "price":
                message += f"💰 **{coin}** - Price < ${alert['target_price']}\n"
            elif alert_type == "drop":
                message += f"📉 **{coin}** - Drop > {alert['drop_percent']}%\n"
            elif alert_type == "volume":
                message += f"📊 **{coin}** - Volume spike > {alert['volume_percent']}%\n"
            elif alert_type == "smart":
                message += f"🧠 **{coin}** - Smart ({alert['drop_percent']}% drop + {alert['volume_percent']}% volume)\n"

            # Add remove button for each alert
            keyboard.append([
                InlineKeyboardButton(
                    f"❌ Remove {coin}",
                    callback_data=f"remove_{alert['coin_id']}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🗑️ Clear All", callback_data="clear_all")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

    async def remove_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /remove command"""
        if len(context.args) != 1:
            await update.message.reply_text(
                "❌ Usage: `/remove <coin>`\n"
                "Example: `/remove dogecoin`",
                parse_mode='Markdown'
            )
            return

        coin_id = context.args[0].lower()
        user_id = update.effective_user.id

        if self.remove_user_alert(user_id, coin_id):
            await update.message.reply_text(
                f"✅ Removed all alerts for **{coin_id.upper()}**!",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ No alerts found for **{coin_id.upper()}**!",
                parse_mode='Markdown'
            )

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /clear command"""
        user_id = update.effective_user.id

        if self.clear_user_alerts(user_id):
            await update.message.reply_text("✅ All your alerts have been cleared!")
        else:
            await update.message.reply_text("📭 No alerts to clear!")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks"""
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        data = query.data

        if data.startswith("remove_"):
            coin_id = data.replace("remove_", "")
            if self.remove_user_alert(user_id, coin_id):
                await query.edit_message_text(
                    f"✅ Removed all alerts for **{coin_id.upper()}**!",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text("❌ Alert not found!")

        elif data == "clear_all":
            if self.clear_user_alerts(user_id):
                await query.edit_message_text("✅ All your alerts have been cleared!")
            else:
                await query.edit_message_text("📭 No alerts to clear!")

    async def verify_coin_exists(self, coin_id: str) -> bool:
        """Verify if coin exists on CoinGecko"""
        try:
            url = f"{COINGECKO_API}/simple/price"
            params = {"ids": coin_id, "vs_currencies": "usd"}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    data = await response.json()
                    return coin_id in data
        except Exception as e:
            logger.error(f"Error verifying coin {coin_id}: {e}")
            return False

    def save_alert(self, user_id: int, username: str, coin_id: str, alert_type: str,
                   target_price: float = None, drop_percent: float = None,
                   volume_percent: float = None):
        """Save alert to database"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # Remove existing alerts for same user and coin
        cursor.execute(
            "DELETE FROM alerts WHERE user_id = ? AND coin_id = ?",
            (user_id, coin_id)
        )

        cursor.execute('''
            INSERT INTO alerts (user_id, username, coin_id, alert_type, 
                               target_price, drop_percent, volume_percent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, coin_id, alert_type, target_price, drop_percent, volume_percent))

        conn.commit()
        conn.close()

    def get_user_alerts(self, user_id: int) -> List[Dict]:
        """Get all alerts for a user"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM alerts WHERE user_id = ? AND is_active = 1",
            (user_id,)
        )

        columns = [desc[0] for desc in cursor.description]
        alerts = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()
        return alerts

    def get_all_active_alerts(self) -> List[Dict]:
        """Get all active alerts"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM alerts WHERE is_active = 1")

        columns = [desc[0] for desc in cursor.description]
        alerts = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()
        return alerts

    def remove_user_alert(self, user_id: int, coin_id: str) -> bool:
        """Remove all alerts for a user and coin"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM alerts WHERE user_id = ? AND coin_id = ?",
            (user_id, coin_id)
        )

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        return rows_affected > 0

    def clear_user_alerts(self, user_id: int) -> bool:
        """Clear all alerts for a user"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        return rows_affected > 0

    def update_alert_triggered(self, alert_id: int):
        """Update last triggered timestamp"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE alerts SET last_triggered = CURRENT_TIMESTAMP WHERE id = ?",
            (alert_id,)
        )

        conn.commit()
        conn.close()

    def should_skip_alert(self, alert: Dict) -> bool:
        """Check if alert was recently triggered (cooldown)"""
        if not alert['last_triggered']:
            return False

        last_triggered = datetime.fromisoformat(alert['last_triggered'])
        cooldown_period = timedelta(hours=1)  # 1 hour cooldown

        return datetime.now() - last_triggered < cooldown_period

    def update_volume_history(self, coin_id: str, volume: float):
        """Update volume history for spike detection"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO volume_history (coin_id, volume_24h)
            VALUES (?, ?)
        ''', (coin_id, volume))

        conn.commit()
        conn.close()

    def get_volume_history(self, coin_id: str) -> Optional[float]:
        """Get previous volume for comparison"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT volume_24h FROM volume_history WHERE coin_id = ?",
            (coin_id,)
        )

        result = cursor.fetchone()
        conn.close()

        return result[0] if result else None

    async def fetch_coin_data(self, coin_ids: List[str]) -> Dict:
        """Fetch coin data from CoinGecko API"""
        try:
            coin_ids_str = ",".join(coin_ids)
            url = f"{COINGECKO_API}/simple/price"
            params = {
                "ids": coin_ids_str,
                "vs_currencies": "usd",
                "include_24hr_vol": "true",
                "include_24hr_change": "true"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"CoinGecko API error: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error fetching coin data: {e}")
            return {}

    async def check_alerts(self):
        """Main alert checking function"""
        logger.info("🔍 Checking alerts...")

        alerts = self.get_all_active_alerts()
        if not alerts:
            return

        # Get unique coin IDs
        coin_ids = list(set(alert['coin_id'] for alert in alerts))

        # Fetch current data
        coin_data = await self.fetch_coin_data(coin_ids)

        for alert in alerts:
            try:
                await self.process_alert(alert, coin_data)
            except Exception as e:
                logger.error(f"Error processing alert {alert['id']}: {e}")

    async def process_alert(self, alert: Dict, coin_data: Dict):
        """Process individual alert"""
        coin_id = alert['coin_id']

        if coin_id not in coin_data:
            logger.warning(f"No data for coin {coin_id}")
            return

        if self.should_skip_alert(alert):
            return

        data = coin_data[coin_id]
        current_price = data.get('usd', 0)
        change_24h = data.get('usd_24h_change', 0)
        volume_24h = data.get('usd_24h_vol', 0)

        # Check volume spike
        previous_volume = self.get_volume_history(coin_id)
        volume_spike = 0
        if previous_volume and previous_volume > 0:
            volume_spike = ((volume_24h - previous_volume) / previous_volume) * 100

        # Update volume history
        self.update_volume_history(coin_id, volume_24h)

        # Check alert conditions
        should_alert = False
        alert_message = ""

        if alert['alert_type'] == 'price' and current_price <= alert['target_price']:
            should_alert = True
            alert_message = f"💰 **PRICE ALERT: {coin_id.upper()}**\n"

        elif alert['alert_type'] == 'drop' and change_24h <= -abs(alert['drop_percent']):
            should_alert = True
            alert_message = f"📉 **DIP ALERT: {coin_id.upper()}**\n"

        elif alert['alert_type'] == 'volume' and volume_spike >= alert['volume_percent']:
            should_alert = True
            alert_message = f"📊 **VOLUME SPIKE: {coin_id.upper()}**\n"

        elif alert['alert_type'] == 'smart':
            drop_triggered = change_24h <= -abs(alert['drop_percent'])
            volume_triggered = volume_spike >= alert['volume_percent']

            if drop_triggered and volume_triggered:
                should_alert = True
                alert_message = f"🧠 **SMART SIGNAL: {coin_id.upper()}** 🚀\n"

        if should_alert:
            # Build complete alert message
            alert_message += f"• **Current Price:** ${current_price:.8f}\n"
            alert_message += f"• **24h Change:** {change_24h:+.2f}%\n"
            alert_message += f"• **24h Volume:** ${volume_24h:,.0f}\n"

            if volume_spike > 0:
                alert_message += f"• **Volume Spike:** +{volume_spike:.1f}%\n"

            if alert['alert_type'] == 'smart':
                alert_message += f"\n🎯 **Perfect Entry Signal!** (Dip + Hype)\n"

            alert_message += f"⏰ {datetime.now().strftime('%H:%M:%S UTC')}"

            # Send alert
            try:
                await self.bot.send_message(
                    chat_id=alert['user_id'],
                    text=alert_message,
                    parse_mode='Markdown'
                )

                # Update last triggered
                self.update_alert_triggered(alert['id'])
                logger.info(f"✅ Alert sent to user {alert['user_id']} for {coin_id}")

            except Exception as e:
                logger.error(f"Failed to send alert to user {alert['user_id']}: {e}")

    async def monitoring_loop(self):
        """Main monitoring loop"""
        while True:
            try:
                await self.check_alerts()
                await asyncio.sleep(CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    def setup_handlers(self):
        """Setup command handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("setprice", self.setprice_command))
        self.application.add_handler(CommandHandler("setdrop", self.setdrop_command))
        self.application.add_handler(CommandHandler("setvolume", self.setvolume_command))
        self.application.add_handler(CommandHandler("setsmart", self.setsmart_command))
        self.application.add_handler(CommandHandler("list", self.list_command))
        self.application.add_handler(CommandHandler("remove", self.remove_command))
        self.application.add_handler(CommandHandler("clear", self.clear_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

    async def run(self):
        """Start the bot"""
        self.setup_handlers()

        # Start monitoring loop
        monitoring_task = asyncio.create_task(self.monitoring_loop())

        # Start bot
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        logger.info("🤖 Memecoin Alert Bot is running!")
        logger.info(f"📊 Checking alerts every {CHECK_INTERVAL} seconds")

        try:
            await monitoring_task
        except KeyboardInterrupt:
            logger.info("🛑 Stopping bot...")
        finally:
            await self.application.stop()


async def main():
    """Main function"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set your bot token!")
        print("1. Create a bot with @BotFather on Telegram")
        print("2. Replace 'YOUR_BOT_TOKEN_HERE' with your actual token")
        return

    bot = MemecoinBot(BOT_TOKEN)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())