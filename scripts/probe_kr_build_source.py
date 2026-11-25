#!/usr/bin/env python3
"""Collect a small KR-only trial outside the repository; never publish data.

Example: python3 scripts/probe_kr_build_source.py --patch 16.21
Exit 0: sample populations match (not production acceptance).
Exit 1: a source/validation failure. Exit 2: team population mismatch.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import ssl
import tempfile
import time
import urllib.error
import urllib.request
import certifi


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from scraper import champion_map as cm
from scraper.lolalytics_build_probe import BuildScope, parse_build_page, compare_team_payload


DEFAULT_CASES = ("leona:support", "thresh:support", "lulu:support", "ashe:bottom",
                 "ashe:support", "missfortune:bottom", "missfortune:support", "karthus:bottom")


def create_output_dir(value=None):
    if value is None:
        base = Path(tempfile.gettempdir()).resolve()
        if base == REPO_ROOT or REPO_ROOT in base.parents:
            raise ValueError("temporary output must be outside the repository")
        return Path(tempfile.mkdtemp(prefix="lol-kr-build-trial-"))
    path = Path(value).expanduser().resolve()
    if path == REPO_ROOT or REPO_ROOT in path.parents:
        raise ValueError("trial output must be outside the repository")
    path.mkdir(parents=True, exist_ok=False)
    return path


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def fetch_evidence(url, destination):
    meta = dict(url=url, requested_at_utc=_utc_now(), raw_file=str(destination))
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        # A disposable Python installation may have no OS certificate bundle.
        # Use the CA bundle already supplied by requests, retaining TLS checks.
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=25, context=context) as response:
            body = response.read()
            meta.update(http_status=response.status, final_url=response.url,
                        response_headers=dict(response.headers))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        meta.update(http_status=exc.code, final_url=exc.url,
                    response_headers=dict(exc.headers))
    except (OSError, urllib.error.URLError) as exc:
        meta.update(completed_at_utc=_utc_now(), error=f"{type(exc).__name__}: {exc}")
        destination.with_suffix(destination.suffix + ".request.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return None, meta
    destination.write_bytes(body)
    meta.update(completed_at_utc=_utc_now(), sha256=hashlib.sha256(body).hexdigest(),
                bytes=len(body))
    destination.with_suffix(destination.suffix + ".request.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body, meta


def collect_case(scope, output):
    directory = output / f"{scope.champion}_{scope.lane}"
    directory.mkdir()
    record = dict(requested_scope=scope.conditions(), status="failed", sources={})
    body, meta = fetch_evidence(scope.build_url(), directory / "build.html")
    record["sources"]["build"] = meta
    try:
        if body is None or meta.get("http_status") != 200:
            raise ValueError("build HTTP request failed; no fallback attempted")
        bundle = parse_build_page(body.decode("utf-8"), scope)
        record.update(status="build_validated_team_pending", base=bundle["hero_stats"],
                      returned_page_scope=bundle["returned_page_scope"],
                      returned_header_scope=bundle["returned_header_scope"],
                      population=bundle["population"], timeline_status=bundle["timeline_status"])
        record["counter_summary"] = {
            lane: dict(rows=len(rows), sum_games=sum(row["games"] for row in rows),
                       max_games=max(row["games"] for row in rows),
                       rows_ge_existing_100_game_threshold=sum(row["games"] >= 100 for row in rows))
            for lane, rows in bundle["counter_by_enemy_lane"].items()
        }
        (directory / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, TypeError, KeyError) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record
    time.sleep(0.3)
    body, meta = fetch_evidence(scope.team_url(), directory / "team.json")
    record["sources"]["team"] = meta
    try:
        if body is None or meta.get("http_status") != 200:
            raise ValueError("team HTTP request failed; no fallback attempted")
        team = compare_team_payload(json.loads(body), bundle, scope)
        (directory / "team_comparison.json").write_text(json.dumps(team, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        record["team"] = {key: value for key, value in team.items() if key != "relation_rows"}
        record["status"] = team["status"]
    except (ValueError, TypeError, KeyError) as exc:
        record.update(status="team_failed", error=f"{type(exc).__name__}: {exc}")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True, help="Explicit numbered patch, e.g. 16.21")
    parser.add_argument("--case", action="append", help="champion:bottom or champion:support; repeatable")
    parser.add_argument("--output-dir", help="New directory outside the repository; default is a temporary directory")
    args = parser.parse_args(argv)
    scopes = []
    try:
        for value in dict.fromkeys(args.case or DEFAULT_CASES):
            champion, lane = value.split(":")
            if champion == "wukong":
                champion = "monkeyking"
            scope = BuildScope(champion, cm.get_id(champion), lane, args.patch)
            if scope not in scopes:
                scopes.append(scope)
        output = create_output_dir(args.output_dir)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(f"Trial output: {output}", flush=True)
    report = dict(started_at_utc=_utc_now(), patch=args.patch, region="kr",
                  scope="isolated source trial; not a production snapshot",
                  production_accepted=False, global_fallback=False, cases=[])
    for scope in scopes:
        record = collect_case(scope, output)
        report["cases"].append(record)
        print(f"{scope.champion}/{scope.lane}: {record['status']} {record.get('error', '')}", flush=True)
        # Preserve progress if a later request is interrupted.
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        time.sleep(0.3)
    statuses = [record["status"] for record in report["cases"]]
    report.update(completed_at_utc=_utc_now(),
                  build_validated=sum("base" in record for record in report["cases"]),
                  population_matches=statuses.count("population_matches_scope_not_echoed"),
                  population_mismatches=statuses.count("population_mismatch"),
                  failures=sum(status in ("failed", "team_failed") for status in statuses))
    report["exit_code"] = 1 if report["failures"] else (2 if report["population_mismatches"] else 0)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output / 'report.json'}; exit={report['exit_code']}", flush=True)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
