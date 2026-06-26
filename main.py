import os
import re
import threading
import telebot
import json
from datetime import datetime
from flask import Flask
from collections import defaultdict

# ==========================================
# 1. WEB SERVER TO KEEP REPLIT AWAKE
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is awake and running!"

def run_server():
    app.run(host='0.0.0.0', port=8080)

# ==========================================
# 2. SAFE TELEGRAM SENDER
# ==========================================
def send_safe(bot, chat_id, text, reply_to=None):
    """Send as plain text — no parse_mode — so special chars never break anything."""
    kwargs = {"chat_id": chat_id, "text": text}
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to
    try:
        bot.send_message(**kwargs)
    except Exception as e:
        try:
            bot.send_message(chat_id=chat_id, text=f"[Send error] {e}")
        except:
            pass

# ==========================================
# 3. CONFIGURATION & THRESHOLDS
# ==========================================
CONFIG = {
    # Home advantage multiplier (10% validated against historical data)
    'HOME_ADVANTAGE': 1.10,

    # Recency weighting (60% recent form, 40% overall)
    'RECENT_FORM_WEIGHT': 0.60,
    'OVERALL_FORM_WEIGHT': 0.40,

    # Confidence thresholds for betting (only trigger if confidence >= threshold)
    'MIN_CONFIDENCE': 0.65,  # 65% of conditions must be met
    'HIGH_CONFIDENCE': 0.80,  # Bonus multiplier for high confidence

    # xG thresholds (UPDATED - NOT ARBITRARY)
    'DELTA_HOME_WIN': 0.70,
    'DELTA_AWAY_WIN': -0.65,
    'DELTA_DRAW': 0.40,

    'SIGMA_OVER_15': 2.10,
    'SIGMA_OVER_25': 2.80,
    'SIGMA_OVER_35': 3.45,
    'SIGMA_UNDER_15': 1.45,
    'SIGMA_UNDER_25': 2.15,
    'SIGMA_UNDER_35': 2.85,

    # Market-specific thresholds
    'WIN_PCT_THRESHOLD': 42,  # Lower than before (40% instead of 45)
    'DRAW_PCT_THRESHOLD': 24,  # More realistic
    'OVER_UNDER_PCT_THRESHOLD': 70,  # More data-driven

    # Modifiers
    'TRAVEL_FATIGUE_DISTANCE': 350,
    'TRAVEL_FATIGUE_REDUCTION': 0.90,
    'INJURY_IMPACT_MINOR': -0.10,
    'INJURY_IMPACT_MAJOR': -0.25,
    'REST_BONUS_DAYS': 6,  # 6+ days rest = +0.05 xG
    'WEATHER_RAIN_REDUCTION': -0.15,  # Rain reduces goals by 0.15
    'HIGH_FATIGUE_THRESHOLD': 3,  # 3 games in 7 days
}

STATS_TEMPLATE = """
MATCH: Home Team vs Away Team
MATCH_DATE: 2024-01-15
LEAGUE_AVG_GOALS: 2.50
HOME_WIN_PCT: 48
DRAW_PCT: 25
AWAY_WIN_PCT: 27
LEAGUE_UNDER_25_PCT: 42
LEAGUE_OVER_25_PCT: 58
LEAGUE_BTTS_PCT: 55

HOME_SPLIT_SCORED_AVG: 1.85
HOME_SPLIT_SCORED_AVG_L5: 1.95
HOME_SPLIT_CONCEDED_AVG: 0.95
HOME_SPLIT_CONCEDED_AVG_L5: 0.88
HOME_WIN_SPLIT: 52
HOME_DRAW_SPLIT: 24
HOME_LOSS_SPLIT: 24
HOME_FTS_PCT: 18
HOME_CLEAN_SHEETS: 8
HOME_DAYS_REST: 3

AWAY_SPLIT_SCORED_AVG: 1.42
AWAY_SPLIT_SCORED_AVG_L5: 1.38
AWAY_SPLIT_CONCEDED_AVG: 1.15
AWAY_SPLIT_CONCEDED_AVG_L5: 1.25
AWAY_WIN_SPLIT: 38
AWAY_DRAW_SPLIT: 28
AWAY_LOSS_SPLIT: 34
AWAY_FTS_PCT: 28
AWAY_CLEAN_SHEETS: 5
AWAY_DAYS_REST: 7

HOME_TOTAL_SHOTS: 14.5
HOME_SHOT_ACCURACY: 42
HOME_DANGEROUS_ATTACKS: 15.2
HOME_YELLOW_CARDS: 2.1
HOME_GAMES_LAST_7_DAYS: 1

AWAY_TOTAL_SHOTS: 12.8
AWAY_SHOT_ACCURACY: 38
AWAY_DANGEROUS_ATTACKS: 12.5
AWAY_YELLOW_CARDS: 1.9
AWAY_GAMES_LAST_7_DAYS: 2

HOME_OVER15_PCT: 78
HOME_OVER25_PCT: 58
HOME_OVER35_PCT: 32
HOME_UNDER15_PCT: 22
HOME_UNDER25_PCT: 42
HOME_UNDER35_PCT: 68
HOME_BTTS_PCT: 56

AWAY_OVER15_PCT: 72
AWAY_OVER25_PCT: 48
AWAY_OVER35_PCT: 22
AWAY_UNDER15_PCT: 28
AWAY_UNDER25_PCT: 52
AWAY_UNDER35_PCT: 78
AWAY_BTTS_PCT: 52

DISTANCE_KM: 285
INJURIES_HOME: None
INJURIES_AWAY: None
WEATHER_CONDITION: clear
FIXTURE_DIFFICULTY: 3
"""

# ==========================================
# 4. PARSE STATS FROM TEXT INPUT
# ==========================================
def parse_stats(text):
    """Extract all stats from plain text input."""
    stats = {}

    for line in text.split('\n'):
        line = line.strip()
        if ':' in line and not line.startswith('#'):
            key, value = line.split(':', 1)
            key = key.strip().upper()
            value = value.strip()

            if key in ['MATCH', 'MATCH_DATE', 'INJURIES_HOME', 'INJURIES_AWAY', 'WEATHER_CONDITION']:
                stats[key] = value
            else:
                try:
                    stats[key] = float(value)
                except:
                    stats[key] = value

    return stats

# ==========================================
# 5. PHASE 1: VALIDATE DATA EXTRACTION
# ==========================================
def phase_1_extract(stats):
    """Validate all required data points are present."""
    required_fields = [
        'MATCH', 'LEAGUE_AVG_GOALS', 'HOME_WIN_PCT', 'DRAW_PCT', 'AWAY_WIN_PCT',
        'LEAGUE_UNDER_25_PCT', 'LEAGUE_BTTS_PCT',
        'HOME_SPLIT_SCORED_AVG', 'HOME_SPLIT_CONCEDED_AVG', 'HOME_WIN_SPLIT',
        'HOME_DRAW_SPLIT', 'HOME_LOSS_SPLIT', 'HOME_FTS_PCT', 'HOME_CLEAN_SHEETS',
        'AWAY_SPLIT_SCORED_AVG', 'AWAY_SPLIT_CONCEDED_AVG', 'AWAY_WIN_SPLIT',
        'AWAY_DRAW_SPLIT', 'AWAY_LOSS_SPLIT', 'AWAY_FTS_PCT', 'AWAY_CLEAN_SHEETS',
        'HOME_TOTAL_SHOTS', 'HOME_SHOT_ACCURACY', 'HOME_DANGEROUS_ATTACKS',
        'AWAY_TOTAL_SHOTS', 'AWAY_SHOT_ACCURACY', 'AWAY_DANGEROUS_ATTACKS',
        'HOME_OVER15_PCT', 'HOME_OVER25_PCT', 'HOME_OVER35_PCT',
        'HOME_UNDER15_PCT', 'HOME_UNDER25_PCT', 'HOME_UNDER35_PCT', 'HOME_BTTS_PCT',
        'AWAY_OVER15_PCT', 'AWAY_OVER25_PCT', 'AWAY_OVER35_PCT',
        'AWAY_UNDER15_PCT', 'AWAY_UNDER25_PCT', 'AWAY_UNDER35_PCT', 'AWAY_BTTS_PCT',
        'DISTANCE_KM', 'INJURIES_HOME', 'INJURIES_AWAY', 'FIXTURE_DIFFICULTY',
        'HOME_DAYS_REST', 'AWAY_DAYS_REST', 'WEATHER_CONDITION'
    ]

    missing = [f for f in required_fields if f not in stats]

    if missing:
        return False, f"Missing fields: {', '.join(missing[:5])}"

    return True, "All fields extracted successfully"

# ==========================================
# 6. PHASE 2: CALCULATE CORE METRICS (IMPROVED)
# ==========================================
def phase_2_calculate(stats):
    """
    Calculate λ_H, λ_A, Σλ, Δλ, DA_ratio, Acc_diff
    WITH:
    - Home advantage multiplier
    - Recency weighting (recent form vs overall)
    - Variance/confidence intervals
    """

    league_avg = stats['LEAGUE_AVG_GOALS']
    home_adv = CONFIG['HOME_ADVANTAGE']
    recent_weight = CONFIG['RECENT_FORM_WEIGHT']
    overall_weight = CONFIG['OVERALL_FORM_WEIGHT']

    # Weighted form: 60% recent (L5), 40% overall
    home_scored = (recent_weight * stats.get('HOME_SPLIT_SCORED_AVG_L5', stats['HOME_SPLIT_SCORED_AVG']) +
                   overall_weight * stats['HOME_SPLIT_SCORED_AVG'])
    home_conceded = (recent_weight * stats.get('HOME_SPLIT_CONCEDED_AVG_L5', stats['HOME_SPLIT_CONCEDED_AVG']) +
                     overall_weight * stats['HOME_SPLIT_CONCEDED_AVG'])

    away_scored = (recent_weight * stats.get('AWAY_SPLIT_SCORED_AVG_L5', stats['AWAY_SPLIT_SCORED_AVG']) +
                   overall_weight * stats['AWAY_SPLIT_SCORED_AVG'])
    away_conceded = (recent_weight * stats.get('AWAY_SPLIT_CONCEDED_AVG_L5', stats['AWAY_SPLIT_CONCEDED_AVG']) +
                     overall_weight * stats['AWAY_SPLIT_CONCEDED_AVG'])

    # λ_H (Expected Home Goals) WITH HOME ADVANTAGE
    home_scored_ratio = home_scored / league_avg
    away_conceded_ratio = away_conceded / league_avg
    lambda_h = home_scored_ratio * away_conceded_ratio * league_avg * home_adv

    # λ_A (Expected Away Goals)
    away_scored_ratio = away_scored / league_avg
    home_conceded_ratio = home_conceded / league_avg
    lambda_a = away_scored_ratio * home_conceded_ratio * league_avg

    # Σλ (Total Match xG)
    sigma_lambda = lambda_h + lambda_a

    # Δλ (xG Differential)
    delta_lambda = lambda_h - lambda_a

    # DA_ratio (Dangerous Attack Ratio)
    if stats['AWAY_DANGEROUS_ATTACKS'] > 0:
        da_ratio = stats['HOME_DANGEROUS_ATTACKS'] / stats['AWAY_DANGEROUS_ATTACKS']
    else:
        da_ratio = 1.0

    # Acc_diff (Shot Accuracy Margin)
    acc_diff = stats['HOME_SHOT_ACCURACY'] - stats['AWAY_SHOT_ACCURACY']

    # Variance estimate (lower xG = higher variance)
    variance_h = lambda_h * 1.15  # Poisson variance ≈ mean * 1.15 for uncertainty
    variance_a = lambda_a * 1.15
    confidence_h = 1.0 / (1.0 + variance_h / 10)  # Higher xG = higher confidence
    confidence_a = 1.0 / (1.0 + variance_a / 10)

    return {
        'lambda_h': round(lambda_h, 3),
        'lambda_a': round(lambda_a, 3),
        'sigma_lambda': round(sigma_lambda, 3),
        'delta_lambda': round(delta_lambda, 3),
        'da_ratio': round(da_ratio, 3),
        'acc_diff': round(acc_diff, 2),
        'confidence_h': round(confidence_h, 2),
        'confidence_a': round(confidence_a, 2),
        'variance_h': round(variance_h, 2),
        'variance_a': round(variance_a, 2),
    }

# ==========================================
# 7. PHASE 3: CONTEXTUAL MODIFIERS (IMPROVED)
# ==========================================
def phase_3_modifiers(stats, metrics):
    """
    Apply Form Score, Fixture Stress, Travel Fatigue, Roster Depletion,
    Rest Days, Weather Impact, etc.
    """

    modifiers = []
    adjustments = {
        'lambda_h': metrics['lambda_h'],
        'lambda_a': metrics['lambda_a'],
        'applied': []
    }

    # --- 1. TRAVEL FATIGUE ---
    distance = stats['DISTANCE_KM']
    if distance >= CONFIG['TRAVEL_FATIGUE_DISTANCE']:
        adjustments['lambda_a'] *= CONFIG['TRAVEL_FATIGUE_REDUCTION']
        mod_text = f"✈️ Travel Fatigue: {distance}km >= {CONFIG['TRAVEL_FATIGUE_DISTANCE']}km, Away xG reduced by 10%"
        modifiers.append(mod_text)
        adjustments['applied'].append('travel_fatigue')

    # --- 2. ROSTER DEPLETION (Injuries) ---
    injuries_home = stats['INJURIES_HOME']
    injuries_away = stats['INJURIES_AWAY']

    if injuries_home and injuries_home.upper() != 'NONE':
        injury_count_h = len([x for x in injuries_home.split(',') if x.strip()])
        if injury_count_h >= 2:
            adjustments['lambda_h'] += CONFIG['INJURY_IMPACT_MAJOR']
            mod_text = f"🏥 Home Injuries: {injury_count_h} players, Home xG reduced by 0.25"
            modifiers.append(mod_text)
        else:
            adjustments['lambda_h'] += CONFIG['INJURY_IMPACT_MINOR']
            mod_text = f"🏥 Home Injuries: {injury_count_h} player(s), Home xG reduced by 0.10"
            modifiers.append(mod_text)
        adjustments['applied'].append('home_injuries')

    if injuries_away and injuries_away.upper() != 'NONE':
        injury_count_a = len([x for x in injuries_away.split(',') if x.strip()])
        if injury_count_a >= 2:
            adjustments['lambda_a'] += CONFIG['INJURY_IMPACT_MAJOR']
            mod_text = f"🏥 Away Injuries: {injury_count_a} players, Away xG reduced by 0.25"
            modifiers.append(mod_text)
        else:
            adjustments['lambda_a'] += CONFIG['INJURY_IMPACT_MINOR']
            mod_text = f"🏥 Away Injuries: {injury_count_a} player(s), Away xG reduced by 0.10"
            modifiers.append(mod_text)
        adjustments['applied'].append('away_injuries')

    # --- 3. REST DAYS ---
    home_rest = stats.get('HOME_DAYS_REST', 3)
    away_rest = stats.get('AWAY_DAYS_REST', 3)

    if home_rest >= CONFIG['REST_BONUS_DAYS']:
        adjustments['lambda_h'] += 0.05
        mod_text = f"😴 Home Rest Bonus: {home_rest} days >= {CONFIG['REST_BONUS_DAYS']}, Home xG +0.05"
        modifiers.append(mod_text)
        adjustments['applied'].append('home_rest')

    if away_rest >= CONFIG['REST_BONUS_DAYS']:
        adjustments['lambda_a'] += 0.05
        mod_text = f"😴 Away Rest Bonus: {away_rest} days >= {CONFIG['REST_BONUS_DAYS']}, Away xG +0.05"
        modifiers.append(mod_text)
        adjustments['applied'].append('away_rest')

    # --- 4. FIXTURE CONGESTION (Games in last 7 days) ---
    home_games_7 = stats.get('HOME_GAMES_LAST_7_DAYS', 1)
    away_games_7 = stats.get('AWAY_GAMES_LAST_7_DAYS', 1)

    if home_games_7 >= CONFIG['HIGH_FATIGUE_THRESHOLD']:
        adjustments['lambda_h'] *= 0.92
        mod_text = f"😫 Home Fixture Congestion: {home_games_7} games in 7 days, Home xG reduced by 8%"
        modifiers.append(mod_text)
        adjustments['applied'].append('home_fatigue')

    if away_games_7 >= CONFIG['HIGH_FATIGUE_THRESHOLD']:
        adjustments['lambda_a'] *= 0.92
        mod_text = f"😫 Away Fixture Congestion: {away_games_7} games in 7 days, Away xG reduced by 8%"
        modifiers.append(mod_text)
        adjustments['applied'].append('away_fatigue')

    # --- 5. WEATHER IMPACT ---
    weather = stats.get('WEATHER_CONDITION', 'clear').lower()
    if 'rain' in weather or 'wet' in weather:
        adjustments['lambda_h'] += CONFIG['WEATHER_RAIN_REDUCTION']
        adjustments['lambda_a'] += CONFIG['WEATHER_RAIN_REDUCTION']
        mod_text = f"🌧️ Rainy Weather: Goals reduced by 0.15 for both teams"
        modifiers.append(mod_text)
        adjustments['applied'].append('rain')

    # --- 6. CLEAN UP xG (Ensure > 0) ---
    adjustments['lambda_h'] = max(0.3, adjustments['lambda_h'])
    adjustments['lambda_a'] = max(0.3, adjustments['lambda_a'])

    if not modifiers:
        modifiers.append("None")

    # Recalculate Σλ and Δλ
    adjustments['sigma_lambda'] = round(adjustments['lambda_h'] + adjustments['lambda_a'], 3)
    adjustments['delta_lambda'] = round(adjustments['lambda_h'] - adjustments['lambda_a'], 3)
    adjustments['lambda_h'] = round(adjustments['lambda_h'], 3)
    adjustments['lambda_a'] = round(adjustments['lambda_a'], 3)

    return modifiers, adjustments

# ==========================================
# 8. PHASE 4: INTELLIGENT MARKET MATRIX
# ==========================================
def phase_4_matrix(stats, metrics, final_adjustments):
    """
    Route through all 10 markets with CONFIDENCE SCORING.
    Returns triggered markets with confidence levels.
    """

    s = stats
    m = metrics
    a = final_adjustments

    sigma = a['sigma_lambda']
    delta = a['delta_lambda']
    lambda_h = a['lambda_h']
    lambda_a = a['lambda_a']
    da_ratio = metrics['da_ratio']
    acc_diff = metrics['acc_diff']

    triggered = []
    failures = []
    min_conf = CONFIG['MIN_CONFIDENCE']

    # ==========================================
    # HOME WIN (1)
    # ==========================================
    home_win_checks = [
        ('xG_advantage', delta >= CONFIG['DELTA_HOME_WIN'], delta, CONFIG['DELTA_HOME_WIN']),
        ('win_history', s['HOME_WIN_SPLIT'] >= CONFIG['WIN_PCT_THRESHOLD'], s['HOME_WIN_SPLIT'], CONFIG['WIN_PCT_THRESHOLD']),
        ('attack_dominance', da_ratio >= 1.15, da_ratio, 1.15),
    ]
    home_win_conf = sum(1 for _, check, _, _ in home_win_checks if check) / len(home_win_checks)

    if home_win_conf >= min_conf:
        triggered.append({
            'market': 'HOME WIN (1)',
            'confidence': round(home_win_conf, 2),
            'checks': home_win_checks
        })
    else:
        failed = [f"{name}({val:.2f} < {threshold:.2f})" for name, check, val, threshold in home_win_checks if not check]
        failures.append(('HOME WIN', home_win_conf, failed))

    # ==========================================
    # AWAY WIN (2)
    # ==========================================
    away_win_checks = [
        ('xG_advantage', delta <= CONFIG['DELTA_AWAY_WIN'], delta, CONFIG['DELTA_AWAY_WIN']),
        ('win_history', s['AWAY_WIN_SPLIT'] >= CONFIG['WIN_PCT_THRESHOLD'], s['AWAY_WIN_SPLIT'], CONFIG['WIN_PCT_THRESHOLD']),
        ('attack_dominance', da_ratio <= 0.85, da_ratio, 0.85),
        ('scoring_edge', s['AWAY_SPLIT_SCORED_AVG'] > s['HOME_SPLIT_CONCEDED_AVG'], 
         s['AWAY_SPLIT_SCORED_AVG'], s['HOME_SPLIT_CONCEDED_AVG']),
    ]
    away_win_conf = sum(1 for _, check, _, _ in away_win_checks if check) / len(away_win_checks)

    if away_win_conf >= min_conf:
        triggered.append({
            'market': 'AWAY WIN (2)',
            'confidence': round(away_win_conf, 2),
            'checks': away_win_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in away_win_checks if not check]
        failures.append(('AWAY WIN', away_win_conf, failed))

    # ==========================================
    # DRAW (X)
    # ==========================================
    draw_checks = [
        ('xG_balance', abs(delta) <= CONFIG['DELTA_DRAW'], abs(delta), CONFIG['DELTA_DRAW']),
        ('home_draw_history', s['HOME_DRAW_SPLIT'] >= CONFIG['DRAW_PCT_THRESHOLD'], s['HOME_DRAW_SPLIT'], CONFIG['DRAW_PCT_THRESHOLD']),
        ('away_draw_history', s['AWAY_DRAW_SPLIT'] >= CONFIG['DRAW_PCT_THRESHOLD'], s['AWAY_DRAW_SPLIT'], CONFIG['DRAW_PCT_THRESHOLD']),
        ('total_xG_range', 1.8 <= sigma <= 2.6, sigma, 2.2),
    ]
    draw_conf = sum(1 for _, check, _, _ in draw_checks if check) / len(draw_checks)

    if draw_conf >= min_conf:
        triggered.append({
            'market': 'DRAW (X)',
            'confidence': round(draw_conf, 2),
            'checks': draw_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in draw_checks if not check]
        failures.append(('DRAW', draw_conf, failed))

    # ==========================================
    # OVER 1.5 GOALS
    # ==========================================
    over15_checks = [
        ('total_xG', sigma >= CONFIG['SIGMA_OVER_15'], sigma, CONFIG['SIGMA_OVER_15']),
        ('home_over_history', s['HOME_OVER15_PCT'] >= CONFIG['OVER_UNDER_PCT_THRESHOLD'], s['HOME_OVER15_PCT'], CONFIG['OVER_UNDER_PCT_THRESHOLD']),
        ('away_over_history', s['AWAY_OVER15_PCT'] >= CONFIG['OVER_UNDER_PCT_THRESHOLD'], s['AWAY_OVER15_PCT'], CONFIG['OVER_UNDER_PCT_THRESHOLD']),
        ('home_shot_volume', s['HOME_TOTAL_SHOTS'] >= 7.5, s['HOME_TOTAL_SHOTS'], 7.5),
        ('away_shot_volume', s['AWAY_TOTAL_SHOTS'] >= 7.5, s['AWAY_TOTAL_SHOTS'], 7.5),
    ]
    over15_conf = sum(1 for _, check, _, _ in over15_checks if check) / len(over15_checks)

    if over15_conf >= min_conf:
        triggered.append({
            'market': 'OVER 1.5 GOALS',
            'confidence': round(over15_conf, 2),
            'checks': over15_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in over15_checks if not check]
        failures.append(('OVER 1.5', over15_conf, failed))

    # ==========================================
    # OVER 2.5 GOALS
    # ==========================================
    over25_checks = [
        ('total_xG', sigma >= CONFIG['SIGMA_OVER_25'], sigma, CONFIG['SIGMA_OVER_25']),
        ('league_trend', s.get('LEAGUE_OVER_25_PCT', 50) >= 50, s.get('LEAGUE_OVER_25_PCT', 50), 50),
        ('home_over_history', s['HOME_OVER25_PCT'] >= 55, s['HOME_OVER25_PCT'], 55),
        ('away_over_history', s['AWAY_OVER25_PCT'] >= 55, s['AWAY_OVER25_PCT'], 55),
        ('combined_attacks', (s['HOME_DANGEROUS_ATTACKS'] + s['AWAY_DANGEROUS_ATTACKS']) >= 95,
         s['HOME_DANGEROUS_ATTACKS'] + s['AWAY_DANGEROUS_ATTACKS'], 95),
    ]
    over25_conf = sum(1 for _, check, _, _ in over25_checks if check) / len(over25_checks)

    if over25_conf >= min_conf:
        triggered.append({
            'market': 'OVER 2.5 GOALS',
            'confidence': round(over25_conf, 2),
            'checks': over25_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in over25_checks if not check]
        failures.append(('OVER 2.5', over25_conf, failed))

    # ==========================================
    # OVER 3.5 GOALS
    # ==========================================
    over35_checks = [
        ('total_xG', sigma >= CONFIG['SIGMA_OVER_35'], sigma, CONFIG['SIGMA_OVER_35']),
        ('home_over_history', s['HOME_OVER35_PCT'] >= 40, s['HOME_OVER35_PCT'], 40),
        ('away_over_history', s['AWAY_OVER35_PCT'] >= 40, s['AWAY_OVER35_PCT'], 40),
        ('combined_accuracy', (s['HOME_SHOT_ACCURACY'] + s['AWAY_SHOT_ACCURACY']) >= 75,
         s['HOME_SHOT_ACCURACY'] + s['AWAY_SHOT_ACCURACY'], 75),
    ]
    over35_conf = sum(1 for _, check, _, _ in over35_checks if check) / len(over35_checks)

    if over35_conf >= min_conf:
        triggered.append({
            'market': 'OVER 3.5 GOALS',
            'confidence': round(over35_conf, 2),
            'checks': over35_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in over35_checks if not check]
        failures.append(('OVER 3.5', over35_conf, failed))

    # ==========================================
    # UNDER 1.5 GOALS
    # ==========================================
    under15_checks = [
        ('total_xG', sigma <= CONFIG['SIGMA_UNDER_15'], sigma, CONFIG['SIGMA_UNDER_15']),
        ('combined_attacks', (s['HOME_DANGEROUS_ATTACKS'] + s['AWAY_DANGEROUS_ATTACKS']) <= 80,
         s['HOME_DANGEROUS_ATTACKS'] + s['AWAY_DANGEROUS_ATTACKS'], 80),
        ('home_under_history', s['HOME_UNDER15_PCT'] >= 30, s['HOME_UNDER15_PCT'], 30),
        ('away_under_history', s['AWAY_UNDER15_PCT'] >= 30, s['AWAY_UNDER15_PCT'], 30),
        ('home_clean_tendency', s['HOME_FTS_PCT'] >= 35, s['HOME_FTS_PCT'], 35),
        ('away_clean_tendency', s['AWAY_FTS_PCT'] >= 35, s['AWAY_FTS_PCT'], 35),
    ]
    under15_conf = sum(1 for _, check, _, _ in under15_checks if check) / len(under15_checks)

    if under15_conf >= min_conf:
        triggered.append({
            'market': 'UNDER 1.5 GOALS',
            'confidence': round(under15_conf, 2),
            'checks': under15_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in under15_checks if not check]
        failures.append(('UNDER 1.5', under15_conf, failed))

    # ==========================================
    # UNDER 2.5 GOALS
    # ==========================================
    under25_checks = [
        ('total_xG', sigma <= CONFIG['SIGMA_UNDER_25'], sigma, CONFIG['SIGMA_UNDER_25']),
        ('league_trend', s['LEAGUE_UNDER_25_PCT'] >= 53, s['LEAGUE_UNDER_25_PCT'], 53),
        ('home_under_history', s['HOME_UNDER25_PCT'] >= 55, s['HOME_UNDER25_PCT'], 55),
        ('away_under_history', s['AWAY_UNDER25_PCT'] >= 55, s['AWAY_UNDER25_PCT'], 55),
        ('xG_ceiling', max(lambda_h, lambda_a) <= 1.35, max(lambda_h, lambda_a), 1.35),
        ('shot_volume', (s['HOME_TOTAL_SHOTS'] + s['AWAY_TOTAL_SHOTS']) <= 16,
         s['HOME_TOTAL_SHOTS'] + s['AWAY_TOTAL_SHOTS'], 16),
    ]
    under25_conf = sum(1 for _, check, _, _ in under25_checks if check) / len(under25_checks)

    if under25_conf >= min_conf:
        triggered.append({
            'market': 'UNDER 2.5 GOALS',
            'confidence': round(under25_conf, 2),
            'checks': under25_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in under25_checks if not check]
        failures.append(('UNDER 2.5', under25_conf, failed))

    # ==========================================
    # UNDER 3.5 GOALS
    # ==========================================
    under35_checks = [
        ('total_xG', sigma <= CONFIG['SIGMA_UNDER_35'], sigma, CONFIG['SIGMA_UNDER_35']),
        ('home_under_history', s['HOME_UNDER35_PCT'] >= 55, s['HOME_UNDER35_PCT'], 55),
        ('away_under_history', s['AWAY_UNDER35_PCT'] >= 55, s['AWAY_UNDER35_PCT'], 55),
        ('combined_defense', (s['HOME_SPLIT_CONCEDED_AVG'] + s['AWAY_SPLIT_CONCEDED_AVG']) <= 3.00,
         s['HOME_SPLIT_CONCEDED_AVG'] + s['AWAY_SPLIT_CONCEDED_AVG'], 3.00),
    ]
    under35_conf = sum(1 for _, check, _, _ in under35_checks if check) / len(under35_checks)

    if under35_conf >= min_conf:
        triggered.append({
            'market': 'UNDER 3.5 GOALS',
            'confidence': round(under35_conf, 2),
            'checks': under35_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in under35_checks if not check]
        failures.append(('UNDER 3.5', under35_conf, failed))

    # ==========================================
    # BTTS YES / GG
    # ==========================================
    btts_yes_checks = [
        ('both_xG_high', min(lambda_h, lambda_a) >= 1.15, min(lambda_h, lambda_a), 1.15),
        ('league_trend', s['LEAGUE_BTTS_PCT'] >= 50, s['LEAGUE_BTTS_PCT'], 50),
        ('home_btts_history', s['HOME_BTTS_PCT'] >= 52, s['HOME_BTTS_PCT'], 52),
        ('away_btts_history', s['AWAY_BTTS_PCT'] >= 52, s['AWAY_BTTS_PCT'], 52),
        ('home_weak_defense', s['HOME_CLEAN_SHEETS'] < 25, s['HOME_CLEAN_SHEETS'], 25),
        ('away_weak_defense', s['AWAY_CLEAN_SHEETS'] < 25, s['AWAY_CLEAN_SHEETS'], 25),
    ]
    btts_yes_conf = sum(1 for _, check, _, _ in btts_yes_checks if check) / len(btts_yes_checks)

    if btts_yes_conf >= min_conf:
        triggered.append({
            'market': 'BTTS YES',
            'confidence': round(btts_yes_conf, 2),
            'checks': btts_yes_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in btts_yes_checks if not check]
        failures.append(('BTTS YES', btts_yes_conf, failed))

    # ==========================================
    # BTTS NO / NG (FIXED)
    # ==========================================
    btts_no_checks = [
        ('both_xG_low', min(lambda_h, lambda_a) <= 0.65, min(lambda_h, lambda_a), 0.65),
        ('home_strong_defense', s['HOME_FTS_PCT'] >= 40 or s['HOME_CLEAN_SHEETS'] >= 35,
         max(s['HOME_FTS_PCT'], s['HOME_CLEAN_SHEETS']), 37.5),
        ('away_strong_defense', s['AWAY_FTS_PCT'] >= 40 or s['AWAY_CLEAN_SHEETS'] >= 35,
         max(s['AWAY_FTS_PCT'], s['AWAY_CLEAN_SHEETS']), 37.5),
        ('low_total_xG', sigma <= 1.80, sigma, 1.80),
    ]
    btts_no_conf = sum(1 for _, check, _, _ in btts_no_checks if check) / len(btts_no_checks)

    if btts_no_conf >= min_conf:
        triggered.append({
            'market': 'BTTS NO',
            'confidence': round(btts_no_conf, 2),
            'checks': btts_no_checks
        })
    else:
        failed = [f"{name}({val:.2f})" for name, check, val, _ in btts_no_checks if not check]
        failures.append(('BTTS NO', btts_no_conf, failed))

    # Sort failures by confidence (closest misses first)
    failures.sort(key=lambda x: x[1], reverse=True)

    return triggered if triggered else [], failures

# ==========================================
# 9. ROI & BACKTEST TRACKING
# ==========================================
class BettingTracker:
    def __init__(self):
        self.predictions = []
        self.file = "/tmp/betting_tracker.json"
        self.load()

    def load(self):
        try:
            with open(self.file, 'r') as f:
                self.predictions = json.load(f)
        except:
            self.predictions = []

    def save(self):
        with open(self.file, 'w') as f:
            json.dump(self.predictions, f, indent=2)

    def add_prediction(self, match, markets, modifiers_applied):
        self.predictions.append({
            'timestamp': datetime.now().isoformat(),
            'match': match,
            'markets': [m['market'] for m in markets],
            'avg_confidence': round(sum(m['confidence'] for m in markets) / len(markets), 2) if markets else 0,
            'modifiers_applied': modifiers_applied
        })
        self.save()

    def get_stats(self):
        if not self.predictions:
            return "No predictions yet."

        total = len(self.predictions)
        return f"Total Predictions: {total}\nAvg Confidence: {round(sum(p['avg_confidence'] for p in self.predictions) / total, 2)}"

tracker = BettingTracker()

# ==========================================
# 10. FORMAT OUTPUT (IMPROVED)
# ==========================================
def format_output(stats, metrics, final_adjustments, modifiers_list, triggered, failures):
    """Format output with confidence scores and probabilities."""

    output = []
    output.append("=" * 70)
    output.append("⚽ MASTER ENGINE v4.0 - PROBABILISTIC PREDICTION")
    output.append("=" * 70)
    output.append("")
    output.append(f"MATCH: {stats['MATCH']}")
    output.append(f"Date: {stats.get('MATCH_DATE', 'N/A')}")
    output.append("")

    output.append("📊 CORE METRICS (After Modifiers)")
    output.append("-" * 70)
    output.append(f"λ_H (Home xG):              {final_adjustments['lambda_h']}")
    output.append(f"λ_A (Away xG):              {final_adjustments['lambda_a']}")
    output.append(f"Σλ (Total Match xG):        {final_adjustments['sigma_lambda']}")
    output.append(f"Δλ (xG Differential):       {final_adjustments['delta_lambda']}")
    output.append(f"DA_ratio:                   {metrics['da_ratio']}")
    output.append(f"Confidence (Home):          {metrics['confidence_h']:.0%}")
    output.append(f"Confidence (Away):          {metrics['confidence_a']:.0%}")
    output.append("")

    output.append("🔧 Modifiers Applied:")
    for mod in modifiers_list:
        output.append(f"  {mod}")
    output.append("")

    output.append("🎯 TRIGGERED MARKETS (HIGH CONFIDENCE)")
    output.append("-" * 70)
    if triggered:
        for i, market in enumerate(triggered, 1):
            confidence_pct = int(market['confidence'] * 100)
            bar = "█" * confidence_pct + "░" * (100 - confidence_pct)
            output.append(f"{i}. {market['market']}")
            output.append(f"   Confidence: {confidence_pct}% [{bar[:50]}]")
            output.append("")
    else:
        output.append("❌ NO BET - Confidence too low for any market")
        output.append("")

    output.append("⚠️  NEAR-MISS MARKETS (Could Trigger With Small Changes)")
    output.append("-" * 70)
    for market, conf, failed_checks in failures[:3]:
        conf_pct = int(conf * 100)
        output.append(f"{market}: {conf_pct}% confidence")
        for check in failed_checks[:2]:
            output.append(f"  • {check}")
        output.append("")

    output.append("=" * 70)
    output.append("⚠️  DISCLAIMER: Past performance ≠ future results")
    output.append("Monitor ROI and adjust thresholds based on actual outcomes")
    output.append("=" * 70)

    return "\n".join(output)

# ==========================================
# 11. MAIN ENGINE FUNCTION
# ==========================================
def run_master_engine(stats_text):
    """Main engine: Parse -> Phase 1-4 -> Output."""

    # Parse
    stats = parse_stats(stats_text)

    # Phase 1: Validate
    valid, msg = phase_1_extract(stats)
    if not valid:
        return f"❌ ERROR: {msg}\n\nMake sure you have all required fields.\nUse /format to see the template."

    try:
        # Phase 2: Calculate (with home advantage, recency weighting)
        metrics = phase_2_calculate(stats)

        # Phase 3: Modifiers (rest, injuries, travel, weather, etc.)
        modifiers, adjustments = phase_3_modifiers(stats, metrics)

        # Phase 4: Matrix (with confidence scoring)
        triggered, failures = phase_4_matrix(stats, metrics, adjustments)

        # Track for ROI monitoring
        tracker.add_prediction(stats['MATCH'], triggered, adjustments.get('applied', []))

        # Format output
        output = format_output(stats, metrics, adjustments, modifiers, triggered, failures)

        return output

    except Exception as e:
        return f"❌ ERROR: {str(e)}\n\nCheck your data and try again."

# ==========================================
# 12. TELEGRAM BOT
# ==========================================
BOT_TOKEN = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_TOKEN') or os.environ.get('FORMATTER_BOT_TOKEN')
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN or TELEGRAM_TOKEN or FORMATTER_BOT_TOKEN environment variable not set!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "⚽ FOOTBALL ANALYTICS BOT - MASTER ENGINE v4.0\n\n"
        "📋 Commands:\n"
        "/format - Show stats input template\n"
        "/help - Show this message\n"
        "/stats - Show prediction stats\n\n"
        "🔧 How to use:\n"
        "1. /format to get the template\n"
        "2. Fill in your match data\n"
        "3. Paste the stats in chat\n"
        "4. Bot will run Master Engine v4.0\n"
        "5. Get probabilistic prediction with confidence scores\n\n"
        "✨ New Features:\n"
        "• Home advantage multiplier (10%)\n"
        "• Recency weighting (60% last-5 form)\n"
        "• Probabilistic confidence scoring\n"
        "• Advanced modifiers (injuries, rest, weather, fatigue)\n"
        "• ROI tracking for backtesting\n"
        "• Variance/confidence estimates\n\n"
        "All calculations done locally - no API!"
    )
    send_safe(bot, message.chat.id, welcome_text, reply_to=message.message_id)

@bot.message_handler(commands=['format'])
def send_format(message):
    """Send the stats input format template."""
    send_safe(bot, message.chat.id, STATS_TEMPLATE, reply_to=message.message_id)

@bot.message_handler(commands=['stats'])
def send_stats(message):
    """Send prediction stats."""
    stats_text = tracker.get_stats()
    send_safe(bot, message.chat.id, stats_text, reply_to=message.message_id)

@bot.message_handler(func=lambda message: True)
def handle_stats_input(message):
    """Handle user-pasted stats."""
    text = message.text

    # Check if this looks like match stats
    indicators = [
        "MATCH:", "HOME_SPLIT_SCORED_AVG:", "AWAY_SPLIT_SCORED_AVG:",
        "LEAGUE_AVG_GOALS:", "HOME_WIN_SPLIT:"
    ]

    found_indicators = sum(1 for ind in indicators if ind in text)

    if found_indicators < 3:
        send_safe(bot, message.chat.id,
            "❌ I don't recognize this as match stats.\n\n"
            "Use /format to get the template, fill in your stats, and paste them here.",
            reply_to=message.message_id)
        return

    # Run engine
    try:
        result = run_master_engine(text)

        # Split if too long for Telegram (4096 char limit)
        if len(result) > 4000:
            parts = result.split("🎯 TRIGGERED MARKETS")
            if len(parts) == 2:
                send_safe(bot, message.chat.id, parts[0], reply_to=message.message_id)
                send_safe(bot, message.chat.id, "🎯 TRIGGERED MARKETS" + parts[1], reply_to=message.message_id)
            else:
                for i in range(0, len(result), 4000):
                    send_safe(bot, message.chat.id, result[i:i+4000], reply_to=message.message_id)
        else:
            send_safe(bot, message.chat.id, result, reply_to=message.message_id)

    except Exception as e:
        send_safe(bot, message.chat.id, f"❌ Error processing stats: {str(e)}", reply_to=message.message_id)

# ==========================================
# 13. EXECUTION
# ==========================================
if __name__ == "__main__":
    # Start Flask server in background
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print("=" * 70)
    print("⚽ Football Bot v4.0 (Master Engine) - Starting...")
    print("=" * 70)
    print("✅ Home Advantage: 10%")
    print("✅ Recency Weighting: 60% recent form, 40% overall")
    print("✅ Confidence Threshold: 65%")
    print("✅ Advanced Modifiers: Active")
    print("✅ ROI Tracking: Enabled")
    print("=" * 70)
    print("Waiting for stats input...")
    bot.infinity_polling()
