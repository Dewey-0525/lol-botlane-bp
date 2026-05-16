#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bp_engine.py - 下路 BP 推荐核心

这个模块不负责命令行打印，也不负责 Web 框架。
它只接收 BP 状态和本地数据库，返回适合 CLI/API/Web 使用的结构化结果。
"""

import math

from champion_tags import CHAMPION_TAGS, COMBO_TAG_OVERRIDES

VALID_ROLES = ("support", "adc")
VALID_KEYS = ("ally_ad", "ally_sup", "enemy_ad", "enemy_sup")
TIME_BUCKETS = ("0-20", "20-25", "25-30", "30-35", "35+")

TIER_SCORES = {
    "S+": 57,
    "S": 55,
    "S-": 53,
    "A+": 51,
    "A": 49,
    "A-": 47,
    "B+": 45,
    "B": 43,
    "B-": 41,
    "C+": 39,
    "C": 37,
    "C-": 35,
    "D+": 33,
    "D": 31,
    "D-": 29,
}

TIER_PRIOR_RATINGS = {
    "S+": 18,
    "S": 14,
    "S-": 10,
    "A+": 7,
    "A": 4,
    "A-": 2,
    "B+": 0,
    "B": -2,
    "B-": -4,
    "C+": -6,
    "C": -8,
    "C-": -10,
    "D+": -12,
    "D": -14,
    "D-": -16,
}

EVIDENCE_LABELS = {
    "counter": "对位",
    "synergy": "配合",
    "base": "基础表现",
}

MISSING_FIELD_LABELS = {
    "counter": "克制数据不足",
    "counter_partial": "部分克制数据不足",
    "synergy": "配合数据不足",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "very_low": "极低",
}

HERO_PRIOR_GAMES = 1000
HERO_CONFIDENCE_GAMES = 3000
TIME_PRIOR_GAMES = 500
DUO_PRIOR_GAMES = 1000
MATCHUP_PRIOR_GAMES = 1000
LEGACY_RELATION_GAMES = 300
RELATION_CONFIDENCE_GAMES = 1500
SYNERGY_ABSOLUTE_WEIGHT = 0.55
SYNERGY_RESIDUAL_WEIGHT = 0.25
COUNTER_ABSOLUTE_WEIGHT = 0.55
COUNTER_RESIDUAL_WEIGHT = 0.25
RELATION_MATURITY_WEIGHT = 0.20
RELATION_MATURITY_REFERENCE_GAMES = 3000
RELATION_LOCAL_MATURITY_FACTOR = 0.85
RELATION_MATURITY_MIN_WINRATE = 0.485
MATURE_RELATION_FLOOR_GAMES = 10000
MATURE_RELATION_FLOOR_WINRATE = 0.49
MATURE_RELATION_FLOOR_MIN_GAP = -1.0
MATURE_RELATION_FLOOR_SCORE = 3.0
COMPONENT_CALIBRATION_SCALE = 30.0
COMPONENT_CALIBRATION_LIMIT = 30.0
STRONG_RELATION_WINRATE = 0.54
STRONG_RELATION_GAMES = 1000
STRONG_SYNERGY_FLOOR = 8.0
STRONG_COUNTER_FLOOR = 6.0
MISSING_SYNERGY_PENALTY = -6.0
MISSING_COUNTER_PENALTY = -4.0
MIN_WINRATE = 0.001
MAX_WINRATE = 0.999

STATE_INFO = {
    "S1": {
        "label": "盲选",
        "description": "还不知道队友和对手，先推荐当前版本表现稳定的英雄。",
    },
    "S2": {
        "label": "已选队友",
        "description": "会优先看这个英雄和己方下路搭档过去配得好不好。",
    },
    "S3": {
        "label": "已知敌方 ADC",
        "description": "会优先看这个英雄打敌方 ADC 时表现好不好。",
    },
    "S4": {
        "label": "已知敌方辅助",
        "description": "会优先看这个英雄面对敌方辅助时表现好不好。",
    },
    "S5": {
        "label": "已知敌方下路",
        "description": "会看这个英雄打敌方 ADC 和辅助时，整体是不是更好打。",
    },
    "S6": {
        "label": "队友 + 敌方 ADC",
        "description": "会同时看和队友是否好配，以及打敌方 ADC 是否舒服。",
    },
    "S7": {
        "label": "队友 + 敌方辅助",
        "description": "会同时看和队友是否好配，以及面对敌方辅助是否舒服。",
    },
    "S8": {
        "label": "下路信息完整",
        "description": "会一起看三件事：英雄本身强不强、和队友搭不搭、打敌方下路好不好打。",
    },
}


def normalize_weights(weight_map):
    total = sum(weight_map.values())
    if total <= 0:
        return {key: 0.0 for key in weight_map}
    return {key: value / total for key, value in weight_map.items()}


def round_weights(weight_map):
    return {key: round(value, 4) for key, value in weight_map.items()}


def clamp_winrate(winrate):
    if winrate is None:
        return None
    winrate = float(winrate)
    if winrate > 1:
        winrate = winrate / 100
    return min(MAX_WINRATE, max(MIN_WINRATE, winrate))


def rating_to_winrate(rating):
    return 1 / (1 + pow(10, -rating / 400))


def winrate_to_rating(winrate):
    winrate = clamp_winrate(winrate)
    return -400 * math.log10(1 / winrate - 1)


def smooth_winrate(wins, games, prior_winrate, prior_games):
    prior_winrate = clamp_winrate(prior_winrate)
    games = max(0, float(games or 0))
    wins = max(0, float(wins or 0))
    prior_games = max(0, float(prior_games or 0))
    if games + prior_games <= 0:
        return prior_winrate
    return clamp_winrate((wins + prior_games * prior_winrate) / (games + prior_games))


def relation_confidence(games):
    return min(1, math.sqrt(max(0, float(games or 0)) / RELATION_CONFIDENCE_GAMES))


def relation_maturity_score(games, local_top_games=0, actual_absolute_winrate=None):
    if actual_absolute_winrate is not None and actual_absolute_winrate < RELATION_MATURITY_MIN_WINRATE:
        return 0.0
    games = max(0, float(games or 0))
    if games <= 0:
        return 0.0
    global_maturity = min(
        10.0,
        math.log1p(games) / math.log1p(RELATION_MATURITY_REFERENCE_GAMES) * 10.0,
    )
    local_top_games = max(0, float(local_top_games or 0))
    if local_top_games <= 0:
        return global_maturity
    local_maturity = min(10.0, games / local_top_games * 10.0)
    return max(global_maturity, RELATION_LOCAL_MATURITY_FACTOR * local_maturity)


def calibrated_component_score(value):
    return math.tanh(float(value or 0) / COMPONENT_CALIBRATION_SCALE) * COMPONENT_CALIBRATION_LIMIT


def blended_relation_score(
    stat,
    expected_rating,
    absolute_weight,
    residual_weight,
    prior_games,
    positive_floor=0.0,
    local_top_games=0,
):
    if stat["games"] <= 0:
        expected_winrate = rating_to_winrate(expected_rating)
        return {
            "score": 0.0,
            "absolute_score": 0.0,
            "residual_score": 0.0,
            "confidence": 0.0,
            "expected_winrate": expected_winrate,
            "actual_winrate": None,
            "actual_absolute_winrate": None,
            "maturity_score": 0.0,
        }

    expected_winrate = rating_to_winrate(expected_rating)
    actual_winrate = smooth_winrate(
        stat["wins"], stat["games"], expected_winrate, prior_games
    )
    actual_absolute_winrate = smooth_winrate(
        stat["wins"], stat["games"], 0.5, prior_games
    )
    actual_rating = winrate_to_rating(actual_winrate)
    actual_absolute_rating = winrate_to_rating(actual_absolute_winrate)
    absolute_score = actual_absolute_rating
    residual_score = actual_rating - expected_rating
    maturity_score = relation_maturity_score(
        stat["games"], local_top_games, actual_absolute_winrate
    )
    confidence = relation_confidence(stat["games"])
    score = confidence * (
        absolute_weight * absolute_score
        + residual_weight * residual_score
        + RELATION_MATURITY_WEIGHT * maturity_score
    )

    raw_winrate = stat["wins"] / stat["games"] if stat["games"] > 0 else None
    if (
        raw_winrate is not None
        and raw_winrate >= STRONG_RELATION_WINRATE
        and stat["games"] >= STRONG_RELATION_GAMES
        and positive_floor > 0
    ):
        score = max(score, positive_floor)
    if (
        actual_absolute_winrate >= MATURE_RELATION_FLOOR_WINRATE
        and stat["games"] >= MATURE_RELATION_FLOOR_GAMES
        and (actual_winrate - expected_winrate) * 100 >= MATURE_RELATION_FLOOR_MIN_GAP
    ):
        score = max(score, MATURE_RELATION_FLOOR_SCORE)

    return {
        "score": score,
        "absolute_score": absolute_score,
        "residual_score": residual_score,
        "confidence": confidence,
        "expected_winrate": expected_winrate,
        "actual_winrate": actual_winrate,
        "actual_absolute_winrate": actual_absolute_winrate,
        "maturity_score": maturity_score,
    }


def extract_stat(raw_value, legacy_games=0):
    if raw_value is None:
        return {"winrate": None, "games": 0, "wins": 0}
    if isinstance(raw_value, dict):
        winrate = clamp_winrate(
            raw_value.get("win_rate", raw_value.get("wr", raw_value.get("winrate")))
        )
        games = raw_value.get("games", raw_value.get("matches", raw_value.get("n", 0)))
        wins = raw_value.get("wins")
    else:
        winrate = clamp_winrate(raw_value)
        games = legacy_games
        wins = None

    games = max(0, float(games or 0))
    if wins is None and winrate is not None:
        wins = winrate * games
    return {"winrate": winrate, "games": games, "wins": float(wins or 0)}


def get_relation_stat(matrix, champion, target):
    direct = matrix.get(champion, {}).get(target)
    if direct is not None:
        return direct
    return matrix.get(target, {}).get(champion)


def top_relation_games(matrix, champion):
    top_games = 0
    for target, raw in matrix.get(champion, {}).items():
        top_games = max(top_games, int(extract_stat(raw, legacy_games=LEGACY_RELATION_GAMES)["games"]))
    for source, targets in matrix.items():
        if source == champion:
            continue
        raw = targets.get(champion)
        if raw is not None:
            top_games = max(top_games, int(extract_stat(raw, legacy_games=LEGACY_RELATION_GAMES)["games"]))
    return top_games


def get_hero_stat(db, lane, champion):
    hero_stats = db.get("hero_stats", {})
    lane_stats = hero_stats.get(lane, {})
    return extract_stat(lane_stats.get(champion), legacy_games=0)


def get_hero_stats_record(db, lane, champion):
    return db.get("hero_stats", {}).get(lane, {}).get(champion, {})


def calculate_base_rating(db, lane, champion, tier_label=None):
    stat = get_hero_stat(db, lane, champion)
    base_winrate = smooth_winrate(
        stat["wins"], stat["games"], 0.5, HERO_PRIOR_GAMES
    )
    raw_rating = winrate_to_rating(base_winrate)
    confidence_multiplier = min(1, math.sqrt(stat["games"] / HERO_CONFIDENCE_GAMES))
    winrate_component = raw_rating * confidence_multiplier
    tier_prior = TIER_PRIOR_RATINGS.get(tier_label, 0)
    return {
        "base_winrate": base_winrate,
        "raw_base_rating": raw_rating,
        "base_rating": tier_prior + winrate_component,
        "expectation_rating": winrate_component,
        "tier_prior_rating": tier_prior,
        "winrate_component": winrate_component,
        "base_confidence_multiplier": confidence_multiplier,
        "base_games": int(stat["games"]),
    }


def calculate_synergy_bonus(
    db, candidate, partner, candidate_expectation_rating, partner_expectation_rating
):
    if not partner:
        return {
            "synergy_bonus": 0.0,
            "synergy_absolute_score": 0.0,
            "synergy_residual_score": 0.0,
            "synergy_maturity_score": 0.0,
            "synergy_confidence": 0.0,
            "duo_games": 0,
            "expected_duo_winrate": None,
            "actual_duo_winrate": None,
            "actual_duo_absolute_winrate": None,
        }

    expected_rating = candidate_expectation_rating + partner_expectation_rating
    synergy_matrix = db.get("synergy", {})
    stat = extract_stat(
        get_relation_stat(synergy_matrix, partner, candidate),
        legacy_games=LEGACY_RELATION_GAMES,
    )
    blended = blended_relation_score(
        stat,
        expected_rating,
        SYNERGY_ABSOLUTE_WEIGHT,
        SYNERGY_RESIDUAL_WEIGHT,
        DUO_PRIOR_GAMES,
        STRONG_SYNERGY_FLOOR,
        local_top_games=top_relation_games(synergy_matrix, partner),
    )
    return {
        "synergy_bonus": blended["score"],
        "synergy_absolute_score": blended["absolute_score"],
        "synergy_residual_score": blended["residual_score"],
        "synergy_maturity_score": blended["maturity_score"],
        "synergy_confidence": blended["confidence"],
        "duo_games": int(stat["games"]),
        "expected_duo_winrate": blended["expected_winrate"],
        "actual_duo_winrate": blended["actual_winrate"],
        "actual_duo_absolute_winrate": blended["actual_absolute_winrate"],
    }


def get_matchup_stat(db, candidate, enemy, enemy_role):
    matchup_key = "vs_adc" if enemy_role == "adc" else "vs_sup"
    return extract_stat(
        db.get("counter", {}).get(candidate, {}).get(matchup_key, {}).get(enemy),
        legacy_games=LEGACY_RELATION_GAMES,
    )


def top_matchup_games(db, candidate, enemy_role):
    matchup_key = "vs_adc" if enemy_role == "adc" else "vs_sup"
    top_games = 0
    for raw in db.get("counter", {}).get(candidate, {}).get(matchup_key, {}).values():
        top_games = max(top_games, int(extract_stat(raw, legacy_games=LEGACY_RELATION_GAMES)["games"]))
    return top_games


def counter_role_weight(recommend_role, enemy_role):
    if recommend_role == "support":
        return 1.35 if enemy_role == "support" else 1.0
    if recommend_role == "adc":
        return 1.35 if enemy_role == "adc" else 1.0
    return 1.0


def calculate_counter_bonus(db, candidate, enemies, candidate_expectation_rating, role=None):
    weighted_bonuses = []
    weighted_absolute_scores = []
    weighted_residual_scores = []
    weighted_maturity_scores = []
    weights = []
    games = 0
    valid_count = 0
    expected_rates = []
    actual_rates = []
    actual_absolute_rates = []
    confidence_values = []

    for enemy, enemy_lane, enemy_role in enemies:
        enemy_tier = db.get("tiers", {}).get(enemy_lane, {}).get(enemy)
        enemy_base = calculate_base_rating(db, enemy_lane, enemy, enemy_tier)
        expected_rating = candidate_expectation_rating - enemy_base["expectation_rating"]
        stat = get_matchup_stat(db, candidate, enemy, enemy_role)
        if stat["games"] > 0:
            valid_count += 1
        blended = blended_relation_score(
            stat,
            expected_rating,
            COUNTER_ABSOLUTE_WEIGHT,
            COUNTER_RESIDUAL_WEIGHT,
            MATCHUP_PRIOR_GAMES,
            STRONG_COUNTER_FLOOR,
            local_top_games=top_matchup_games(db, candidate, enemy_role),
        )
        weight = counter_role_weight(role, enemy_role)
        weighted_bonuses.append(blended["score"] * weight)
        weighted_absolute_scores.append(blended["absolute_score"] * weight)
        weighted_residual_scores.append(blended["residual_score"] * weight)
        weighted_maturity_scores.append(blended["maturity_score"] * weight)
        weights.append(weight)
        games += int(stat["games"])
        expected_rates.append(blended["expected_winrate"])
        if blended["actual_winrate"] is not None:
            actual_rates.append(blended["actual_winrate"])
        if blended["actual_absolute_winrate"] is not None:
            actual_absolute_rates.append(blended["actual_absolute_winrate"])
        confidence_values.append(blended["confidence"])

    if not weights:
        return {
            "counter_bonus": 0.0,
            "counter_absolute_score": 0.0,
            "counter_residual_score": 0.0,
            "counter_maturity_score": 0.0,
            "counter_confidence": 0.0,
            "matchup_games": 0,
            "expected_matchup_winrate": None,
            "actual_matchup_winrate": None,
            "actual_matchup_absolute_winrate": None,
            "counter_data_count": 0,
        }
    total_weight = sum(weights)

    return {
        "counter_bonus": sum(weighted_bonuses) / total_weight,
        "counter_absolute_score": sum(weighted_absolute_scores) / total_weight,
        "counter_residual_score": sum(weighted_residual_scores) / total_weight,
        "counter_maturity_score": sum(weighted_maturity_scores) / total_weight,
        "counter_confidence": sum(confidence_values) / len(confidence_values),
        "matchup_games": games,
        "expected_matchup_winrate": sum(expected_rates) / len(expected_rates),
        "actual_matchup_winrate": (
            sum(actual_rates) / len(actual_rates) if actual_rates else None
        ),
        "actual_matchup_absolute_winrate": (
            sum(actual_absolute_rates) / len(actual_absolute_rates)
            if actual_absolute_rates
            else None
        ),
        "counter_data_count": valid_count,
    }


def confidence_from_games(games):
    if games >= 3000:
        return "high"
    if games >= 1000:
        return "medium"
    if games >= 300:
        return "low"
    return "very_low"


def describe_rating(value, high_text, low_text, neutral_text):
    if value >= 20:
        return high_text
    if value <= -20:
        return low_text
    return neutral_text


def describe_bonus(value, high_text, low_text, neutral_text):
    if value >= 12:
        return high_text
    if value <= -12:
        return low_text
    return neutral_text


def build_explanation(row, ally, enemies):
    parts = []
    parts.append(
        describe_rating(
            row["base_rating"],
            "基础表现较高",
            "基础表现偏低",
            "基础表现接近平均",
        )
    )

    if ally:
        if row["duo_games"] <= 0:
            parts.append("缺少配合样本")
        else:
            parts.append(
                describe_bonus(
                    row["synergy_bonus"],
                    "配合表现优于正常水平",
                    "配合表现低于正常水平",
                    "配合表现接近正常水平",
                )
            )

    if enemies:
        if row["matchup_games"] <= 0:
            parts.append("缺少对位样本")
        else:
            parts.append(
                describe_bonus(
                    row["counter_bonus"],
                    "对位表现优于正常水平",
                    "对位表现低于正常水平",
                    "对位表现接近正常水平",
                )
            )

    if row["confidence_level"] == "very_low":
        parts.append("样本量不足")

    return "；".join(parts) + "。"


def build_display_meta(score, ally, enemies, has_context=False):
    evidence_keys = []
    if score["matchup_games"] > 0:
        evidence_keys.append("counter")
    if score["duo_games"] > 0:
        evidence_keys.append("synergy")
    evidence_keys.append("base")

    missing_fields = []
    enemy_count = len(enemies)
    if enemy_count > 0:
        if score["matchup_games"] == 0:
            missing_fields.append("counter")
        elif score["counter_data_count"] < enemy_count:
            missing_fields.append("counter_partial")
    if ally and score["duo_games"] == 0:
        missing_fields.append("synergy")

    relation_games = score["duo_games"] + score["matchup_games"]
    confidence_games = relation_games if has_context else score.get("base_games", 0)
    confidence_level = confidence_from_games(confidence_games)

    return {
        "evidence_keys": evidence_keys,
        "evidence_label": "、".join(EVIDENCE_LABELS[key] for key in evidence_keys),
        "missing_fields": missing_fields,
        "missing_labels": [MISSING_FIELD_LABELS[key] for key in missing_fields],
        "relation_games": relation_games,
        "confidence_games": int(confidence_games),
        "sample_games": int(confidence_games),
        "confidence_level": confidence_level,
        "confidence_label": CONFIDENCE_LABELS[confidence_level],
    }


def get_component_weights(state, has_ally, enemy_count):
    if state == "S1":
        weights = {"base": 1.0, "synergy": 0.0, "counter": 0.0}
    elif has_ally and enemy_count <= 0:
        weights = {"base": 0.35, "synergy": 0.65, "counter": 0.0}
    elif not has_ally and enemy_count > 0:
        weights = {"base": 0.40, "synergy": 0.0, "counter": 0.60}
    elif has_ally and enemy_count > 0:
        weights = {"base": 0.20, "synergy": 0.40, "counter": 0.40}
    else:
        weights = {"base": 1.0, "synergy": 0.0, "counter": 0.0}

    available = {
        "base": True,
        "synergy": has_ally,
        "counter": enemy_count > 0,
    }
    active = {
        key: value if available[key] else 0.0
        for key, value in weights.items()
    }
    return normalize_weights(active)


def normalize_champion(value):
    if value is None:
        return None
    value = str(value).strip().lower()
    return value or None


def new_bp_state():
    return {"ally_ad": None, "ally_sup": None, "enemy_ad": None, "enemy_sup": None}


def normalize_bp_state(bp_state):
    normalized = new_bp_state()
    for key in VALID_KEYS:
        normalized[key] = normalize_champion(bp_state.get(key))
    return normalized


def derive_state(bp_state):
    bp_state = normalize_bp_state(bp_state)
    has_ally = bp_state["ally_ad"] is not None or bp_state["ally_sup"] is not None
    n_enemies = sum(
        1 for key in ("enemy_ad", "enemy_sup") if bp_state[key] is not None
    )

    if has_ally and n_enemies == 2:
        return "S8"
    if has_ally and n_enemies == 1:
        return "S7" if bp_state["enemy_sup"] else "S6"
    if not has_ally and n_enemies == 2:
        return "S5"
    if not has_ally and n_enemies == 1:
        return "S4" if bp_state["enemy_sup"] else "S3"
    if has_ally:
        return "S2"
    return "S1"


def get_state_info(state):
    return STATE_INFO.get(
        state,
        {
            "label": state,
            "description": "当前 BP 信息不足，使用可用数据生成推荐。",
        },
    )


def get_role_meta(role):
    if role not in VALID_ROLES:
        raise ValueError("role must be 'support' or 'adc'")
    return {
        "lane": "support" if role == "support" else "bottom",
        "label": "辅助" if role == "support" else "ADC",
        "ally_key": "ally_ad" if role == "support" else "ally_sup",
    }


def adjusted_synergy_score(synergy, has_ally):
    if has_ally and synergy["duo_games"] <= 0:
        return MISSING_SYNERGY_PENALTY
    return synergy["synergy_bonus"]


def adjusted_counter_score(counter, enemy_count):
    if enemy_count <= 0:
        return counter["counter_bonus"]
    missing_count = max(0, enemy_count - counter["counter_data_count"])
    missing_penalty = MISSING_COUNTER_PENALTY * missing_count / enemy_count
    return counter["counter_bonus"] + missing_penalty


def normalize_time_stats(record):
    raw_rows = record.get("stats_by_time") or record.get("statsByTime") or []
    rows = []
    for index, raw in enumerate(raw_rows[: len(TIME_BUCKETS)]):
        if isinstance(raw, dict):
            games = raw.get("games", raw.get("n", raw.get("matches", 0)))
            wins = raw.get("wins", raw.get("timeWin", 0))
            label = raw.get("label", TIME_BUCKETS[index])
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            wins, games = raw[0], raw[1]
            label = TIME_BUCKETS[index]
        else:
            continue
        rows.append(
            {
                "label": label,
                "wins": max(0.0, float(wins or 0)),
                "games": max(0.0, float(games or 0)),
            }
        )
    return rows


def build_hero_time_profile(db, lane, champion):
    record = get_hero_stats_record(db, lane, champion)
    rows = normalize_time_stats(record)
    if not rows:
        return None

    stat = extract_stat(record)
    if stat["winrate"] is None or stat["games"] <= 0:
        return None

    base_winrate = smooth_winrate(stat["wins"], stat["games"], 0.5, HERO_PRIOR_GAMES)
    base_rating = winrate_to_rating(base_winrate)
    points = []
    for row in rows:
        smoothed = smooth_winrate(
            row["wins"], row["games"], base_winrate, TIME_PRIOR_GAMES
        )
        bonus = winrate_to_rating(smoothed) - base_rating
        points.append(
            {
                "label": row["label"],
                "时间段": row["label"],
                "平滑胜率": round(smoothed * 100, 2),
                "样本场次": int(row["games"]),
                "强势偏移": round(bonus, 2),
                "强度": describe_time_strength(bonus),
            }
        )

    return {
        "source": "hero",
        "来源": "单英雄趋势",
        "champion": champion,
        "lane": lane,
        "整体平滑胜率": round(base_winrate * 100, 2),
        "points": points,
        "summary": summarize_time_points(points),
        "结论": summarize_time_points(points),
    }


def combine_time_profiles(label, profiles):
    usable = [profile for profile in profiles if profile and profile.get("points")]
    if not usable:
        return None
    points = []
    for index, bucket in enumerate(TIME_BUCKETS):
        bucket_points = [
            profile["points"][index]
            for profile in usable
            if index < len(profile.get("points", []))
        ]
        if not bucket_points:
            continue
        bonus = sum(point["强势偏移"] for point in bucket_points)
        games = sum(point["样本场次"] for point in bucket_points)
        avg_wr = sum(point["平滑胜率"] for point in bucket_points) / len(bucket_points)
        points.append(
            {
                "label": bucket,
                "时间段": bucket,
                "平滑胜率": round(avg_wr, 2),
                "样本场次": int(games),
                "强势偏移": round(bonus, 2),
                "强度": describe_time_strength(bonus),
            }
        )
    return {
        "source": "lane_combo",
        "来源": "英雄趋势加总",
        "label": label,
        "points": points,
        "summary": summarize_time_points(points),
        "结论": summarize_time_points(points),
    }


def describe_time_strength(bonus):
    if bonus >= 12:
        return "强"
    if bonus <= -12:
        return "弱"
    return "中"


def summarize_time_points(points):
    if not points:
        return "暂无时间线数据"
    best = max(points, key=lambda item: item["强势偏移"])
    worst = min(points, key=lambda item: item["强势偏移"])
    spread = best["强势偏移"] - worst["强势偏移"]
    if spread < 8:
        return "曲线平稳"
    if best["时间段"] in ("0-20", "20-25"):
        return "偏前中期强势"
    if best["时间段"] in ("30-35", "35+"):
        return "偏后期强势"
    return "偏中期强势"


def build_side_time_profile(db, champions):
    profiles = []
    names = []
    for champion, lane in champions:
        profile = build_hero_time_profile(db, lane, champion)
        if profile:
            profiles.append(profile)
            names.append(champion)
    if not profiles:
        return None
    if len(profiles) == 1:
        profile = profiles[0].copy()
        profile["label"] = names[0]
        profile["来源"] = "单英雄趋势"
        return profile
    return combine_time_profiles(" + ".join(names), profiles)


def build_time_detail(db, role, candidate, ally, bp_state):
    candidate_lane = "support" if role == "support" else "bottom"
    candidate_profile = build_hero_time_profile(db, candidate_lane, candidate)
    profiles = []
    if candidate_profile:
        profiles.append(
            {
                "title": f"{candidate} 自身时间线",
                "kind": "hero",
                **candidate_profile,
            }
        )

    combo_profile = None
    if ally:
        partner_lane = "bottom" if role == "support" else "support"
        partner_profile = build_hero_time_profile(db, partner_lane, ally)
        combo_profile = combine_time_profiles(f"{ally} + {candidate}", [partner_profile, candidate_profile])
        if combo_profile:
            profiles.insert(
                0,
                {
                    "title": f"{ally} + {candidate} 组合时间线",
                    "kind": "combo",
                    **combo_profile,
                },
            )

    primary = combo_profile or candidate_profile
    enemy_champions = []
    if bp_state.get("enemy_ad"):
        enemy_champions.append((bp_state["enemy_ad"], "bottom"))
    if bp_state.get("enemy_sup"):
        enemy_champions.append((bp_state["enemy_sup"], "support"))
    enemy_profile = build_side_time_profile(db, enemy_champions)

    note = "我方线基于推荐英雄自身时间段表现。"
    if combo_profile:
        note = "我方线由己方 ADC 与推荐英雄各自的时间段强势偏移加总推算。"
    if enemy_profile:
        if len(enemy_champions) == 1:
            note += " 敌方线基于当前已知的单个敌方英雄，仅作参考。"
        else:
            note += " 敌方线由敌方 ADC 与辅助各自的时间段强势偏移加总推算。"
    else:
        note += " 当前敌方下路未知，暂不显示敌方参考线。"

    return {
        "available": primary is not None,
        "primary": primary,
        "ally": primary,
        "enemy": enemy_profile,
        "profiles": profiles,
        "note": note,
        "说明": note,
        "口径": "强势偏移 = 该英雄该时间段表现 - 该英雄自身整体平均表现；下路趋势为 ADC 与辅助的强势偏移加总，不代表真实 duo 组合分时胜率。",
    }


def precision_confidence_bonus(level):
    return {"high": 2.0, "medium": 1.0, "low": -2.0, "very_low": -4.0}.get(level, 0.0)


def precision_tier_bonus(tier):
    if tier in ("S+", "S", "S-", "A+", "A", "A-"):
        return 0.5
    if tier in ("C+", "C", "C-", "D+", "D", "D-"):
        return -0.5
    return 0.0


def append_unique(target, values, limit=None):
    for value in values:
        if value and value not in target:
            target.append(value)
            if limit and len(target) >= limit:
                break
    return target


def get_champion_tags(champion):
    return CHAMPION_TAGS.get(champion, [])


def infer_combo_tags(candidate_tags, ally_tags, candidate=None, ally=None):
    if candidate and ally:
        override = COMBO_TAG_OVERRIDES.get(frozenset((candidate, ally)))
        if override:
            return list(override)

    candidate_set = set(candidate_tags)
    ally_set = set(ally_tags)
    combo_tags = []

    def has_any(tag_set, names):
        return any(name in tag_set for name in names)

    def paired(left_names, right_names):
        return (
            has_any(candidate_set, left_names) and has_any(ally_set, right_names)
        ) or (
            has_any(ally_set, left_names) and has_any(candidate_set, right_names)
        )

    if paired(
        ("爆发换血", "爆发", "抢线"),
        ("消耗", "强化普攻", "前中期", "抢线"),
    ):
        combo_tags.append("爆发换血")
    if paired(
        ("强开", "控制链", "团控", "先手机会", "开团"),
        ("爆发", "all-in", "近身爆发", "收割"),
    ):
        combo_tags.append("all-in爆发")
    if paired(
        ("发育", "后期", "后期收割", "成长", "保护核心"),
        ("保护", "强化核心", "续航", "容错", "反开"),
    ):
        combo_tags.append("发育保护")
    if paired(
        ("消耗", "远程消耗", "压血线", "推线"),
        ("消耗", "远程消耗", "压血线", "推线", "控制衔接"),
    ):
        combo_tags.append("远程消耗")
    if paired(
        ("反开", "反打", "防突进"),
        ("保护", "自保", "拉扯", "发育", "后期"),
    ):
        combo_tags.append("反打拉扯")
    if paired(
        ("抢线", "推线", "前期压制"),
        ("控龙", "消耗", "远程消耗", "压血线", "开视野"),
    ):
        combo_tags.append("抢线控龙")
    return combo_tags


def build_data_tags(row, relation_risk_penalty):
    tags = []
    if row.get("confidence_level") in ("high", "medium") and not row.get("missing_fields"):
        tags.append("样本较稳")
    if row.get("base_rating", 0) >= 12:
        tags.append("版本强势")
    if row.get("synergy_bonus", 0) >= 8:
        tags.append("搭档适配")
    if row.get("counter_bonus", 0) >= 8:
        tags.append("对位优势")
    if row["display_winrate"] >= 53 and row.get("confidence_level") in ("low", "very_low"):
        tags.append("高收益")
    if row.get("confidence_level") in ("low", "very_low"):
        tags.append("样本谨慎")
    if row.get("missing_fields"):
        tags.append("数据不足")
    if relation_risk_penalty > 0:
        tags.append("关系风险")
    if not tags:
        tags.append("综合推荐")
    return tags


def select_card_tags(data_tags, combo_tags, hero_tags, time_detail):
    tags = []
    append_unique(tags, data_tags, limit=2)
    append_unique(tags, combo_tags, limit=4)
    append_unique(tags, hero_tags, limit=4)
    if time_detail.get("primary"):
        append_unique(tags, [time_detail["primary"]["summary"]], limit=4)
    return tags[:4] or ["综合推荐"]


def build_precision_tag_groups(data_tags, combo_tags, hero_tags, time_detail):
    combo_display = []
    append_unique(combo_display, combo_tags, limit=3)
    if time_detail.get("primary"):
        append_unique(combo_display, [time_detail["primary"]["summary"]], limit=3)

    hero_display = []
    append_unique(hero_display, hero_tags, limit=3)

    data_display = []
    append_unique(data_display, data_tags, limit=3)

    return {
        "combo": combo_display,
        "hero": hero_display,
        "data": data_display,
    }


def build_coach_summary(row, data_tags, combo_tags, hero_tags, time_detail):
    tags = set(data_tags + combo_tags + hero_tags)
    if "all-in爆发" in tags or "控制进场" in tags or "前期击杀" in tags:
        return "适合主动找机会，配合控制链打击杀。"
    if "击杀压制" in tags or "高风险滚雪球" in tags:
        return "适合前期打击杀滚雪球，但容错会更低。"
    if "爆发换血" in tags or "先手爆发" in tags or "强化换血" in tags:
        return "适合围绕当前搭档打前中期换血。"
    if "发育保护" in tags or "保护核心" in tags or "保护发育" in tags:
        return "适合稳住对线，把重心放到中后期团战。"
    if "推线消耗" in tags or "抢线压制" in tags or "远程消耗" in tags:
        return "适合用推线和消耗建立下路主动权。"
    if "反打拉扯" in tags or "反打保护" in tags or "反打换血" in tags:
        return "适合稳住站位，等对手先手后反打。"
    if "对位优势" in tags:
        return "面对当前敌方下路有更好的对位价值。"
    if "搭档适配" in tags:
        return "和当前搭档适配度较高，适合优先考虑。"
    if "消耗" in tags or "压血线" in tags:
        return "适合用手长和消耗建立线权。"
    if "强开" in tags or "all-in爆发" in tags or "先手机会" in tags:
        return "适合主动找机会，配合打野打击杀。"
    if time_detail.get("primary") and "前" in time_detail["primary"]["summary"]:
        return "强势期偏前中期，适合尽早争线权。"
    if time_detail.get("primary") and "后" in time_detail["primary"]["summary"]:
        return "强势期偏中后期，适合稳住发育。"
    if "版本强势" in tags or "样本较稳" in tags:
        return "版本表现和数据稳定性较好，适合直接选。"
    if "高收益" in tags:
        return "模型收益较高，但需要结合熟练度判断。"
    return "综合表现靠前，适合作为本局候选。"


def build_coach_playstyle(row, data_tags, combo_tags, hero_tags, time_detail):
    tags = set(data_tags + combo_tags + hero_tags)
    parts = []
    if "抢线控龙" in tags or "抢线" in tags or "推线" in tags:
        parts.append("前期优先处理兵线，争取下河道和小龙前视野。")
    if "爆发换血" in tags or "all-in爆发" in tags or "强开" in tags:
        parts.append("找到等级或技能窗口后可以主动换血，配合打野扩大击杀压力。")
    if "远程消耗" in tags or "消耗" in tags or "压血线" in tags:
        parts.append("用射程和技能消耗压低血线，不急着硬拼。")
    if "发育保护" in tags or "保护" in tags or "强化核心" in tags:
        parts.append("重点保护核心输出，前期少给机会，中后期围绕团战站位。")
    if "游走" in tags:
        parts.append("线权出来后可以带动中野节奏，但要避免让 ADC 长时间独自抗压。")
    if time_detail.get("primary"):
        parts.append(f"时间线判断为{time_detail['primary']['summary']}，发力节奏可以围绕这个阶段安排。")
    if not parts:
        parts.append("打法上以稳住对线、根据打野位置决定进退为主。")
    return "".join(parts)


def build_coach_risks(row, base_risks, hero_tags):
    risks = list(base_risks)
    tags = set(hero_tags)
    if "高风险" in tags:
        risks.append("英雄容错偏低，熟练度不足时不建议只看模型分数。")
    if "技能命中" in tags:
        risks.append("强度比较依赖关键技能命中率，空技能后对线压力会明显下降。")
    if "游走" in tags:
        risks.append("游走收益需要线权支撑，否则容易让下路亏线。")
    if "后期" in tags or "发育" in tags:
        risks.append("前期需要避免无意义硬拼，过早崩线会影响后期价值。")
    if row.get("confidence_level") in ("low", "very_low"):
        risks.append("样本量偏少，更适合熟练玩家作为参考。")
    return append_unique([], risks)


def build_precision_meta(db, role, bp_state, row, ally):
    data_bonus = 0.0
    if row.get("duo_games", 0) > 0:
        data_bonus += 0.5
    if row.get("matchup_games", 0) > 0:
        data_bonus += 0.5
    missing_penalty = len(row.get("missing_fields", [])) * 2.0
    relation_risk_penalty = 0.0
    if ally and row.get("synergy_calibrated_score", row.get("synergy_adjusted_score", 0)) < -6:
        relation_risk_penalty += 6.0
    if (bp_state.get("enemy_ad") or bp_state.get("enemy_sup")) and row.get("counter_calibrated_score", row.get("counter_adjusted_score", 0)) < -6:
        relation_risk_penalty += 6.0
    precision_score = (
        row["final_rating"]
        + precision_confidence_bonus(row.get("confidence_level"))
        + data_bonus
        + precision_tier_bonus(row.get("tier"))
        - missing_penalty
        - relation_risk_penalty
    )

    time_detail = build_time_detail(db, role, row["name"], ally, bp_state)
    hero_tags = get_champion_tags(row["name"])
    ally_tags = get_champion_tags(ally) if ally else []
    combo_tags = infer_combo_tags(hero_tags, ally_tags, row["name"], ally) if ally else []
    data_tags = build_data_tags(row, relation_risk_penalty)
    tags = select_card_tags(data_tags, combo_tags, hero_tags, time_detail)
    tag_groups = build_precision_tag_groups(data_tags, combo_tags, hero_tags, time_detail)

    reasons = [row.get("explanation", "").rstrip("。")]
    if row.get("synergy_residual_score", 0) >= 5:
        reasons.append("组合历史表现高于理论预期")
    if row.get("counter_residual_score", 0) >= 5:
        reasons.append("当前对位表现高于理论预期")
    if time_detail.get("primary"):
        reasons.append(f"时间线显示{time_detail['primary']['summary']}")
    reason = "；".join(part for part in reasons if part) + "。"

    risks = []
    if row.get("confidence_level") in ("low", "very_low"):
        risks.append("样本量偏少，建议结合熟练度判断。")
    if row.get("missing_labels"):
        risks.append("、".join(row["missing_labels"]) + "。")
    if ally and row.get("synergy_calibrated_score", row.get("synergy_adjusted_score", 0)) < -6:
        risks.append("与当前己方搭档的历史配合分明显偏低。")
    if (bp_state.get("enemy_ad") or bp_state.get("enemy_sup")) and row.get("counter_calibrated_score", row.get("counter_adjusted_score", 0)) < -6:
        risks.append("面对当前敌方下路的历史对位分明显偏低。")
    if time_detail.get("available") is False:
        risks.append("当前缓存暂无时间段强势数据，更新数据后可显示曲线。")
    if not risks:
        risks.append("暂无明显数据风险，仍需结合阵容和熟练度。")
    coach_summary = build_coach_summary(row, data_tags, combo_tags, hero_tags, time_detail)
    coach_playstyle = build_coach_playstyle(row, data_tags, combo_tags, hero_tags, time_detail)
    coach_risks = build_coach_risks(row, risks, hero_tags)

    return {
        "precision_score": round(precision_score, 2),
        "precision_tags": tags,
        "precision_reason": reason,
        "coach_summary": coach_summary,
        "coach_playstyle": coach_playstyle,
        "coach_risks": coach_risks,
        "hero_tags": hero_tags,
        "combo_tags": combo_tags,
        "data_tags": data_tags,
        "precision_tag_groups": tag_groups,
        "precision_detail": {
            "conclusion": coach_summary,
            "coach_playstyle": coach_playstyle,
            "timeline": time_detail,
            "data": {
                "recommend_index": row["display_winrate"],
                "confidence_label": row.get("confidence_label"),
                "base_rating": row.get("base_rating"),
                "base_calibrated_score": row.get("base_calibrated_score"),
                "synergy_bonus": row.get("synergy_bonus"),
                "synergy_calibrated_score": row.get("synergy_calibrated_score"),
                "synergy_maturity_score": row.get("synergy_maturity_score"),
                "counter_bonus": row.get("counter_bonus"),
                "counter_calibrated_score": row.get("counter_calibrated_score"),
                "counter_maturity_score": row.get("counter_maturity_score"),
                "duo_games": row.get("duo_games"),
                "matchup_games": row.get("matchup_games"),
            },
            "risks": coach_risks,
        },
    }


def run_recommend(role, bp_state, db, top_n=None):
    """
    返回推荐结果，不打印。

    results 中每一项都保留 raw score，便于前端展示解释。
    """
    role = normalize_champion(role)
    meta = get_role_meta(role)
    bp_state = normalize_bp_state(bp_state)
    state = derive_state(bp_state)
    ally = bp_state[meta["ally_key"]]
    enemy_context = []
    if bp_state["enemy_ad"]:
        enemy_context.append((bp_state["enemy_ad"], "bottom", "adc"))
    if bp_state["enemy_sup"]:
        enemy_context.append((bp_state["enemy_sup"], "support", "support"))
    enemies = [enemy for enemy, _, _ in enemy_context]
    selected_champions = {
        value for value in bp_state.values() if value is not None
    }

    candidates = {}
    for champ, tier_label in db["tiers"].get(meta["lane"], {}).items():
        if tier_label == "?":
            continue
        if champ in selected_champions:
            continue
        base = calculate_base_rating(db, meta["lane"], champ, tier_label)
        candidates[champ] = {
            "tier_label": tier_label,
            "tier_score": TIER_SCORES.get(tier_label, 47),
            **base,
        }

    partner_rating = 0.0
    if ally:
        partner_lane = "bottom" if role == "support" else "support"
        partner_tier = db.get("tiers", {}).get(partner_lane, {}).get(ally)
        partner_rating = calculate_base_rating(db, partner_lane, ally, partner_tier)["expectation_rating"]

    component_weights = get_component_weights(state, bool(ally), len(enemy_context))

    results = []
    for name, score in candidates.items():
        synergy = calculate_synergy_bonus(
            db, name, ally, score["expectation_rating"], partner_rating
        )
        counter = calculate_counter_bonus(
            db, name, enemy_context, score["expectation_rating"], role=role
        )
        synergy_adjusted = adjusted_synergy_score(synergy, bool(ally))
        counter_adjusted = adjusted_counter_score(counter, len(enemy_context))
        base_calibrated = calibrated_component_score(score["base_rating"])
        synergy_calibrated = calibrated_component_score(synergy_adjusted)
        counter_calibrated = calibrated_component_score(counter_adjusted)
        final_rating = (
            component_weights["base"] * base_calibrated
            + component_weights["synergy"] * synergy_calibrated
            + component_weights["counter"] * counter_calibrated
        )
        row = {
            "name": name,
            "tier": score["tier_label"],
            "tier_score": score["tier_score"],
            "base_winrate": round(score["base_winrate"] * 100, 2),
            "raw_base_rating": round(score["raw_base_rating"], 2),
            "base_rating": round(score["base_rating"], 2),
            "base_calibrated_score": round(base_calibrated, 2),
            "expectation_rating": round(score["expectation_rating"], 2),
            "tier_prior_rating": round(score["tier_prior_rating"], 2),
            "winrate_component": round(score["winrate_component"], 2),
            "base_confidence_multiplier": round(score["base_confidence_multiplier"], 4),
            "base_games": score["base_games"],
            "final_rating": round(final_rating, 2),
            "display_winrate": round(rating_to_winrate(final_rating) * 100, 2),
            "synergy_bonus": round(synergy["synergy_bonus"], 2),
            "synergy_adjusted_score": round(synergy_adjusted, 2),
            "synergy_calibrated_score": round(synergy_calibrated, 2),
            "synergy_absolute_score": round(synergy["synergy_absolute_score"], 2),
            "synergy_residual_score": round(synergy["synergy_residual_score"], 2),
            "synergy_maturity_score": round(synergy["synergy_maturity_score"], 2),
            "synergy_confidence": round(synergy["synergy_confidence"], 4),
            "counter_bonus": round(counter["counter_bonus"], 2),
            "counter_adjusted_score": round(counter_adjusted, 2),
            "counter_calibrated_score": round(counter_calibrated, 2),
            "counter_absolute_score": round(counter["counter_absolute_score"], 2),
            "counter_residual_score": round(counter["counter_residual_score"], 2),
            "counter_maturity_score": round(counter["counter_maturity_score"], 2),
            "counter_confidence": round(counter["counter_confidence"], 4),
            "duo_games": synergy["duo_games"],
            "matchup_games": counter["matchup_games"],
            "counter_data_count": counter["counter_data_count"],
            "synergy_data_found": synergy["duo_games"] > 0,
            "expected_duo_winrate": (
                round(synergy["expected_duo_winrate"] * 100, 2)
                if synergy["expected_duo_winrate"] is not None
                else None
            ),
            "actual_duo_winrate": (
                round(synergy["actual_duo_winrate"] * 100, 2)
                if synergy["actual_duo_winrate"] is not None
                else None
            ),
            "actual_duo_absolute_winrate": (
                round(synergy["actual_duo_absolute_winrate"] * 100, 2)
                if synergy["actual_duo_absolute_winrate"] is not None
                else None
            ),
            "expected_matchup_winrate": (
                round(counter["expected_matchup_winrate"] * 100, 2)
                if counter["expected_matchup_winrate"] is not None
                else None
            ),
            "actual_matchup_winrate": (
                round(counter["actual_matchup_winrate"] * 100, 2)
                if counter["actual_matchup_winrate"] is not None
                else None
            ),
            "actual_matchup_absolute_winrate": (
                round(counter["actual_matchup_absolute_winrate"] * 100, 2)
                if counter["actual_matchup_absolute_winrate"] is not None
                else None
            ),
        }
        row.update(build_display_meta(row, ally, enemies, has_context=bool(ally or enemies)))
        row["explanation"] = build_explanation(row, ally, enemies)
        row.update(build_precision_meta(db, role, bp_state, row, ally))
        # Legacy aliases keep existing clients usable while the UI migrates.
        row["final_score"] = row["display_winrate"]
        row["counter_score"] = row["counter_calibrated_score"]
        row["synergy_score"] = row["synergy_calibrated_score"]
        row["base_score"] = row["base_calibrated_score"]
        row["effective_weights"] = round_weights(component_weights)
        results.append(row)

    results.sort(key=lambda item: item["final_rating"], reverse=True)
    precise_results = sorted(
        results, key=lambda item: item.get("precision_score", item["final_rating"]), reverse=True
    )[:7]
    if top_n is not None:
        results = results[:top_n]

    return {
        "role": role,
        "role_label": meta["label"],
        "lane": meta["lane"],
        "state": state,
        "state_info": get_state_info(state),
        "bp_state": bp_state,
        "ally": ally,
        "enemies": enemies,
        "excluded_champions": sorted(selected_champions),
        "weights": round_weights(component_weights),
        "score_model": "absolute_residual_blend_v2",
        "results": results,
        "precise_results": precise_results,
        "meta": db.get("meta", {}),
    }


def find_counter_picks(enemy, role, db, top_n=5):
    """
    查“敌方选了 enemy，我方选谁更好打”。

    单英雄克制查询采用双向校验：
    1. 正向：候选英雄自己的 matchup 表里，候选打 enemy 的胜率。
    2. 反向：enemy 自己的 matchup 表里，enemy 打候选的胜率，再反推为我方胜率。

    两边按 sqrt(场次) 加权融合。这样既保留候选英雄视角，也让结果
    更贴近“敌方选了 X，我方选谁更克制 X”的查询语义。
    """
    enemy = normalize_champion(enemy)
    role = normalize_champion(role)
    meta = get_role_meta(role)
    enemy_key_for_candidate = (
        "vs_adc" if enemy in db["tiers"].get("bottom", {}) else "vs_sup"
    )
    candidate_key_for_enemy = "vs_adc" if role == "adc" else "vs_sup"
    enemy_matchups = db.get("counter", {}).get(enemy, {}).get(candidate_key_for_enemy, {})

    results = []
    for champ, tier in db["tiers"].get(meta["lane"], {}).items():
        if tier == "?":
            continue
        forward_stat = extract_stat(
            db.get("counter", {}).get(champ, {}).get(enemy_key_for_candidate, {}).get(enemy),
            legacy_games=LEGACY_RELATION_GAMES,
        )
        reverse_stat = extract_stat(enemy_matchups.get(champ), legacy_games=0)

        evidence = []
        if forward_stat["winrate"] is not None and forward_stat["games"] > 0:
            evidence.append(
                {
                    "win_rate": forward_stat["winrate"] * 100,
                    "games": int(forward_stat["games"]),
                    "weight": math.sqrt(forward_stat["games"]),
                }
            )
        if reverse_stat["winrate"] is not None and reverse_stat["games"] > 0:
            evidence.append(
                {
                    "win_rate": (1 - reverse_stat["winrate"]) * 100,
                    "games": int(reverse_stat["games"]),
                    "weight": math.sqrt(reverse_stat["games"]),
                }
            )
        if not evidence:
            continue

        total_weight = sum(item["weight"] for item in evidence)
        win_rate = sum(item["win_rate"] * item["weight"] for item in evidence) / total_weight
        total_games = sum(item["games"] for item in evidence)
        forward_win_rate = (
            forward_stat["winrate"] * 100
            if forward_stat["winrate"] is not None and forward_stat["games"] > 0
            else None
        )
        reverse_win_rate = (
            (1 - reverse_stat["winrate"]) * 100
            if reverse_stat["winrate"] is not None and reverse_stat["games"] > 0
            else None
        )
        consistency_gap = (
            abs(forward_win_rate - reverse_win_rate)
            if forward_win_rate is not None and reverse_win_rate is not None
            else None
        )
        confidence_level = confidence_from_games(total_games)
        if consistency_gap is None:
            data_note = "单向数据参考"
        elif consistency_gap <= 2:
            data_note = "双向数据一致"
        elif consistency_gap <= 5:
            data_note = "双向数据基本一致"
        else:
            data_note = "双向数据分歧较大"

        consistency_penalty = max(0.0, (consistency_gap or 0.0) - 2.0) * 0.25
        single_side_penalty = 2.5 if len(evidence) < 2 else 0.0
        sample_bonus = min(1.5, math.sqrt(total_games / 3000) * 1.5)
        counter_index = win_rate + sample_bonus - consistency_penalty - single_side_penalty
        results.append(
            {
                "name": champ,
                "role": role,
                "role_label": meta["label"],
                "tier": tier,
                "win_rate": round(win_rate, 2),
                "counter_index": round(counter_index, 2),
                "forward_win_rate": (
                    round(forward_win_rate, 2) if forward_win_rate is not None else None
                ),
                "forward_games": int(forward_stat["games"]),
                "reverse_win_rate": (
                    round(reverse_win_rate, 2) if reverse_win_rate is not None else None
                ),
                "reverse_games": int(reverse_stat["games"]),
                "games": int(total_games),
                "consistency_gap": (
                    round(consistency_gap, 2) if consistency_gap is not None else None
                ),
                "confidence_level": confidence_level,
                "confidence_label": CONFIDENCE_LABELS[confidence_level],
                "data_note": data_note,
            }
        )

    results.sort(key=lambda item: item["counter_index"], reverse=True)
    return results[:top_n]
