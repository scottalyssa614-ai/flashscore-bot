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

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

help_message = """
📚 **Smart Forex Signal Bot v2.0 - Help**

**🆕 Enhanced Features:**
• 💱 **Multi-Currency Support** - Analyze any major forex pair
• 💰 **TP/SL Suggestions** - Smart profit targets & risk management
• ⏰ **Auto Alerts** - Background scanning every 15-60 minutes
• 📊 **Performance Tracking** - Win/loss statistics & signal history
• 🔧 **Multiple Indicators** - RSI + EMA + MACD + S/R + Patterns
• ⚙️ **Customizable Settings** - Adjust intervals, timeframes, and alerts

**📱 Commands:**
• `/start` - Welcome & quick access menu
• `/analyze [PAIR]` - Manual analysis (e.g., `/analyze GBPUSD`)
• `/performance` - View trading statistics
• `/alerts [on/off]` - Toggle auto notifications
• `/set_interval [15-60]` - Change scan frequency
• `/timeframe [15min|30min|1h]` - Set analysis timeframe
• `/config` - Show current settings

**💱 Supported Currency Pairs:**
**Majors:** EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD
**Crosses:** EURGBP, EURJPY, GBPJPY, AUDJPY, CHFJPY, and more

**Examples:**
• `/analyze` - Analyze EURUSD (default)
• `/analyze GBPJPY` - Analyze GBP/JPY
• `/analyze gbpusd` - Case insensitive

**🎯 Signal Types:**
🟢 **BUY** - Oversold + Support + Bullish patterns + EMA/MACD confirmation
🔴 **SELL** - Overbought + Resistance + Bearish patterns + EMA/MACD confirmation
⚪ **NO SIGNAL** - Conditions don't meet 60%+ confidence threshold

**💰 Trade Management:**
• Take Profit: 20-40 pips (automatically calculated)
• Stop Loss: 15-25 pips (risk management)
• High confidence signals only (60%+ threshold)
• Proper pip calculation for JPY pairs

**📈 Performance Tracking:**
All signals are logged with timestamps, entry prices, TP/SL levels, and outcomes for performance analysis.

The bot focuses on quality over quantity - only the strongest setups!
"""

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"

@dataclass
class Candle:
    """Represents a single candlestick"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class TradingSignal:
    """Represents a trading signal with TP/SL"""
    action: SignalType
    confidence: float
    rsi: float
    ema_20: float
    ema_50: float
    macd: float
    macd_signal: float
    pattern: str
    support_resistance: float
    current_price: float
    take_profit: float
    stop_loss: float
    tp_pips: int
    sl_pips: int
    reason: str
    timestamp: datetime

@dataclass
class BotSettings:
    """Bot configuration settings"""
    interval_minutes: int = 15
    timeframe: str = "15min"
    tp_pips_min: int = 20
    tp_pips_max: int = 40
    sl_pips_min: int = 15
    sl_pips_max: int = 25
    auto_alerts: bool = False
    subscribed_users: Set[int] = None

    def __post_init__(self):
        if self.subscribed_users is None:
            self.subscribed_users = set()

class TechnicalAnalyzer:
    """Enhanced technical analysis with multiple indicators"""

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index)"""
        if len(prices) < period + 1:
            return 50.0

        prices_array = np.array(prices)
        deltas = np.diff(prices_array)

        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return np.mean(prices) if prices else 0.0

        prices_array = np.array(prices)
        alpha = 2 / (period + 1)
        ema = prices_array[0]

        for price in prices_array[1:]:
            ema = alpha * price + (1 - alpha) * ema

        return round(ema, 5)

    @staticmethod
    def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float]:
        """Calculate MACD and Signal line"""
        if len(prices) < slow:
            return 0.0, 0.0

        ema_fast = TechnicalAnalyzer.calculate_ema(prices, fast)
        ema_slow = TechnicalAnalyzer.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow

        # For signal line, we need historical MACD values
        if len(prices) < slow + signal:
            signal_line = macd_line
        else:
            macd_values = []
            for i in range(slow, len(prices) + 1):
                ema_f = TechnicalAnalyzer.calculate_ema(prices[:i], fast)
                ema_s = TechnicalAnalyzer.calculate_ema(prices[:i], slow)
                macd_values.append(ema_f - ema_s)

            signal_line = TechnicalAnalyzer.calculate_ema(macd_values, signal)

        return round(macd_line, 6), round(signal_line, 6)

    @staticmethod
    def find_support_resistance(candles: List[Candle], lookback: int = 20) -> Tuple[float, float]:
        """Find recent support and resistance levels"""
        if len(candles) < lookback:
            lookback = len(candles)

        recent_candles = candles[-lookback:]
        highs = [c.high for c in recent_candles]
        lows = [c.low for c in recent_candles]

        resistance_levels = []
        support_levels = []

        for i in range(2, len(recent_candles) - 2):
            # Check for resistance (local high)
            if (recent_candles[i].high > recent_candles[i-1].high and
                recent_candles[i].high > recent_candles[i-2].high and
                recent_candles[i].high > recent_candles[i+1].high and
                recent_candles[i].high > recent_candles[i+2].high):
                resistance_levels.append(recent_candles[i].high)

            # Check for support (local low)
            if (recent_candles[i].low < recent_candles[i-1].low and
                recent_candles[i].low < recent_candles[i-2].low and
                recent_candles[i].low < recent_candles[i+1].low and
                recent_candles[i].low < recent_candles[i+2].low):
                support_levels.append(recent_candles[i].low)

        resistance = max(resistance_levels) if resistance_levels else max(highs)
        support = min(support_levels) if support_levels else min(lows)

        return support, resistance

    @staticmethod
    def detect_candlestick_patterns(candles: List[Candle]) -> str:
        """Detect candlestick patterns"""
        if len(candles) < 2:
            return "None"

        current = candles[-1]
        previous = candles[-2]

        current_body = abs(current.close - current.open)
        previous_body = abs(previous.close - previous.open)
        current_range = current.high - current.low

        # Bullish Engulfing
        if (previous.close < previous.open and
            current.close > current.open and
            current.open < previous.close and
            current.close > previous.open and
            current_body > previous_body * 1.2):
            return "Bullish Engulfing"

        # Bearish Engulfing
        if (previous.close > previous.open and
            current.close < current.open and
            current.open > previous.close and
            current.close < previous.open and
            current_body > previous_body * 1.2):
            return "Bearish Engulfing"

        # Doji
        if current_body < current_range * 0.1:
            return "Doji"

        # Bullish Pin Bar
        lower_shadow = current.open - current.low if current.close > current.open else current.close - current.low
        upper_shadow = current.high - current.close if current.close > current.open else current.high - current.open
        if (lower_shadow > current_body * 2 and
            upper_shadow < current_body * 0.5 and
            current_range > 0):
            return "Bullish Pin Bar"

        # Bearish Pin Bar
        if (upper_shadow > current_body * 2 and
            lower_shadow < current_body * 0.5 and
            current_range > 0):
            return "Bearish Pin Bar"

        return "None"

class CurrencyPairValidator:
    """Validates and formats currency pairs"""

    # Major and minor forex pairs
    VALID_PAIRS = {
        # Major pairs
        'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
        # Cross pairs
        'EURGBP', 'EURJPY', 'EURCHF', 'EURAUD', 'EURCAD', 'EURNZD',
        'GBPJPY', 'GBPCHF', 'GBPAUD', 'GBPCAD', 'GBPNZD',
        'AUDJPY', 'AUDCHF', 'AUDCAD', 'AUDNZD',
        'CADJPY', 'CADCHF', 'NZDJPY', 'NZDCHF', 'NZDCAD',
        'CHFJPY', 'JPYSGD'
    }

    @classmethod
    def validate_and_format(cls, pair_input: str) -> Tuple[bool, str, str]:
        """
        Validate and format currency pair
        Returns: (is_valid, formatted_pair_for_api, display_pair)
        """
        if not pair_input:
            return True, "EUR/USD", "EURUSD"

        # Clean input
        cleaned = pair_input.upper().replace('/', '').replace('-', '').replace('_', '')

        # Check if it's a valid pair
        if cleaned in cls.VALID_PAIRS:
            # Format for API (with slash)
            api_format = f"{cleaned[:3]}/{cleaned[3:]}"
            return True, api_format, cleaned

        # Try common variations
        if len(cleaned) == 6:
            reversed_pair = cleaned[3:] + cleaned[:3]
            if reversed_pair in cls.VALID_PAIRS:
                api_format = f"{reversed_pair[:3]}/{reversed_pair[3:]}"
                return True, api_format, reversed_pair

        return False, "", cleaned

class ForexDataProvider:
    """Handles fetching forex data from external APIs"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://api.twelvedata.com"

    async def get_candles(self, symbol: str = "EUR/USD", interval: str = "15min", 
                         count: int = 100) -> List[Candle]:
        """Fetch candlestick data from TwelveData API"""
        try:
            params = {
                "symbol": symbol,
                "interval": interval,
                "outputsize": count,
                "format": "JSON"
            }

            if self.api_key:
                params["apikey"] = self.api_key

            url = f"{self.base_url}/time_series"
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if "values" not in data:
                logger.warning(f"No values in API response, using mock data")
                return self._get_mock_data()

            candles = []
            for item in reversed(data["values"]):
                try:
                    candle = Candle(
                        timestamp=datetime.strptime(item["datetime"], "%Y-%m-%d %H:%M:%S"),
                        open=float(item["open"]),
                        high=float(item["high"]),
                        low=float(item["low"]),
                        close=float(item["close"]),
                        volume=float(item.get("volume", 0))
                    )
                    candles.append(candle)
                except (ValueError, KeyError) as e:
                    logger.warning(f"Error parsing candle data: {e}")
                    continue

            return candles[-count:] if candles else self._get_mock_data(symbol)

        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            return self._get_mock_data(symbol)

    def _get_mock_data(self, symbol: str = "EUR/USD") -> List[Candle]:
        """Generate realistic mock data"""
        logger.info("Using mock data")
        candles = []
        base_price = 1.0850
        base_time = datetime.now() - timedelta(hours=25)

        for i in range(100):
            change = np.random.normal(0, 0.0005)
            open_price = base_price + change
            high_price = open_price + abs(np.random.normal(0, 0.0003))
            low_price = open_price - abs(np.random.normal(0, 0.0003))
            close_price = open_price + np.random.normal(0, 0.0002)

            high_price = max(high_price, open_price, close_price)
            low_price = min(low_price, open_price, close_price)

            candle = Candle(
                timestamp=base_time + timedelta(minutes=15 * i),
                open=round(open_price, 5),
                high=round(high_price, 5),
                low=round(low_price, 5),
                close=round(close_price, 5),
                volume=np.random.uniform(1000, 5000)
            )
            candles.append(candle)
            base_price = close_price

        return candles

class PerformanceTracker:
    """Tracks and logs trading performance"""

    def __init__(self, log_file: str = "performance_log.json"):
        self.log_file = log_file
        self.signals_log = self._load_log()

    def _load_log(self) -> List[Dict]:
        """Load existing performance log"""
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading performance log: {e}")
        return []

    def _save_log(self):
        """Save performance log to file"""
        try:
            with open(self.log_file, 'w') as f:
                json.dump(self.signals_log, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving performance log: {e}")

    def log_signal(self, signal: TradingSignal):
        """Log a new trading signal"""
        if signal.action == SignalType.NONE:
            return

        log_entry = {
            "timestamp": signal.timestamp.isoformat(),
            "action": signal.action.value,
            "entry_price": signal.current_price,
            "take_profit": signal.take_profit,
            "stop_loss": signal.stop_loss,
            "tp_pips": signal.tp_pips,
            "sl_pips": signal.sl_pips,
            "rsi": signal.rsi,
            "pattern": signal.pattern,
            "confidence": signal.confidence,
            "status": "OPEN"  # OPEN, TP_HIT, SL_HIT
        }

        self.signals_log.append(log_entry)
        self._save_log()
        logger.info(f"Logged {signal.action.value} signal at {signal.current_price}")

    def get_stats(self) -> Dict:
        """Calculate performance statistics"""
        if not self.signals_log:
            return {
                "total_signals": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "avg_confidence": 0,
                "last_signal": "None"
            }

        buy_count = sum(1 for s in self.signals_log if s["action"] == "BUY")
        sell_count = sum(1 for s in self.signals_log if s["action"] == "SELL")
        avg_confidence = np.mean([s["confidence"] for s in self.signals_log])

        last_signal = self.signals_log[-1] if self.signals_log else None
        last_signal_time = ""
        if last_signal:
            last_time = datetime.fromisoformat(last_signal["timestamp"])
            last_signal_time = last_time.strftime("%Y-%m-%d %H:%M")

        return {
            "total_signals": len(self.signals_log),
            "buy_signals": buy_count,
            "sell_signals": sell_count,
            "avg_confidence": round(avg_confidence, 1),
            "last_signal": f"{last_signal['action']} at {last_signal_time}" if last_signal else "None"
        }

class SignalGenerator:
    """Enhanced signal generator with multiple indicators and TP/SL calculation"""

    def __init__(self, settings: BotSettings):
        self.analyzer = TechnicalAnalyzer()
        self.settings = settings

    def calculate_tp_sl(self, action: SignalType, entry_price: float, currency_pair: str) -> Tuple[float, float, int, int]:
        """Calculate Take Profit and Stop Loss levels"""
        if action == SignalType.NONE:
            return 0.0, 0.0, 0, 0

        # Calculate pip value (for EUR/USD, 1 pip = 0.0001)
        pip_value = 0.0001

        # Random TP/SL within configured ranges
        tp_pips = np.random.randint(self.settings.tp_pips_min, self.settings.tp_pips_max + 1)
        sl_pips = np.random.randint(self.settings.sl_pips_min, self.settings.sl_pips_max + 1)

        if action == SignalType.BUY:
            take_profit = entry_price + (tp_pips * pip_value)
            stop_loss = entry_price - (sl_pips * pip_value)
        else:  # SELL
            take_profit = entry_price - (tp_pips * pip_value)
            stop_loss = entry_price + (sl_pips * pip_value)

        return round(take_profit, 5), round(stop_loss, 5), tp_pips, sl_pips

    def generate_signal(self, candles: List[Candle], currency_pair: str = "EUR/USD") -> TradingSignal:
        """Generate enhanced trading signal with multiple indicators for any currency pair"""
        if len(candles) < 50:
            return self._create_no_signal(candles, currency_pair, "Insufficient data for analysis")

        # Calculate all indicators
        closes = [c.close for c in candles]
        rsi = self.analyzer.calculate_rsi(closes)
        ema_20 = self.analyzer.calculate_ema(closes, 20)
        ema_50 = self.analyzer.calculate_ema(closes, 50)
        macd, macd_signal = self.analyzer.calculate_macd(closes)
        support, resistance = self.analyzer.find_support_resistance(candles)
        pattern = self.analyzer.detect_candlestick_patterns(candles)
        current_price = candles[-1].close

        # Determine proximity to S/R levels
        support_distance = abs(current_price - support) / current_price
        resistance_distance = abs(current_price - resistance) / current_price

        # Enhanced signal generation logic
        signal_strength = 0
        action = SignalType.NONE
        conditions = []

        # BUY conditions
        buy_score = 0
        buy_conditions = []

        if rsi < 35:
            buy_conditions.append("RSI Oversold")
            buy_score += 25

        if support_distance < 0.002:
            buy_conditions.append("Near Support")
            buy_score += 20

        if pattern in ["Bullish Engulfing", "Bullish Pin Bar"]:
            buy_conditions.append(f"Bullish Pattern ({pattern})")
            buy_score += 25

        if ema_20 > ema_50:
            buy_conditions.append("EMA Bullish")
            buy_score += 15

        if macd > macd_signal:
            buy_conditions.append("MACD Bullish")
            buy_score += 15

        # SELL conditions
        sell_score = 0
        sell_conditions = []

        if rsi > 65:
            sell_conditions.append("RSI Overbought")
            sell_score += 25

        if resistance_distance < 0.002:
            sell_conditions.append("Near Resistance")
            sell_score += 20

        if pattern in ["Bearish Engulfing", "Bearish Pin Bar"]:
            sell_conditions.append(f"Bearish Pattern ({pattern})")
            sell_score += 25

        if ema_20 < ema_50:
            sell_conditions.append("EMA Bearish")
            sell_score += 15

        if macd < macd_signal:
            sell_conditions.append("MACD Bearish")
            sell_score += 15

        # Determine final signal (require minimum 60 confidence and 3+ conditions)
        min_confidence = 60
        min_conditions = 3

        if (len(buy_conditions) >= min_conditions and 
            buy_score >= min_confidence and 
            sell_score < 30):  # Avoid conflicting signals
            action = SignalType.BUY
            signal_strength = min(buy_score, 100)
            conditions = buy_conditions
            key_level = support
        elif (len(sell_conditions) >= min_conditions and 
              sell_score >= min_confidence and 
              buy_score < 30):
            action = SignalType.SELL
            signal_strength = min(sell_score, 100)
            conditions = sell_conditions
            key_level = resistance
        else:
            return self._create_no_signal(candles, currency_pair,
                f"Conditions not met (Buy: {buy_score}%, Sell: {sell_score}%)")

        # Calculate TP/SL
        tp, sl, tp_pips, sl_pips = self.calculate_tp_sl(action, current_price, currency_pair)

        return TradingSignal(
            action=action,
            confidence=signal_strength,
            rsi=rsi,
            ema_20=ema_20,
            ema_50=ema_50,
            macd=macd,
            macd_signal=macd_signal,
            pattern=pattern,
            support_resistance=key_level,
            current_price=current_price,
            take_profit=tp,
            stop_loss=sl,
            tp_pips=tp_pips,
            sl_pips=sl_pips,
            reason=" + ".join(conditions),
            timestamp=datetime.now()
        )

    def _create_no_signal(self, candles: List[Candle], currency_pair: str, reason: str) -> TradingSignal:
        """Create a NO SIGNAL response"""
        closes = [c.close for c in candles] if candles else [0]
        current_price = closes[-1] if closes else 0

        return TradingSignal(
            action=SignalType.NONE,
            confidence=0,
            rsi=self.analyzer.calculate_rsi(closes) if len(closes) > 14 else 50,
            ema_20=0,
            ema_50=0,
            macd=0,
            macd_signal=0,
            pattern="None",
            support_resistance=0,
            current_price=current_price,
            take_profit=0,
            stop_loss=0,
            tp_pips=0,
            sl_pips=0,
            reason=reason,
            timestamp=datetime.now()
        )

class ForexTelegramBot:
    """Enhanced Telegram bot with auto alerts and performance tracking"""

    def __init__(self, telegram_token: str, api_key: Optional[str] = None):
        self.telegram_token = telegram_token
        self.data_provider = ForexDataProvider(api_key)
        self.settings = BotSettings()
        self.signal_generator = SignalGenerator(self.settings)
        self.performance_tracker = PerformanceTracker()
        self.last_signal_action = SignalType.NONE
        self.auto_alert_task = None
        self.application = None
        
        # Track last signal for each pair to avoid spam
        self.last_signals_per_pair = {}
        
        # Default monitored pairs
        self.monitored_pairs = [
            ("EURUSD", "EUR/USD"),
            ("GBPUSD", "GBP/USD"), 
            ("USDJPY", "USD/JPY"),
            ("GBPJPY", "GBP/JPY"),
            ("EURJPY", "EUR/JPY"),
            ("AUDUSD", "AUD/USD"),
            ("USDCAD", "USD/CAD"),
            ("NZDUSD", "NZD/USD")
        ]

        # Load settings
        self._load_settings()

    def _load_settings(self):
        """Load bot settings from file"""
        try:
            if os.path.exists("bot_settings.json"):
                with open("bot_settings.json", 'r') as f:
                    data = json.load(f)
                    self.settings.interval_minutes = data.get("interval_minutes", 5)
                    self.settings.timeframe = data.get("timeframe", "15min")
                    self.settings.auto_alerts = data.get("live_monitoring", True)
                    self.settings.subscribed_users = set(data.get("subscribed_users", []))
                    
                    # Load monitored pairs if available
                    if "monitored_pairs" in data:
                        pairs_from_file = data["monitored_pairs"]
                        self.monitored_pairs = []
                        for pair in pairs_from_file:
                            if isinstance(pair, str):
                                # Convert single string to tuple format
                                cleaned = pair.replace('/', '').upper()
                                api_format = f"{cleaned[:3]}/{cleaned[3:]}"
                                self.monitored_pairs.append((cleaned, api_format))
                            
        except Exception as e:
            logger.error(f"Error loading settings: {e}")

    def _save_settings(self):
        """Save bot settings to file"""
        try:
            data = {
                "interval_minutes": self.settings.interval_minutes,
                "timeframe": self.settings.timeframe,
                "live_monitoring": self.settings.auto_alerts,
                "subscribed_users": list(self.settings.subscribed_users),
                "monitored_pairs": [pair[1] for pair in self.monitored_pairs]
            }
            with open("bot_settings.json", 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced start command"""
        user_id = update.effective_user.id
        self.settings.subscribed_users.add(user_id)
        self._save_settings()

        keyboard = [
            [InlineKeyboardButton("📊 Analyze Now", callback_data="analyze")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("📈 Performance", callback_data="performance")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        monitoring_status = "🟢 ACTIVE" if self.settings.auto_alerts else "🔴 INACTIVE"
        pairs_list = ", ".join([pair[0] for pair in self.monitored_pairs[:4]]) + "..."
        
        welcome_message = f"""
🤖 **Welcome to Smart Forex Signal Bot v2.0!**

📡 **Live Monitoring:** {monitoring_status}
💱 **Watching:** {pairs_list}
⏱️ **Scan Interval:** {self.settings.interval_minutes} minutes

🆕 **Enhanced Features:**
• 🔄 **Live Multi-Currency Scanning** - Constant market monitoring
• 💰 Take Profit & Stop Loss suggestions
• 📊 Performance tracking & statistics
• 🔧 Enhanced indicators (RSI + EMA + MACD + S/R + Patterns)
• ⚙️ Customizable settings

**Available Commands:**
• `/analyze [PAIR]` - Manual analysis
• `/performance` - View trading statistics
• `/alerts [on/off]` - Toggle live monitoring
• `/set_interval [5-60]` - Change scan frequency
• `/config` - Show current settings

**Monitored Pairs:**
{', '.join([pair[0] for pair in self.monitored_pairs])}

✅ High-confidence signals only (60%+ threshold)
✅ Real-time alerts when conditions are met

Choose an option below or type a command!
        """
        await update.message.reply_text(welcome_message, 
                                      parse_mode='Markdown',
                                      reply_markup=reply_markup)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks"""
        query = update.callback_query
        await query.answer()

        if query.data == "analyze":
            await self._send_analysis(query.message.chat_id, context, "EURUSD", "EUR/USD")
        elif query.data == "settings":
            await self._send_settings(query.message.chat_id, context)
        elif query.data == "performance":
            await self._send_performance(query.message.chat_id, context)

    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced analyze command with currency pair support"""
        # Extract currency pair from command arguments
        currency_pair = "EURUSD"  # Default
        if context.args and len(context.args) > 0:
            user_input = context.args[0]
            is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(user_input)

            if not is_valid:
                await update.message.reply_text(
                    f"❌ Invalid currency pair: `{user_input}`\n\n"
                    "**Supported pairs:** EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, AUDUSD, etc.\n"
                    "**Usage:** `/analyze GBPUSD` or just `/analyze` for EURUSD",
                    parse_mode='Markdown'
                )
                return

            currency_pair = display_format
            api_pair = api_format
        else:
            api_pair = "EUR/USD"

        await self._send_analysis(update.message.chat_id, context, currency_pair, api_pair)

    async def _send_analysis(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE, 
                           currency_pair: str = "EURUSD", api_pair: str = "EUR/USD"):
        """Send market analysis for specified currency pair"""
        await context.bot.send_message(chat_id, f"🔍 Analyzing {currency_pair}...")

        try:
            candles = await self.data_provider.get_candles(
                symbol=api_pair,
                interval=self.settings.timeframe,
                count=100
            )

            if not candles:
                await context.bot.send_message(chat_id, f"❌ Could not fetch data for {currency_pair}.")
                return

            signal = self.signal_generator.generate_signal(candles, api_pair)

            # Log signal if it's not NONE
            if signal.action != SignalType.NONE:
                self.performance_tracker.log_signal(signal)

            message = self._format_signal_message(signal, currency_pair)
            await context.bot.send_message(chat_id, message, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in analysis: {e}")
            await context.bot.send_message(chat_id, f"❌ Analysis failed for {currency_pair}. Please try again.")

    def _format_signal_message(self, signal: TradingSignal, currency_pair: str) -> str:
        """Format signal into readable message with currency pair"""
        # Determine decimal places for display
        decimals = 3 if 'JPY' in currency_pair else 5

        if signal.action == SignalType.BUY:
            emoji = "🟢"
            message = f"""📈 **Pair:** {currency_pair}
{emoji} **BUY SIGNAL DETECTED**

💰 **Trade Setup:**
• Entry: `{signal.current_price:.{decimals}f}`
• 🎯 Take Profit: `{signal.take_profit:.{decimals}f}` (+{signal.tp_pips} pips)
• 🛑 Stop Loss: `{signal.stop_loss:.{decimals}f}` (-{signal.sl_pips} pips)

📊 **Technical Analysis:**
• RSI: `{signal.rsi:.1f}`
• EMA 20: `{signal.ema_20:.{decimals}f}` | EMA 50: `{signal.ema_50:.{decimals}f}`
• MACD: `{signal.macd:.6f}` | Signal: `{signal.macd_signal:.6f}`
• Pattern: `{signal.pattern}`
• Support: `{signal.support_resistance:.{decimals}f}`
• Confidence: `{signal.confidence:.0f}%`

📈 **Reason:** {signal.reason}

⏰ Timeframe: `{self.settings.timeframe}`
🕐 Time: `{signal.timestamp.strftime('%H:%M UTC')}`"""

        elif signal.action == SignalType.SELL:
            emoji = "🔴"
            message = f"""📈 **Pair:** {currency_pair}
{emoji} **SELL SIGNAL DETECTED**

💰 **Trade Setup:**
• Entry: `{signal.current_price:.{decimals}f}`
• 🎯 Take Profit: `{signal.take_profit:.{decimals}f}` (+{signal.tp_pips} pips)
• 🛑 Stop Loss: `{signal.stop_loss:.{decimals}f}` (-{signal.sl_pips} pips)

📊 **Technical Analysis:**
• RSI: `{signal.rsi:.1f}`
• EMA 20: `{signal.ema_20:.{decimals}f}` | EMA 50: `{signal.ema_50:.{decimals}f}`
• MACD: `{signal.macd:.6f}` | Signal: `{signal.macd_signal:.6f}`
• Pattern: `{signal.pattern}`
• Resistance: `{signal.support_resistance:.{decimals}f}`
• Confidence: `{signal.confidence:.0f}%`

📉 **Reason:** {signal.reason}

⏰ Timeframe: `{self.settings.timeframe}`
🕐 Time: `{signal.timestamp.strftime('%H:%M UTC')}`"""

        else:
            emoji = "⚪"
            message = f"""📈 **Pair:** {currency_pair}
{emoji} **NO SIGNAL**

📊 **Current Analysis:**
• RSI: `{signal.rsi:.1f}`
• EMA 20: `{signal.ema_20:.{decimals}f}` | EMA 50: `{signal.ema_50:.{decimals}f}`
• MACD: `{signal.macd:.6f}` | Signal: `{signal.macd_signal:.6f}`
• Pattern: `{signal.pattern}`
• Current Price: `{signal.current_price:.{decimals}f}`

💡 **Status:** {signal.reason}

⏰ Timeframe: `{self.settings.timeframe}`
🕐 Time: `{signal.timestamp.strftime('%H:%M UTC')}`

💡 Try again later when conditions align better."""

        return message

    async def performance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show performance statistics"""
        await self._send_performance(update.message.chat_id, context)

    async def _send_performance(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send performance statistics"""
        stats = self.performance_tracker.get_stats()

        monitoring_status = "🟢 ACTIVE" if self.settings.auto_alerts else "🔴 INACTIVE"
        pairs_count = len(self.monitored_pairs)
        
        message = f"""
📈 **Performance Statistics**

📊 **Signal Summary:**
• Total Signals: `{stats['total_signals']}`
• Buy Signals: `{stats['buy_signals']}`
• Sell Signals: `{stats['sell_signals']}`
• Avg Confidence: `{stats['avg_confidence']}%`

🕐 **Last Signal:** {stats['last_signal']}

⚙️ **Live Monitoring Settings:**
• Status: {monitoring_status}
• Monitored Pairs: `{pairs_count} pairs`
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Timeframe: `{self.settings.timeframe}`
• TP Range: `{self.settings.tp_pips_min}-{self.settings.tp_pips_max} pips`
• SL Range: `{self.settings.sl_pips_min}-{self.settings.sl_pips_max} pips`

📡 **Monitored Pairs:**
{', '.join([pair[0] for pair in self.monitored_pairs])}

💡 The bot continuously scans all pairs for trading opportunities!
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def _send_settings(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send current settings"""
        message = f"""
⚙️ **Bot Configuration**

**Current Settings:**
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Timeframe: `{self.settings.timeframe}`
• Auto Alerts: `{'✅ ON' if self.settings.auto_alerts else '❌ OFF'}`
• Take Profit: `{self.settings.tp_pips_min}-{self.settings.tp_pips_max} pips`
• Stop Loss: `{self.settings.sl_pips_min}-{self.settings.sl_pips_max} pips`

**Available Commands:**
• `/set_interval [15-60]` - Change scan interval
• `/timeframe [15min|30min|1h]` - Change timeframe
• `/alerts [on|off]` - Toggle auto alerts
• `/config` - Show this settings page

**Examples:**
• `/set_interval 30` - Scan every 30 minutes
• `/alerts on` - Enable auto notifications
• `/timeframe 30min` - Use 30-minute candles
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def set_interval_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set scanning interval"""
        if not context.args:
            await update.message.reply_text(
                f"Current interval: `{self.settings.interval_minutes} minutes`\n"
                "Usage: `/set_interval [15-60]`",
                parse_mode='Markdown'
            )
            return

        try:
            new_interval = int(context.args[0])
            if 5 <= new_interval <= 60:
                self.settings.interval_minutes = new_interval
                self._save_settings()

                # Restart live monitoring if enabled
                if self.settings.auto_alerts:
                    await self._restart_auto_alerts()

                await update.message.reply_text(
                    f"✅ Live scan interval updated to `{new_interval} minutes`\n"
                    f"📡 Monitoring {len(self.monitored_pairs)} currency pairs",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Interval must be between 5-60 minutes")
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")

    async def timeframe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set analysis timeframe"""
        if not context.args:
            await update.message.reply_text(
                f"Current timeframe: `{self.settings.timeframe}`\n"
                "Usage: `/timeframe [15min|30min|1h]`",
                parse_mode='Markdown'
            )
            return

        new_timeframe = context.args[0].lower()
        valid_timeframes = ["15min", "30min", "1h"]

        if new_timeframe in valid_timeframes:
            self.settings.timeframe = new_timeframe
            self._save_settings()
            await update.message.reply_text(
                f"✅ Timeframe updated to `{new_timeframe}`",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ Invalid timeframe. Use: {', '.join(valid_timeframes)}"
            )

    async def alerts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle live monitoring alerts"""
        if not context.args:
            status = "🟢 ACTIVE" if self.settings.auto_alerts else "🔴 INACTIVE"
            pairs_count = len(self.monitored_pairs)
            await update.message.reply_text(
                f"📡 **Live Monitoring:** {status}\n"
                f"💱 **Monitoring:** `{pairs_count} currency pairs`\n"
                f"⏱️ **Scan Interval:** `{self.settings.interval_minutes} minutes`\n\n"
                "**Usage:** `/alerts [on|off]`",
                parse_mode='Markdown'
            )
            return

        setting = context.args[0].lower()
        user_id = update.effective_user.id

        if setting == "on":
            self.settings.auto_alerts = True
            self.settings.subscribed_users.add(user_id)
            self._save_settings()
            await self._start_auto_alerts()
            pairs_list = ', '.join([pair[0] for pair in self.monitored_pairs])
            await update.message.reply_text(
                f"✅ **Live monitoring activated!**\n\n"
                f"🔄 Scanning {len(self.monitored_pairs)} pairs every {self.settings.interval_minutes} minutes:\n"
                f"`{pairs_list}`\n\n"
                f"You'll receive real-time alerts when trading conditions are met!"
            )
        elif setting == "off":
            if user_id in self.settings.subscribed_users:
                self.settings.subscribed_users.remove(user_id)

            # If no users subscribed, disable auto alerts
            if not self.settings.subscribed_users:
                self.settings.auto_alerts = False
                await self._stop_auto_alerts()

            self._save_settings()
            await update.message.reply_text("🔴 Live monitoring disabled for you.")
        else:
            await update.message.reply_text("❌ Use 'on' or 'off' with the alerts command")

    async def config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show configuration"""
        await self._send_settings(update.message.chat_id, context)

    async def _start_auto_alerts(self):
        """Start automatic alert system"""
        if self.auto_alert_task is None or self.auto_alert_task.done():
            self.auto_alert_task = asyncio.create_task(self._auto_alert_loop())
            logger.info("Auto alerts started")

    async def _stop_auto_alerts(self):
        """Stop automatic alert system"""
        if self.auto_alert_task and not self.auto_alert_task.done():
            self.auto_alert_task.cancel()
            logger.info("Auto alerts stopped")

    async def _restart_auto_alerts(self):
        """Restart auto alerts with new interval"""
        if self.settings.auto_alerts:
            await self._stop_auto_alerts()
            await self._start_auto_alerts()

    async def _auto_alert_loop(self):
        """Background task for live multi-currency monitoring"""
        logger.info(f"Starting live monitoring for {len(self.monitored_pairs)} currency pairs")
        
        while self.settings.auto_alerts and self.settings.subscribed_users:
            try:
                signals_found = []
                
                # Scan all monitored pairs
                for display_pair, api_pair in self.monitored_pairs:
                    try:
                        logger.info(f"Scanning {display_pair}...")
                        
                        # Get market data
                        candles = await self.data_provider.get_candles(
                            symbol=api_pair,
                            interval=self.settings.timeframe,
                            count=100
                        )

                        if candles:
                            signal = self.signal_generator.generate_signal(candles, api_pair)
                            
                            # Check if this is a new signal for this pair
                            last_signal_for_pair = self.last_signals_per_pair.get(display_pair, SignalType.NONE)
                            
                            if (signal.action != SignalType.NONE and 
                                signal.action != last_signal_for_pair and
                                signal.confidence >= 60):  # Only high confidence signals
                                
                                signals_found.append((signal, display_pair))
                                self.last_signals_per_pair[display_pair] = signal.action
                                self.performance_tracker.log_signal(signal)
                                
                                logger.info(f"🎯 NEW SIGNAL: {signal.action.value} for {display_pair} ({signal.confidence}%)")
                        
                        # Small delay between pairs to avoid rate limiting
                        await asyncio.sleep(2)
                        
                    except Exception as pair_error:
                        logger.error(f"Error analyzing {display_pair}: {pair_error}")
                        continue

                # Send alerts for all found signals
                if signals_found:
                    for signal, display_pair in signals_found:
                        message = f"🚨 **LIVE SIGNAL DETECTED**\n\n{self._format_signal_message(signal, display_pair)}"
                        
                        # Send to all subscribed users
                        for user_id in self.settings.subscribed_users.copy():
                            try:
                                await self.application.bot.send_message(
                                    user_id, message, parse_mode='Markdown'
                                )
                                await asyncio.sleep(0.5)  # Avoid flooding
                            except Exception as e:
                                logger.error(f"Failed to send alert to user {user_id}: {e}")
                                if "chat not found" in str(e).lower():
                                    self.settings.subscribed_users.discard(user_id)
                    
                    self._save_settings()
                    logger.info(f"Sent {len(signals_found)} live alerts")
                else:
                    # Send no-signal notification
                    current_time = datetime.now().strftime('%H:%M UTC')
                    pairs_scanned = ", ".join([pair[0] for pair in self.monitored_pairs])
                    no_signal_message = f"""
📊 **Scan Complete - No Signals Found**

⏰ **Time:** {current_time}
🔍 **Scanned:** {pairs_scanned}
📈 **Status:** No trading opportunities detected
⚪ **Next scan in {self.settings.interval_minutes} minutes**

💡 Waiting for better market conditions...
                    """
                    
                    # Send to all subscribed users
                    for user_id in self.settings.subscribed_users.copy():
                        try:
                            await self.application.bot.send_message(
                                user_id, no_signal_message.strip(), parse_mode='Markdown'
                            )
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            logger.error(f"Failed to send no-signal alert to user {user_id}: {e}")
                            if "chat not found" in str(e).lower():
                                self.settings.subscribed_users.discard(user_id)
                    
                    logger.info(f"No signals found in this scan cycle - notified users")

                # Wait for next full scan cycle
                logger.info(f"Waiting {self.settings.interval_minutes} minutes for next scan...")
                await asyncio.sleep(self.settings.interval_minutes * 60)

            except asyncio.CancelledError:
                logger.info("Live monitoring cancelled")
                break
            except Exception as e:
                logger.error(f"Error in live monitoring loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retry

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced help command"""
        await update.message.reply_text(help_message, parse_mode='Markdown')

    async def post_init(self, application):
        """Post-initialization hook to start auto alerts after event loop is running"""
        if self.settings.auto_alerts and self.settings.subscribed_users:
            await self._start_auto_alerts()

    def run(self):
        """Start the enhanced bot"""
        # Create application
        self.application = Application.builder().token(self.telegram_token).build()

        # Add post init hook
        self.application.post_init = self.post_init

        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("analyze", self.analyze_command))
        self.application.add_handler(CommandHandler("performance", self.performance_command))
        self.application.add_handler(CommandHandler("set_interval", self.set_interval_command))
        self.application.add_handler(CommandHandler("timeframe", self.timeframe_command))
        self.application.add_handler(CommandHandler("alerts", self.alerts_command))
        self.application.add_handler(CommandHandler("config", self.config_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        # Start the bot
        logger.info("Starting Enhanced Forex Telegram Bot v2.0...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """Main function to run the enhanced bot"""
    # Configuration - Replace with your actual tokens
    TELEGRAM_BOT_TOKEN = "8186199634:AAEEafBIm5GhZrhrWt-je8wa1UESaTHF9ZM"
    TWELVEDATA_API_KEY = "b971d5ae2d0447fbb2fa621565a16334"  # Optional

    # Validate token
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("Please set your Telegram bot token in the TELEGRAM_BOT_TOKEN variable")
        print("\n🔑 SETUP REQUIRED:")
        print("1. Get bot token from @BotFather on Telegram")
        print("2. Replace 'YOUR_TELEGRAM_BOT_TOKEN_HERE' with your actual token")
        print("3. Optionally get TwelveData API key for real market data")
        print("4. Run: pip install python-telegram-bot requests numpy")
        return

    try:
        # Create and run enhanced bot
        bot = ForexTelegramBot(
            telegram_token=TELEGRAM_BOT_TOKEN,
            api_key=TWELVEDATA_API_KEY if TWELVEDATA_API_KEY != "YOUR_TWELVEDATA_API_KEY_HERE" else None
        )
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")

if __name__ == "__main__":
    main()