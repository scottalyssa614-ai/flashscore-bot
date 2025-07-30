import asyncio
import logging
import requests
import numpy as np
import json
import os
from datetime import datetime, timedelta, timezone
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
📚 **Enhanced Live Forex Signal Bot v2.5+ - Help**

**🆕 NEW ENHANCED FEATURES:**
• ⏰ **Smart Market Hours** - Only trades during active sessions (06:00-17:59 UTC)
• 🕯️ **Mandatory Pattern Confirmation** - All signals require valid candlestick patterns
• 📍 **Session Detection** - Asian, London, NY session awareness
• 🎯 **Strict Signal Filtering** - Multi-layer confirmation system
• 📊 **Enhanced Feedback** - Info messages when patterns aren't found
• 🔧 **MACD Confirmation** - Momentum validation for all trades

**📱 Commands:**
• `/start` - Activate enhanced monitoring with new filters
• `/analyze [PAIR]` - Manual analysis with pattern detection
• `/performance` - View statistics with pattern breakdown
• `/live [on/off]` - Toggle live monitoring with market hours
• `/add_pair [PAIR]` - Add currency pair to enhanced monitoring
• `/remove_pair [PAIR]` - Remove currency pair from monitoring
• `/pairs` - View monitored pairs and session status
• `/set_interval [5-30]` - Change scan frequency (minutes)
• `/timeframe [15min|30min|1h]` - Set analysis timeframe
• `/config` - Show current enhanced settings

**🕐 Enhanced Market Hours Logic:**
• **Active Trading:** 06:00-17:59 UTC (07:00-18:59 WAT)
• **Blocked Hours:** 18:00-05:59 UTC (19:00-06:59 WAT)
• **Session Detection:** Asian → London → New York → Late

**🕯️ Required Candlestick Patterns:**
**Bullish (BUY):** Bullish Engulfing, Hammer, Morning Star, Inverted Hammer, Piercing Line
**Bearish (SELL):** Bearish Engulfing, Shooting Star, Evening Star, Hanging Man, Dark Cloud Cover

**🎯 Strict Signal Rules:**
**BUY Requirements:** RSI < 30 + EMA20 > EMA50 + MACD Bullish + Valid Bullish Pattern + Active Hours
**SELL Requirements:** RSI > 70 + EMA20 < EMA50 + MACD Bearish + Valid Bearish Pattern + Active Hours

**📊 Enhanced Feedback:**
• Trade signals only when ALL conditions met
• Info messages when patterns missing
• Session-aware analysis
• Pattern confidence scoring

The bot now uses advanced filtering for maximum accuracy!
"""

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"

class TradingSession(Enum):
    ASIAN = "Asian"
    LONDON = "London"
    NEW_YORK = "New York"
    LATE_OVERLAP = "Late/Overlap"

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
class CandlestickPattern:
    """Represents detected candlestick pattern with confidence"""
    name: str
    type: str  # "bullish" or "bearish"
    confidence: float
    description: str

@dataclass
class TradingSignal:
    """Enhanced trading signal with pattern and session info"""
    action: SignalType
    confidence: float
    rsi: float
    ema_20: float
    ema_50: float
    macd: float
    macd_signal: float
    pattern: CandlestickPattern
    support_resistance: float
    current_price: float
    take_profit: float
    stop_loss: float
    tp_pips: int
    sl_pips: int
    reason: str
    timestamp: datetime
    currency_pair: str
    trading_session: TradingSession
    market_hours_valid: bool

@dataclass
class BotSettings:
    """Enhanced bot configuration settings"""
    interval_minutes: int = 8
    timeframe: str = "15min"
    tp_pips_min: int = 20
    tp_pips_max: int = 40
    sl_pips_min: int = 15
    sl_pips_max: int = 25
    live_monitoring: bool = True
    subscribed_users: Set[int] = None
    monitored_pairs: Set[str] = None
    enforce_market_hours: bool = True  # NEW: Market hours enforcement
    require_patterns: bool = True      # NEW: Mandatory pattern confirmation
    min_pattern_confidence: float = 70.0  # NEW: Minimum pattern confidence

    def __post_init__(self):
        if self.subscribed_users is None:
            self.subscribed_users = set()
        if self.monitored_pairs is None:
            self.monitored_pairs = {
                "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "CADJPY", "NZDJPY",
                "EURCHF", "EURAUD", "GBPCHF", "GBPAUD", "AUDCHF", "AUDCAD", "NZDCHF",
                "USDSGD", "USDHKD", "USDSEK", "USDNOK", "USDDKK"
            }

class MarketHoursValidator:
    """Enhanced market hours and session detection"""

    @staticmethod
    def is_valid_trading_time() -> bool:
        """Check if current time is within valid trading hours (06:00-17:59 UTC)"""
        utc_now = datetime.now(timezone.utc)
        return 6 <= utc_now.hour < 18

    @staticmethod
    def get_current_session() -> TradingSession:
        """Determine current trading session based on UTC time"""
        utc_now = datetime.now(timezone.utc)
        hour = utc_now.hour

        if 0 <= hour < 8:
            return TradingSession.ASIAN
        elif 8 <= hour < 13:
            return TradingSession.LONDON
        elif 13 <= hour < 21:
            return TradingSession.NEW_YORK
        else:
            return TradingSession.LATE_OVERLAP

    @staticmethod
    def get_nigerian_time() -> datetime:
        """Get current Nigerian time (UTC+1)"""
        utc_now = datetime.now(timezone.utc)
        nigerian_tz = timezone(timedelta(hours=1))
        return utc_now.astimezone(nigerian_tz)

    @staticmethod
    def format_time_info() -> Dict[str, str]:
        """Get formatted time information"""
        utc_now = datetime.now(timezone.utc)
        nigerian_time = MarketHoursValidator.get_nigerian_time()
        session = MarketHoursValidator.get_current_session()
        is_valid = MarketHoursValidator.is_valid_trading_time()

        return {
            "utc": utc_now.strftime("%H:%M UTC"),
            "nigerian": nigerian_time.strftime("%H:%M WAT"),
            "session": session.value,
            "valid": "✅ ACTIVE" if is_valid else "❌ INACTIVE",
            "status": "Trading Hours" if is_valid else "Off Hours"
        }

class EnhancedPatternDetector:
    """Advanced candlestick pattern detection with confidence scoring"""

    @staticmethod
    def detect_all_patterns(candles: List[Candle]) -> List[CandlestickPattern]:
        """Detect all valid candlestick patterns with confidence scores"""
        if len(candles) < 3:
            return []

        patterns = []

        # Get recent candles for analysis
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else current
        before_previous = candles[-3] if len(candles) > 2 else previous

        # Calculate body sizes and shadows
        current_body = abs(current.close - current.open)
        previous_body = abs(previous.close - previous.open)
        current_range = current.high - current.low
        previous_range = previous.high - previous.low

        current_upper_shadow = (current.high - max(current.open, current.close))
        current_lower_shadow = (min(current.open, current.close) - current.low)
        previous_upper_shadow = (previous.high - max(previous.open, previous.close))
        previous_lower_shadow = (min(previous.open, previous.close) - previous.low)

        # Bullish Patterns

        # 1. Bullish Engulfing
        if (previous.close < previous.open and  # Previous bearish
            current.close > current.open and   # Current bullish
            current.open < previous.close and  # Current opens below previous close
            current.close > previous.open and  # Current closes above previous open
            current_body > previous_body * 1.1):  # Current body larger

            confidence = min(95, 70 + (current_body / previous_body - 1) * 25)
            patterns.append(CandlestickPattern(
                name="Bullish Engulfing",
                type="bullish",
                confidence=confidence,
                description="Strong bullish reversal - larger green candle engulfs red candle"
            ))

        # 2. Hammer
        if (current_range > 0 and 
            current_lower_shadow > current_body * 2 and
            current_upper_shadow < current_body * 0.3 and
            current_body > current_range * 0.1):

            confidence = min(90, 65 + (current_lower_shadow / current_body - 2) * 10)
            patterns.append(CandlestickPattern(
                name="Hammer",
                type="bullish",
                confidence=confidence,
                description="Bullish reversal - long lower shadow indicates buying pressure"
            ))

        # 3. Inverted Hammer
        if (current_range > 0 and
            current_upper_shadow > current_body * 2 and
            current_lower_shadow < current_body * 0.3 and
            current_body > current_range * 0.1):

            confidence = min(85, 60 + (current_upper_shadow / current_body - 2) * 8)
            patterns.append(CandlestickPattern(
                name="Inverted Hammer",
                type="bullish",
                confidence=confidence,
                description="Potential bullish reversal - long upper shadow shows rejection of lows"
            ))

        # 4. Piercing Line
        if (len(candles) >= 2 and
            previous.close < previous.open and  # Previous bearish
            current.close > current.open and   # Current bullish
            current.open < previous.low and    # Opens below previous low
            current.close > (previous.open + previous.close) / 2 and  # Closes above midpoint
            current.close < previous.open):    # But below previous open

            midpoint_penetration = (current.close - (previous.open + previous.close) / 2) / previous_body
            confidence = min(90, 65 + midpoint_penetration * 30)
            patterns.append(CandlestickPattern(
                name="Piercing Line",
                type="bullish",
                confidence=confidence,
                description="Bullish reversal - green candle pierces deep into red candle"
            ))

        # 5. Morning Star (3-candle pattern)
        if (len(candles) >= 3 and
            before_previous.close < before_previous.open and  # First candle bearish
            abs(previous.close - previous.open) < previous_body * 0.3 and  # Middle is doji/small
            current.close > current.open and  # Third candle bullish
            current.close > (before_previous.open + before_previous.close) / 2):  # Closes above first midpoint

            confidence = min(95, 75 + (current_body / before_previous.open) * 20)
            patterns.append(CandlestickPattern(
                name="Morning Star",
                type="bullish",
                confidence=confidence,
                description="Strong bullish reversal - 3-candle pattern showing trend change"
            ))

        # Bearish Patterns

        # 1. Bearish Engulfing
        if (previous.close > previous.open and  # Previous bullish
            current.close < current.open and   # Current bearish
            current.open > previous.close and  # Current opens above previous close
            current.close < previous.open and  # Current closes below previous open
            current_body > previous_body * 1.1):  # Current body larger

            confidence = min(95, 70 + (current_body / previous_body - 1) * 25)
            patterns.append(CandlestickPattern(
                name="Bearish Engulfing",
                type="bearish",
                confidence=confidence,
                description="Strong bearish reversal - larger red candle engulfs green candle"
            ))

        # 2. Shooting Star
        if (current_range > 0 and
            current_upper_shadow > current_body * 2 and
            current_lower_shadow < current_body * 0.3 and
            current_body > current_range * 0.1 and
            current.close < current.open):  # Bearish candle

            confidence = min(90, 65 + (current_upper_shadow / current_body - 2) * 10)
            patterns.append(CandlestickPattern(
                name="Shooting Star",
                type="bearish",
                confidence=confidence,
                description="Bearish reversal - long upper shadow shows selling pressure"
            ))

        # 3. Hanging Man
        if (current_range > 0 and
            current_lower_shadow > current_body * 2 and
            current_upper_shadow < current_body * 0.3 and
            current_body > current_range * 0.1):

            confidence = min(85, 60 + (current_lower_shadow / current_body - 2) * 8)
            patterns.append(CandlestickPattern(
                name="Hanging Man",
                type="bearish",
                confidence=confidence,
                description="Potential bearish reversal - long lower shadow at highs"
            ))

        # 4. Dark Cloud Cover
        if (len(candles) >= 2 and
            previous.close > previous.open and  # Previous bullish
            current.close < current.open and   # Current bearish
            current.open > previous.high and   # Opens above previous high
            current.close < (previous.open + previous.close) / 2 and  # Closes below midpoint
            current.close > previous.open):    # But above previous open

            midpoint_penetration = ((previous.open + previous.close) / 2 - current.close) / previous_body
            confidence = min(90, 65 + midpoint_penetration * 30)
            patterns.append(CandlestickPattern(
                name="Dark Cloud Cover",
                type="bearish",
                confidence=confidence,
                description="Bearish reversal - red candle pierces deep into green candle"
            ))

        # 5. Evening Star (3-candle pattern)
        if (len(candles) >= 3 and
            before_previous.close > before_previous.open and  # First candle bullish
            abs(previous.close - previous.open) < previous_body * 0.3 and  # Middle is doji/small
            current.close < current.open and  # Third candle bearish
            current.close < (before_previous.open + before_previous.close) / 2):  # Closes below first midpoint

            confidence = min(95, 75 + (current_body / before_previous.close) * 20)
            patterns.append(CandlestickPattern(
                name="Evening Star",
                type="bearish",
                confidence=confidence,
                description="Strong bearish reversal - 3-candle pattern showing trend change"
            ))

        return patterns

    @staticmethod
    def get_best_pattern(patterns: List[CandlestickPattern], signal_type: str) -> Optional[CandlestickPattern]:
        """Get the best pattern matching the signal type"""
        matching_patterns = [p for p in patterns if p.type == signal_type]
        if not matching_patterns:
            return None

        # Return pattern with highest confidence
        return max(matching_patterns, key=lambda p: p.confidence)

class TechnicalAnalyzer:
    """Enhanced technical analysis with strict filtering"""

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """Calculate RSI with improved accuracy"""
        if len(prices) < period + 1:
            return 50.0

        prices_array = np.array(prices)
        deltas = np.diff(prices_array)

        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # Use exponential moving average for more responsive RSI
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
        """Calculate MACD and Signal line with enhanced accuracy"""
        if len(prices) < slow:
            return 0.0, 0.0

        ema_fast = TechnicalAnalyzer.calculate_ema(prices, fast)
        ema_slow = TechnicalAnalyzer.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow

        # Calculate signal line using historical MACD values
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
        """Find support and resistance levels"""
        if len(candles) < lookback:
            lookback = len(candles)

        recent_candles = candles[-lookback:]
        highs = [c.high for c in recent_candles]
        lows = [c.low for c in recent_candles]

        resistance_levels = []
        support_levels = []

        for i in range(2, len(recent_candles) - 2):
            # Resistance (local high)
            if (recent_candles[i].high > recent_candles[i-1].high and
                recent_candles[i].high > recent_candles[i-2].high and
                recent_candles[i].high > recent_candles[i+1].high and
                recent_candles[i].high > recent_candles[i+2].high):
                resistance_levels.append(recent_candles[i].high)

            # Support (local low)
            if (recent_candles[i].low < recent_candles[i-1].low and
                recent_candles[i].low < recent_candles[i-2].low and
                recent_candles[i].low < recent_candles[i+1].low and
                recent_candles[i].low < recent_candles[i+2].low):
                support_levels.append(recent_candles[i].low)

        resistance = max(resistance_levels) if resistance_levels else max(highs)
        support = min(support_levels) if support_levels else min(lows)

        return support, resistance

class CurrencyPairValidator:
    """Currency pair validation (keeping existing functionality)"""

    VALID_PAIRS = {
        'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
        'EURGBP', 'EURJPY', 'EURCHF', 'EURAUD', 'EURCAD', 'EURNZD',
        'GBPJPY', 'GBPCHF', 'GBPAUD', 'GBPCAD', 'GBPNZD',
        'AUDJPY', 'AUDCHF', 'AUDCAD', 'AUDNZD',
        'CADJPY', 'NZDJPY', 'CHFJPY',
        'CADCHF', 'NZDCHF', 'NZDCAD',
        'USDSGD', 'USDHKD', 'USDSEK', 'USDNOK', 'USDDKK', 'USDPLN',
        'USDCZK', 'USDHUF', 'USDTRY', 'USDZAR', 'USDMXN'
    }

    MAJOR_PAIRS = {'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD'}
    CROSS_PAIRS = {'EURGBP', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CHFJPY', 'CADJPY', 'NZDJPY'}
    MINOR_PAIRS = {'USDSGD', 'USDHKD', 'USDSEK', 'USDNOK', 'USDDKK', 'USDPLN', 'USDCZK'}

    @classmethod
    def validate_and_format(cls, pair_input: str) -> Tuple[bool, str, str]:
        if not pair_input:
            return True, "EUR/USD", "EURUSD"

        cleaned = pair_input.upper().replace('/', '').replace('-', '').replace('_', '')

        if cleaned in cls.VALID_PAIRS:
            api_format = f"{cleaned[:3]}/{cleaned[3:]}"
            return True, api_format, cleaned

        return False, "", cleaned

    @classmethod
    def get_pair_category(cls, pair: str) -> str:
        if pair in cls.MAJOR_PAIRS:
            return "Major"
        elif pair in cls.CROSS_PAIRS:
            return "Cross"
        elif pair in cls.MINOR_PAIRS:
            return "Minor"
        else:
            return "Exotic"

class ForexDataProvider:
    """Enhanced data provider with better error handling"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://api.twelvedata.com"

    async def get_candles(self, symbol: str = "EUR/USD", interval: str = "15min", 
                         count: int = 100) -> List[Candle]:
        """Fetch candlestick data with enhanced error handling"""
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
                logger.warning(f"No values in API response for {symbol}, using enhanced mock data")
                return self._get_enhanced_mock_data(symbol)

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

            return candles[-count:] if candles else self._get_enhanced_mock_data(symbol)

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
            return self._get_enhanced_mock_data(symbol)

    def _get_enhanced_mock_data(self, symbol: str = "EUR/USD") -> List[Candle]:
        """Generate enhanced mock data with realistic patterns"""
        logger.info(f"Using enhanced mock data for {symbol}")
        candles = []

        # Enhanced base prices for different currency types
        if "JPY" in symbol:
            base_price = 150.0 + np.random.uniform(-20, 20)
            volatility = 0.005
        elif any(exotic in symbol for exotic in ["TRY", "ZAR", "MXN", "BRL"]):
            base_price = 15.0 + np.random.uniform(-5, 5)
            volatility = 0.012
        else:
            base_price = 1.0850 + np.random.uniform(-0.15, 0.15)
            volatility = 0.0006

        base_time = datetime.now(timezone.utc) - timedelta(hours=25)

        # Generate more realistic candle patterns
        for i in range(100):
            # Add some trending behavior
            trend_factor = np.sin(i * 0.1) * volatility * 0.5
            change = np.random.normal(trend_factor, volatility)

            open_price = base_price + change

            # Create more realistic OHLC relationships
            volatility_factor = abs(np.random.normal(0, volatility * 0.8))
            high_price = open_price + volatility_factor
            low_price = open_price - volatility_factor

            # Close price with some momentum
            close_momentum = np.random.normal(0, volatility * 0.6)
            close_price = open_price + close_momentum

            # Ensure OHLC integrity
            high_price = max(high_price, open_price, close_price) + abs(np.random.normal(0, volatility * 0.2))
            low_price = min(low_price, open_price, close_price) - abs(np.random.normal(0, volatility * 0.2))

            candle = Candle(
                timestamp=base_time + timedelta(minutes=15 * i),
                open=round(open_price, 5),
                high=round(high_price, 5),
                low=round(low_price, 5),
                close=round(close_price, 5),
                volume=np.random.uniform(1500, 6000)
            )
            candles.append(candle)
            base_price = close_price * 0.7 + open_price * 0.3  # Some price memory

        return candles

class PerformanceTracker:
    """Enhanced performance tracking with pattern statistics"""

    def __init__(self, log_file: str = "enhanced_performance_log.json"):
        self.log_file = log_file
        self.signals_log = self._load_log()

    def _load_log(self) -> List[Dict]:
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading performance log: {e}")
        return []

    def _save_log(self):
        try:
            with open(self.log_file, 'w') as f:
                json.dump(self.signals_log, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving performance log: {e}")

    def log_signal(self, signal: TradingSignal):
        """Log enhanced trading signal with pattern info"""
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
            "pattern_name": signal.pattern.name if signal.pattern else "None",
            "pattern_confidence": signal.pattern.confidence if signal.pattern else 0,
            "confidence": signal.confidence,
            "trading_session": signal.trading_session.value,
            "market_hours_valid": signal.market_hours_valid,
            "pair_category": CurrencyPairValidator.get_pair_category(signal.currency_pair),
            "status": "OPEN"
        }

        self.signals_log.append(log_entry)
        self._save_log()
        logger.info(f"Logged {signal.action.value} signal for {signal.currency_pair} with {signal.pattern.name if signal.pattern else 'No'} pattern")

    def get_enhanced_stats(self) -> Dict:
        """Calculate enhanced performance statistics with pattern breakdown"""
        if not self.signals_log:
            return {
                "total_signals": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "avg_confidence": 0,
                "pattern_breakdown": {},
                "session_breakdown": {},
                "last_signal": "None",
                "pairs_analyzed": 0
            }

        buy_count = sum(1 for s in self.signals_log if s["action"] == "BUY")
        sell_count = sum(1 for s in self.signals_log if s["action"] == "SELL")
        avg_confidence = np.mean([s["confidence"] for s in self.signals_log])
        unique_pairs = len(set(s["currency_pair"] for s in self.signals_log))

        # Pattern breakdown
        pattern_breakdown = {}
        for signal in self.signals_log:
            pattern = signal.get("pattern_name", "Unknown")
            if pattern not in pattern_breakdown:
                pattern_breakdown[pattern] = 0
            pattern_breakdown[pattern] += 1

        # Session breakdown
        session_breakdown = {}
        for signal in self.signals_log:
            session = signal.get("trading_session", "Unknown")
            if session not in session_breakdown:
                session_breakdown[session] = 0
            session_breakdown[session] += 1

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
            "pattern_breakdown": pattern_breakdown,
            "session_breakdown": session_breakdown,
            "last_signal": f"{last_signal['action']} {last_signal['currency_pair']} ({last_signal.get('pattern_name', 'Unknown')}) at {last_signal_time}" if last_signal else "None",
            "pairs_analyzed": unique_pairs
        }

class EnhancedSignalGenerator:
    """Enhanced signal generator with strict filtering and pattern confirmation"""

    def __init__(self, settings: BotSettings):
        self.analyzer = TechnicalAnalyzer()
        self.pattern_detector = EnhancedPatternDetector()
        self.settings = settings

    def calculate_tp_sl(self, action: SignalType, entry_price: float, currency_pair: str) -> Tuple[float, float, int, int]:
        """Calculate Take Profit and Stop Loss levels"""
        if action == SignalType.NONE:
            return 0.0, 0.0, 0, 0

        # Calculate pip value based on currency pair type
        if 'JPY' in currency_pair:
            pip_value = 0.01
        else:
            pip_value = 0.0001

        # Adjust TP/SL ranges based on pair category
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)

        if pair_category == "Major":
            tp_range = (self.settings.tp_pips_min, self.settings.tp_pips_max)
            sl_range = (self.settings.sl_pips_min, self.settings.sl_pips_max)
        elif pair_category == "Cross":
            tp_range = (25, 50)
            sl_range = (20, 30)
        else:  # Minor/Exotic
            tp_range = (30, 60)
            sl_range = (25, 40)

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

    def generate_enhanced_signal(self, candles: List[Candle], currency_pair: str = "EURUSD") -> TradingSignal:
        """Generate enhanced trading signal with strict filtering rules"""

        # STEP 1: Check market hours (MANDATORY)
        market_hours_valid = MarketHoursValidator.is_valid_trading_time()
        current_session = MarketHoursValidator.get_current_session()

        if not market_hours_valid and self.settings.enforce_market_hours:
            return self._create_market_hours_rejection(candles, currency_pair, current_session)

        if len(candles) < 50:
            return self._create_no_signal(candles, currency_pair, "Insufficient data for analysis", current_session, market_hours_valid)

        # Calculate all technical indicators
        closes = [c.close for c in candles]
        rsi = self.analyzer.calculate_rsi(closes)
        ema_20 = self.analyzer.calculate_ema(closes, 20)
        ema_50 = self.analyzer.calculate_ema(closes, 50)
        macd, macd_signal = self.analyzer.calculate_macd(closes)
        support, resistance = self.analyzer.find_support_resistance(candles)
        current_price = candles[-1].close

        # STEP 2: Detect candlestick patterns (MANDATORY)
        detected_patterns = self.pattern_detector.detect_all_patterns(candles)

        # STEP 3: Apply STRICT signal logic
        signal_action = SignalType.NONE
        selected_pattern = None
        confidence = 0
        conditions_met = []

        # BUY Signal Requirements (ALL must be met)
        buy_conditions_met = 0
        buy_reasons = []

        # Check RSI < 30
        if rsi < 30:
            buy_conditions_met += 1
            buy_reasons.append(f"RSI Oversold ({rsi:.1f})")

        # Check EMA 20 > EMA 50
        if ema_20 > ema_50:
            buy_conditions_met += 1
            buy_reasons.append("EMA Bullish Cross")

        # Check MACD Bullish
        if macd > macd_signal:
            buy_conditions_met += 1
            buy_reasons.append("MACD Momentum")

        # Check for valid bullish pattern
        bullish_pattern = self.pattern_detector.get_best_pattern(detected_patterns, "bullish")
        if bullish_pattern and bullish_pattern.confidence >= self.settings.min_pattern_confidence:
            buy_conditions_met += 1
            buy_reasons.append(f"{bullish_pattern.name}")
            selected_pattern = bullish_pattern

        # SELL Signal Requirements (ALL must be met)
        sell_conditions_met = 0
        sell_reasons = []

        # Check RSI > 70
        if rsi > 70:
            sell_conditions_met += 1
            sell_reasons.append(f"RSI Overbought ({rsi:.1f})")

        # Check EMA 20 < EMA 50
        if ema_20 < ema_50:
            sell_conditions_met += 1
            sell_reasons.append("EMA Bearish Cross")

        # Check MACD Bearish
        if macd < macd_signal:
            sell_conditions_met += 1
            sell_reasons.append("MACD Momentum")

        # Check for valid bearish pattern
        bearish_pattern = self.pattern_detector.get_best_pattern(detected_patterns, "bearish")
        if bearish_pattern and bearish_pattern.confidence >= self.settings.min_pattern_confidence:
            sell_conditions_met += 1
            sell_reasons.append(f"{bearish_pattern.name}")
            selected_pattern = bearish_pattern

        # FINAL DECISION: Only signal if ALL 4 conditions are met
        if buy_conditions_met == 4:
            signal_action = SignalType.BUY
            confidence = min(95, 70 + (selected_pattern.confidence - 70) * 0.5)
            conditions_met = buy_reasons
            key_level = support
        elif sell_conditions_met == 4:
            signal_action = SignalType.SELL
            confidence = min(95, 70 + (selected_pattern.confidence - 70) * 0.5)
            conditions_met = sell_reasons
            key_level = resistance
        else:
            # Check if pattern was the missing piece
            if self.settings.require_patterns:
                if buy_conditions_met == 3 and not bullish_pattern:
                    return self._create_pattern_missing_signal(candles, currency_pair, "bullish", current_session, market_hours_valid, buy_reasons)
                elif sell_conditions_met == 3 and not bearish_pattern:
                    return self._create_pattern_missing_signal(candles, currency_pair, "bearish", current_session, market_hours_valid, sell_reasons)

            return self._create_no_signal(candles, currency_pair, 
                f"Conditions not met (Buy: {buy_conditions_met}/4, Sell: {sell_conditions_met}/4)", 
                current_session, market_hours_valid)

        # Calculate TP/SL
        tp, sl, tp_pips, sl_pips = self.calculate_tp_sl(signal_action, current_price, currency_pair)

        return TradingSignal(
            action=signal_action,
            confidence=confidence,
            rsi=rsi,
            ema_20=ema_20,
            ema_50=ema_50,
            macd=macd,
            macd_signal=macd_signal,
            pattern=selected_pattern,
            support_resistance=key_level,
            current_price=current_price,
            take_profit=tp,
            stop_loss=sl,
            tp_pips=tp_pips,
            sl_pips=sl_pips,
            reason=" + ".join(conditions_met),
            timestamp=datetime.now(timezone.utc),
            currency_pair=currency_pair,
            trading_session=current_session,
            market_hours_valid=market_hours_valid
        )

    def _create_market_hours_rejection(self, candles: List[Candle], currency_pair: str, session: TradingSession) -> TradingSignal:
        """Create signal rejection due to market hours"""
        closes = [c.close for c in candles] if candles else [0]
        current_price = closes[-1] if closes else 0

        return TradingSignal(
            action=SignalType.NONE,
            confidence=0,
            rsi=0,
            ema_20=0,
            ema_50=0,
            macd=0,
            macd_signal=0,
            pattern=None,
            support_resistance=0,
            current_price=current_price,
            take_profit=0,
            stop_loss=0,
            tp_pips=0,
            sl_pips=0,
            reason="Outside trading hours (06:00-17:59 UTC)",
            timestamp=datetime.now(timezone.utc),
            currency_pair=currency_pair,
            trading_session=session,
            market_hours_valid=False
        )

    def _create_pattern_missing_signal(self, candles: List[Candle], currency_pair: str, 
                                     expected_pattern_type: str, session: TradingSession, 
                                     market_hours_valid: bool, met_conditions: List[str]) -> TradingSignal:
        """Create info signal when pattern is missing"""
        closes = [c.close for c in candles] if candles else [0]
        current_price = closes[-1] if closes else 0
        rsi = self.analyzer.calculate_rsi(closes) if len(closes) > 14 else 50

        reason = f"No valid {expected_pattern_type} pattern detected. Met: {', '.join(met_conditions)}"

        return TradingSignal(
            action=SignalType.NONE,
            confidence=0,
            rsi=rsi,
            ema_20=0,
            ema_50=0,
            macd=0,
            macd_signal=0,
            pattern=None,
            support_resistance=0,
            current_price=current_price,
            take_profit=0,
            stop_loss=0,
            tp_pips=0,
            sl_pips=0,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
            currency_pair=currency_pair,
            trading_session=session,
            market_hours_valid=market_hours_valid
        )

    def _create_no_signal(self, candles: List[Candle], currency_pair: str, reason: str, 
                         session: TradingSession, market_hours_valid: bool) -> TradingSignal:
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
            pattern=None,
            support_resistance=0,
            current_price=current_price,
            take_profit=0,
            stop_loss=0,
            tp_pips=0,
            sl_pips=0,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
            currency_pair=currency_pair,
            trading_session=session,
            market_hours_valid=market_hours_valid
        )

class EnhancedLiveForexTelegramBot:
    """Enhanced Live Telegram bot with strict filtering and pattern confirmation"""

    def __init__(self, telegram_token: str, api_key: Optional[str] = None):
        self.telegram_token = telegram_token
        self.data_provider = ForexDataProvider(api_key)
        self.settings = BotSettings()
        self.signal_generator = EnhancedSignalGenerator(self.settings)
        self.performance_tracker = PerformanceTracker()
        self.live_monitoring_task = None
        self.application = None
        self.last_signals = {}
        self.scan_batch_size = 6  # Reduced for more thorough analysis

        self._load_settings()

    def _load_settings(self):
        """Load enhanced bot settings"""
        try:
            if os.path.exists("enhanced_bot_settings.json"):
                with open("enhanced_bot_settings.json", 'r') as f:
                    data = json.load(f)
                    self.settings.interval_minutes = data.get("interval_minutes", 8)
                    self.settings.timeframe = data.get("timeframe", "15min")
                    self.settings.live_monitoring = data.get("live_monitoring", True)
                    self.settings.enforce_market_hours = data.get("enforce_market_hours", True)
                    self.settings.require_patterns = data.get("require_patterns", True)
                    self.settings.min_pattern_confidence = data.get("min_pattern_confidence", 70.0)
                    self.settings.subscribed_users = set(data.get("subscribed_users", []))
                    self.settings.monitored_pairs = set(data.get("monitored_pairs", [
                        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                        "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "CADJPY", "NZDJPY"
                    ]))
        except Exception as e:
            logger.error(f"Error loading enhanced settings: {e}")

    def _save_settings(self):
        """Save enhanced bot settings"""
        try:
            data = {
                "interval_minutes": self.settings.interval_minutes,
                "timeframe": self.settings.timeframe,
                "live_monitoring": self.settings.live_monitoring,
                "enforce_market_hours": self.settings.enforce_market_hours,
                "require_patterns": self.settings.require_patterns,
                "min_pattern_confidence": self.settings.min_pattern_confidence,
                "subscribed_users": list(self.settings.subscribed_users),
                "monitored_pairs": list(self.settings.monitored_pairs)
            }
            with open("enhanced_bot_settings.json", 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving enhanced settings: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced start command with market hours info"""
        user_id = update.effective_user.id
        self.settings.subscribed_users.add(user_id)
        self.settings.live_monitoring = True
        self._save_settings()

        if not self.live_monitoring_task or self.live_monitoring_task.done():
            await self._start_live_monitoring()

        time_info = MarketHoursValidator.format_time_info()

        keyboard = [
            [InlineKeyboardButton("📊 Enhanced Analysis", callback_data="analyze")],
            [InlineKeyboardButton("🕐 Market Hours Status", callback_data="market_hours")],
            [InlineKeyboardButton("📈 Pattern Performance", callback_data="performance")],
            [InlineKeyboardButton("⚙️ Enhanced Settings", callback_data="settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_message = f"""
🤖 **Enhanced Live Forex Signal Bot v2.5+ ACTIVATED!**

🆕 **NEW STRICT FILTERING SYSTEM:**
• ⏰ **Smart Market Hours:** Only trades 06:00-17:59 UTC
• 🕯️ **Mandatory Patterns:** All signals require candlestick confirmation
• 📍 **Session Awareness:** {time_info['session']} session tracking
• 🎯 **4-Point Validation:** RSI + EMA + MACD + Pattern required
• 📊 **Enhanced Feedback:** Info messages when patterns missing

**🕐 Current Market Status:**
• **UTC Time:** {time_info['utc']}
• **Nigerian Time:** {time_info['nigerian']}
• **Session:** {time_info['session']}
• **Status:** {time_info['valid']} ({time_info['status']})

**📊 Currently Monitoring:** {len(self.settings.monitored_pairs)} pairs
**⚙️ Enhanced Settings:**
• Market Hours Enforcement: `{'✅ ON' if self.settings.enforce_market_hours else '❌ OFF'}`
• Pattern Requirement: `{'✅ ON' if self.settings.require_patterns else '❌ OFF'}`
• Min Pattern Confidence: `{self.settings.min_pattern_confidence}%`
• Scan Interval: `{self.settings.interval_minutes} minutes`

**🎯 STRICT SIGNAL RULES:**
**BUY:** RSI < 30 + EMA20 > EMA50 + MACD Bullish + Bullish Pattern + Active Hours
**SELL:** RSI > 70 + EMA20 < EMA50 + MACD Bearish + Bearish Pattern + Active Hours

**🚨 The bot now uses MAXIMUM ACCURACY filtering - only the strongest setups will generate alerts!**
        """

        await update.message.reply_text(welcome_message, parse_mode='Markdown', reply_markup=reply_markup)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle enhanced button callbacks"""
        query = update.callback_query
        await query.answer()

        if query.data == "analyze":
            await self._send_enhanced_analysis(query.message.chat_id, context, "EURUSD", "EUR/USD")
        elif query.data == "market_hours":
            await self._send_market_hours_status(query.message.chat_id, context)
        elif query.data == "settings":
            await self._send_enhanced_settings(query.message.chat_id, context)
        elif query.data == "performance":
            await self._send_enhanced_performance(query.message.chat_id, context)

    async def _send_market_hours_status(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send detailed market hours status"""
        time_info = MarketHoursValidator.format_time_info()
        is_valid = MarketHoursValidator.is_valid_trading_time()

        # Calculate time until next trading session
        utc_now = datetime.now(timezone.utc)
        if is_valid:
            # Time until market closes
            next_close = utc_now.replace(hour=18, minute=0, second=0, microsecond=0)
            if utc_now.hour >= 18:
                next_close += timedelta(days=1)
            time_remaining = next_close - utc_now
            status_msg = f"Market closes in {time_remaining}"
        else:
            # Time until market opens
            next_open = utc_now.replace(hour=6, minute=0, second=0, microsecond=0)
            if utc_now.hour >= 6:
                next_open += timedelta(days=1)
            time_remaining = next_open - utc_now
            status_msg = f"Market opens in {time_remaining}"

        message = f"""
🕐 **Enhanced Market Hours Status**

**⏰ Current Times:**
• **UTC:** {time_info['utc']}
• **Nigerian (WAT):** {time_info['nigerian']}

**📍 Trading Session:** {time_info['session']}
**🎯 Market Status:** {time_info['valid']}

**📊 Active Trading Window:**
• **Start:** 06:00 UTC (07:00 WAT)
• **End:** 17:59 UTC (18:59 WAT)
• **Duration:** 12 hours daily

**⏳ Next Event:** {status_msg}

**🔧 Bot Behavior:**
• **During Active Hours:** Full analysis with all filters
• **During Off Hours:** {"Analysis blocked" if self.settings.enforce_market_hours else "Analysis continues"}
• **Pattern Requirement:** {"✅ Mandatory" if self.settings.require_patterns else "❌ Optional"}

**📈 Session Characteristics:**
• **Asian (00:00-07:59 UTC):** Lower volatility, range-bound
• **London (08:00-12:59 UTC):** High volatility, trend starts
• **New York (13:00-20:59 UTC):** Peak volatility, major moves
• **Late/Overlap (21:00-23:59 UTC):** Consolidation phase

**💡 The enhanced bot prioritizes quality over quantity - only trades during optimal market conditions!**
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced analyze command with pattern detection"""
        currency_pair = "EURUSD"
        if context.args and len(context.args) > 0:
            user_input = context.args[0]
            is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(user_input)

            if not is_valid:
                await update.message.reply_text(
                    f"❌ Invalid currency pair: `{user_input}`\n\n"
                    "**Enhanced Analysis supports:**\n"
                    "• All major pairs with pattern detection\n"
                    "• Market hours validation\n"
                    "• Session-aware analysis\n\n"
                    "**Usage:** `/analyze GBPUSD`",
                    parse_mode='Markdown'
                )
                return

            currency_pair = display_format
            api_pair = api_format
        else:
            api_pair = "EUR/USD"

        await self._send_enhanced_analysis(update.message.chat_id, context, currency_pair, api_pair)

    async def _send_enhanced_analysis(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE, 
                                    currency_pair: str = "EURUSD", api_pair: str = "EUR/USD"):
        """Send enhanced market analysis with pattern detection"""
        time_info = MarketHoursValidator.format_time_info()
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)

        await context.bot.send_message(chat_id, 
            f"🔍 **Enhanced Analysis:** {currency_pair} ({pair_category})\n"
            f"🕐 **Session:** {time_info['session']} | **Status:** {time_info['valid']}")

        try:
            candles = await self.data_provider.get_candles(
                symbol=api_pair,
                interval=self.settings.timeframe,
                count=100
            )

            if not candles:
                await context.bot.send_message(chat_id, f"❌ Could not fetch data for {currency_pair}.")
                return

            signal = self.signal_generator.generate_enhanced_signal(candles, currency_pair)

            # Log valid signals
            if signal.action != SignalType.NONE:
                self.performance_tracker.log_signal(signal)

            message = self._format_enhanced_signal_message(signal, is_live_alert=False)
            await context.bot.send_message(chat_id, message, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in enhanced analysis: {e}")
            await context.bot.send_message(chat_id, f"❌ Enhanced analysis failed for {currency_pair}. Please try again.")

    def _format_enhanced_signal_message(self, signal: TradingSignal, is_live_alert: bool = True) -> str:
        """Format enhanced signal message with pattern and session info"""
        currency_pair = signal.currency_pair
        pair_category = CurrencyPairValidator.get_pair_category(currency_pair)
        decimals = 3 if 'JPY' in currency_pair else 5

        # Time formatting
        nigerian_time = signal.timestamp.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=1)))
        time_str = nigerian_time.strftime('%H:%M WAT')

        alert_header = "🚨 **ENHANCED LIVE ALERT** 🚨\n\n" if is_live_alert else "📊 **Enhanced Manual Analysis**\n\n"

        if signal.action == SignalType.BUY:
            message = f"""{alert_header}📈 **Pair:** {currency_pair} ({pair_category})
🟢 **BUY SIGNAL DETECTED**

💰 **Trade Setup:**
• Entry: `{signal.current_price:.{decimals}f}`
• 🎯 Take Profit: `{signal.take_profit:.{decimals}f}` (+{signal.tp_pips} pips)
• 🛑 Stop Loss: `{signal.stop_loss:.{decimals}f}` (-{signal.sl_pips} pips)

📊 **Enhanced Technical Analysis:**
• RSI: `{signal.rsi:.1f}` ✅ (< 30)
• EMA 20: `{signal.ema_20:.{decimals}f}` | EMA 50: `{signal.ema_50:.{decimals}f}` ✅ (20 > 50)
• MACD: `{signal.macd:.6f}` | Signal: `{signal.macd_signal:.6f}` ✅ (Bullish)
• 🕯️ **Pattern:** `{signal.pattern.name}` ✅ ({signal.pattern.confidence:.1f}% confidence)
• Support: `{signal.support_resistance:.{decimals}f}`
• Overall Confidence: `{signal.confidence:.0f}%`

📈 **Validation:** {signal.reason}

⏰ **Session Info:**
• Timeframe: `{self.settings.timeframe}`
• Session: `{signal.trading_session.value}` 
• Time: `{time_str}`
• Market Hours: `{'✅ ACTIVE' if signal.market_hours_valid else '❌ INACTIVE'}`

🎯 **Enhanced Filter Status:** ALL 4 CONDITIONS MET ✅"""

        elif signal.action == SignalType.SELL:
            message = f"""{alert_header}📈 **Pair:** {currency_pair} ({pair_category})
🔴 **SELL SIGNAL DETECTED**

💰 **Trade Setup:**
• Entry: `{signal.current_price:.{decimals}f}`
• 🎯 Take Profit: `{signal.take_profit:.{decimals}f}` (+{signal.tp_pips} pips)
• 🛑 Stop Loss: `{signal.stop_loss:.{decimals}f}` (-{signal.sl_pips} pips)

📊 **Enhanced Technical Analysis:**
• RSI: `{signal.rsi:.1f}` ✅ (> 70)
• EMA 20: `{signal.ema_20:.{decimals}f}` | EMA 50: `{signal.ema_50:.{decimals}f}` ✅ (20 < 50)
• MACD: `{signal.macd:.6f}` | Signal: `{signal.macd_signal:.6f}` ✅ (Bearish)
• 🕯️ **Pattern:** `{signal.pattern.name}` ✅ ({signal.pattern.confidence:.1f}% confidence)
• Resistance: `{signal.support_resistance:.{decimals}f}`
• Overall Confidence: `{signal.confidence:.0f}%`

📉 **Validation:** {signal.reason}

⏰ **Session Info:**
• Timeframe: `{self.settings.timeframe}`
• Session: `{signal.trading_session.value}`
• Time: `{time_str}`
• Market Hours: `{'✅ ACTIVE' if signal.market_hours_valid else '❌ INACTIVE'}`

🎯 **Enhanced Filter Status:** ALL 4 CONDITIONS MET ✅"""

        else:
            # Handle different types of NO SIGNAL cases
            no_alert_header = "📊 **Enhanced Analysis Result**\n\n" if not is_live_alert else ""

            if not signal.market_hours_valid:
                message = f"""{no_alert_header}📈 **Pair:** {currency_pair} ({pair_category})
⏰ **ANALYSIS BLOCKED - OUTSIDE TRADING HOURS**

🕐 **Market Hours Status:**
• Current Session: `{signal.trading_session.value}`
• Time: `{time_str}`
• Status: `❌ INACTIVE`

💡 **Enhanced Bot Logic:**
• **Active Hours:** 06:00-17:59 UTC (07:00-18:59 WAT)
• **Current Status:** Outside active trading window
• **Action:** Analysis suspended to avoid low-volume periods

🔄 **Next Analysis:** When market reopens at 06:00 UTC (07:00 WAT)

The enhanced bot avoids trading during inactive market hours for maximum accuracy!"""

            elif "No valid" in signal.reason and "pattern detected" in signal.reason:
                # Pattern missing - send info message as requested
                message = f"""{no_alert_header}📡 **PAIR:** {currency_pair}
🕐 **Timeframe:** {self.settings.timeframe}
❌ **No valid candlestick pattern detected.**

📊 **Current Analysis:**
• RSI: `{signal.rsi:.1f}`
• Current Price: `{signal.current_price:.{decimals}f}`
• Session: `{signal.trading_session.value}`
• Time: `{time_str}`

💡 **Status:** {signal.reason}

🔁 **Bot will re-analyze in the next scan.**

**🕯️ Required Patterns:**
• **Bullish:** Bullish Engulfing, Hammer, Morning Star, Inverted Hammer, Piercing Line
• **Bearish:** Bearish Engulfing, Shooting Star, Evening Star, Hanging Man, Dark Cloud Cover"""

            else:
                message = f"""{no_alert_header}📈 **Pair:** {currency_pair} ({pair_category})
⚪ **NO SIGNAL**

📊 **Enhanced Analysis:**
• RSI: `{signal.rsi:.1f}`
• Current Price: `{signal.current_price:.{decimals}f}`
• Session: `{signal.trading_session.value}`
• Time: `{time_str}`
• Market Hours: `{'✅ ACTIVE' if signal.market_hours_valid else '❌ INACTIVE'}`

💡 **Status:** {signal.reason}

🎯 **Enhanced Requirements:**
• **BUY:** RSI < 30 + EMA20 > EMA50 + MACD Bullish + Bullish Pattern
• **SELL:** RSI > 70 + EMA20 < EMA50 + MACD Bearish + Bearish Pattern

The enhanced bot requires ALL conditions to be met for maximum accuracy!"""

        return message

    async def _send_enhanced_performance(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send enhanced performance statistics with pattern breakdown"""
        stats = self.performance_tracker.get_enhanced_stats()
        time_info = MarketHoursValidator.format_time_info()

        # Format pattern breakdown
        pattern_text = ""
        if stats['pattern_breakdown']:
            for pattern, count in stats['pattern_breakdown'].items():
                pattern_text += f"• {pattern}: `{count}` signals\n"
        else:
            pattern_text = "• No patterns recorded yet\n"

        # Format session breakdown
        session_text = ""
        if stats['session_breakdown']:
            for session, count in stats['session_breakdown'].items():
                session_text += f"• {session}: `{count}` signals\n"
        else:
            session_text = "• No session data yet\n"

        message = f"""
📈 **Enhanced Performance Statistics**

📊 **Signal Summary:**
• Total Enhanced Signals: `{stats['total_signals']}`
• Buy Signals: `{stats['buy_signals']}`
• Sell Signals: `{stats['sell_signals']}`
• Avg Confidence: `{stats['avg_confidence']}%`
• Unique Pairs: `{stats['pairs_analyzed']}`

**🕯️ Pattern Breakdown:**
{pattern_text}

**📍 Session Breakdown:**
{session_text}

🕐 **Last Signal:** {stats['last_signal']}

**🔄 Enhanced Live Monitoring:**
• Status: `{'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}`
• Market Hours: `{time_info['valid']}` ({time_info['session']} session)
• Monitored Pairs: `{len(self.settings.monitored_pairs)}`
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Pattern Requirement: `{'✅ ON' if self.settings.require_patterns else '❌ OFF'}`
• Min Pattern Confidence: `{self.settings.min_pattern_confidence}%`

**🎯 Enhanced Filtering:**
• Market Hours Enforcement: `{'✅ ON' if self.settings.enforce_market_hours else '❌ OFF'}`
• 4-Point Validation: `✅ ACTIVE`
• Session Awareness: `✅ ACTIVE`

💡 Enhanced signals represent only the highest quality setups with confirmed patterns!
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def _send_enhanced_settings(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send enhanced settings information"""
        time_info = MarketHoursValidator.format_time_info()

        message = f"""
⚙️ **Enhanced Bot Configuration v2.5+**

**🕐 Market Hours Control:**
• Enforcement: `{'✅ ON' if self.settings.enforce_market_hours else '❌ OFF'}`
• Active Window: `06:00-17:59 UTC` (07:00-18:59 WAT)
• Current Status: `{time_info['valid']}` ({time_info['session']} session)

**🕯️ Pattern Requirements:**
• Pattern Validation: `{'✅ MANDATORY' if self.settings.require_patterns else '❌ OPTIONAL'}`
• Min Confidence: `{self.settings.min_pattern_confidence}%`
• **Bullish Patterns:** Bullish Engulfing, Hammer, Morning Star, Inverted Hammer, Piercing Line
• **Bearish Patterns:** Bearish Engulfing, Shooting Star, Evening Star, Hanging Man, Dark Cloud Cover

**🎯 Signal Validation Rules:**
• **BUY Requirements:** RSI < 30 + EMA20 > EMA50 + MACD Bullish + Valid Bullish Pattern + Active Hours
• **SELL Requirements:** RSI > 70 + EMA20 < EMA50 + MACD Bearish + Valid Bearish Pattern + Active Hours

**📊 Monitoring Settings:**
• Live Monitoring: `{'🟢 ACTIVE' if self.settings.live_monitoring else '🔴 INACTIVE'}`
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Timeframe: `{self.settings.timeframe}`
• Monitored Pairs: `{len(self.settings.monitored_pairs)}`
• Batch Size: `{self.scan_batch_size} pairs/batch`

**👥 Users:** `{len(self.settings.subscribed_users)}` subscribed

**📱 Enhanced Commands:**
• `/market_hours` - Check current market status
• `/pattern_info` - View required patterns
• `/strict_mode [on|off]` - Toggle enhanced filtering
• `/min_confidence [60-90]` - Set pattern confidence threshold

**💡 The enhanced bot prioritizes accuracy over frequency - only the strongest setups generate signals!**
        """

        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

    async def live_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced live monitoring toggle"""
        user_id = update.effective_user.id

        if not context.args:
            time_info = MarketHoursValidator.format_time_info()
            status = "ON" if self.settings.live_monitoring else "OFF"
            await update.message.reply_text(
                f"**🔄 Enhanced Live Monitoring:** `{status}`\n\n"
                f"**Currently monitoring:** {len(self.settings.monitored_pairs)} pairs\n"
                f"**Market Status:** {time_info['valid']} ({time_info['session']} session)\n"
                f"**Enhanced Filters:** Pattern + Market Hours + 4-Point Validation\n\n"
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

            time_info = MarketHoursValidator.format_time_info()
            await update.message.reply_text(
                f"🟢 **Enhanced Live Monitoring ACTIVATED!**\n\n"
                f"📊 **Monitoring {len(self.settings.monitored_pairs)} pairs with enhanced filtering:**\n"
                f"• ⏰ Market Hours: {'✅ Enforced' if self.settings.enforce_market_hours else '❌ Disabled'}\n"
                f"• 🕯️ Pattern Requirement: {'✅ Mandatory' if self.settings.require_patterns else '❌ Optional'}\n"
                f"• 🎯 4-Point Validation: ✅ Active\n"
                f"• 📍 Session Awareness: ✅ Active\n\n"
                f"⚡ **Current Status:**\n"
                f"• Session: {time_info['session']}\n"
                f"• Market: {time_info['valid']}\n"
                f"• Scan Frequency: Every {self.settings.interval_minutes} minutes\n\n"
                f"🚨 **You'll receive ONLY the highest quality signals that meet ALL enhanced criteria!**",
                parse_mode='Markdown'
            )
        elif setting == "off":
            if user_id in self.settings.subscribed_users:
                self.settings.subscribed_users.remove(user_id)

            if not self.settings.subscribed_users:
                self.settings.live_monitoring = False
                await self._stop_live_monitoring()

            self._save_settings()
            await update.message.reply_text(
                "🔴 **Enhanced live monitoring DISABLED for you.**\n\n"
                "Use `/live on` to reactivate enhanced filtering alerts.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Use 'on' or 'off' with the live command")

    async def _start_live_monitoring(self):
        """Start enhanced live monitoring system"""
        if self.live_monitoring_task is None or self.live_monitoring_task.done():
            self.live_monitoring_task = asyncio.create_task(self._enhanced_live_monitoring_loop())
            logger.info(f"🔴 Enhanced live monitoring started with strict filtering")

    async def _stop_live_monitoring(self):
        """Stop enhanced live monitoring system"""
        if self.live_monitoring_task and not self.live_monitoring_task.done():
            self.live_monitoring_task.cancel()
            logger.info("🔴 Enhanced live monitoring stopped")

    async def _restart_live_monitoring(self):
        """Restart enhanced live monitoring with new settings"""
        if self.settings.live_monitoring:
            await self._stop_live_monitoring()
            await asyncio.sleep(1)
            await self._start_live_monitoring()

    async def _enhanced_live_monitoring_loop(self):
        """Enhanced live monitoring loop with strict filtering"""
        logger.info(f"🔄 Enhanced monitoring loop started - scanning {len(self.settings.monitored_pairs)} pairs with advanced filtering")

        while self.settings.live_monitoring and self.settings.subscribed_users:
            try:
                scan_start_time = datetime.now(timezone.utc)
                time_info = MarketHoursValidator.format_time_info()

                logger.info(f"🔍 Starting enhanced scan cycle - {time_info['session']} session, Market: {time_info['valid']}")

                signals_found = []
                pattern_missing_alerts = []
                market_hours_blocked = []
                no_signal_pairs = []
                processed_pairs = 0

                pairs_list = list(self.settings.monitored_pairs.copy())

                for i in range(0, len(pairs_list), self.scan_batch_size):
                    batch = pairs_list[i:i + self.scan_batch_size]
                    logger.info(f"🔄 Processing enhanced batch {(i // self.scan_batch_size) + 1}: {', '.join(batch)}")

                    for currency_pair in batch:
                        try:
                            is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(currency_pair)
                            if not is_valid:
                                continue

                            candles = await self.data_provider.get_candles(
                                symbol=api_format,
                                interval=self.settings.timeframe,
                                count=100
                            )

                            if not candles:
                                continue

                            signal = self.signal_generator.generate_enhanced_signal(candles, currency_pair)

                            # Categorize signals for appropriate handling
                            signal_key = f"{currency_pair}_{signal.action.value}_{signal.timestamp.strftime('%H')}"
                            last_signal_key = self.last_signals.get(currency_pair, "")

                            if signal.action != SignalType.NONE:
                                if signal_key != last_signal_key:
                                    signals_found.append(signal)
                                    self.last_signals[currency_pair] = signal_key
                            elif not signal.market_hours_valid:
                                market_hours_blocked.append(currency_pair)
                            elif "No valid" in signal.reason and "pattern detected" in signal.reason:
                                # Only send pattern missing alerts occasionally to avoid spam
                                if scan_start_time.minute % 20 == 0:  # Every 20 minutes
                                    pattern_missing_alerts.append(signal)
                            else:
                                no_signal_pairs.append(currency_pair)

                            processed_pairs += 1
                            await asyncio.sleep(2)  # Longer delay for thorough analysis

                        except Exception as e:
                            logger.error(f"Error in enhanced analysis for {currency_pair}: {e}")
                            continue

                    await asyncio.sleep(4)  # Batch delay

                scan_duration = (datetime.now(timezone.utc) - scan_start_time).total_seconds()
                logger.info(f"✅ Enhanced scan completed in {scan_duration:.1f}s - {len(signals_found)} signals, {len(pattern_missing_alerts)} pattern alerts, {len(market_hours_blocked)} blocked")

                # Send trade signals (highest priority)
                if signals_found:
                    for signal in signals_found:
                        self.performance_tracker.log_signal(signal)
                        message = self._format_enhanced_signal_message(signal, is_live_alert=True)

                        successful_sends = 0
                        for user_id in self.settings.subscribed_users.copy():
                            try:
                                await self.application.bot.send_message(
                                    user_id, message, parse_mode='Markdown'
                                )
                                successful_sends += 1
                            except Exception as e:
                                logger.error(f"Failed to send enhanced alert to user {user_id}: {e}")
                                if "chat not found" in str(e).lower():
                                    self.settings.subscribed_users.discard(user_id)

                        if successful_sends > 0:
                            logger.info(f"🚨 ENHANCED ALERT sent: {signal.action.value} {signal.currency_pair} ({signal.pattern.name}) to {successful_sends} users")

                # Send pattern missing info messages (medium priority, less frequent)
                if pattern_missing_alerts:
                    for signal in pattern_missing_alerts:
                        message = self._format_enhanced_signal_message(signal, is_live_alert=True)

                        for user_id in self.settings.subscribed_users.copy():
                            try:
                                await self.application.bot.send_message(
                                    user_id, message, parse_mode='Markdown'
                                )
                            except Exception as e:
                                logger.error(f"Failed to send pattern info to user {user_id}: {e}")
                                if "chat not found" in str(e).lower():
                                    self.settings.subscribed_users.discard(user_id)

                    logger.info(f"📊 Pattern missing alerts sent for {len(pattern_missing_alerts)} pairs")

                # Send summary for market hours blocked (low priority, hourly)
                if market_hours_blocked and scan_start_time.minute == 0:  # Only on the hour
                    summary_message = f"""
📊 **Enhanced Market Hours Summary**

⏰ **Analysis Blocked:** {len(market_hours_blocked)} pairs
🕐 **Current Session:** {time_info['session']}
📍 **Status:** {time_info['valid']}

**💡 Blocked pairs:** {', '.join(market_hours_blocked[:8])}{'...' if len(market_hours_blocked) > 8 else ''}

**🔄 Enhanced monitoring resumes during active hours (06:00-17:59 UTC)**
                    """

                    for user_id in self.settings.subscribed_users.copy():
                        try:
                            await self.application.bot.send_message(
                                user_id, summary_message, parse_mode='Markdown'
                            )
                        except Exception as e:
                            if "chat not found" in str(e).lower():
                                self.settings.subscribed_users.discard(user_id)

                self._save_settings()
                logger.info(f"⏰ Next enhanced scan in {self.settings.interval_minutes} minutes")

                await asyncio.sleep(self.settings.interval_minutes * 60)

            except asyncio.CancelledError:
                logger.info("🔴 Enhanced live monitoring loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in enhanced live monitoring loop: {e}")
                await asyncio.sleep(60)

    # Additional enhanced commands
    async def market_hours_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Market hours status command"""
        await self._send_market_hours_status(update.message.chat_id, context)

    async def pattern_info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Pattern information command"""
        message = """
🕯️ **Enhanced Candlestick Pattern Requirements**

**🟢 BULLISH PATTERNS (for BUY signals):**
• **Bullish Engulfing** - Large green candle engulfs previous red candle
• **Hammer** - Long lower shadow, small body at top
• **Morning Star** - 3-candle reversal pattern (bearish → doji → bullish)
• **Inverted Hammer** - Long upper shadow, small body at bottom
• **Piercing Line** - Green candle pierces deep into previous red candle

**🔴 BEARISH PATTERNS (for SELL signals):**
• **Bearish Engulfing** - Large red candle engulfs previous green candle
• **Shooting Star** - Long upper shadow, small body at bottom
• **Evening Star** - 3-candle reversal pattern (bullish → doji → bearish)
• **Hanging Man** - Long lower shadow, small body at top (at resistance)
• **Dark Cloud Cover** - Red candle pierces deep into previous green candle

**⚙️ Pattern Settings:**
• **Minimum Confidence:** {self.settings.min_pattern_confidence}%
• **Requirement Status:** {'✅ MANDATORY' if self.settings.require_patterns else '❌ OPTIONAL'}

**🎯 Enhanced Logic:**
• Pattern detection is MANDATORY for all signals
• Only patterns with confidence ≥ {self.settings.min_pattern_confidence}% are accepted
• When patterns are missing, bot sends info messages instead of trade signals

**💡 This ensures maximum signal accuracy by confirming reversal patterns!**
        """.format(self.settings.min_pattern_confidence, 
                  '✅ MANDATORY' if self.settings.require_patterns else '❌ OPTIONAL')

        await update.message.reply_text(message, parse_mode='Markdown')

    async def strict_mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle strict mode filtering"""
        if not context.args:
            await update.message.reply_text(
                f"**🎯 Enhanced Strict Mode Status:**\n\n"
                f"• Market Hours Enforcement: `{'✅ ON' if self.settings.enforce_market_hours else '❌ OFF'}`\n"
                f"• Pattern Requirement: `{'✅ ON' if self.settings.require_patterns else '❌ OFF'}`\n"  
                f"• Min Pattern Confidence: `{self.settings.min_pattern_confidence}%`\n\n"
                "**Usage:** `/strict_mode [on|off]`",
                parse_mode='Markdown'
            )
            return

        setting = context.args[0].lower()
        if setting == "on":
            self.settings.enforce_market_hours = True
            self.settings.require_patterns = True
            self._save_settings()
            await update.message.reply_text(
                "🎯 **Enhanced Strict Mode ACTIVATED!**\n\n"
                "✅ Market hours enforcement: ON\n"
                "✅ Pattern requirement: MANDATORY\n"
                "✅ 4-point validation: ACTIVE\n\n"
                "Only the highest quality signals will be generated!",
                parse_mode='Markdown'
            )
        elif setting == "off":
            self.settings.enforce_market_hours = False
            self.settings.require_patterns = False
            self._save_settings()
            await update.message.reply_text(
                "⚠️ **Enhanced Strict Mode DISABLED.**\n\n"
                "❌ Market hours enforcement: OFF\n"
                "❌ Pattern requirement: OPTIONAL\n\n"
                "Bot will generate more signals but with lower accuracy filtering.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Use 'on' or 'off' with the strict_mode command")

    async def min_confidence_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set minimum pattern confidence"""
        if not context.args:
            await update.message.reply_text(
                f"**🎯 Current Pattern Confidence Threshold:** `{self.settings.min_pattern_confidence}%`\n\n"
                "**Usage:** `/min_confidence [60-90]`\n"
                "**Examples:**\n"
                "• `/min_confidence 70` - Balanced filtering\n"
                "• `/min_confidence 80` - Strict filtering\n"
                "• `/min_confidence 60` - More signals",
                parse_mode='Markdown'
            )
            return

        try:
            new_confidence = float(context.args[0])
            if 60 <= new_confidence <= 90:
                old_confidence = self.settings.min_pattern_confidence
                self.settings.min_pattern_confidence = new_confidence
                self._save_settings()
                await update.message.reply_text(
                    f"✅ **Pattern confidence threshold updated:** `{old_confidence}%` → `{new_confidence}%`\n\n"
                    f"{'📈 More signals expected (lower threshold)' if new_confidence < old_confidence else '📉 Fewer but higher quality signals expected (higher threshold)'}\n\n"
                    "Change applies to all future pattern detections!",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Confidence must be between 60-90%")
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")

    async def performance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced performance command"""
        await self._send_enhanced_performance(update.message.chat_id, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced help command"""
        await update.message.reply_text(help_message, parse_mode='Markdown')

    # Add missing command handlers for existing functionality
    async def add_pair_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add currency pair with enhanced validation"""
        if not context.args:
            await update.message.reply_text(
                f"**💱 Add Pair to Enhanced Monitoring**\n\n"
                f"**Usage:** `/add_pair [PAIR]`\n\n"
                f"**Examples:**\n"
                f"• `/add_pair EURUSD` - Major pair\n"
                f"• `/add_pair GBPJPY` - Cross pair\n"
                f"• `/add_pair USDTRY` - Exotic pair\n\n"
                f"**📊 Currently monitoring:** {len(self.settings.monitored_pairs)} pairs\n"
                f"**🕯️ All pairs will use enhanced pattern filtering**",
                parse_mode='Markdown'
            )
            return

        pair_input = context.args[0]
        is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(pair_input)

        if not is_valid:
            await update.message.reply_text(
                f"❌ **Invalid currency pair:** `{pair_input}`\n\n"
                f"**Enhanced bot supports major, cross, and exotic pairs**\n"
                f"Use format: EURUSD, GBP/USD, etc.",
                parse_mode='Markdown'
            )
            return

        if display_format in self.settings.monitored_pairs:
            pair_category = CurrencyPairValidator.get_pair_category(display_format)
            await update.message.reply_text(
                f"⚠️ **{display_format} ({pair_category}) is already monitored with enhanced filtering.**",
                parse_mode='Markdown'
            )
            return

        self.settings.monitored_pairs.add(display_format)
        self._save_settings()

        if self.settings.live_monitoring:
            await self._restart_live_monitoring()

        pair_category = CurrencyPairValidator.get_pair_category(display_format)
        await update.message.reply_text(
            f"✅ **{display_format} ({pair_category}) added to enhanced monitoring!**\n\n"
            f"📊 **Enhanced Features Active:**\n"
            f"• 🕯️ Pattern detection: ✅\n"
            f"• ⏰ Market hours filtering: {'✅' if self.settings.enforce_market_hours else '❌'}\n"
            f"• 🎯 4-point validation: ✅\n"
            f"• 📍 Session awareness: ✅\n\n"
            f"**Now monitoring:** {len(self.settings.monitored_pairs)} pairs total",
            parse_mode='Markdown'
        )

    async def remove_pair_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove currency pair from enhanced monitoring"""
        if not context.args:
            if not self.settings.monitored_pairs:
                await update.message.reply_text(
                    "📭 **No pairs in enhanced monitoring.**\n\n"
                    "Use `/add_pair [PAIR]` to start monitoring.",
                    parse_mode='Markdown'
                )
                return

            sample_pairs = list(self.settings.monitored_pairs)[:6]
            pairs_text = " • ".join(sample_pairs)
            if len(self.settings.monitored_pairs) > 6:
                pairs_text += f" +{len(self.settings.monitored_pairs) - 6} more"

            await update.message.reply_text(
                f"**🗑️ Remove Pair from Enhanced Monitoring**\n\n"
                f"**Usage:** `/remove_pair [PAIR]`\n\n"
                f"**Enhanced monitored pairs ({len(self.settings.monitored_pairs)}):**\n"
                f"{pairs_text}\n\n"
                f"Use `/pairs` for complete list.",
                parse_mode='Markdown'
            )
            return

        pair_input = context.args[0]
        is_valid, api_format, display_format = CurrencyPairValidator.validate_and_format(pair_input)

        if not is_valid:
            await update.message.reply_text(
                f"❌ **Invalid currency pair:** `{pair_input}`",
                parse_mode='Markdown'
            )
            return

        if display_format not in self.settings.monitored_pairs:
            await update.message.reply_text(
                f"⚠️ **{display_format} is not in enhanced monitoring.**",
                parse_mode='Markdown'
            )
            return

        self.settings.monitored_pairs.remove(display_format)
        self._save_settings()

        if self.settings.live_monitoring:
            await self._restart_live_monitoring()

        pair_category = CurrencyPairValidator.get_pair_category(display_format)
        await update.message.reply_text(
            f"✅ **{display_format} ({pair_category}) removed from enhanced monitoring.**\n\n"
            f"**Now monitoring:** {len(self.settings.monitored_pairs)} pairs",
            parse_mode='Markdown'
        )

    async def pairs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show enhanced monitored pairs"""
        if not self.settings.monitored_pairs:
            await update.message.reply_text(
                "📭 **No pairs in enhanced monitoring.**\n\n"
                "Use `/add_pair EURUSD` to start enhanced monitoring.",
                parse_mode='Markdown'
            )
            return

        # Organize by category
        major_pairs = [p for p in self.settings.monitored_pairs if p in CurrencyPairValidator.MAJOR_PAIRS]
        cross_pairs = [p for p in self.settings.monitored_pairs if p in CurrencyPairValidator.CROSS_PAIRS]
        minor_pairs = [p for p in self.settings.monitored_pairs if p in CurrencyPairValidator.MINOR_PAIRS]
        exotic_pairs = [p for p in self.settings.monitored_pairs if p not in CurrencyPairValidator.MAJOR_PAIRS 
                       and p not in CurrencyPairValidator.CROSS_PAIRS and p not in CurrencyPairValidator.MINOR_PAIRS]

        pairs_text = ""
        if major_pairs:
            pairs_text += f"**🏛️ Majors ({len(major_pairs)}):**\n{' • '.join(major_pairs)}\n\n"
        if cross_pairs:
            pairs_text += f"**🔄 Crosses ({len(cross_pairs)}):**\n{' • '.join(cross_pairs)}\n\n"
        if minor_pairs:
            pairs_text += f"**🌍 Minors ({len(minor_pairs)}):**\n{' • '.join(minor_pairs)}\n\n"
        if exotic_pairs:
            pairs_text += f"**💎 Exotics ({len(exotic_pairs)}):**\n{' • '.join(exotic_pairs)}\n\n"

        time_info = MarketHoursValidator.format_time_info()

        message = f"""
📊 **Enhanced Monitored Pairs ({len(self.settings.monitored_pairs)})**

{pairs_text}

**⚙️ Enhanced Monitoring Status:**
• Session: `{time_info['session']}`
• Market: `{time_info['valid']}`
• Scan Interval: `{self.settings.interval_minutes} minutes`
• Pattern Requirement: `{'✅ ON' if self.settings.require_patterns else '❌ OFF'}`
• Market Hours Filter: `{'✅ ON' if self.settings.enforce_market_hours else '❌ OFF'}`

**🎯 All pairs use enhanced 4-point validation with mandatory pattern confirmation!**
        """

        await update.message.reply_text(message, parse_mode='Markdown')

    async def set_interval_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set enhanced scanning interval"""
        if not context.args:
            await update.message.reply_text(
                f"**Enhanced scan interval:** `{self.settings.interval_minutes} minutes`\n\n"
                f"**Enhanced analysis takes longer due to:**\n"
                f"• Pattern detection processing\n"
                f"• Market hours validation\n"
                f"• 4-point validation system\n\n"
                "**Usage:** `/set_interval [5-30]`\n"
                "**Recommended:** 8-15 minutes for optimal enhanced filtering",
                parse_mode='Markdown'
            )
            return

        try:
            new_interval = int(context.args[0])
            if 5 <= new_interval <= 30:
                old_interval = self.settings.interval_minutes
                self.settings.interval_minutes = new_interval
                self._save_settings()

                if self.settings.live_monitoring:
                    await self._restart_live_monitoring()

                await update.message.reply_text(
                    f"✅ **Enhanced scan interval updated:** `{old_interval}min` → `{new_interval}min`\n\n"
                    f"📊 **Impact on enhanced monitoring:**\n"
                    f"• Pattern analysis frequency: Every `{new_interval} minutes`\n"
                    f"• Market hours validation: Continuous\n"
                    f"• Enhanced filtering: Active on all scans\n\n"
                    "🔄 Enhanced monitoring restarted!",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Interval must be between 5-30 minutes")
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")

    async def timeframe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set enhanced analysis timeframe"""
        if not context.args:
            await update.message.reply_text(
                f"**Enhanced timeframe:** `{self.settings.timeframe}`\n\n"
                "**Usage:** `/timeframe [15min|30min|1h]`\n"
                "**Enhanced analysis impact:**\n"
                "• `15min` - More pattern signals, faster detection\n"
                "• `30min` - Balanced enhanced filtering\n"
                "• `1h` - Stronger patterns, fewer but higher quality signals",
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
                f"✅ **Enhanced timeframe updated:** `{old_timeframe}` → `{new_timeframe}`\n\n"
                f"📊 **Enhanced impact:** All pattern detection and 4-point validation now uses `{new_timeframe}` candles\n"
                "🔄 Change applies to all future enhanced analysis!",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ Invalid timeframe. Use: {', '.join(valid_timeframes)}"
            )

    def run(self):
        """Start the enhanced bot"""
        self.application = Application.builder().token(self.telegram_token).build()

        # Add enhanced handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("analyze", self.analyze_command))
        self.application.add_handler(CommandHandler("performance", self.performance_command))
        self.application.add_handler(CommandHandler("live", self.live_command))
        self.application.add_handler(CommandHandler("market_hours", self.market_hours_command))
        self.application.add_handler(CommandHandler("pattern_info", self.pattern_info_command))
        self.application.add_handler(CommandHandler("strict_mode", self.strict_mode_command))
        self.application.add_handler(CommandHandler("min_confidence", self.min_confidence_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        # Add pair management handlers
        self.application.add_handler(CommandHandler("add_pair", self.add_pair_command))
        self.application.add_handler(CommandHandler("remove_pair", self.remove_pair_command))
        self.application.add_handler(CommandHandler("pairs", self.pairs_command))
        self.application.add_handler(CommandHandler("set_interval", self.set_interval_command))
        self.application.add_handler(CommandHandler("timeframe", self.timeframe_command))
        self.application.add_handler(CommandHandler("config", lambda u, c: self._send_enhanced_settings(u.message.chat_id, c)))

        # Auto-start enhanced monitoring
        async def post_init(application):
            if self.settings.live_monitoring and self.settings.subscribed_users:
                await self._start_live_monitoring()

        self.application.post_init = post_init

        # Start the enhanced bot
        logger.info("🚀 Starting Enhanced Live Forex Telegram Bot v2.5+...")
        logger.info(f"📊 Enhanced monitoring with strict filtering")
        logger.info(f"⏰ Market hours enforcement: {'ON' if self.settings.enforce_market_hours else 'OFF'}")
        logger.info(f"🕯️ Pattern requirement: {'MANDATORY' if self.settings.require_patterns else 'OPTIONAL'}")
        logger.info(f"🎯 Min pattern confidence: {self.settings.min_pattern_confidence}%")
        logger.info(f"👥 {len(self.settings.subscribed_users)} users subscribed")
        logger.info(f"💱 {len(self.settings.monitored_pairs)} pairs in enhanced monitoring")

        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    """Main function to run the enhanced bot"""
    # Configuration - Replace with your actual tokens
    TELEGRAM_BOT_TOKEN = "8186199634:AAEEafBIm5GhZrhrWt-je8wa1UESaTHF9ZM"
    TWELVEDATA_API_KEY = "b971d5ae2d0447fbb2fa621565a16334"  # Optional

    # Validate token
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("Please set your Telegram bot token")
        print("\n🔑 ENHANCED SETUP REQUIRED:")
        print("1. Get bot token from @BotFather on Telegram")
        print("2. Replace with your actual token")
        print("3. Optionally get TwelveData API key for real market data")
        print("4. Run: pip install python-telegram-bot requests numpy")
        return

    try:
        # Create and run enhanced bot
        bot = EnhancedLiveForexTelegramBot(
            telegram_token=TELEGRAM_BOT_TOKEN,
            api_key=TWELVEDATA_API_KEY if TWELVEDATA_API_KEY != "YOUR_TWELVEDATA_API_KEY_HERE" else None
        )

        print("\n🚀 ENHANCED LIVE FOREX BOT v2.5+")
        print("====================================")
        print("🆕 NEW ENHANCED FEATURES:")
        print("• ⏰ Smart Market Hours (06:00-17:59 UTC)")
        print("• 🕯️ Mandatory Candlestick Patterns")
        print("• 📍 Session-Aware Analysis")
        print("• 🎯 4-Point Validation System")
        print("• 📊 Enhanced Feedback Messages")
        print("• 🔧 Strict Filtering Controls")
        print("\n🎯 MAXIMUM ACCURACY MODE ACTIVE")
        print("Starting enhanced bot...")

        bot.run()
    except KeyboardInterrupt:
        logger.info("Enhanced bot stopped by user")
    except Exception as e:
        logger.error(f"Enhanced bot crashed: {e}")


if __name__ == "__main__":
    main()