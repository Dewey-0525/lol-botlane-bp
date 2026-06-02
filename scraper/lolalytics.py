"""
lolalytics.py - 数据抓取模块 (增强版)
增加: 反制位计算、综合BP评分、Tier List获取
"""
import json
import os
import re
import time
from typing import Dict, List

import requests

from . import champion_map as cm


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_cache = {}
MIN_GAMES = 100
TIME_BUCKETS = ["0-20", "20-25", "25-30", "30-35", "35+"]
TIER_KEY_LABELS = {
    "1": "S+",
    "2": "S",
    "3": "S-",
    "4": "A+",
    "5": "A",
    "6": "A-",
    "7": "B+",
    "8": "B",
    "9": "B-",
    "10": "C+",
    "11": "C",
    "12": "C-",
    "13": "D+",
    "14": "D",
    "15": "D-",
}


def _to_base36(num):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    result = ""
    while num > 0:
        result = chars[num % 36] + result
        num = num // 36
    return result


def _parse_obj(objs, id_str):
    idx = int(id_str, 36)
    obj = objs[idx]
    if isinstance(obj, list):
        return [_parse_obj(objs, v) for v in obj]
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _parse_obj(objs, v) for k, v in obj.items()}
    return obj


def _find_target(objs):
    for i, obj in enumerate(objs):
        if isinstance(obj, dict) and "analysed" in obj and "avgWr" in obj and "enemy" in obj:
            return i
    return None


def _find_time_container(obj):
    if isinstance(obj, dict):
        if (
            "time" in obj
            and "timeWin" in obj
            and isinstance(obj.get("time"), (dict, list))
            and isinstance(obj.get("timeWin"), (dict, list))
        ):
            return obj
        for value in obj.values():
            found = _find_time_container(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_time_container(value)
            if found:
                return found
    return None


def extract_stats_by_time(data):
    container = _find_time_container(data)
    if not container:
        return []
    games_by_time = container.get("time") or []
    wins_by_time = container.get("timeWin") or []

    if isinstance(games_by_time, dict) and isinstance(wins_by_time, dict):
        bucket_groups = [
            ("0-20", ("1", "2")),
            ("20-25", ("3",)),
            ("25-30", ("4",)),
            ("30-35", ("5",)),
            ("35+", ("6", "7")),
        ]
        result = []
        for label, keys in bucket_groups:
            games = sum(float(games_by_time.get(key, 0) or 0) for key in keys)
            wins = sum(float(wins_by_time.get(key, 0) or 0) for key in keys)
            result.append({"label": label, "wins": wins, "games": games})
        return result

    result = []
    for index, (games, wins) in enumerate(zip(games_by_time, wins_by_time)):
        if index >= len(TIME_BUCKETS):
            break
        result.append(
            {
                "label": TIME_BUCKETS[index],
                "wins": float(wins or 0),
                "games": float(games or 0),
            }
        )
    return result


def _to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def add_patch_param(params, patch):
    if patch:
        params["patch"] = patch


def mega_get_json(params, timeout=15, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            res = requests.get(
                "https://a1.lolalytics.com/mega/",
                params=params,
                timeout=timeout,
                headers=HEADERS,
            )
            if res.status_code == 200:
                return res.json()
            last_error = ConnectionError(f"mega API 返回 {res.status_code}")
        except requests.RequestException as exc:
            last_error = exc

        if attempt < retries - 1:
            time.sleep(0.8 * (attempt + 1))

    raise ConnectionError(f"mega API 请求失败: {last_error}")


def _champion_has_lane(cid, lane, patch=None, region="kr"):
    if lane not in ("bottom", "support"):
        return True

    cache_key = f"tier_lane_cids_{patch}_{lane}_{region}"
    if cache_key not in _cache:
        tier_data = get_full_tier_list(list(cm.get_all().keys()), patch=patch, lane=lane, region=region)
        lane_cids = set()
        for row in tier_data:
            champion = row.get("champion")
            if not champion:
                continue
            champion_id = cm.get_id(champion)
            if champion_id is None:
                continue
            try:
                lane_cids.add(int(champion_id))
            except (TypeError, ValueError):
                continue
        _cache[cache_key] = lane_cids

    try:
        return int(cid) in _cache[cache_key]
    except (TypeError, ValueError):
        return False


def get_synergy(champion_name, patch=None, region="kr", lane="bottom"):
    cache_key = f"synergy_{champion_name}_{patch}_{region}_{lane}"
    if cache_key in _cache:
        return _cache[cache_key]

    name = champion_name.strip().lower()
    if name == "wukong":
        name = "monkeyking"

    params = {
        "ep": "build-team",
        "v": "1",
        "tier": "emerald_plus",
        "queue": "ranked",
        "region": region,
        "c": name,
        "lane": lane,
    }
    add_patch_param(params, patch)

    data = mega_get_json(params, timeout=15)
    if data.get("status") == 404:
        raise ValueError(f"没有 {champion_name} 的协同数据")

    _cache[cache_key] = data
    return data


def get_matchup(champion_name, patch=None, lane="bottom", region="kr"):
    cache_key = f"matchup_{champion_name}_{patch}_{lane}_{region}"
    if cache_key in _cache:
        return _cache[cache_key]

    name = champion_name.strip().lower()
    if name == "wukong":
        name = "monkeyking"

    enemy = {}
    header = {}
    for vs_lane in ("bottom", "support"):
        params = {
            "ep": "counter",
            "v": "1",
            "tier": "emerald_plus",
            "queue": "ranked",
            "region": region,
            "c": name,
            "lane": lane,
            "vslane": vs_lane,
        }
        add_patch_param(params, patch)

        data = mega_get_json(params, timeout=20)
        if not data.get("response", {}).get("valid", False):
            raise ValueError(f"counter API 返回无效数据: {champion_name}/{lane}/{vs_lane}")

        if not header:
            stats = data.get("stats", {})
            header = {
                "tier": None,
                "rank": None,
                "rankTotal": None,
                "wr": _to_float(stats.get("wr")),
                "pr": _to_float(stats.get("pr")),
                "br": _to_float(stats.get("br")),
                "n": _to_int(stats.get("analysed")),
            }
        enemy[vs_lane] = data.get("counters", [])

    parsed = {"header": header, "enemy": enemy, "team": {}}
    _cache[cache_key] = parsed
    return parsed


def get_counter_picks(enemy_champion: str, target_role: str = "bottom", top_n: int = 10) -> List[Dict]:
    try:
        data = get_matchup(enemy_champion)
    except Exception as e:
        print(f"错误: 无法获取 {enemy_champion} 的数据。原因: {e}")
        return []

    lookup_key = target_role
    if target_role == "adc":
        lookup_key = "bottom"

    rows = data.get("enemy", {}).get(lookup_key, [])

    counters = []
    for row in rows:
        if isinstance(row, dict):
            key = row.get("cid")
            enemy_win_rate = row.get("vsWr")
            pr = row.get("pr", 0)
            n = row.get("n", 0)
        else:
            key, enemy_win_rate, _d1, _d2, pr, n = row

        if n < MIN_GAMES:
            continue

        name = cm.get_name(key)
        if name is None:
            continue

        my_advantage_win_rate = 100 - enemy_win_rate
        counters.append(
            {
                "champion": name,
                "my_win_rate": round(my_advantage_win_rate, 1),
                "enemy_win_rate": round(enemy_win_rate, 1),
                "matches": n,
                "pick_rate": round(pr * 100, 1) if pr < 1 else round(pr, 1),
            }
        )

    counters.sort(key=lambda x: x["my_win_rate"], reverse=True)
    return counters[:top_n]


def format_synergy(data, target_role="support", top_n=10):
    rows = data.get("team", {}).get(target_role, [])
    result = []
    for row in rows:
        key, wr, _d1, _d2, pr, n = row
        if n < MIN_GAMES:
            continue
        name = cm.get_name(key)
        if name is None:
            continue
        result.append((name, wr, n, pr))
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top_n]


def format_matchup(data, enemy_role="bottom", top_n=10):
    rows = data.get("enemy", {}).get(enemy_role, [])
    result = []
    for row in rows:
        if isinstance(row, dict):
            key = row.get("cid")
            wr = row.get("vsWr")
            pr = row.get("pr", 0)
            n = row.get("n", 0)
        else:
            key, wr, _d1, _d2, pr, n = row

        if n < MIN_GAMES:
            continue
        if enemy_role in ("bottom", "support") and not _champion_has_lane(key, enemy_role):
            continue

        name = cm.get_name(key)
        if name is None:
            continue
        result.append((name, wr, n, pr))
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top_n]


def get_champion_tier(champion_name: str, patch=None, lane="bottom", region="kr") -> Dict:
    tier_list = get_full_tier_list([champion_name], patch=patch, lane=lane, region=region)
    normalized = champion_name.strip().lower()
    for row in tier_list:
        if row.get("champion") == normalized:
            return row
    return None


def get_full_tier_list(champion_names, patch=None, lane="bottom", region="kr", delay=0.5, max_workers=5):
    """通过 Lolalytics mega tier 端点获取指定位置梯队数据。"""
    cache_key = f"tier_{patch}_{lane}_{region}"
    if cache_key in _cache:
        tier_data = _cache[cache_key]
    else:
        params = {
            "ep": "tier",
            "v": "1",
            "tier": "emerald_plus",
            "queue": "ranked",
            "region": region,
            "lane": lane,
        }
        add_patch_param(params, patch)
        tier_data = mega_get_json(params, timeout=20)
        if not tier_data.get("response", {}).get("valid", False):
            raise ValueError(f"tier API 返回无效数据: {lane}")
        _cache[cache_key] = tier_data

    requested = {name.strip().lower() for name in champion_names}
    tier_list = []
    lane_data = tier_data.get("tier", {})

    for tier_key, tier_bucket in lane_data.items():
        tier_label = TIER_KEY_LABELS.get(str(tier_key), "?")
        cid_map = tier_bucket.get("lane", {}).get(lane, {}).get("cid", {})
        for cid, row in cid_map.items():
            champion = cm.get_name(int(cid))
            if not champion or champion not in requested:
                continue

            matches = _to_int(row.get("games"))
            if matches < MIN_GAMES:
                continue

            tier_list.append(
                {
                    "champion": champion,
                    "tier": tier_label,
                    "rank": row.get("rank"),
                    "rank_total": None,
                    "win_rate": _to_float(row.get("wr")),
                    "pick_rate": _to_float(row.get("pr")),
                    "ban_rate": _to_float(row.get("br")),
                    "matches": matches,
                    "damage": None,
                    "stats_by_time": [],
                }
            )

    tier_list.sort(
        key=lambda x: int(x.get("rank", 9999)) if str(x.get("rank", "")).isdigit() else 9999
    )
    return tier_list
