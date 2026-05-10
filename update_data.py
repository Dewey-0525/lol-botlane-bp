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


def run_command(args):
    print(f"\n$ {' '.join(args)}")
    return subprocess.run(args, cwd=BASE_DIR, check=True)


def validate_dataset(path=DATA_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据库不存在: {path}")

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    for key in ["meta", "tiers", "synergy", "counter"]:
        if key not in data:
            raise ValueError(f"数据库缺少字段: {key}")

    tiers = data["tiers"]
    bottom_count = len(tiers.get("bottom", {}))
    support_count = len(tiers.get("support", {}))
    if bottom_count < 10 or support_count < 10:
        raise ValueError(
            f"梯队数据过少: bottom={bottom_count}, support={support_count}"
        )

    synergy_rows = sum(len(value) for value in data["synergy"].values())
    counter_adc_rows = sum(
        len(value.get("vs_adc", {})) for value in data["counter"].values()
    )
    counter_sup_rows = sum(
        len(value.get("vs_sup", {})) for value in data["counter"].values()
    )
    if synergy_rows <= 0 or counter_adc_rows <= 0 or counter_sup_rows <= 0:
        raise ValueError(
            "协同/克制数据为空: "
            f"synergy={synergy_rows}, "
            f"counter_adc={counter_adc_rows}, "
            f"counter_sup={counter_sup_rows}"
        )

    print(
        "数据校验通过: "
        f"bottom={bottom_count}, support={support_count}, "
        f"synergy={synergy_rows}, "
        f"counter_adc={counter_adc_rows}, "
        f"counter_sup={counter_sup_rows}"
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
