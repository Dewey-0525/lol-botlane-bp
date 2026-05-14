#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bp_engine.py - 下路 BP 推荐核心

这个模块不负责命令行打印，也不负责 Web 框架。
它只接收 BP 状态和本地数据库，返回适合 CLI/API/小程序使用的结构化结果。
"""

import math

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
SYNERGY_ABSOLUTE_WEIGHT = 0.65
SYNERGY_RESIDUAL_WEIGHT = 0.35
COUNTER_ABSOLUTE_WEIGHT = 0.60
COUNTER_RESIDUAL_WEIGHT = 0.40
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


def blended_relation_score(
    stat,
    expected_rating,
    absolute_weight,
    residual_weight,
    prior_games,
    positive_floor=0.0,
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
    confidence = relation_confidence(stat["games"])
    score = confidence * (
        absolute_weight * absolute_score + residual_weight * residual_score
    )

    raw_winrate = stat["wins"] / stat["games"] if stat["games"] > 0 else None
    if (
        raw_winrate is not None
        and raw_winrate >= STRONG_RELATION_WINRATE
        and stat["games"] >= STRONG_RELATION_GAMES
        and positive_floor > 0
    ):
        score = max(score, positive_floor)

    return {
        "score": score,
        "absolute_score": absolute_score,
        "residual_score": residual_score,
        "confidence": confidence,
        "expected_winrate": expected_winrate,
        "actual_winrate": actual_winrate,
        "actual_absolute_winrate": actual_absolute_winrate,
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
            "synergy_confidence": 0.0,
            "duo_games": 0,
            "expected_duo_winrate": None,
            "actual_duo_winrate": None,
            "actual_duo_absolute_winrate": None,
        }

    expected_rating = candidate_expectation_rating + partner_expectation_rating
    stat = extract_stat(
        get_relation_stat(db.get("synergy", {}), partner, candidate),
        legacy_games=LEGACY_RELATION_GAMES,
    )
    blended = blended_relation_score(
        stat,
        expected_rating,
        SYNERGY_ABSOLUTE_WEIGHT,
        SYNERGY_RESIDUAL_WEIGHT,
        DUO_PRIOR_GAMES,
        STRONG_SYNERGY_FLOOR,
    )
    return {
        "synergy_bonus": blended["score"],
        "synergy_absolute_score": blended["absolute_score"],
        "synergy_residual_score": blended["residual_score"],
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
        )
        weight = counter_role_weight(role, enemy_role)
        weighted_bonuses.append(blended["score"] * weight)
        weighted_absolute_scores.append(blended["absolute_score"] * weight)
        weighted_residual_scores.append(blended["residual_score"] * weight)
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
            row.get("base_adjusted_score", row["base_rating"]),
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
                    row.get("synergy_final_score", row["synergy_bonus"]),
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
                    row.get("counter_final_score", row["counter_bonus"]),
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
        weights = {"base": 0.30, "synergy": 0.35, "counter": 0.35}
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


def smooth_component_score(value, scale=30.0, limit=30.0):
    return math.tanh(float(value or 0) / scale) * limit


def relation_games_bonus(games, cap, reference_games):
    games = max(0, float(games or 0))
    if games <= 0:
        return 0.0
    return min(cap, math.log1p(games) / math.log1p(reference_games) * cap)


def apply_relation_games_bonus(score, games, cap, reference_games):
    score = float(score or 0)
    if score < -3:
        return score, 0.0
    bonus = relation_games_bonus(games, cap, reference_games)
    return score + bonus, bonus


def precision_sort_score(row):
    confidence_bonus = {
        "high": 3.0,
        "medium": 1.0,
        "low": -2.0,
        "very_low": -5.0,
    }.get(row.get("confidence_level"), 0.0)
    missing_penalty = len(row.get("missing_fields", [])) * 2.0
    return row.get("final_rating", 0.0) + confidence_bonus - missing_penalty


def is_precision_candidate(row, has_ally, has_enemies):
    if row.get("confidence_level") == "very_low":
        return False
    if has_ally and row.get("synergy_final_score", row.get("synergy_adjusted_score", 0)) < -8:
        return False
    if has_enemies and row.get("counter_final_score", row.get("counter_adjusted_score", 0)) < -8:
        return False
    if len(row.get("missing_fields", [])) >= 2 and row.get("confidence_level") in ("low", "very_low"):
        return False
    return True


def select_precise_results(results, bp_state):
    has_ally = bool(bp_state.get("ally_ad") or bp_state.get("ally_sup"))
    has_enemies = bool(bp_state.get("enemy_ad") or bp_state.get("enemy_sup"))
    preferred = [row for row in results if is_precision_candidate(row, has_ally, has_enemies)]
    fallback = [row for row in results if row not in preferred]
    preferred.sort(key=precision_sort_score, reverse=True)
    fallback.sort(key=precision_sort_score, reverse=True)
    return (preferred + fallback)[:5]


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
    return {"high": 2.0, "medium": 1.0, "low": -1.0, "very_low": -3.0}.get(level, 0.0)


def precision_tier_bonus(tier):
    if tier in ("S+", "S", "S-", "A+", "A", "A-"):
        return 1.0
    if tier in ("C+", "C", "C-", "D+", "D", "D-"):
        return -1.0
    return 0.0


def build_precision_meta(db, role, bp_state, row, ally):
    data_bonus = 0.0
    if row.get("duo_games", 0) > 0:
        data_bonus += 1.0
    if row.get("matchup_games", 0) > 0:
        data_bonus += 1.0
    missing_penalty = len(row.get("missing_fields", [])) * 1.5
    precision_score = (
        row["display_winrate"]
        + precision_confidence_bonus(row.get("confidence_level"))
        + data_bonus
        + precision_tier_bonus(row.get("tier"))
        - missing_penalty
    )

    tags = []
    if row.get("confidence_level") in ("high", "medium") and not row.get("missing_fields"):
        tags.append("稳健首选")
    if row.get("base_adjusted_score", row.get("base_rating", 0)) >= 12:
        tags.append("版本强势")
    if row.get("synergy_final_score", row.get("synergy_bonus", 0)) >= 8:
        tags.append("搭档适配")
    if row.get("counter_final_score", row.get("counter_bonus", 0)) >= 8:
        tags.append("对位优势")
    if row.get("synergy_games_bonus", 0) >= 2.5:
        tags.append("成熟组合")
    if row.get("counter_games_bonus", 0) >= 2:
        tags.append("对位样本足")
    if row["display_winrate"] >= 53 and row.get("confidence_level") in ("low", "very_low"):
        tags.append("高收益选择")
    if row.get("confidence_level") in ("low", "very_low"):
        tags.append("样本偏少")
    if row.get("missing_fields"):
        tags.append("数据不足")
    if not tags:
        tags.append("综合推荐")

    time_detail = build_time_detail(db, role, row["name"], ally, bp_state)
    if time_detail.get("primary"):
        tags.append(time_detail["primary"]["summary"])

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
    if time_detail.get("available") is False:
        risks.append("当前缓存暂无时间段强势数据，更新数据后可显示曲线。")
    if not risks:
        risks.append("暂无明显数据风险，仍需结合阵容和熟练度。")

    return {
        "precision_score": round(precision_score, 2),
        "precision_tags": tags[:4],
        "precision_reason": reason,
        "precision_detail": {
            "conclusion": reason,
            "timeline": time_detail,
            "data": {
                "recommend_index": row["display_winrate"],
                "confidence_label": row.get("confidence_label"),
                "base_rating": row.get("base_rating"),
                "base_adjusted_score": row.get("base_adjusted_score"),
                "synergy_bonus": row.get("synergy_bonus"),
                "synergy_final_score": row.get("synergy_final_score"),
                "synergy_games_bonus": row.get("synergy_games_bonus"),
                "counter_bonus": row.get("counter_bonus"),
                "counter_final_score": row.get("counter_final_score"),
                "counter_games_bonus": row.get("counter_games_bonus"),
                "duo_games": row.get("duo_games"),
                "matchup_games": row.get("matchup_games"),
            },
            "risks": risks,
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
        base_adjusted = smooth_component_score(score["base_rating"])
        synergy_final, synergy_games_bonus = apply_relation_games_bonus(
            synergy_adjusted, synergy["duo_games"], 4.0, 8000
        )
        counter_final, counter_games_bonus = apply_relation_games_bonus(
            counter_adjusted, counter["matchup_games"], 3.0, 5000
        )
        final_rating = (
            component_weights["base"] * base_adjusted
            + component_weights["synergy"] * synergy_final
            + component_weights["counter"] * counter_final
        )
        row = {
            "name": name,
            "tier": score["tier_label"],
            "tier_score": score["tier_score"],
            "base_winrate": round(score["base_winrate"] * 100, 2),
            "raw_base_rating": round(score["raw_base_rating"], 2),
            "base_rating": round(score["base_rating"], 2),
            "base_adjusted_score": round(base_adjusted, 2),
            "expectation_rating": round(score["expectation_rating"], 2),
            "tier_prior_rating": round(score["tier_prior_rating"], 2),
            "winrate_component": round(score["winrate_component"], 2),
            "base_confidence_multiplier": round(score["base_confidence_multiplier"], 4),
            "base_games": score["base_games"],
            "final_rating": round(final_rating, 2),
            "display_winrate": round(rating_to_winrate(final_rating) * 100, 2),
            "synergy_bonus": round(synergy["synergy_bonus"], 2),
            "synergy_adjusted_score": round(synergy_adjusted, 2),
            "synergy_final_score": round(synergy_final, 2),
            "synergy_games_bonus": round(synergy_games_bonus, 2),
            "synergy_absolute_score": round(synergy["synergy_absolute_score"], 2),
            "synergy_residual_score": round(synergy["synergy_residual_score"], 2),
            "synergy_confidence": round(synergy["synergy_confidence"], 4),
            "counter_bonus": round(counter["counter_bonus"], 2),
            "counter_adjusted_score": round(counter_adjusted, 2),
            "counter_final_score": round(counter_final, 2),
            "counter_games_bonus": round(counter_games_bonus, 2),
            "counter_absolute_score": round(counter["counter_absolute_score"], 2),
            "counter_residual_score": round(counter["counter_residual_score"], 2),
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
        row["precision_score"] = round(precision_sort_score(row), 2)
        # Legacy aliases keep existing clients usable while the UI migrates.
        row["final_score"] = row["display_winrate"]
        row["counter_score"] = row["counter_final_score"]
        row["synergy_score"] = row["synergy_final_score"]
        row["base_score"] = row["base_adjusted_score"]
        row["effective_weights"] = round_weights(component_weights)
        results.append(row)

    results.sort(key=lambda item: item["final_rating"], reverse=True)
    precise_results = select_precise_results(results, bp_state)
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
