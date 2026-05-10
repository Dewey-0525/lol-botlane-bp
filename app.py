#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - LoL 下路 BP 助手 API 服务

本地开发启动:
  python3 app.py

主要接口:
  GET  /api/health
  GET  /api/champions
  POST /api/recommend
  GET  /api/counter/<champion>
"""

import json
import os

from flask import Flask, jsonify, render_template, request

from bp_engine import (
    VALID_KEYS,
    extract_stat,
    find_counter_picks,
    new_bp_state,
    run_recommend,
)
from champion_aliases import CHAMPION_ALIASES
from scraper.chinese_getchampion.hero_id_mapping import HERO_ID_MAPPING

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "botlane_dataset.json")
CN_NAMES_PATH = os.path.join(BASE_DIR, "scraper", "chinese_getchampion", "英雄名字.txt")

app = Flask(__name__)
DB = None
CHAMPION_META = None


def load_db():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            "找不到 data/botlane_dataset.json，请先运行 python3 pre_fetch.py"
        )
    with open(DB_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def get_db():
    global DB
    if DB is None:
        DB = load_db()
    return DB


def load_champion_meta():
    cn_names = []
    if os.path.exists(CN_NAMES_PATH):
        with open(CN_NAMES_PATH, "r", encoding="utf-8") as file:
            cn_names = [line.strip() for line in file if line.strip()]

    meta = {}
    for (key, champion_id), cn_name in zip(HERO_ID_MAPPING.items(), cn_names):
        aliases = CHAMPION_ALIASES.get(key, [])
        search_text = " ".join(
            [cn_name, key, key.title(), str(champion_id), *aliases]
        ).lower()
        meta[key] = {
            "id": key,
            "name": key.title(),
            "cn_name": cn_name,
            "aliases": aliases,
            "champion_id": champion_id,
            "avatar": f"/static/avatars/{champion_id}.png",
            "search_text": search_text,
        }
    return meta


def get_champion_meta():
    global CHAMPION_META
    if CHAMPION_META is None:
        CHAMPION_META = load_champion_meta()
    return CHAMPION_META


def enrich_champion(champion_id, fallback_tier=None):
    meta = get_champion_meta().get(champion_id, {})
    return {
        "id": champion_id,
        "name": meta.get("name", champion_id.title()),
        "cn_name": meta.get("cn_name", champion_id.title()),
        "aliases": meta.get("aliases", []),
        "champion_id": meta.get("champion_id"),
        "tier": fallback_tier,
        "avatar": meta.get("avatar", f"/static/avatars/{champion_id}.png"),
        "search_text": meta.get("search_text", champion_id),
    }


def enrich_recommendation_rows(rows):
    for row in rows:
        meta = enrich_champion(row["name"], row.get("tier"))
        row["display_name"] = meta["cn_name"]
        row["english_name"] = meta["name"]
        row["champion_id"] = meta["champion_id"]
        row["avatar"] = meta["avatar"]
    return rows


def build_bp_display(bp_state):
    labels = {
        "ally_ad": "己方 ADC",
        "ally_sup": "己方辅助",
        "enemy_ad": "敌方 ADC",
        "enemy_sup": "敌方辅助",
    }
    display = {}
    for key, value in bp_state.items():
        champion = enrich_champion(value) if value else None
        display[key] = {
            "label": labels[key],
            "id": value,
            "display_name": champion["cn_name"] if champion else "未知",
            "avatar": champion["avatar"] if champion else None,
        }
    return display


def build_result_summary(results):
    confidence_counts = {"high": 0, "medium": 0, "low": 0, "very_low": 0}
    for row in results:
        level = row.get("confidence_level")
        if level in confidence_counts:
            confidence_counts[level] += 1
    return {
        "total": len(results),
        "confidence_counts": confidence_counts,
        "complete_data_count": confidence_counts["high"],
    }


def build_score_rows(score_map, db, lane=None, top_n=20):
    rows = []
    for champion_id, raw_value in score_map.items():
        tier = None
        if lane:
            tier = db["tiers"].get(lane, {}).get(champion_id)
        if tier == "?":
            continue
        stat = extract_stat(raw_value, legacy_games=300)
        row = {
            "name": champion_id,
            "tier": tier,
            "win_rate": round(stat["winrate"] * 100, 2) if stat["winrate"] else 0,
            "games": int(stat["games"]),
        }
        rows.append(row)
    rows.sort(key=lambda item: item["win_rate"], reverse=True)
    return enrich_recommendation_rows(rows[:top_n])


def api_error(message, status_code=400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status_code


def normalize_top_n(value, default=10, minimum=1, maximum=50):
    if value is None:
        return default
    try:
        top_n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, top_n))


def build_champion_list(db, lane):
    champions = []
    for name, tier in db["tiers"].get(lane, {}).items():
        if tier == "?":
            continue
        champion = enrich_champion(name, tier)
        champions.append(champion)
    return champions


@app.get("/api/health")
def health():
    db = get_db()
    return jsonify(
        {
            "ok": True,
            "service": "lol-botlane-bp",
            "data_meta": db.get("meta", {}),
        }
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/champions")
def champions():
    db = get_db()
    return jsonify(
        {
            "ok": True,
            "data_meta": db.get("meta", {}),
            "bottom": build_champion_list(db, "bottom"),
            "support": build_champion_list(db, "support"),
        }
    )


@app.post("/api/recommend")
def recommend():
    db = get_db()
    payload = request.get_json(silent=True) or {}

    role = str(payload.get("role", "")).strip().lower()
    if role not in ("support", "adc"):
        return api_error("role 必须是 support 或 adc")

    raw_bp_state = payload.get("bp_state") or {}
    if not isinstance(raw_bp_state, dict):
        return api_error("bp_state 必须是对象")

    bp_state = new_bp_state()
    for key in VALID_KEYS:
        value = raw_bp_state.get(key)
        bp_state[key] = str(value).strip().lower() if value else None

    top_n = normalize_top_n(payload.get("top_n"))
    result = run_recommend(role, bp_state, db, top_n=top_n)
    enrich_recommendation_rows(result["results"])
    result["bp_display"] = build_bp_display(result["bp_state"])
    result["summary"] = build_result_summary(result["results"])
    result["ok"] = True
    return jsonify(result)


@app.get("/api/counter/<champion>")
def counter(champion):
    db = get_db()
    top_n = normalize_top_n(request.args.get("top_n"), default=5)
    champion = champion.strip().lower()
    if not champion:
        return api_error("champion 不能为空")

    return jsonify(
        {
            "ok": True,
            "champion": champion,
            "data_meta": db.get("meta", {}),
            "adc": enrich_recommendation_rows(
                find_counter_picks(champion, "adc", db, top_n=top_n)
            ),
            "support": enrich_recommendation_rows(
                find_counter_picks(champion, "support", db, top_n=top_n)
            ),
        }
    )


@app.get("/api/synergy/<champion>")
def synergy(champion):
    db = get_db()
    top_n = normalize_top_n(request.args.get("top_n"), default=20)
    champion = champion.strip().lower()
    if not champion:
        return api_error("champion 不能为空")

    rows = []
    for partner, raw_value in db["synergy"].get(champion, {}).items():
        lane = "bottom" if partner in db["tiers"].get("bottom", {}) else "support"
        tier = db["tiers"].get(lane, {}).get(partner)
        if tier == "?":
            continue
        stat = extract_stat(raw_value, legacy_games=300)
        rows.append(
            {
                "name": partner,
                "tier": tier,
                "win_rate": round(stat["winrate"] * 100, 2) if stat["winrate"] else 0,
                "games": int(stat["games"]),
            }
        )
    rows.sort(key=lambda item: item["win_rate"], reverse=True)

    return jsonify(
        {
            "ok": True,
            "champion": enrich_champion(champion),
            "data_meta": db.get("meta", {}),
            "results": enrich_recommendation_rows(rows[:top_n]),
        }
    )


@app.get("/api/matchup/<champion>")
def matchup(champion):
    db = get_db()
    top_n = normalize_top_n(request.args.get("top_n"), default=20)
    champion = champion.strip().lower()
    if not champion:
        return api_error("champion 不能为空")

    data = db["counter"].get(champion, {})
    return jsonify(
        {
            "ok": True,
            "champion": enrich_champion(champion),
            "data_meta": db.get("meta", {}),
            "vs_adc": build_score_rows(
                data.get("vs_adc", {}), db, lane="bottom", top_n=top_n
            ),
            "vs_support": build_score_rows(
                data.get("vs_sup", {}), db, lane="support", top_n=top_n
            ),
        }
    )


@app.get("/api/tier/<lane>")
def tier(lane):
    db = get_db()
    lane = lane.strip().lower()
    if lane == "adc":
        lane = "bottom"
    if lane not in ("bottom", "support"):
        return api_error("lane 必须是 bottom/adc 或 support")

    return jsonify(
        {
            "ok": True,
            "lane": lane,
            "data_meta": db.get("meta", {}),
            "results": build_champion_list(db, lane),
        }
    )


if __name__ == "__main__":
    get_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=False, use_reloader=False)
