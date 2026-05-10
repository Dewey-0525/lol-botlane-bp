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
                }
        except Exception as e:
            print(f"\n  获取 {lane} 梯队失败: {e}")
    return tiers, hero_stats


def fetch_single_champ(champ_key):
    """
    获取单个英雄的协同和克制数据
    关键修复：对 bottom 和 support 两个位置都发起请求，合并结果
    """
    syn_dict = {}
    vs_adc = {}
    vs_sup = {}

    for lane in LANES:
        try:
            # 1. 协同数据
            try:
                raw_syn = la.get_synergy(champ_key, lane=lane)
                if raw_syn:
                    # lane=bottom 时，team.support 给出搭配的辅助
                    for name, wr, games, _ in la.format_synergy(raw_syn, target_role="support", top_n=200):
                        syn_dict[name] = {"win_rate": wr, "games": games}
                    # lane=support 时，team.bottom 给出搭配的ADC
                    for name, wr, games, _ in la.format_synergy(raw_syn, target_role="bottom", top_n=200):
                        syn_dict[name] = {"win_rate": wr, "games": games}
            except Exception:
                pass  # 该位置无协同数据，跳过

            # 2. 克制数据
            try:
                raw_mat = la.get_matchup(champ_key, lane=lane)
                if raw_mat:
                    # enemy.bottom = 打敌方ADC的胜率
                    for name, wr, games, _ in la.format_matchup(raw_mat, enemy_role="bottom", top_n=200):
                        vs_adc[name] = {"win_rate": wr, "games": games}
                    # enemy.support = 打敌方辅助的胜率
                    for name, wr, games, _ in la.format_matchup(raw_mat, enemy_role="support", top_n=200):
                        vs_sup[name] = {"win_rate": wr, "games": games}
            except Exception:
                pass  # 该位置无对位数据，跳过

        except Exception:
            pass

    return champ_key, syn_dict, vs_adc, vs_sup


def fetch_matrix():
    print("\n[2/3] 正在获取协同与克制矩阵 (每个英雄爬2个位置，预计2-4分钟)...")
    all_champs = list(get_all().keys())
    synergy_db = {}
    counter_db = {}

    total = len(all_champs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_single_champ, c): c for c in all_champs}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            champ, syn, adc, sup = future.result()
            synergy_db[champ] = syn
            counter_db[champ] = {"vs_adc": adc, "vs_sup": sup}
            progress(i, total, champ)

    print("\n  矩阵获取完成!")
    return synergy_db, counter_db


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    start_time = time.time()

    print("=" * 50)
    print(" 🚀 LoL 下路 BP 数据预热工具 (双位置版)")
    print("=" * 50)

    tiers, hero_stats = fetch_tiers()
    synergy, counter = fetch_matrix()

    dataset = {
        "meta": {
            "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "Lolalytics (KR Emerald+)"
        },
        "tiers": tiers,
        "hero_stats": hero_stats,
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
