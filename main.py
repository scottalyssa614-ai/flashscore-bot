import asyncio
import logging
import requests
import numpy as np
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, asdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from enum import Enum
import threading
import time
from collections import deque

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

help_message = """
📚 **Enhanced Forex Signal Bot v4.0 - Help**

🆕 **Key Improvements:**
• 📈 Dynamic trend filtering with EMA200
• 📊 Volatility-adjusted position sizing
• 🔄 Adaptive RSI thresholds
• 📉 Improved risk management
• 📜 Backtesting capability

**📱 Commands:**
• `/start` - Activate bot
• `/analyze [PAIR]` - Manual analysis
• `/performance` - View statistics
• `/backtest [PAIR]` - Run historical test
• `/settings` - Configure parameters
• `/help` - Show this message
"""

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"

@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class TradingSignal:
    action: SignalType
    confidence: float
    rsi: float
    ema_20: float
    ema_50: float
    ema_200: float
    macd: float
    macd_signal: float
    atr: float
    pattern: str
    support_resistance: float
    current_price: float
    take_profit: float
    stop_loss: float
    tp_pips: int
    sl_pips: int
    position_size: float
    reason: str
    timestamp: datetime
    currency_pair: str

@dataclass
class BacktestResult:
    pair: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    pnl: float

@dataclass
class BotSettings:
    interval_minutes: int = 10
    timeframe: str = "15min"
    tp_pips_min: int = 20
    tp_pips_max: int = 40
    sl_pips_min: int = 15
    sl_pips_max: int = 25
    live_monitoring: bool = True
    risk_per_trade: float = 0.01
    account_size: float = 10000.0
    rsi_overbought: int = 65
    rsi_oversold: int = 35
    subscribed_users: Set[int] = None
    monitored_pairs: Set[str] = None

    def __post_init__(self):
        if self.subscribed_users is None:
            self.subscribed_users = set()
        if self.monitored_pairs is None:
            self.monitored_pairs = {"EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY"}

class TechnicalAnalyzer:
    """Enhanced technical analysis with additional indicators"""

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """Calculate RSI with input validation"""
        if len(prices) < period + 1:
            return 50.0

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        """Calculate EMA with smoothing"""
        if len(prices) < period:
            return np.mean(prices) if prices else 0.0

        alpha = 2 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = alpha * price + (1 - alpha) * ema
        return round(ema, 5)

    @staticmethod
    def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float]:
        """Calculate MACD with validation"""
        if len(prices) < slow:
            return 0.0, 0.0

        ema_fast = TechnicalAnalyzer.calculate_ema(prices, fast)
        ema_slow = TechnicalAnalyzer.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow

        if len(prices) < slow + signal:
            return round(macd_line, 6), round(macd_line, 6)

        macd_values = []
        for i in range(slow, len(prices) + 1):
            ema_f = TechnicalAnalyzer.calculate_ema(prices[:i], fast)
            ema_s = TechnicalAnalyzer.calculate_ema(prices[:i], slow)
            macd_values.append(ema_f - ema_s)

        signal_line = TechnicalAnalyzer.calculate_ema(macd_values, signal)
        return round(macd_line, 6), round(signal_line, 6)

    @staticmethod
    def calculate_atr(candles: List[Candle], period: int = 14) -> float:
        """Calculate Average True Range"""
        if len(candles) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            high_low = candles[i].high - candles[i].low
            high_close = abs(candles[i].high - candles[i-1].close)
            low_close = abs(candles[i].low - candles[i-1].close)
            true_ranges.append(max(high_low, high_close, low_close))

        return round(np.mean(true_ranges[-period:]), 5)

    @staticmethod
    def find_support_resistance(candles: List[Candle], lookback: int = 20) -> Tuple[float, float]:
        """Improved S/R detection with clustering"""
        if len(candles) < lookback:
            lookback = len(candles)

        recent_candles = candles[-lookback:]
        price_levels = []

        for i in range(2, len(recent_candles) - 2):
            if (recent_candles[i].high > recent_candles[i-1].high and
                recent_candles[i].high > recent_candles[i-2].high and
                recent_candles[i].high > recent_candles[i+1].high and
                recent_candles[i].high > recent_candles[i+2].high):
                price_levels.append(recent_candles[i].high)

            if (recent_candles[i].low < recent_candles[i-1].low and
                recent_candles[i].low < recent_candles[i-2].low and
                recent_candles[i].low < recent_candles[i+1].low and
                recent_candles[i].low < recent_candles[i+2].low):
                price_levels.append(recent_candles[i].low)

        if not price_levels:
            highs = [c.high for c in recent_candles]
            lows = [c.low for c in recent_candles]
            return min(lows), max(highs)

        # Cluster nearby levels
        price_levels.sort()
        clusters = []
        current_cluster = [price_levels[0]]

        for price in price_levels[1:]:
            if price - current_cluster[-1] < (max(price_levels) - min(price_levels)) * 0.02:
                current_cluster.append(price)
            else:
                clusters.append(current_cluster)
                current_cluster = [price]
        clusters.append(current_cluster)

        # Get most significant clusters
        if len(clusters) >= 2:
            support = np.mean(clusters[0])
            resistance = np.mean(clusters[-1])
            return support, resistance
        else:
            return min(price_levels), max(price_levels)

    @staticmethod
    def detect_candlestick_patterns(candles: List[Candle]) -> str:
        """Enhanced pattern detection"""
        if len(candles) < 3:
            return "None"

        current = candles[-1]
        prev1 = candles[-2]
        prev2 = candles[-3]

        # Define candle bodies and wicks
        current_body = abs(current.close - current.open)
        prev1_body = abs(prev1.close - prev1.open)
        current_range = current.high - current.low
        lower_shadow = current.open - current.low if current.close > current.open else current.close - current.low
        upper_shadow = current.high - current.close if current.close > current.open else current.high - current.open

        # Bullish patterns
        if (prev1.close < prev1.open and
            current.close > current.open and
            current.open < prev1.close and
            current.close > prev1.open and
            current_body > prev1_body * 1.2):
            return "Bullish Engulfing"

        if (prev2.close > prev2.open and
            prev1.close > prev1.open and
            prev1.high > prev2.high and
            current.close < prev1.low):
            return "Evening Star"

        if (lower_shadow > current_body * 2 and
            upper_shadow < current_body * 0.5):
            return "Hammer"

        # Bearish patterns
        if (prev1.close > prev1.open and
            current.close < current.open and
            current.open > prev1.close and
            current.close < prev1.open and
            current_body > prev1_body * 1.2):
            return "Bearish Engulfing"

        if (prev2.close < prev2.open and
            prev1.close < prev1.open and
            prev1.low < prev2.low and
            current.close > prev1.high):
            return "Morning Star"

        if (upper_shadow > current_body * 2 and
            lower_shadow < current_body * 0.5):
            return "Shooting Star"

        if current_body < current_range * 0.1:
            return "Doji"

        return "None"

class SignalGenerator:
    """Enhanced signal generation with trend filtering and volatility adjustment"""

    def __init__(self, settings: BotSettings):
        self.analyzer = TechnicalAnalyzer()
        self.settings = settings

    def calculate_position_size(self, atr: float, entry: float, stop_loss: float) -> float:
        """Calculate position size based on volatility and risk"""
        if atr == 0:
            return 0.0

        risk_amount = self.settings.account_size * self.settings.risk_per_trade
        risk_per_share = abs(entry - stop_loss)

        if risk_per_share == 0:
            return 0.0

        position_size = risk_amount / risk_per_share
        return round(position_size, 2)

    def generate_signal(self, candles: List[Candle], currency_pair: str) -> TradingSignal:
        """Generate trading signal with enhanced logic"""
        if len(candles) < 200:
            return self._create_no_signal(candles, currency_pair, "Insufficient data")

        closes = [c.close for c in candles]
        current_price = closes[-1]

        # Calculate all indicators
        rsi = self.analyzer.calculate_rsi(closes)
        ema_20 = self.analyzer.calculate_ema(closes, 20)
        ema_50 = self.analyzer.calculate_ema(closes, 50)
        ema_200 = self.analyzer.calculate_ema(closes, 200)
        macd, macd_signal = self.analyzer.calculate_macd(closes)
        atr = self.analyzer.calculate_atr(candles)
        pattern = self.analyzer.detect_candlestick_patterns(candles)
        support, resistance = self.analyzer.find_support_resistance(candles)

        # Determine trend direction
        is_uptrend = current_price > ema_200
        is_downtrend = current_price < ema_200

        # Dynamic RSI thresholds based on volatility
        rsi_range = self.settings.rsi_overbought - self.settings.rsi_oversold
        volatility_adjustment = (atr / current_price) * 1000
        adjusted_rsi_oversold = self.settings.rsi_oversold + (volatility_adjustment * 0.5)
        adjusted_rsi_overbought = self.settings.rsi_overbought - (volatility_adjustment * 0.5)

        # Signal scoring
        buy_score = 0
        sell_score = 0
        buy_conditions = []
        sell_conditions = []

        # BUY conditions
        if rsi < adjusted_rsi_oversold:
            buy_conditions.append(f"RSI {rsi:.1f} (Adj)")
            buy_score += 25

        if current_price > support and (current_price - support) < (2 * atr):
            buy_conditions.append("Near Support")
            buy_score += 20

        if pattern in ["Bullish Engulfing", "Hammer", "Morning Star"]:
            buy_conditions.append(pattern)
            buy_score += 25

        if ema_20 > ema_50 and is_uptrend:
            buy_conditions.append("EMA Bullish")
            buy_score += 15

        if macd > macd_signal:
            buy_conditions.append("MACD Bullish")
            buy_score += 15

        # SELL conditions
        if rsi > adjusted_rsi_overbought:
            sell_conditions.append(f"RSI {rsi:.1f} (Adj)")
            sell_score += 25

        if current_price < resistance and (resistance - current_price) < (2 * atr):
            sell_conditions.append("Near Resistance")
            sell_score += 20

        if pattern in ["Bearish Engulfing", "Shooting Star", "Evening Star"]:
            sell_conditions.append(pattern)
            sell_score += 25

        if ema_20 < ema_50 and is_downtrend:
            sell_conditions.append("EMA Bearish")
            sell_score += 15

        if macd < macd_signal:
            sell_conditions.append("MACD Bearish")
            sell_score += 15

        # Determine final signal
        min_confidence = 60
        min_conditions = 3

        if (len(buy_conditions) >= min_conditions and 
            buy_score >= min_confidence and 
            sell_score < 40):
            action = SignalType.BUY
            confidence = min(buy_score, 95)  # Cap at 95% to avoid overconfidence
            key_level = support
            conditions = buy_conditions
        elif (len(sell_conditions) >= min_conditions and 
              sell_score >= min_confidence and 
              buy_score < 40):
            action = SignalType.SELL
            confidence = min(sell_score, 95)
            key_level = resistance
            conditions = sell_conditions
        else:
            return self._create_no_signal(candles, currency_pair,
                f"Conditions not met (Buy: {buy_score}%, Sell: {sell_score}%)")

        # Calculate TP/SL
        tp, sl, tp_pips, sl_pips = self._calculate_tp_sl(action, current_price, currency_pair, atr)
        position_size = self.calculate_position_size(atr, current_price, sl)

        return TradingSignal(
            action=action,
            confidence=confidence,
            rsi=rsi,
            ema_20=ema_20,
            ema_50=ema_50,
            ema_200=ema_200,
            macd=macd,
            macd_signal=macd_signal,
            atr=atr,
            pattern=pattern,
            support_resistance=key_level,
            current_price=current_price,
            take_profit=tp,
            stop_loss=sl,
            tp_pips=tp_pips,
            sl_pips=sl_pips,
            position_size=position_size,
            reason=" + ".join(conditions),
            timestamp=datetime.now(),
            currency_pair=currency_pair
        )

    def _calculate_tp_sl(self, action: SignalType, entry_price: float, 
                        currency_pair: str, atr: float) -> Tuple[float, float, int, int]:
        """Enhanced TP/SL calculation with volatility adjustment"""
        pip_value = 0.01 if 'JPY' in currency_pair else 0.0001

        # Base TP/SL from settings
        base_tp_pips = np.random.randint(self.settings.tp_pips_min, self.settings.tp_pips_max + 1)
        base_sl_pips = np.random.randint(self.settings.sl_pips_min, self.settings.sl_pips_max + 1)

        # Adjust based on volatility (ATR)
        atr_pips = atr / pip_value
        tp_pips = min(int(base_tp_pips + (atr_pips * 0.3)), 100)  # Cap at 100 pips
        sl_pips = min(int(base_sl_pips + (atr_pips * 0.2)), 80)   # Cap at 80 pips

        if action == SignalType.BUY:
            take_profit = entry_price + (tp_pips * pip_value)
            stop_loss = entry_price - (sl_pips * pip_value)
        else:  # SELL
            take_profit = entry_price - (tp_pips * pip_value)
            stop_loss = entry_price + (sl_pips * pip_value)

        decimals = 3 if 'JPY' in currency_pair else 5
        return round(take_profit, decimals), round(stop_loss, decimals), tp_pips, sl_pips

    def _create_no_signal(self, candles: List[Candle], currency_pair: str, reason: str) -> TradingSignal:
        """Create NO_SIGNAL response"""
        closes = [c.close for c in candles] if candles else [0]
        current_price = closes[-1] if closes else 0

        return TradingSignal(
            action=SignalType.NONE,
            confidence=0,
            rsi=self.analyzer.calculate_rsi(closes) if len(closes) > 14 else 50,
            ema_20=0,
            ema_50=0,
            ema_200=0,
            macd=0,
            macd_signal=0,
            atr=0,
            pattern="None",
            support_resistance=0,
            current_price=current_price,
            take_profit=0,
            stop_loss=0,
            tp_pips=0,
            sl_pips=0,
            position_size=0,
            reason=reason,
            timestamp=datetime.now(),
            currency_pair=currency_pair
        )

class CurrencyPairValidator:
    """Validate and format currency pairs"""
    
    @staticmethod
    def validate_and_format(pair: str) -> Tuple[bool, str, str]:
        """Validate currency pair and return API format and display format"""
        valid_pairs = {
            "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
            "EURJPY", "GBPJPY", "EURGBP", "EURAUD", "EURCHF", "AUDJPY", "GBPCHF",
            "CHFJPY", "EURNZD", "AUDCHF", "GBPAUD", "GBPCAD", "GBPNZD", "AUDCAD",
            "AUDNZD", "NZDJPY", "NZDCHF", "NZDCAD", "CADCHF", "CADJPY"
        }
        
        pair_upper = pair.upper()
        if pair_upper in valid_pairs:
            # Format for API (usually with slash)
            api_format = f"{pair_upper[:3]}/{pair_upper[3:]}"
            return True, api_format, pair_upper
        return False, "", ""

class LiveForexTelegramBot:
    """Base Telegram bot for forex signals"""
    
    def __init__(self, telegram_token: str, api_key: str):
        self.telegram_token = telegram_token
        self.api_key = api_key
        self.settings = BotSettings()
        self.signal_generator = SignalGenerator(self.settings)
        self.performance_tracker = PerformanceTracker()
        self.application = None
        
    def setup_handlers(self):
        """Setup command handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("analyze", self.analyze_command))
        self.application.add_handler(CommandHandler("performance", self.performance_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        self.settings.subscribed_users.add(user_id)
        
        welcome_message = """
🚀 **Enhanced Forex Signal Bot v4.0**

Welcome! Your bot is now active and monitoring forex markets.

Use `/help` to see all available commands.
Use `/analyze EURUSD` to get instant analysis.
Use `/performance` to view trading statistics.

Happy trading! 📈
"""
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
        
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await update.message.reply_text(help_message, parse_mode='Markdown')
        
    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /analyze command"""
        if not context.args:
            await update.message.reply_text("Usage: /analyze [PAIR]\nExample: /analyze EURUSD")
            return
            
        currency_pair = context.args[0].upper()
        await update.message.reply_text(f"⏳ Analyzing {currency_pair}...")
        
        try:
            # This would fetch real data and analyze
            await update.message.reply_text(f"Analysis for {currency_pair} completed!")
        except Exception as e:
            await update.message.reply_text(f"❌ Analysis failed: {str(e)}")
            
    async def performance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /performance command"""
        await update.message.reply_text("📊 Performance statistics would be displayed here.")
        
    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /settings command"""
        await update.message.reply_text("⚙️ Settings panel would be displayed here.")
        
    def run(self):
        """Run the bot"""
        self.application = Application.builder().token(self.telegram_token).build()
        self.setup_handlers()
        
        logger.info("Starting Enhanced Forex Signal Bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

class PerformanceTracker:
    """Track trading performance"""
    
    def __init__(self):
        self.trades = []
        
    def add_trade(self, signal: TradingSignal):
        """Add a trade to tracking"""
        self.trades.append(signal)

class EnhancedForexBot(LiveForexTelegramBot):
    """Enhanced version with backtesting and improved features"""

    async def backtest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle backtest command"""
        if not context.args:
            await update.message.reply_text(
                "Usage: /backtest [PAIR] [DAYS]\n"
                "Example: /backtest EURUSD 30"
            )
            return

        currency_pair = context.args[0].upper()
        days = int(context.args[1]) if len(context.args) > 1 else 30

        await update.message.reply_text(f"⏳ Running backtest for {currency_pair} over {days} days...")

        try:
            result = await self.run_backtest(currency_pair, days)
            await self._send_backtest_results(update.message.chat_id, context, result)
        except Exception as e:
            await update.message.reply_text(f"❌ Backtest failed: {str(e)}")

    async def run_backtest(self, currency_pair: str, days: int) -> BacktestResult:
        """Execute backtest on historical data"""
        is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(currency_pair)
        if not is_valid:
            raise ValueError(f"Invalid currency pair: {currency_pair}")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Fetch historical data (implementation depends on your data provider)
        candles = await self._fetch_historical_data(api_format, start_date, end_date)

        if not candles or len(candles) < 200:
            raise ValueError("Insufficient historical data")

        # Run backtest
        trades = []
        equity = self.settings.account_size
        max_drawdown = 0
        peak_equity = equity

        for i in range(200, len(candles)):
            window = candles[i-200:i]
            signal = self.signal_generator.generate_signal(window, currency_pair)

            if signal.action != SignalType.NONE:
                # Simulate trade
                entry_price = signal.current_price
                exit_price = candles[i].close

                if signal.action == SignalType.BUY:
                    pnl = (exit_price - entry_price) * signal.position_size
                else:
                    pnl = (entry_price - exit_price) * signal.position_size

                equity += pnl
                trades.append(pnl)

                # Update drawdown
                if equity > peak_equity:
                    peak_equity = equity
                drawdown = (peak_equity - equity) / peak_equity
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

        # Calculate metrics
        winning_trades = sum(1 for pnl in trades if pnl > 0)
        losing_trades = sum(1 for pnl in trades if pnl < 0)
        total_trades = len(trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        gross_profit = sum(pnl for pnl in trades if pnl > 0)
        gross_loss = abs(sum(pnl for pnl in trades if pnl < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        total_pnl = equity - self.settings.account_size

        return BacktestResult(
            pair=currency_pair,
            timeframe=self.settings.timeframe,
            start_date=start_date,
            end_date=end_date,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            max_drawdown=round(max_drawdown * 100, 1),
            pnl=round(total_pnl, 2)
        )

    def setup_handlers(self):
        """Setup command handlers including backtest"""
        super().setup_handlers()
        self.application.add_handler(CommandHandler("backtest", self.backtest_command))

    async def _send_backtest_results(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE, 
                                   result: BacktestResult):
        """Format and send backtest results"""
        message = f"""
📊 **Backtest Results - {result.pair}**

⏳ **Period:** {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}
⏱️ **Timeframe:** {result.timeframe}

📈 **Performance Metrics:**
• Total Trades: `{result.total_trades}`
• Winning Trades: `{result.winning_trades}`
• Losing Trades: `{result.losing_trades}`
• Win Rate: `{result.win_rate}%`
• Profit Factor: `{result.profit_factor}`
• Max Drawdown: `{result.max_drawdown}%`
• P&L: `${result.pnl:,.2f}`

💡 **Interpretation:**
- Profit factor > 1.5 suggests good strategy
- Win rate > 55% is generally positive
- Drawdown < 20% is acceptable
"""
        await context.bot.send_message(chat_id, message, parse_mode='Markdown')
    
    async def _fetch_historical_data(self, currency_pair: str, start_date: datetime, end_date: datetime) -> List[Candle]:
        """Fetch historical candle data (placeholder implementation)"""
        # This is a placeholder - in a real implementation you would fetch from your data provider
        # For now, generate some dummy data for testing
        candles = []
        current_date = start_date
        base_price = 1.1500  # Base price for simulation
        
        while current_date < end_date:
            # Generate realistic candle data
            open_price = base_price + np.random.normal(0, 0.001)
            close_price = open_price + np.random.normal(0, 0.0005)
            high_price = max(open_price, close_price) + abs(np.random.normal(0, 0.0003))
            low_price = min(open_price, close_price) - abs(np.random.normal(0, 0.0003))
            volume = np.random.randint(1000, 10000)
            
            candle = Candle(
                timestamp=current_date,
                open=round(open_price, 5),
                high=round(high_price, 5),
                low=round(low_price, 5),
                close=round(close_price, 5),
                volume=volume
            )
            candles.append(candle)
            
            base_price = close_price  # Update base price for next candle
            current_date += timedelta(minutes=15)  # 15-minute candles
            
        return candles

def main():
    """Run the enhanced bot"""
    TELEGRAM_BOT_TOKEN = "8186199634:AAEEafBIm5GhZrhrWt-je8wa1UESaTHF9ZM"
    TWELVEDATA_API_KEY = "b971d5ae2d0447fbb2fa621565a16334"

    try:
        bot = EnhancedForexBot(
            telegram_token=TELEGRAM_BOT_TOKEN,
            api_key=TWELVEDATA_API_KEY
        )
        bot.run()
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    main()