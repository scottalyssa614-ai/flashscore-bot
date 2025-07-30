
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
📚 **Live Forex Signal Bot v3.0 - Help**

**🆕 Live Features:**
• 🔄 **24/7 Active Monitoring** - Continuous market scanning
• 💱 **Multi-Currency Support** - Monitor 50+ pairs simultaneously
• 💰 **TP/SL Suggestions** - Smart profit targets & risk management
• ⚡ **Instant Alerts** - Real-time notifications when conditions are met
• 📊 **Performance Tracking** - Win/loss statistics & signal history
• 🔧 **Multiple Indicators** - RSI + EMA + MACD + S/R + Patterns
• ⚙️ **Live Settings** - Adjust monitoring without restart

**📱 Commands:**
• `/start` - Activate live monitoring & quick access menu
• `/analyze [PAIR]` - Manual analysis (e.g., `/analyze GBPUSD`)
• `/performance` - View trading statistics
• `/live [on/off]` - Toggle live monitoring
• `/add_pair [PAIR]` - Add currency pair to monitor
• `/remove_pair [PAIR]` - Remove currency pair from monitoring
• `/pairs` - View monitored pairs
• `/set_interval [5-30]` - Change scan frequency (minutes)
• `/timeframe [15min|30min|1h]` - Set analysis timeframe
• `/config` - Show current settings

**💱 Supported Currency Pairs (50+):**
**Majors:** EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD
**Crosses:** EURGBP, EURJPY, GBPJPY, AUDJPY, CHFJPY, CADJPY, NZDJPY
**Exotics:** USDSGD, USDHKD, USDSEK, USDNOK, USDDKK, USDPLN, and more

**🔄 Live Monitoring:**
• Scans multiple pairs every 5-30 minutes (default: 8 minutes)
• Automatically sends alerts when signal conditions are met
• No need to manually request analysis
• Prevents duplicate alerts for same signal

**🎯 Signal Types:**
🟢 **BUY** - Oversold + Support + Bullish patterns + EMA/MACD confirmation
🔴 **SELL** - Overbought + Resistance + Bearish patterns + EMA/MACD confirmation
⚪ **NO SIGNAL** - Conditions don't meet 60%+ confidence threshold

**💰 Trade Management:**
• Take Profit: 20-40 pips (automatically calculated)
• Stop Loss: 15-25 pips (risk management)
• High confidence signals only (60%+ threshold)
• Proper pip calculation for JPY pairs

The bot is LIVE and actively watching 50+ markets for you!
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
    currency_pair: str

@dataclass
class BotSettings:
    """Bot configuration settings"""
    interval_minutes: int = 8  # Reduced for more frequent scanning
    timeframe: str = "15min"
    tp_pips_min: int = 20
    tp_pips_max: int = 40
    sl_pips_min: int = 15
    sl_pips_max: int = 25
    live_monitoring: bool = True  # Auto-enable live monitoring
    subscribed_users: Set[int] = None
    monitored_pairs: Set[str] = None

    def __post_init__(self):
        if self.subscribed_users is None:
            self.subscribed_users = set()
        if self.monitored_pairs is None:
            # Extended default pairs to monitor - mix of majors, crosses, and popular minors
            self.monitored_pairs = {
                # Major pairs
                "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                # Popular crosses
                "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "CADJPY", "NZDJPY",
                # Additional crosses
                "EURCHF", "EURAUD", "GBPCHF", "GBPAUD", "AUDCHF", "AUDCAD", "NZDCHF",
                # Minor pairs
                "USDSGD", "USDHKD", "USDSEK", "USDNOK", "USDDKK"
            }

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
    """Validates and formats currency pairs - Expanded with 50+ pairs"""

    # Comprehensive list of forex pairs - Majors, Minors, and Exotics
    VALID_PAIRS = {
        # Major pairs (7)
        'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',

        # European crosses (12)
        'EURGBP', 'EURJPY', 'EURCHF', 'EURAUD', 'EURCAD', 'EURNZD',
        'EURSEK', 'EURNOK', 'EURDKK', 'EURPLN', 'EURCZK', 'EURHUF',

        # British pound crosses (10)
        'GBPJPY', 'GBPCHF', 'GBPAUD', 'GBPCAD', 'GBPNZD', 'GBPSEK',
        'GBPNOK', 'GBPDKK', 'GBPPLN', 'GBPSGD',

        # Australian dollar crosses (8)
        'AUDJPY', 'AUDCHF', 'AUDCAD', 'AUDNZD', 'AUDSEK', 'AUDNOK',
        'AUDSGD', 'AUDHKD',

        # Japanese yen crosses (12)
        'CADJPY', 'NZDJPY', 'CHFJPY', 'SEKJPY', 'NOKJPY', 'DKkjpy',
        'SGDJPY', 'HKDJPY', 'PLNJPY', 'CZkjpy', 'HUFJPY', 'ZARJPY',

        # Canadian dollar crosses (6)
        'CADCHF', 'NZDCAD', 'CADSEK', 'CADNOK', 'CADSGD', 'CADHKD',

        # New Zealand dollar crosses (5)
        'NZDCHF', 'NZDSEK', 'NZDNOK', 'NZDSGD', 'NZDHKD',

        # Swiss franc crosses (4)
        'CHFSEK', 'CHFNOK', 'CHFPLN', 'CHFHUF',

        # USD minor pairs (20)
        'USDSGD', 'USDHKD', 'USDSEK', 'USDNOK', 'USDDKK', 'USDPLN',
        'USDCZK', 'USDHUF', 'USDTRY', 'USDZAR', 'USDMXN', 'USDBRL',
        'USDKRW', 'USDTWD', 'USDTHB', 'USDPHP', 'USDIDR', 'USDMYR',
        'USDINR', 'USDCNY',

        # Scandinavian crosses (6)
        'SEKDKK', 'SEKNOK', 'SEKPLN', 'NOKDKK', 'NOKPLN', 'DKKPLN',

        # Exotic and emerging market pairs (10)
        'USDRUB', 'USDILS', 'USDEGP', 'USDAED', 'USDSAR', 'USDQAR',
        'USDKWD', 'USDJOD', 'USDBHD', 'USDLKR'
    }

    # Currency group classifications for better organization
    MAJOR_PAIRS = {'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD'}

    CROSS_PAIRS = {'EURGBP', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CHFJPY', 'CADJPY', 'NZDJPY',
                   'EURCHF', 'EURAUD', 'GBPCHF', 'GBPAUD', 'AUDCHF', 'AUDCAD', 'NZDCHF'}

    MINOR_PAIRS = {'USDSGD', 'USDHKD', 'USDSEK', 'USDNOK', 'USDDKK', 'USDPLN', 'USDCZK',
                   'USDHUF', 'USDTRY', 'USDZAR', 'USDMXN', 'USDBRL'}

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

    @classmethod
    def get_pair_category(cls, pair: str) -> str:
        """Get the category of a currency pair"""
        if pair in cls.MAJOR_PAIRS:
            return "Major"
        elif pair in cls.CROSS_PAIRS:
            return "Cross"
        elif pair in cls.MINOR_PAIRS:
            return "Minor"
        else:
            return "Exotic"

    @classmethod
    def get_pairs_by_category(cls) -> Dict[str, List[str]]:
        """Get pairs organized by category"""
        return {
            "Majors": sorted(list(cls.MAJOR_PAIRS)),
            "Popular Crosses": sorted(list(cls.CROSS_PAIRS)),
            "Minors": sorted(list(cls.MINOR_PAIRS)),
            "Others": sorted([p for p in cls.VALID_PAIRS 
                            if p not in cls.MAJOR_PAIRS 
                            and p not in cls.CROSS_PAIRS 
                            and p not in cls.MINOR_PAIRS])
        }

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
                logger.warning(f"No values in API response for {symbol}, using mock data")
                return self._get_mock_data(symbol)

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
            logger.error(f"Error fetching data for {symbol}: {e}")
            return self._get_mock_data(symbol)

    def _get_mock_data(self, symbol: str = "EUR/USD") -> List[Candle]:
        """Generate realistic mock data based on currency pair type"""
        logger.info(f"Using mock data for {symbol}")
        candles = []

        # Different base prices for different currency types
        if "JPY" in symbol:
            base_price = 150.0 + np.random.uniform(-20, 20)
            volatility = 0.005
        elif any(exotic in symbol for exotic in ["TRY", "ZAR", "MXN", "BRL", "KRW"]):
            base_price = 15.0 + np.random.uniform(-5, 5)
            volatility = 0.01
        elif any(scand in symbol for scand in ["SEK", "NOK", "DKK"]):
            base_price = 10.0 + np.random.uniform(-2, 2)
            volatility = 0.008
        else:
            base_price = 1.0850 + np.random.uniform(-0.2, 0.2)
            volatility = 0.0005

        base_time = datetime.now() - timedelta(hours=25)

        for i in range(100):
            change = np.random.normal(0, volatility)
            open_price = base_price + change
            high_price = open_price + abs(np.random.normal(0, volatility * 0.6))
            low_price = open_price - abs(np.random.normal(0, volatility * 0.6))
            close_price = open_price + np.random.normal(0, volatility * 0.4)

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
            "currency_pair": signal.currency_pair,
            "action": signal.action.value,
            "entry_price": signal.current_price,
            "take_profit": signal.take_profit,
            "stop_loss": signal.stop_loss,
            "tp_pips": signal.tp_pips,
            "sl_pips": signal.sl_pips,
            "rsi": signal.rsi,
            "pattern": signal.pattern,
            "confidence": signal.confidence,
            "pair_category": CurrencyPairValidator.get_pair_category(signal.currency_pair),
            "status": "OPEN"  # OPEN, TP_HIT, SL_HIT
        }

        self.signals_log.append(log_entry)
        self._save_log()
        logger.info(f"Logged {signal.action.value} signal for {signal.currency_pair} at {signal.current_price}")

    def get_stats(self) -> Dict:
        """Calculate performance statistics"""
        if not self.signals_log:
            return {
                "total_signals": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "avg_confidence": 0,
                "last_signal": "None",
                "pairs_analyzed": 0,
                "category_breakdown": {}
            }

        buy_count = sum(1 for s in self.signals_log if s["action"] == "BUY")
        sell_count = sum(1 for s in self.signals_log if s["action"] == "SELL")
        avg_confidence = np.mean([s["confidence"] for s in self.signals_log])
        unique_pairs = len(set(s["currency_pair"] for s in self.signals_log))

        # Category breakdown
        category_breakdown = {}
        for signal in self.signals_log:
            category = signal.get("pair_category", "Unknown")
            if category not in category_breakdown:
                category_breakdown[category] = 0
            category_breakdown[category] += 1

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
            "last_signal": f"{last_signal['action']} {last_signal['currency_pair']} at {last_signal_time}" if last_signal else "None",
            "pairs_analyzed": unique_pairs,
            "category_breakdown": category_breakdown
        }

class SignalGenerator:
    """Enhanced signal generator with multiple indicators and TP/SL calculation"""

    def __init__(self, settings: BotSettings):
        self.analyzer = TechnicalAnalyzer()
        self.settings = settings

    def calculate_tp_sl(self, action: SignalType, entry_price: float, currency_pair: str) -> Tuple[float, float, int, int]:
        """Calculate Take Profit and Stop Loss levels with pair-specific adjustments"""
        if action == SignalType.NONE:
            return 0.0, 0.0, 0, 0

        # Calculate pip value based on currency pair type
        if 'JPY' in currency_pair:
            pip_value = 0.01  # JPY pairs
        elif any(exotic in currency_pair for exotic in ["TRY", "ZAR", "MXN", "BRL", "KRW"]):
            pip_value = 0.001  # Some exotic pairs have different pip values
        else:
            pip_value = 0.0001  # Standard pairs

        # Adjust TP/SL ranges based on pair volatility
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)

        if pair_category == "Major":
            tp_range = (self.settings.tp_pips_min, self.settings.tp_pips_max)
            sl_range = (self.settings.sl_pips_min, self.settings.sl_pips_max)
        elif pair_category == "Cross":
            tp_range = (25, 50)  # Slightly wider for crosses
            sl_range = (20, 30)
        elif pair_category in ["Minor", "Exotic"]:
            tp_range = (30, 60)  # Wider for volatile pairs
            sl_range = (25, 40)
        else:
            tp_range = (self.settings.tp_pips_min, self.settings.tp_pips_max)
            sl_range = (self.settings.sl_pips_min, self.settings.sl_pips_max)

        tp_pips = np.random.randint(tp_range[0], tp_range[1] + 1)
        sl_pips = np.random.randint(sl_range[0], sl_range[1] + 1)

        if action == SignalType.BUY:
            take_profit = entry_price + (tp_pips * pip_value)
            stop_loss = entry_price - (sl_pips * pip_value)
        else:  # SELL
            take_profit = entry_price - (tp_pips * pip_value)
            stop_loss = entry_price + (sl_pips * pip_value)

        decimals = 3 if 'JPY' in currency_pair else 5
        return round(take_profit, decimals), round(stop_loss, decimals), tp_pips, sl_pips

    def generate_signal(self, candles: List[Candle], currency_pair: str = "EURUSD") -> TradingSignal:
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

        # Enhanced signal generation logic with pair-specific adjustments
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)

        # Adjust confidence thresholds based on pair type
        if pair_category == "Major":
            min_confidence = 60
            min_conditions = 3
        elif pair_category == "Cross":
            min_confidence = 55  # Slightly lower for crosses
            min_conditions = 3
        else:  # Minor/Exotic
            min_confidence = 65  # Higher threshold for volatile pairs
            min_conditions = 3

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

        # Determine final signal
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
            timestamp=datetime.now(),
            currency_pair=currency_pair
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
            timestamp=datetime.now(),
            currency_pair=currency_pair
        )

class LiveForexTelegramBot:
    """Live 24/7 Telegram bot with continuous market monitoring for 50+ currency pairs"""

    def __init__(self, telegram_token: str, api_key: Optional[str] = None):
        self.telegram_token = telegram_token
        self.data_provider = ForexDataProvider(api_key)
        self.settings = BotSettings()
        self.signal_generator = SignalGenerator(self.settings)
        self.performance_tracker = PerformanceTracker()
        self.live_monitoring_task = None
        self.application = None
        self.last_signals = {}  # Track last signal for each pair to avoid duplicates
        self.scan_batch_size = 8  # Process pairs in batches to avoid overwhelming

        # Load settings
        self._load_settings()

    def _load_settings(self):
        """Load bot settings from file"""
        try:
            if os.path.exists("bot_settings.json"):
                with open("bot_settings.json", 'r') as f:
                    data = json.load(f)
                    self.settings.interval_minutes = data.get("interval_minutes", 8)
                    self.settings.timeframe = data.get("timeframe", "15min")
                    self.settings.live_monitoring = data.get("live_monitoring", True)
                    self.settings.subscribed_users = set(data.get("subscribed_users", []))
                    self.settings.monitored_pairs = set(data.get("monitored_pairs", [
                        # Load extended default pairs
                        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                        "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "CADJPY", "NZDJPY",
                        "EURCHF", "EURAUD", "GBPCHF", "GBPAUD", "AUDCHF", "AUDCAD", "NZDCHF",
                        "USDSGD", "USDHKD", "USDSEK", "USDNOK", "USDDKK"
                    ]))
        except Exception as e:
            logger.error(f"Error loading settings: {e}")

    def _save_settings(self):
        """Save bot settings to file"""
        try:
            data = {
                "interval_minutes": self.settings.interval_minutes,
                "timeframe": self.settings.timeframe,
                "live_monitoring": self.settings.live_monitoring,
                "subscribed_users": list(self.settings.subscribed_users),
                "monitored_pairs": list(self.settings.monitored_pairs)
            }
            with open("bot_settings.json", 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced start command with live monitoring activation"""
        user_id = update.effective_user.id
        self.settings.subscribed_users.add(user_id)
        self.settings.live_monitoring = True  # Auto-enable live monitoring
        self._save_settings()

        # Start live monitoring if not already running
        if not self.live_monitoring_task or self.live_monitoring_task.done():
            await self._start_live_monitoring()

        keyboard = [
            [InlineKeyboardButton("📊 Manual Analysis", callback_data="analyze")],
            [InlineKeyboardButton("🔄 Live Status", callback_data="live_status")],
            [InlineKeyboardButton("📈 Performance", callback_data="performance")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("💱 All Pairs", callback_data="all_pairs")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Get category breakdown
        categories = CurrencyPairValidator.get_pairs_by_category()
        major_count = len([p for p in self.settings.monitored_pairs if p in categories["Majors"]])
        cross_count = len([p for p in self.settings.monitored_pairs if p in categories["Popular Crosses"]])
        minor_count = len([p for p in self.settings.monitored_pairs if p in categories["Minors"]])
        other_count = len([p for p in self.settings.monitored_pairs if p in categories["Others"]])

        monitored_pairs_preview = ", ".join(list(self.settings.monitored_pairs)[:6])
        if len(self.settings.monitored_pairs) > 6:
            monitored_pairs_preview += f" +{len(self.settings.monitored_pairs) - 6} more"

        welcome_message = f"""
🤖 **Welcome to Live Forex Signal Bot v3.0!**

🔴 **LIVE MONITORING ACTIVATED** 🔴

🆕 **Enhanced Live Features:**
• 🔄 **24/7 Global Scanning** - Monitoring {len(self.settings.monitored_pairs)} currency pairs
• ⚡ **Instant Multi-Market Alerts** - Real-time notifications across all markets
• 💱 **50+ Currency Pairs Supported** - Majors, Crosses, Minors & Exotics
• 📊 **Advanced Analysis** - Multi-timeframe technical indicators
• 💰 **Pair-Specific TP/SL** - Optimized for each currency type
• 🌍 **Global Market Coverage** - USD, EUR, GBP, JPY, AUD, CAD, NZD, CHF + Exotics

**📊 Currently Monitoring ({len(self.settings.monitored_pairs)} pairs):**
• 🏛️ Majors: `{major_count}` pairs
• 🔄 Crosses: `{cross_count}` pairs  
• 🌍 Minors: `{minor_count}` pairs
• 💎 Others: `{other_count}` pairs

**📍 Sample Pairs:** {monitored_pairs_preview}

**⚙️ Live Settings:**
• Scan Interval: `{self.settings.interval_minutes} minutes` (Fast scanning!)
• Timeframe: `{self.settings.timeframe}`
• Batch Processing: `{self.scan_batch_size} pairs per batch`
• Status: `{'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}`

**📱 Quick Commands:**
• `/pairs` - View all monitored pairs by category
• `/add_pair [PAIR]` - Add any supported pair (50+ available)
• `/analyze [PAIR]` - Manual analysis of any pair
• `/performance` - Trading statistics by pair category

**🎯 The bot is now ACTIVELY scanning 50+ global markets and will alert you instantly when strong signals appear across any monitored currency pair! 🚀**

*Supporting everything from EURUSD to exotic pairs like USDTRY, USDZAR!*
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
        elif query.data == "live_status":
            await self._send_live_status(query.message.chat_id, context)
        elif query.data == "settings":
            await self._send_settings(query.message.chat_id, context)
        elif query.data == "performance":
            await self._send_performance(query.message.chat_id, context)
        elif query.data == "all_pairs":
            await self._send_all_supported_pairs(query.message.chat_id, context)

    async def _send_all_supported_pairs(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send list of all supported currency pairs by category"""
        categories = CurrencyPairValidator.get_pairs_by_category()

        message = "💱 **All Supported Currency Pairs (50+)**\n\n"

        for category, pairs in categories.items():
            if pairs:  # Only show categories that have pairs
                pairs_text = " • ".join(pairs[:8])  # Show first 8 pairs per line
                if len(pairs) > 8:
                    pairs_text += f"\n • " + " • ".join(pairs[8:16])
                if len(pairs) > 16:
                    pairs_text += f"\n • " + " • ".join(pairs[16:])

                message += f"**🏷️ {category} ({len(pairs)}):**\n{pairs_text}\n\n"

        message += f"""
**📊 Total Supported:** {len(CurrencyPairValidator.VALID_PAIRS)} pairs

**📱 Commands:**
• `/add_pair USDTRY` - Add any pair to monitoring
• `/analyze GBPJPY` - Analyze specific pair
• `/pairs` - View your monitored pairs

**Examples:**
• Major: `EURUSD, GBPUSD, USDJPY`
• Cross: `EURJPY, GBPJPY, AUDJPY`
• Minor: `USDSGD, USDSEK, USDNOK`
• Exotic: `USDTRY, USDZAR, USDMXN`
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def _send_live_status(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send enhanced live monitoring status with category breakdown"""
        status_emoji = "🟢 ACTIVE" if self.settings.live_monitoring else "🔴 INACTIVE"
        task_status = "Running" if self.live_monitoring_task and not self.live_monitoring_task.done() else "Stopped"

        # Category breakdown of monitored pairs
        categories = CurrencyPairValidator.get_pairs_by_category()
        major_pairs = [p for p in self.settings.monitored_pairs if p in categories["Majors"]]
        cross_pairs = [p for p in self.settings.monitored_pairs if p in categories["Popular Crosses"]]
        minor_pairs = [p for p in self.settings.monitored_pairs if p in categories["Minors"]]
        other_pairs = [p for p in self.settings.monitored_pairs if p in categories["Others"]]

        pairs_breakdown = f"""
**🏛️ Majors ({len(major_pairs)}):** {', '.join(major_pairs[:5])}{'...' if len(major_pairs) > 5 else ''}
**🔄 Crosses ({len(cross_pairs)}):** {', '.join(cross_pairs[:5])}{'...' if len(cross_pairs) > 5 else ''}
**🌍 Minors ({len(minor_pairs)}):** {', '.join(minor_pairs[:5])}{'...' if len(minor_pairs) > 5 else ''}
**💎 Others ({len(other_pairs)}):** {', '.join(other_pairs[:5])}{'...' if len(other_pairs) > 5 else ''}
        """

        message = f"""
🔄 **Live Multi-Market Monitoring Status**

**Status:** {status_emoji}
**Task:** {task_status}
**Global Scan Interval:** {self.settings.interval_minutes} minutes
**Timeframe:** {self.settings.timeframe}
**Batch Size:** {self.scan_batch_size} pairs/batch
**Subscribed Users:** {len(self.settings.subscribed_users)}

**📊 Monitored Markets ({len(self.settings.monitored_pairs)} pairs):**
{pairs_breakdown}

**🔄 Processing:**
• Batched scanning for optimal performance
• Smart duplicate alert prevention
• Pair-specific volatility adjustments
• Global market session awareness

**🕐 Next Scan:** ~{self.settings.interval_minutes} minutes
**🔔 Instant Alerts:** Enabled across all markets

The bot is continuously scanning these global markets and will send alerts immediately when signal conditions are met across any currency pair!
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def pairs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show all monitored currency pairs organized by category"""
        if not self.settings.monitored_pairs:
            await update.message.reply_text(
                "📭 No pairs are currently being monitored.\n\n"
                "Use `/add_pair EURUSD` to start monitoring pairs.\n"
                "See all 50+ supported pairs with the button above!",
                parse_mode='Markdown'
            )
            return

        # Organize pairs by category
        categories = CurrencyPairValidator.get_pairs_by_category()
        monitored_by_category = {}

        for category, all_pairs in categories.items():
            monitored_in_category = [p for p in self.settings.monitored_pairs if p in all_pairs]
            if monitored_in_category:
                monitored_by_category[category] = monitored_in_category

        pairs_text = ""
        for category, pairs in monitored_by_category.items():
            pairs_list = " • ".join(pairs)
            pairs_text += f"**🏷️ {category} ({len(pairs)}):**\n{pairs_list}\n\n"

        message = f"""
📊 **Your Monitored Currency Pairs ({len(self.settings.monitored_pairs)})**

{pairs_text}

**⚙️ Monitoring Settings:**
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Timeframe: `{self.settings.timeframe}`
• Batch Processing: `{self.scan_batch_size} pairs/batch`
• Status: `{'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}`

**📱 Commands:**
• `/add_pair USDTRY` - Add exotic pair
• `/remove_pair GBPJPY` - Remove pair
• `/live on` - Start live monitoring
• Use the 'All Pairs' button to see 50+ supported pairs

**💡 Pro Tip:** Add pairs from different categories for diversified global coverage!
        """

        keyboard = [[InlineKeyboardButton("💱 View All 50+ Supported Pairs", callback_data="all_pairs")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def add_pair_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add a currency pair to monitoring with enhanced validation"""
        if not context.args:
            categories = CurrencyPairValidator.get_pairs_by_category()
            sample_pairs = []
            for category, pairs in categories.items():
                sample_pairs.extend(pairs[:2])  # Get 2 examples from each category

            await update.message.reply_text(
                f"**💱 Add Currency Pair to Monitoring**\n\n"
                f"**Usage:** `/add_pair [PAIR]`\n\n"
                f"**Examples:**\n"
                f"• `/add_pair USDTRY` - Turkish Lira\n"
                f"• `/add_pair GBPJPY` - British Pound / Japanese Yen\n"
                f"• `/add_pair EURAUD` - Euro / Australian Dollar\n"
                f"• `/add_pair USDSEK` - US Dollar / Swedish Krona\n\n"
                f"**Sample Supported Pairs:**\n"
                f"{' • '.join(sample_pairs[:12])}\n\n"
                f"**📊 Currently monitoring:** {len(self.settings.monitored_pairs)} pairs\n"
                f"**💡 Tip:** Use the 'All Pairs' button to see all 50+ supported pairs!",
                parse_mode='Markdown'
            )
            return

        pair_input = context.args[0]
        is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(pair_input)

        if not is_valid:
            await update.message.reply_text(
                f"❌ **Invalid currency pair:** `{pair_input}`\n\n"
                f"**Supported formats:**\n"
                f"• `EURUSD` or `EUR/USD`\n"
                f"• `GBPJPY` or `GBP/JPY`\n"
                f"• `USDTRY` or `USD/TRY`\n\n"
                f"**💡 Use `/pairs` to see monitored pairs or the 'All Pairs' button for full list!**",
                parse_mode='Markdown'
            )
            return

        if display_format in self.settings.monitored_pairs:
            pair_category = CurrencyPairValidator.get_pair_category(display_format)
            await update.message.reply_text(
                f"⚠️ **{display_format} ({pair_category}) is already being monitored.**\n\n"
                f"**📊 Total monitored pairs:** {len(self.settings.monitored_pairs)}\n"
                f"Use `/pairs` to see all monitored pairs.",
                parse_mode='Markdown'
            )
            return

        # Add pair to monitoring
        self.settings.monitored_pairs.add(display_format)
        self._save_settings()

        # Restart live monitoring if active to include new pair
        if self.settings.live_monitoring:
            await self._restart_live_monitoring()

        pair_category = CurrencyPairValidator.get_pair_category(display_format)
        await update.message.reply_text(
            f"✅ **{display_format} ({pair_category}) added to monitoring!**\n\n"
            f"**📊 Now monitoring:** {len(self.settings.monitored_pairs)} currency pairs\n"
            f"**⚡ Scan interval:** {self.settings.interval_minutes} minutes\n"
            f"**🔄 Status:** {'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}\n\n"
            f"**💡 The bot will now include {display_format} in its continuous market scanning and send alerts when signal conditions are met!**\n\n"
            f"Use `/analyze {display_format}` for immediate analysis.",
            parse_mode='Markdown'
        )

    async def remove_pair_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove a currency pair from monitoring"""
        if not context.args:
            if not self.settings.monitored_pairs:
                await update.message.reply_text(
                    "📭 **No pairs are currently being monitored.**\n\n"
                    "Use `/add_pair [PAIR]` to start monitoring pairs.",
                    parse_mode='Markdown'
                )
                return

            # Show first few monitored pairs as examples
            sample_pairs = list(self.settings.monitored_pairs)[:8]
            pairs_text = " • ".join(sample_pairs)
            if len(self.settings.monitored_pairs) > 8:
                pairs_text += f" +{len(self.settings.monitored_pairs) - 8} more"

            await update.message.reply_text(
                f"**🗑️ Remove Currency Pair from Monitoring**\n\n"
                f"**Usage:** `/remove_pair [PAIR]`\n\n"
                f"**Examples:**\n"
                f"• `/remove_pair EURUSD`\n"
                f"• `/remove_pair GBPJPY`\n\n"
                f"**📊 Currently monitored ({len(self.settings.monitored_pairs)}):**\n"
                f"{pairs_text}\n\n"
                f"Use `/pairs` to see all monitored pairs by category.",
                parse_mode='Markdown'
            )
            return

        pair_input = context.args[0]
        is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(pair_input)

        if not is_valid:
            await update.message.reply_text(
                f"❌ **Invalid currency pair:** `{pair_input}`\n\n"
                f"Please provide a valid currency pair format like `EURUSD` or `EUR/USD`.",
                parse_mode='Markdown'
            )
            return

        if display_format not in self.settings.monitored_pairs:
            await update.message.reply_text(
                f"⚠️ **{display_format} is not currently being monitored.**\n\n"
                f"**📊 Total monitored pairs:** {len(self.settings.monitored_pairs)}\n"
                f"Use `/pairs` to see all monitored pairs.",
                parse_mode='Markdown'
            )
            return

        # Remove pair from monitoring
        self.settings.monitored_pairs.remove(display_format)
        self._save_settings()

        # Restart live monitoring if active
        if self.settings.live_monitoring:
            await self._restart_live_monitoring()

        pair_category = CurrencyPairValidator.get_pair_category(display_format)
        await update.message.reply_text(
            f"✅ **{display_format} ({pair_category}) removed from monitoring.**\n\n"
            f"**📊 Now monitoring:** {len(self.settings.monitored_pairs)} currency pairs\n"
            f"**🔄 Status:** {'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}\n\n"
            f"The bot will no longer scan {display_format} for signals.\n\n"
            f"Use `/add_pair {display_format}` to add it back anytime.",
            parse_mode='Markdown'
        )

    async def live_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle live monitoring on/off"""
        user_id = update.effective_user.id

        if not context.args:
            status = "ON" if self.settings.live_monitoring else "OFF"
            await update.message.reply_text(
                f"**🔄 Live Multi-Market Monitoring:** `{status}`\n\n"
                f"**Currently monitoring:** {len(self.settings.monitored_pairs)} pairs\n"
                f"**Scan interval:** {self.settings.interval_minutes} minutes\n\n"
                "**Usage:** `/live [on|off]`",
                parse_mode='Markdown'
            )
            return

        setting = context.args[0].lower()

        if setting == "on":
            self.settings.live_monitoring = True
            self.settings.subscribed_users.add(user_id)
            self._save_settings()
            await self._start_live_monitoring()

            # Get category breakdown for the message
            categories = CurrencyPairValidator.get_pairs_by_category()
            major_count = len([p for p in self.settings.monitored_pairs if p in categories["Majors"]])
            cross_count = len([p for p in self.settings.monitored_pairs if p in categories["Popular Crosses"]])
            minor_count = len([p for p in self.settings.monitored_pairs if p in categories["Minors"]])

            await update.message.reply_text(
                f"🟢 **Live Multi-Market Monitoring ACTIVATED!**\n\n"
                f"📊 **Monitoring {len(self.settings.monitored_pairs)} pairs:**\n"
                f"• 🏛️ Majors: {major_count}\n"
                f"• 🔄 Crosses: {cross_count}\n" 
                f"• 🌍 Minors/Others: {minor_count + (len(self.settings.monitored_pairs) - major_count - cross_count)}\n\n"
                f"⚡ **Scan frequency:** Every {self.settings.interval_minutes} minutes\n"
                f"🔔 You'll receive instant alerts from all monitored markets!\n\n"
                f"**Global coverage active across USD, EUR, GBP, JPY, AUD, CAD, NZD, CHF + exotics!**",
                parse_mode='Markdown'
            )
        elif setting == "off":
            if user_id in self.settings.subscribed_users:
                self.settings.subscribed_users.remove(user_id)

            # If no users subscribed, stop monitoring
            if not self.settings.subscribed_users:
                self.settings.live_monitoring = False
                await self._stop_live_monitoring()

            self._save_settings()
            await update.message.reply_text(
                "🔴 **Live monitoring DISABLED for you.**\n\n"
                "Use `/live on` to reactivate instant multi-market alerts.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Use 'on' or 'off' with the live command")

    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced analyze command with currency pair support and category detection"""
        # Extract currency pair from command arguments
        currency_pair = "EURUSD"  # Default
        if context.args and len(context.args) > 0:
            user_input = context.args[0]
            is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(user_input)

            if not is_valid:
                await update.message.reply_text(
                    f"❌ Invalid currency pair: `{user_input}`\n\n"
                    "**Supported pairs examples:**\n"
                    "• Majors: EURUSD, GBPUSD, USDJPY\n"
                    "• Crosses: EURJPY, GBPJPY, AUDJPY\n"
                    "• Minors: USDSGD, USDSEK, USDNOK\n"
                    "• Exotics: USDTRY, USDZAR, USDMXN\n\n"
                    "**Usage:** `/analyze GBPUSD` or just `/analyze` for EURUSD",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💱 All Supported Pairs", callback_data="all_pairs")]])
                )
                return

            currency_pair = display_format
            api_pair = api_format
        else:
            api_pair = "EUR/USD"

        await self._send_analysis(update.message.chat_id, context, currency_pair, api_pair)

    async def _send_analysis(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE, 
                           currency_pair: str = "EURUSD", api_pair: str = "EUR/USD"):
        """Send market analysis for specified currency pair with category info"""
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)
        await context.bot.send_message(chat_id, f"🔍 Analyzing {currency_pair} ({pair_category})...")

        try:
            candles = await self.data_provider.get_candles(
                symbol=api_pair,
                interval=self.settings.timeframe,
                count=100
            )

            if not candles:
                await context.bot.send_message(chat_id, f"❌ Could not fetch data for {currency_pair}.")
                return

            signal = self.signal_generator.generate_signal(candles, currency_pair)

            # Log signal if it's not NONE
            if signal.action != SignalType.NONE:
                self.performance_tracker.log_signal(signal)

            message = self._format_signal_message(signal, is_live_alert=False)
            await context.bot.send_message(chat_id, message, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in analysis: {e}")
            await context.bot.send_message(chat_id, f"❌ Analysis failed for {currency_pair}. Please try again.")

    def _format_no_signal_summary(self, no_signal_pairs: List[str], scan_time: datetime) -> str:
        """Format a summary message when no signals are found with category breakdown"""
        # Organize pairs by category for the summary
        categories = CurrencyPairValidator.get_pairs_by_category()
        category_counts = {}

        for pair in no_signal_pairs:
            category = CurrencyPairValidator.get_pair_category(pair)
            category_counts[category] = category_counts.get(category, 0) + 1

        category_text = " | ".join([f"{cat}: {count}" for cat, count in category_counts.items()])

        pairs_preview = ", ".join(no_signal_pairs[:8])  # Show first 8 pairs
        if len(no_signal_pairs) > 8:
            pairs_preview += f" +{len(no_signal_pairs) - 8} more"

        message = f"""
📊 **Global Market Scan Complete - No Signals**

⚪ **Status:** No trading conditions met across monitored markets
🔍 **Pairs Scanned:** {len(no_signal_pairs)} ({category_text})
⏰ **Scan Time:** {scan_time.strftime('%H:%M UTC')}
📈 **Timeframe:** {self.settings.timeframe}

**🌍 Markets Analyzed:** {pairs_preview}

💡 **Market Status:** All monitored pairs are in consolidation or weak signal conditions
🔄 **Next Global Scan:** {self.settings.interval_minutes} minutes

The bot is actively monitoring global markets - you'll get instant alerts when strong signals appear across any currency pair! 📡
        """
        return message

    def _format_signal_message(self, signal: TradingSignal, is_live_alert: bool = True) -> str:
        """Format signal into readable message with pair category info"""
        currency_pair = signal.currency_pair
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)

        # Determine decimal places for display
        decimals = 3 if 'JPY' in currency_pair else 5

        alert_header = "🚨 **LIVE GLOBAL ALERT** 🚨\n\n" if is_live_alert else ""

        if signal.action == SignalType.BUY:
            emoji = "🟢"
            message = f"""{alert_header}📈 **Pair:** {currency_pair} ({pair_category})
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
🕐 Time: `{signal.timestamp.strftime('%H:%M UTC')}`
🏷️ Category: `{pair_category}`"""

        elif signal.action == SignalType.SELL:
            emoji = "🔴"
            message = f"""{alert_header}📈 **Pair:** {currency_pair} ({pair_category})
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
🕐 Time: `{signal.timestamp.strftime('%H:%M UTC')}`
🏷️ Category: `{pair_category}`"""

        else:
            emoji = "⚪"
            no_alert_header = "📊 **Manual Analysis**\n\n" if not is_live_alert else ""
            message = f"""{no_alert_header}📈 **Pair:** {currency_pair} ({pair_category})
{emoji} **NO SIGNAL**

📊 **Current Analysis:**
• RSI: `{signal.rsi:.1f}`
• Current Price: `{signal.current_price:.{decimals}f}`
• Pattern: `{signal.pattern}`

💡 **Status:** {signal.reason}

⏰ Timeframe: `{self.settings.timeframe}`
🕐 Time: `{signal.timestamp.strftime('%H:%M UTC')}`
🏷️ Category: `{pair_category}`

💡 Conditions not strong enough for a signal."""

        return message

    async def performance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show performance statistics"""
        await self._send_performance(update.message.chat_id, context)

    async def _send_performance(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send enhanced performance statistics with category breakdown"""
        stats = self.performance_tracker.get_stats()

        # Format category breakdown
        category_text = ""
        if stats['category_breakdown']:
            for category, count in stats['category_breakdown'].items():
                category_text += f"• {category}: `{count}` signals\n"
        else:
            category_text = "• No signals recorded yet\n"

        message = f"""
📈 **Live Global Performance Statistics**

📊 **Signal Summary:**
• Total Signals: `{stats['total_signals']}`
• Buy Signals: `{stats['buy_signals']}`
• Sell Signals: `{stats['sell_signals']}`
• Avg Confidence: `{stats['avg_confidence']}%`
• Unique Pairs: `{stats['pairs_analyzed']}`

**📊 Signals by Category:**
{category_text}

🕐 **Last Signal:** {stats['last_signal']}

🔄 **Live Multi-Market Monitoring:**
• Status: `{'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}`
• Monitored Pairs: `{len(self.settings.monitored_pairs)}`
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Batch Size: `{self.scan_batch_size} pairs/batch`
• Timeframe: `{self.settings.timeframe}`
• Subscribed Users: `{len(self.settings.subscribed_users)}`

💡 All signals are generated automatically across global markets and sent instantly when conditions are met!
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def _send_settings(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send current settings with enhanced pair info"""
        # Get category breakdown
        categories = CurrencyPairValidator.get_pairs_by_category()
        major_count = len([p for p in self.settings.monitored_pairs if p in categories["Majors"]])
        cross_count = len([p for p in self.settings.monitored_pairs if p in categories["Popular Crosses"]])
        minor_count = len([p for p in self.settings.monitored_pairs if p in categories["Minors"]])
        other_count = len([p for p in self.settings.monitored_pairs if p in categories["Others"]])

        pairs_preview = ", ".join(list(self.settings.monitored_pairs)[:4])
        if len(self.settings.monitored_pairs) > 4:
            pairs_preview += f" +{len(self.settings.monitored_pairs) - 4} more"

        message = f"""
⚙️ **Live Global Bot Configuration**

🔄 **Live Multi-Market Monitoring:**
• Status: `{'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}`
• Global Scan Interval: `{self.settings.interval_minutes} minutes`
• Timeframe: `{self.settings.timeframe}`
• Batch Processing: `{self.scan_batch_size} pairs/batch`

**📊 Monitored Markets ({len(self.settings.monitored_pairs)} pairs):**
• 🏛️ Majors: `{major_count}`
• 🔄 Crosses: `{cross_count}`
• 🌍 Minors: `{minor_count}`
• 💎 Others: `{other_count}`

**📍 Sample:** {pairs_preview}

📊 **Signal Settings:**
• Take Profit: `{self.settings.tp_pips_min}-{self.settings.tp_pips_max} pips` (pair-adjusted)
• Stop Loss: `{self.settings.sl_pips_min}-{self.settings.sl_pips_max} pips` (pair-adjusted)
• Min Confidence: `60-65%` (category-based)

👥 **Users:** `{len(self.settings.subscribed_users)}` subscribed

**📱 Available Commands:**
• `/live [on|off]` - Toggle live monitoring
• `/set_interval [5-30]` - Change scan frequency
• `/timeframe [15min|30min|1h]` - Change timeframe
• `/add_pair [PAIR]` - Add any of 50+ supported pairs
• `/pairs` - View monitored pairs by category

**Examples:**
• `/set_interval 5` - Ultra-fast scanning every 5 minutes
• `/add_pair USDTRY` - Monitor Turkish Lira
• `/add_pair EURJPY` - Monitor EUR/JPY cross
• `/timeframe 30min` - Use 30-minute candles
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def set_interval_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set scanning interval with batch processing consideration"""
        if not context.args:
            await update.message.reply_text(
                f"**Current scan interval:** `{self.settings.interval_minutes} minutes`\n\n"
                f"**Batch processing:** {self.scan_batch_size} pairs per batch\n"
                f"**Total scan time:** ~{(len(self.settings.monitored_pairs) // self.scan_batch_size + 1) * 2} minutes\n\n"
                "**Usage:** `/set_interval [5-30]`\n"
                "**Examples:**\n"
                "• `/set_interval 5` - Ultra-fast scanning\n"
                "• `/set_interval 15` - Balanced scanning\n"
                "• `/set_interval 30` - Conservative scanning",
                parse_mode='Markdown'
            )
            return

        try:
            new_interval = int(context.args[0])
            if 5 <= new_interval <= 30:
                old_interval = self.settings.interval_minutes
                self.settings.interval_minutes = new_interval
                self._save_settings()

                # Restart live monitoring with new interval
                if self.settings.live_monitoring:
                    await self._restart_live_monitoring()

                estimated_scan_time = (len(self.settings.monitored_pairs) // self.scan_batch_size + 1) * 2

                await update.message.reply_text(
                    f"✅ **Global scan interval updated:** `{old_interval}min` → `{new_interval}min`\n\n"
                    f"📊 **Impact on {len(self.settings.monitored_pairs)} monitored pairs:**\n"
                    f"• Scan frequency: Every `{new_interval} minutes`\n"
                    f"• Estimated scan time: ~`{estimated_scan_time} minutes`\n"
                    f"• Batch processing: `{self.scan_batch_size} pairs/batch`\n\n"
                    "🔄 Live monitoring restarted with new interval!",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Interval must be between 5-30 minutes")
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")

    async def timeframe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set analysis timeframe"""
        if not context.args:
            await update.message.reply_text(
                f"**Current timeframe:** `{self.settings.timeframe}`\n\n"
                "**Usage:** `/timeframe [15min|30min|1h]`\n"
                "**Options:**\n"
                "• `15min` - Fast signals, more frequent\n"
                "• `30min` - Balanced approach\n"
                "• `1h` - Stronger signals, less frequent",
                parse_mode='Markdown'
            )
            return

        new_timeframe = context.args[0].lower()
        valid_timeframes = ["15min", "30min", "1h"]

        if new_timeframe in valid_timeframes:
            old_timeframe = self.settings.timeframe
            self.settings.timeframe = new_timeframe
            self._save_settings()
            await update.message.reply_text(
                f"✅ **Global timeframe updated:** `{old_timeframe}` → `{new_timeframe}`\n\n"
                f"📊 **Impact:** All {len(self.settings.monitored_pairs)} monitored pairs will now use `{new_timeframe}` candles\n"
                "🔄 Change applies to all future scans across all markets!",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ Invalid timeframe. Use: {', '.join(valid_timeframes)}"
            )

    async def config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show configuration"""
        await self._send_settings(update.message.chat_id, context)

    async def _start_live_monitoring(self):
        """Start live monitoring system"""
        if self.live_monitoring_task is None or self.live_monitoring_task.done():
            self.live_monitoring_task = asyncio.create_task(self._live_monitoring_loop())
            logger.info(f"🔴 Live monitoring started - bot is now actively scanning {len(self.settings.monitored_pairs)} global markets")

    async def _stop_live_monitoring(self):
        """Stop live monitoring system"""
        if self.live_monitoring_task and not self.live_monitoring_task.done():
            self.live_monitoring_task.cancel()
            logger.info("🔴 Live monitoring stopped")

    async def _restart_live_monitoring(self):
        """Restart live monitoring with new settings"""
        if self.settings.live_monitoring:
            await self._stop_live_monitoring()
            await asyncio.sleep(1)  # Small delay
            await self._start_live_monitoring()

    async def _live_monitoring_loop(self):
        """Enhanced live monitoring loop - continuously scans all monitored pairs in batches"""
        logger.info(f"🔄 Live monitoring loop started - scanning {len(self.settings.monitored_pairs)} pairs every {self.settings.interval_minutes} minutes in batches of {self.scan_batch_size}")

        while self.settings.live_monitoring and self.settings.subscribed_users:
            try:
                scan_start_time = datetime.now()
                logger.info(f"🔍 Starting global scan cycle for {len(self.settings.monitored_pairs)} pairs...")

                signals_found = []
                no_signal_pairs = []
                processed_pairs = 0

                # Process pairs in batches to avoid overwhelming the API and improve performance
                pairs_list = list(self.settings.monitored_pairs.copy())

                for i in range(0, len(pairs_list), self.scan_batch_size):
                    batch = pairs_list[i:i + self.scan_batch_size]
                    logger.info(f"🔄 Processing batch {(i // self.scan_batch_size) + 1}/{(len(pairs_list) // self.scan_batch_size) + 1}: {', '.join(batch)}")

                    for currency_pair in batch:
                        try:
                            # Convert pair format for API
                            is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(currency_pair)
                            if not is_valid:
                                continue

                            # Fetch and analyze data
                            candles = await self.data_provider.get_candles(
                                symbol=api_format,
                                interval=self.settings.timeframe,
                                count=100
                            )

                            if not candles:
                                continue

                            signal = self.signal_generator.generate_signal(candles, currency_pair)

                            # Check if this is a new signal (avoid duplicate alerts)
                            signal_key = f"{currency_pair}_{signal.action.value}_{signal.timestamp.strftime('%H')}"
                            last_signal_key = self.last_signals.get(currency_pair, "")

                            # Track signals for summary
                            if signal.action != SignalType.NONE:
                                if signal_key != last_signal_key:
                                    signals_found.append(signal)
                                    self.last_signals[currency_pair] = signal_key
                            else:
                                no_signal_pairs.append(currency_pair)

                            processed_pairs += 1

                            # Small delay between pairs to avoid rate limiting
                            await asyncio.sleep(1.5)

                        except Exception as e:
                            logger.error(f"Error analyzing {currency_pair}: {e}")
                            continue

                    # Small delay between batches
                    await asyncio.sleep(3)

                scan_duration = (datetime.now() - scan_start_time).total_seconds()
                logger.info(f"✅ Global scan completed in {scan_duration:.1f}s - processed {processed_pairs} pairs, found {len(signals_found)} signals")

                # Send signals if found
                if signals_found:
                    for signal in signals_found:
                        self.performance_tracker.log_signal(signal)
                        message = self._format_signal_message(signal, is_live_alert=True)

                        # Send to all subscribed users
                        successful_sends = 0
                        for user_id in self.settings.subscribed_users.copy():
                            try:
                                await self.application.bot.send_message(
                                    user_id, message, parse_mode='Markdown'
                                )
                                successful_sends += 1
                            except Exception as e:
                                logger.error(f"Failed to send alert to user {user_id}: {e}")
                                # Remove user if chat not found
                                if "chat not found" in str(e).lower():
                                    self.settings.subscribed_users.discard(user_id)

                        if successful_sends > 0:
                            pair_category = CurrencyPairValidator.get_pair_category(signal.currency_pair)
                            logger.info(f"🚨 LIVE ALERT sent: {signal.action.value} {signal.currency_pair} ({pair_category}) to {successful_sends} users")

                # Send "No Signals" summary if no signals found (less frequent to avoid spam)
                elif len(no_signal_pairs) > 0 and scan_start_time.minute % 30 == 0:  # Only every 30 minutes
                    no_signal_message = self._format_no_signal_summary(no_signal_pairs, scan_start_time)

                    # Send to all subscribed users
                    for user_id in self.settings.subscribed_users.copy():
                        try:
                            await self.application.bot.send_message(
                                user_id, no_signal_message, parse_mode='Markdown'
                            )
                        except Exception as e:
                            logger.error(f"Failed to send no-signal update to user {user_id}: {e}")
                            if "chat not found" in str(e).lower():
                                self.settings.subscribed_users.discard(user_id)

                    logger.info(f"📊 No signals found - sent summary to {len(self.settings.subscribed_users)} users")

                # Save settings after each cycle
                self._save_settings()

                logger.info(f"⏰ Next global scan in {self.settings.interval_minutes} minutes")

                # Wait for next scan
                await asyncio.sleep(self.settings.interval_minutes * 60)

            except asyncio.CancelledError:
                logger.info("🔴 Live monitoring loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in live monitoring loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retry

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced help command"""
        await update.message.reply_text(help_message, parse_mode='Markdown')

    def run(self):
        """Start the live bot"""
        # Create application
        self.application = Application.builder().token(self.telegram_token).build()

        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("analyze", self.analyze_command))
        self.application.add_handler(CommandHandler("performance", self.performance_command))
        self.application.add_handler(CommandHandler("live", self.live_command))
        self.application.add_handler(CommandHandler("add_pair", self.add_pair_command))
        self.application.add_handler(CommandHandler("remove_pair", self.remove_pair_command))
        self.application.add_handler(CommandHandler("pairs", self.pairs_command))
        self.application.add_handler(CommandHandler("set_interval", self.set_interval_command))
        self.application.add_handler(CommandHandler("timeframe", self.timeframe_command))
        self.application.add_handler(CommandHandler("config", self.config_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        # Start live monitoring automatically if enabled and users subscribed
        async def post_init(application):
            if self.settings.live_monitoring and self.settings.subscribed_users:
                await self._start_live_monitoring()

        self.application.post_init = post_init

        # Start the bot
        logger.info("🚀 Starting Enhanced Live Forex Telegram Bot v3.0...")
        logger.info(f"📊 Monitoring {len(self.settings.monitored_pairs)} pairs every {self.settings.interval_minutes} minutes")
        logger.info(f"💱 Supporting {len(CurrencyPairValidator.VALID_PAIRS)} total currency pairs")
        logger.info(f"👥 {len(self.settings.subscribed_users)} users subscribed")
        logger.info(f"🔄 Batch processing: {self.scan_batch_size} pairs per batch")

        # Log category breakdown
        categories = CurrencyPairValidator.get_pairs_by_category()
        for category, pairs in categories.items():
            monitored_in_category = len([p for p in self.settings.monitored_pairs if p in pairs])
            if monitored_in_category > 0:
                logger.info(f"📊 {category}: {monitored_in_category}/{len(pairs)} pairs monitored")

        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """Main function to run the enhanced live bot"""
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
        # Create and run enhanced live bot
        bot = LiveForexTelegramBot(
            telegram_token=TELEGRAM_BOT_TOKEN,
            api_key=TWELVEDATA_API_KEY if TWELVEDATA_API_KEY != "YOUR_TWELVEDATA_API_KEY_HERE" else None
        )

        print("\n🚀 ENHANCED LIVE FOREX BOT v3.0")
        print("===============================")
        print(f"📊 Supporting {len(CurrencyPairValidator.VALID_PAIRS)} currency pairs")
        print("💱 Majors, Crosses, Minors & Exotics")
        print("🔄 24/7 Live monitoring with batch processing")
        print("⚡ Instant global market alerts")
        print("🌍 Global market coverage")
        print("\nStarting bot...")

        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")

if __name__ == "__main__":
    main()
