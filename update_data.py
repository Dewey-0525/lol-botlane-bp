#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全数据更新脚本。

常用命令:
  python3 update_data.py                 # 运行 pre_fetch.py 更新数据
  python3 update_data.py --with-timeline # 额外抓取时间趋势
  python3 update_data.py --check-only    # 只校验当前数据和 API

策略:
  1. 独占更新锁，在独立候选路径运行 pre_fetch.py
  2. 校验候选数据结构和覆盖率
  3. 新进程针对候选文件运行 API 回归
  4. 备份原数据，再原子替换正式文件
  5. 发布前失败或进程中断均不改动正式文件
"""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "botlane_dataset.json")
BACKUP_DIR = os.path.join(BASE_DIR, "data", "backups")
MIN_BOTTOM_CHAMPIONS = 25
MIN_SUPPORT_CHAMPIONS = 35
MIN_HERO_STATS_RATIO = 0.9
MIN_SYNERGY_ROWS = 10
MIN_COUNTER_ADC_ROWS = 50
MIN_COUNTER_SUP_ROWS = 30
MAX_FETCH_ERROR_RATIO = 0.25


def run_command(args, env=None):
    print(f"\n$ {' '.join(args)}")
    return subprocess.run(args, cwd=BASE_DIR, check=True, env=env)


def validate_dataset(path=None):
    path = path or DATA_PATH
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
    stamp = time.strftime("%Y%m%d_%H%M%S") + f".{time.time_ns()}"
    backup_path = os.path.join(BACKUP_DIR, f"botlane_dataset.{stamp}.json")
    shutil.copy2(DATA_PATH, backup_path)
    print(f"已备份旧数据: {backup_path}")
    return backup_path


def restore_backup(backup_path):
    if not backup_path:
        return
    with dataset_lock(), tempfile.TemporaryDirectory(prefix=".botlane-restore-", dir=os.path.dirname(DATA_PATH)) as directory:
        candidate = os.path.join(directory, "restored.json")
        shutil.copy2(backup_path, candidate)
        os.replace(candidate, DATA_PATH)
    print(f"已恢复旧数据: {backup_path}")


@contextmanager
def dataset_lock():
    """Serialize cooperative updates and restores on supported macOS/Linux hosts."""
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH + ".update.lock", "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有数据更新或回退正在执行") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def file_digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def run_api_tests(dataset_path=None):
    env = os.environ.copy()
    env["BOTLANE_DATA_PATH"] = os.path.abspath(dataset_path or DATA_PATH)
    run_command([sys.executable, "test_api.py"], env=env)


def update_data(with_timeline=False, timeline_limit=None, timeline_headed=False):
    with dataset_lock(), tempfile.TemporaryDirectory(prefix=".botlane-update-", dir=os.path.dirname(DATA_PATH)) as directory:
        original_digest = file_digest(DATA_PATH)
        candidate = os.path.join(directory, "botlane_dataset.json")
        args = [sys.executable, "pre_fetch.py", "--output", candidate]
        if with_timeline:
            args.append("--with-timeline")
        if timeline_limit is not None:
            args.extend(["--timeline-limit", str(timeline_limit)])
        if timeline_headed:
            args.append("--timeline-headed")
        run_command(args)
        validate_dataset(candidate)
        run_api_tests(candidate)
        if file_digest(DATA_PATH) != original_digest:
            raise RuntimeError("验证期间正式数据被其他程序修改，停止发布")
        backup_dataset()
        with open(candidate, "rb") as file:
            os.fsync(file.fileno())
        os.replace(candidate, DATA_PATH)

    print("\n数据文件更新完成；运行中的网页需重新部署以加载新快照。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只校验当前数据和 API，不运行 pre_fetch.py",
    )
    parser.add_argument(
        "--with-timeline",
        action="store_true",
        help="额外用 Scrapling 抓取 Lolalytics build 页时间趋势。",
    )
    parser.add_argument(
        "--timeline-limit",
        type=int,
        default=None,
        help="只抓取前 N 个时间趋势样本，便于本地测试。",
    )
    parser.add_argument(
        "--timeline-headed",
        action="store_true",
        help="使用有界面浏览器抓取时间趋势，便于手动通过 Cloudflare。",
    )
    args = parser.parse_args()

    if args.check_only:
        validate_dataset()
        run_api_tests()
        print("\n当前数据可用。")
        return

    update_data(
        with_timeline=args.with_timeline,
        timeline_limit=args.timeline_limit,
        timeline_headed=args.timeline_headed,
    )


if __name__ == "__main__":
    main()
