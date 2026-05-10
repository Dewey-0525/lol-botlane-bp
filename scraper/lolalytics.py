"""
lolalytics.py - 数据抓取模块 (增强版)
增加: 反制位计算、综合BP评分、Tier List获取
"""
import concurrent.futures
import threading
import requests
import json
import re
import os
import time
from typing import List, Dict, Tuple

# 导入 champion_map
from . import champion_map as cm

# 配置
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_cache = {}
MIN_GAMES = 300  # 最低场次阈值


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


def get_synergy(champion_name, patch="16.9", region="kr", lane="bottom"):
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
        "patch": patch,
        "c": name,
        "lane": lane,
    }

    res = requests.get(
        "https://a1.lolalytics.com/mega/", params=params, timeout=15, headers=HEADERS
    )
    if res.status_code != 200:
        raise ConnectionError(f"synergy API 返回 {res.status_code}")
    data = res.json()
    
    if data.get("status") == 404:
        raise ValueError(f"没有 {champion_name} 的协同数据")

    _cache[cache_key] = data
    return data


def get_matchup(champion_name, patch="16.9", lane="bottom", region="kr"):
    cache_key = f"matchup_{champion_name}_{patch}_{lane}_{region}"
    if cache_key in _cache:
        return _cache[cache_key]

    name = champion_name.strip().lower()
    if name == "wukong":
        name = "monkeyking"

    params = {
        "tier": "emerald_plus",
        "region": region,
        "patch": patch,
    }
    if lane:
        params["lane"] = lane

    url = f"https://lolalytics.com/lol/{name}/build/"
    res = requests.get(url, params=params, timeout=20, headers=HEADERS)
    if res.status_code != 200:
        raise ConnectionError(f"matchup 页面返回 {res.status_code}")

    html = res.text
    regex = r'<script\s+type=["\']qwik/json["\'][^>]*>([\s\S]*?)</script>'
    match = re.search(regex, html, re.IGNORECASE)
    if not match:
        raise ValueError(f"在 {name} 的页面中没找到 qwik/json")

    qwik = json.loads(match.group(1))
    objs = qwik.get("objs", [])
    target_idx = _find_target(objs)
    if target_idx is None:
        raise ValueError(f"在 {name} 的 qwik 数据中没找到目标对象")

    parsed = _parse_obj(objs, _to_base36(target_idx))
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
        key, enemy_win_rate, d1, d2, pr, n = row
        
        if n < MIN_GAMES:
            continue
            
        name = cm.get_name(key)
        if name is None:
            continue
            
        my_advantage_win_rate = 100 - enemy_win_rate
        
        counters.append({
            "champion": name,
            "my_win_rate": round(my_advantage_win_rate, 1),
            "enemy_win_rate": round(enemy_win_rate, 1),
            "matches": n,
            "pick_rate": round(pr * 100, 1) if pr < 1 else round(pr, 1)
        })
    
    counters.sort(key=lambda x: x["my_win_rate"], reverse=True)
    return counters[:top_n]


def format_synergy(data, target_role="support", top_n=10):
    rows = data.get("team", {}).get(target_role, [])
    result = []
    for row in rows:
        key, wr, d1, d2, pr, n = row
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
        key, wr, d1, d2, pr, n = row
        if n < MIN_GAMES:
            continue
        name = cm.get_name(key)
        if name is None:
            continue
        result.append((name, wr, n, pr))
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top_n]


# =====================================================================
# 🎯 新增功能：Tier List (英雄梯队) 数据获取
# =====================================================================

def get_champion_tier(champion_name: str, patch="16.9", lane="bottom", region="kr") -> Dict:
    """
    获取单个英雄的梯队信息和基础数据
    复用 get_matchup 的缓存，不产生多余网络请求
    """
    try:
        # 直接复用已有函数，利用 _cache 避免重复抓取
        data = get_matchup(champion_name, patch, lane, region)
        header = data.get("header", {})
        
        return {
            "champion": champion_name,
            "tier": header.get("tier"),            # "S+", "S", "A", "B", "C" 等
            "rank": header.get("rank"),            # 当前位置排名数字 (越小越强)
            "rank_total": header.get("rankTotal"), # 当前位置总英雄数
            "win_rate": header.get("wr"),          # 胜率
            "pick_rate": header.get("pr"),         # 登场率
            "ban_rate": header.get("br"),          # 禁用率
            "matches": header.get("n"),            # 总场次
            "damage": header.get("damage"),        # 伤害分布字典
        }
    except Exception as e:
        # print(f"[Tier] 获取 {champion_name} 梯队失败: {e}")
        return None


def get_full_tier_list(champion_names, patch="16.9", lane="bottom", region="kr", delay=0.5, max_workers=5):
    """
    并发获取所有英雄的梯队数据
    """
    import concurrent.futures
    import threading
    
    tier_list = []
    total = len(champion_names)
    
    # 1. 先查缓存，能拿的先拿
    cached_count = 0
    uncached = []
    for name in champion_names:
        cache_key = f"matchup_{name}_{patch}_{lane}_{region}"
        if cache_key in _cache:
            result = get_champion_tier(name, patch, lane, region)
            if result and result.get("matches", 0) >= MIN_GAMES:
                tier_list.append(result)
            cached_count += 1
        else:
            uncached.append(name)
    
    if cached_count > 0:
        print(f"  📦 缓存命中: {cached_count}/{total}")
    
    if uncached:
        print(f"  🌐 需请求: {len(uncached)} 个英雄 ({max_workers}线程并发)...")
        done_count = [cached_count]  # 用列表方便闭包修改
        lock = threading.Lock()
        
        def fetch_one(name):
            result = get_champion_tier(name, patch, lane, region)
            with lock:
                done_count[0] += 1
                d = done_count[0]
                bar_len = 30
                filled = int(bar_len * d / total)
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"\r  [{bar}] {d}/{total} {name}", end="", flush=True)
            return name, result
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_one, name): name for name in uncached}
            for future in concurrent.futures.as_completed(futures):
                name, result = future.result()
                if result and result.get("matches", 0) >= MIN_GAMES:
                    tier_list.append(result)
        
        print()  # 换行
    
    # 2. 按排名排序 (强制 int)
    tier_list.sort(key=lambda x: int(x.get("rank", 9999)) if str(x.get("rank", "")).isdigit() else 9999)
    return tier_list
    

