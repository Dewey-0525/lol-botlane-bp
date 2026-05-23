#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pre_fetch.py - 数据预热脚本 (修复版)
修复: 对每个英雄同时爬取 bottom + support 两个位置的数据
"""
import json, os, time, sys
import concurrent.futures
import scraper.lolalytics as la
from scraper.champion_map import get_all

DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "botlane_dataset.json")
MAX_WORKERS = 5
LANES = ["bottom", "support"]  # 关键修复：爬两个位置


def progress(current, total, name=""):
    pct = int(current / total * 100)
    bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
    sys.stdout.write(f"\r  [{bar}] {pct}% ({current}/{total}) {name:<15}")
    sys.stdout.flush()


def fetch_tiers():
    """获取两个位置的梯队和基础强度数据"""
    print("\n[1/3] 正在获取梯队数据...")
    tiers = {"support": {}, "bottom": {}}
    hero_stats = {"support": {}, "bottom": {}}
    fetch_errors = []
    
    all_champs = list(get_all().keys())
    for lane in LANES:
        try:
            tier_list = la.get_full_tier_list(all_champs, lane=lane, region="kr")
            for champ in tier_list:
                champion = champ["champion"]
                tiers[lane][champion] = champ["tier"]
                hero_stats[lane][champion] = {
                    "win_rate": champ.get("win_rate"),
                    "games": champ.get("matches", 0),
                    "stats_by_time": champ.get("stats_by_time", []),
                }
        except Exception as e:
            print(f"\n  获取 {lane} 梯队失败: {e}")
            fetch_errors.append(
                {
                    "stage": "tier",
                    "lane": lane,
                    "error": str(e),
                }
            )
    return tiers, hero_stats, fetch_errors


def fetch_single_champ_lane(champ_key, lane):
    """
    获取单个英雄在指定位置的协同和克制数据。
    """
    syn_dict = {}
    vs_adc = {}
    vs_sup = {}
    fetch_errors = []

    try:
        raw_syn = la.get_synergy(champ_key, lane=lane)
        if raw_syn:
            partner_role = "support" if lane == "bottom" else "bottom"
            for name, wr, games, _ in la.format_synergy(
                raw_syn, target_role=partner_role, top_n=200
            ):
                syn_dict[name] = {"win_rate": wr, "games": games}
    except Exception as e:
        fetch_errors.append(
            {
                "stage": "synergy",
                "champion": champ_key,
                "lane": lane,
                "error": str(e),
            }
        )

    try:
        raw_mat = la.get_matchup(champ_key, lane=lane)
        if raw_mat:
            for name, wr, games, _ in la.format_matchup(
                raw_mat, enemy_role="bottom", top_n=200
            ):
                vs_adc[name] = {"win_rate": wr, "games": games}
            for name, wr, games, _ in la.format_matchup(
                raw_mat, enemy_role="support", top_n=200
            ):
                vs_sup[name] = {"win_rate": wr, "games": games}
    except Exception as e:
        fetch_errors.append(
            {
                "stage": "matchup",
                "champion": champ_key,
                "lane": lane,
                "error": str(e),
            }
        )

    return champ_key, lane, syn_dict, vs_adc, vs_sup, fetch_errors


def fetch_single_champ(champ_key):
    """
    兼容旧调用：获取单个英雄两个位置的数据，并合并为旧结构。
    新推荐逻辑优先使用 fetch_matrix 生成的 by-lane 数据，避免双位置互相覆盖。
    """
    merged_syn = {}
    merged_adc = {}
    merged_sup = {}
    all_errors = []
    for lane in LANES:
        champ, _, syn, adc, sup, errors = fetch_single_champ_lane(champ_key, lane)
        merged_syn.update(syn)
        merged_adc.update(adc)
        merged_sup.update(sup)
        all_errors.extend(errors)
    return champ_key, merged_syn, merged_adc, merged_sup, all_errors


def merge_legacy_matrix(target, champ, syn, adc, sup):
    target["synergy"].setdefault(champ, {}).update(syn)
    target["counter"].setdefault(champ, {"vs_adc": {}, "vs_sup": {}})
    target["counter"][champ]["vs_adc"].update(adc)
    target["counter"][champ]["vs_sup"].update(sup)


def fetch_matrix(tiers=None):
    print("\n[2/3] 正在获取协同与克制矩阵 (按位置保存，预计2-4分钟)...")
    if tiers:
        lane_champs = {
            lane: [
                champ
                for champ, tier in tiers.get(lane, {}).items()
                if tier and tier != "?"
            ]
            for lane in LANES
        }
    else:
        all_champs = list(get_all().keys())
        lane_champs = {lane: all_champs for lane in LANES}

    synergy_by_lane = {lane: {} for lane in LANES}
    counter_by_lane = {lane: {} for lane in LANES}
    legacy = {"synergy": {}, "counter": {}}
    fetch_errors = []

    tasks = [
        (champ, lane)
        for lane in LANES
        for champ in lane_champs.get(lane, [])
    ]
    total = len(tasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_single_champ_lane, champ, lane): (champ, lane)
            for champ, lane in tasks
        }
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            champ, lane, syn, adc, sup, errors = future.result()
            synergy_by_lane[lane][champ] = syn
            counter_by_lane[lane][champ] = {"vs_adc": adc, "vs_sup": sup}
            merge_legacy_matrix(legacy, champ, syn, adc, sup)
            fetch_errors.extend(errors)
            progress(i, total, f"{champ}/{lane}")

    print("\n  矩阵获取完成!")
    return (
        legacy["synergy"],
        legacy["counter"],
        synergy_by_lane,
        counter_by_lane,
        fetch_errors,
        total,
    )


def legacy_fetch_matrix():
    """
    旧版全英雄双位置抓取逻辑保留作排查用，不在主流程使用。
    """
    all_champs = list(get_all().keys())
    synergy_db = {}
    counter_db = {}
    fetch_errors = []

    total = len(all_champs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_single_champ, c): c for c in all_champs}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            champ, syn, adc, sup, errors = future.result()
            synergy_db[champ] = syn
            counter_db[champ] = {"vs_adc": adc, "vs_sup": sup}
            fetch_errors.extend(errors)
            progress(i, total, champ)

    print("\n  矩阵获取完成!")
    return synergy_db, counter_db, fetch_errors


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    start_time = time.time()

    print("=" * 50)
    print(" 🚀 LoL 下路 BP 数据预热工具 (双位置版)")
    print("=" * 50)

    all_champions = list(get_all().keys())
    tiers, hero_stats, tier_errors = fetch_tiers()
    synergy, counter, synergy_by_lane, counter_by_lane, matrix_errors, matrix_attempts = fetch_matrix(tiers)
    fetch_errors = tier_errors + matrix_errors
    fetch_attempts = len(LANES) + matrix_attempts * 2
    coverage = {
        "bottom_champions": len(tiers.get("bottom", {})),
        "support_champions": len(tiers.get("support", {})),
        "bottom_hero_stats": len(hero_stats.get("bottom", {})),
        "support_hero_stats": len(hero_stats.get("support", {})),
        "synergy_rows": sum(len(value) for value in synergy.values()),
        "counter_adc_rows": sum(len(value["vs_adc"]) for value in counter.values()),
        "counter_support_rows": sum(len(value["vs_sup"]) for value in counter.values()),
        "bottom_synergy_rows": sum(len(value) for value in synergy_by_lane["bottom"].values()),
        "support_synergy_rows": sum(len(value) for value in synergy_by_lane["support"].values()),
        "bottom_counter_adc_rows": sum(len(value["vs_adc"]) for value in counter_by_lane["bottom"].values()),
        "support_counter_adc_rows": sum(len(value["vs_adc"]) for value in counter_by_lane["support"].values()),
        "bottom_counter_support_rows": sum(len(value["vs_sup"]) for value in counter_by_lane["bottom"].values()),
        "support_counter_support_rows": sum(len(value["vs_sup"]) for value in counter_by_lane["support"].values()),
        "fetch_attempts": fetch_attempts,
        "fetch_errors": len(fetch_errors),
    }

    dataset = {
        "meta": {
            "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "Lolalytics (KR Emerald+, current patch)",
            "coverage": coverage,
            "fetch_errors_count": len(fetch_errors),
            "fetch_errors": fetch_errors[:100],
        },
        "tiers": tiers,
        "hero_stats": hero_stats,
        "synergy_by_lane": synergy_by_lane,
        "counter_by_lane": counter_by_lane,
        "synergy": synergy,
        "counter": counter
    }

    tmp_file = OUTPUT_FILE + ".tmp"
    print(f"\n[3/3] 正在保存到 {OUTPUT_FILE} ...")
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False)
    os.replace(tmp_file, OUTPUT_FILE)

    # 统计数据覆盖率
    total_syn = sum(len(v) for v in synergy.values())
    total_counter_adc = sum(len(v["vs_adc"]) for v in counter.values())
    total_counter_sup = sum(len(v["vs_sup"]) for v in counter.values())
    
    elapsed = time.time() - start_time
    print(f"""
┌─────────────────────────────────┐
│  ✅ 预热完成!                   │
│  耗时: {elapsed:.0f} 秒                    │
│  协同数据: {total_syn} 条                │
│  克制(vs ADC): {total_counter_adc} 条           │
│  克制(vs 辅助): {total_counter_sup} 条           │
│  文件大小: {os.path.getsize(OUTPUT_FILE)/1024:.0f} KB               │
│  现在可秒出推荐结果 ⚡             │
└─────────────────────────────────┘
""")


if __name__ == "__main__":
    main()
