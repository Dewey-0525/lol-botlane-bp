#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全数据更新脚本。

常用命令:
  python3 update_data.py              # 运行 pre_fetch.py 更新数据
  python3 update_data.py --check-only # 只校验当前数据和 API

策略:
  1. 更新前备份旧数据
  2. pre_fetch.py 写临时文件并原子替换正式数据
  3. 校验新数据结构和覆盖率
  4. 运行 API 冒烟测试
  5. 任一步失败则恢复旧数据
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "botlane_dataset.json")
BACKUP_DIR = os.path.join(BASE_DIR, "data", "backups")
MIN_BOTTOM_CHAMPIONS = 30
MIN_SUPPORT_CHAMPIONS = 60
MIN_HERO_STATS_RATIO = 0.9
MIN_SYNERGY_ROWS = 700
MIN_COUNTER_ADC_ROWS = 700
MIN_COUNTER_SUP_ROWS = 900
MAX_FETCH_ERROR_RATIO = 0.25


def run_command(args):
    print(f"\n$ {' '.join(args)}")
    return subprocess.run(args, cwd=BASE_DIR, check=True)


def validate_dataset(path=DATA_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据库不存在: {path}")

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    for key in [
        "meta",
        "tiers",
        "hero_stats",
        "synergy",
        "counter",
        "synergy_by_lane",
        "counter_by_lane",
    ]:
        if key not in data:
            raise ValueError(f"数据库缺少字段: {key}")

    tiers = data["tiers"]
    bottom_count = sum(
        1 for tier in tiers.get("bottom", {}).values() if tier and tier != "?"
    )
    support_count = sum(
        1 for tier in tiers.get("support", {}).values() if tier and tier != "?"
    )
    if bottom_count < MIN_BOTTOM_CHAMPIONS or support_count < MIN_SUPPORT_CHAMPIONS:
        raise ValueError(
            f"梯队数据过少: bottom={bottom_count}, support={support_count}"
        )

    hero_stats = data["hero_stats"]
    bottom_stats_count = len(hero_stats.get("bottom", {}))
    support_stats_count = len(hero_stats.get("support", {}))
    bottom_stats_ratio = bottom_stats_count / bottom_count if bottom_count else 0
    support_stats_ratio = support_stats_count / support_count if support_count else 0
    if (
        bottom_stats_ratio < MIN_HERO_STATS_RATIO
        or support_stats_ratio < MIN_HERO_STATS_RATIO
    ):
        raise ValueError(
            "基础英雄数据覆盖不足: "
            f"bottom={bottom_stats_count}/{bottom_count}, "
            f"support={support_stats_count}/{support_count}"
        )

    synergy_rows = sum(len(value) for value in data["synergy"].values())
    counter_adc_rows = sum(
        len(value.get("vs_adc", {})) for value in data["counter"].values()
    )
    counter_sup_rows = sum(
        len(value.get("vs_sup", {})) for value in data["counter"].values()
    )
    if (
        synergy_rows < MIN_SYNERGY_ROWS
        or counter_adc_rows < MIN_COUNTER_ADC_ROWS
        or counter_sup_rows < MIN_COUNTER_SUP_ROWS
    ):
        raise ValueError(
            "协同/克制数据覆盖不足: "
            f"synergy={synergy_rows}, "
            f"counter_adc={counter_adc_rows}, "
            f"counter_sup={counter_sup_rows}"
        )

    for lane in ("bottom", "support"):
        if lane not in data["synergy_by_lane"] or lane not in data["counter_by_lane"]:
            raise ValueError(f"按位置矩阵缺少 lane: {lane}")
        if not data["synergy_by_lane"][lane] or not data["counter_by_lane"][lane]:
            raise ValueError(f"按位置矩阵为空: {lane}")

    meta = data.get("meta", {})
    fetch_errors_count = int(meta.get("fetch_errors_count") or 0)
    coverage = meta.get("coverage") or {}
    fetch_attempts = int(coverage.get("fetch_attempts") or 0)
    if fetch_attempts and fetch_errors_count / fetch_attempts > MAX_FETCH_ERROR_RATIO:
        raise ValueError(
            "抓取失败比例过高: "
            f"errors={fetch_errors_count}, attempts={fetch_attempts}"
        )

    print(
        "数据校验通过: "
        f"bottom={bottom_count}, support={support_count}, "
        f"hero_stats={bottom_stats_count + support_stats_count}, "
        f"synergy={synergy_rows}, "
        f"counter_adc={counter_adc_rows}, "
        f"counter_sup={counter_sup_rows}, "
        f"fetch_errors={fetch_errors_count}"
    )
    return data


def backup_dataset():
    if not os.path.exists(DATA_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"botlane_dataset.{stamp}.json")
    shutil.copy2(DATA_PATH, backup_path)
    print(f"已备份旧数据: {backup_path}")
    return backup_path


def restore_backup(backup_path):
    if not backup_path:
        return
    shutil.copy2(backup_path, DATA_PATH)
    print(f"已恢复旧数据: {backup_path}")


def run_api_tests():
    run_command([sys.executable, "test_api.py"])


def update_data():
    backup_path = backup_dataset()
    try:
        run_command([sys.executable, "pre_fetch.py"])
        validate_dataset()
        run_api_tests()
    except Exception:
        print("\n更新失败，准备恢复旧数据。")
        restore_backup(backup_path)
        raise

    print("\n数据更新完成。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只校验当前数据和 API，不运行 pre_fetch.py",
    )
    args = parser.parse_args()

    if args.check_only:
        validate_dataset()
        run_api_tests()
        print("\n当前数据可用。")
        return

    update_data()


if __name__ == "__main__":
    main()
