#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os, json

from bp_engine import (
    VALID_KEYS,
    derive_state,
    find_counter_picks,
    new_bp_state,
    run_recommend as engine_run_recommend,
)

# ==========================================
# 本地数据库加载
# ==========================================
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "botlane_dataset.json")
DB = None

def load_db():
    global DB
    if not os.path.exists(DB_PATH):
        print("\n❌ 错误: 找不到本地数据库文件！")
        print("👉 请先运行预热脚本: python3 pre_fetch.py\n")
        sys.exit(1)
    with open(DB_PATH, 'r', encoding='utf-8') as f:
        DB = json.load(f)

# ==========================================
# 工具函数
# ==========================================
def print_header(title):
    print("\n" + "=" * 50)
    print(f" {title}")
    print("=" * 50)

def print_table(headers, rows):
    if not rows: print("  (无数据)"); return
    cw = [len(h) for h in headers]
    for row in rows:
        for i, item in enumerate(row):
            cw[i] = max(cw[i], len(str(item)))
    hdr = "  ".join(h.ljust(w) for h, w in zip(headers, cw))
    print(hdr); print("-" * len(hdr))
    for row in rows:
        print("  ".join(str(item).ljust(w) for item, w in zip(row, cw)))

def format_evidence(result):
    return result.get("evidence_label", "梯队")

def parse_recommend_args(args):
    if len(args) < 3: return None
    role = args[2].lower()
    if role not in ['support', 'adc']: return None
    bp_state = new_bp_state()
    for item in args[3:]:
        if '=' not in item: return None
        key, value = item.split('=', 1)
        if key not in VALID_KEYS: return None
        bp_state[key] = value.lower()
    return {"role": role, "bp_state": bp_state, "state": derive_state(bp_state)}

def run_recommend(role, bp_state):
    recommendation = engine_run_recommend(role, bp_state, DB)
    role_cn = recommendation["role_label"]
    
    info_str = f"状态 {recommendation['state']}"
    if recommendation["ally"]:
        info_str += f" | 己方: {recommendation['ally'].upper()}"
    if recommendation["enemies"]:
        enemy_text = ", ".join(e.upper() for e in recommendation["enemies"])
        info_str += f" | 敌方: {enemy_text}"
    
    print_header(f"BP 推荐: {role_cn} ({info_str})")
    print(f"  📦 数据源: 本地缓存 (更新于 {DB['meta']['update_time']}) ⚡")

    print("\n  模型: 基础表现 + 配合 + 对位\n")
    rows = [
        (
            i,
            r["name"].title(),
            r["tier"],
            f"{r['display_winrate']:.1f}%",
            f"{r['base_rating']:.1f}",
            f"{r['counter_bonus']:.1f}",
            f"{r['synergy_bonus']:.1f}",
            r.get("confidence_label", "-"),
        )
        for i, r in enumerate(recommendation["results"], 1)
    ]
    print_table(["排名", f"推荐{role_cn}", "梯队", "预计胜率", "基础表现", "对位", "配合", "可信度"], rows)

# ==========================================
# 原有功能模块 (纯本地读取)
# ==========================================
def cmd_tier(lane="bottom", region="kr"):
    lane_label = "ADC" if lane == "bottom" else "辅助"
    print_header(f"Tier List - {lane_label} (韩服 翡翠+ 单双排)")
    print(f"  📦 数据源: 本地缓存 (更新于 {DB['meta']['update_time']}) ⚡\n")
    
    tier_data = DB['tiers'].get(lane, {})
    # 按 Tier 排序
    order = {'S+':0, 'S':1, 'S-':2, 'A+':3, 'A':4, 'A-':5, 'B+':6, 'B':7, 'B-':8, 'C+':9, 'C':10, 'C-':11, 'D':12}
    sorted_champs = sorted(tier_data.items(), key=lambda x: order.get(x[1], 99))
    
    rows = []; current_tier = ""
    for champ, tier in sorted_champs:
        if tier != current_tier:
            if rows: print_table(["排名","英雄","梯队",""], rows); rows = []
            current_tier = tier
            print(f"\n  【{current_tier} 梯队】\n")
        rows.append((len(rows)+1, champ.title(), tier, ""))
    if rows: print_table(["排名","英雄","梯队",""], rows)

def cmd_synergy(name):
    print_header(f"{name.upper()} - 协同数据 (队友选谁赢面大)")
    print(f"  📦 数据源: 本地缓存 ⚡\n")
    syn_data = DB['synergy'].get(name, {})
    if not syn_data: print("  未找到数据"); return
    sorted_syn = sorted(syn_data.items(), key=lambda x: x[1], reverse=True)[:15]
    rows = [(i, p.title(), f"{w}%", "✅" if w > 53.0 else "") for i, (p, w) in enumerate(sorted_syn, 1)]
    print_table(["排名","英雄","胜率",""], rows)

def cmd_matchup(name):
    print_header(f"{name.upper()} - 克制数据 (我打谁好打)")
    print(f"  📦 数据源: 本地缓存 ⚡\n")
    mat_data = DB['counter'].get(name, {})
    vs_adc = mat_data.get('vs_adc', {})
    vs_sup = mat_data.get('vs_sup', {})
    
    if vs_adc:
        print("  [ 打敌方 ADC ]")
        sorted_adc = sorted(vs_adc.items(), key=lambda x: x[1], reverse=True)[:10]
        rows = [(i, p.title(), f"{w}%", "✅" if w > 53.0 else "") for i, (p, w) in enumerate(sorted_adc, 1)]
        print_table(["排名","敌方ADC","胜率",""], rows)
        
    if vs_sup:
        print("\n  [ 打敌方 辅助 ]")
        sorted_sup = sorted(vs_sup.items(), key=lambda x: x[1], reverse=True)[:10]
        rows = [(i, p.title(), f"{w}%", "✅" if w > 53.0 else "") for i, (p, w) in enumerate(sorted_sup, 1)]
        print_table(["排名","敌方辅助","胜率",""], rows)

def cmd_counter(name):
    print_header(f"敌方选了 {name.upper()}，我该选谁反制？")
    print(f"  📦 数据源: 本地缓存 ⚡\n")

    best_adc = find_counter_picks(name, "adc", DB, top_n=5)
    if best_adc:
        print("  [ 推荐选择 ADC ]")
        rows = [(i, r["name"].title(), r["tier"], f"{r['win_rate']}%") for i, r in enumerate(best_adc, 1)]
        print_table(["排名","推荐ADC","梯队","我方胜率"], rows)

    best_sup = find_counter_picks(name, "support", DB, top_n=5)
    if best_sup:
        print("\n  [ 推荐选择 辅助 ]")
        rows = [(i, r["name"].title(), r["tier"], f"{r['win_rate']}%") for i, r in enumerate(best_sup, 1)]
        print_table(["排名","推荐辅助","梯队","我方胜率"], rows)

    if not best_adc and not best_sup:
        print("  未找到足够的反制数据")

# ==========================================
# CLI 入口
# ==========================================
def print_help():
    print("""
使用说明:
  python3 main.py recommend <support|adc> [key=value] ...  (智能推荐)
  python3 main.py tier [lane] [region]                      (梯队查询)
  python3 main.py synergy <champion>                        (协同查询)
  python3 main.py matchup <champion>                        (克制查询)
  python3 main.py counter <champion>                        (反制推荐)

提示: 使用任何功能前，请确保已运行 python3 pre_fetch.py 生成数据库！
    """)

if __name__ == "__main__":
    load_db() # 启动直接加载数据库，不联网
    
    if len(sys.argv) < 2:
        print_help()
    elif sys.argv[1] == "recommend":
        parsed = parse_recommend_args(sys.argv)
        if not parsed:
            print("参数错误！格式: python3 main.py recommend support ally_ad=lucian")
        else:
            run_recommend(parsed['role'], parsed['bp_state'])
    elif sys.argv[1] == "tier":
        lane = sys.argv[2] if len(sys.argv) > 2 else "bottom"
        cmd_tier(lane=lane)
    elif sys.argv[1] == "synergy":
        if len(sys.argv) < 3: print("请输入英雄名"); sys.exit(1)
        cmd_synergy(sys.argv[2].lower())
    elif sys.argv[1] == "matchup":
        if len(sys.argv) < 3: print("请输入英雄名"); sys.exit(1)
        cmd_matchup(sys.argv[2].lower())
    elif sys.argv[1] == "counter":
        if len(sys.argv) < 3: print("请输入英雄名"); sys.exit(1)
        cmd_counter(sys.argv[2].lower())
    else:
        print_help()
