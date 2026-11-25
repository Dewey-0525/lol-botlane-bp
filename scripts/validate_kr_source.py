#!/usr/bin/env python3
"""Full-pool repeated KR source validation; never invokes production updates."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
from email.utils import parsedate_to_datetime
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_kr_build_source import (
    REPO_ROOT, create_output_dir, fetch_evidence, collect_case, _utc_now,
)
from scraper import champion_map as cm
from scraper.lolalytics_build_probe import BuildScope, SourceValidationError, _number


PROTECTED = ("pre_fetch.py", "update_data.py", "bp_engine.py", "app.py",
             "scraper/lolalytics.py", "scraper/lolalytics_timeline.py",
             "data/botlane_dataset.json", ".github/workflows/update-data.yml", "requirements.txt")
IMPLEMENTATION = ("scraper/lolalytics_build_probe.py", "scripts/probe_kr_build_source.py",
                  "scripts/validate_kr_source.py")


def hashes(paths):
    return {name: hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() for name in paths}


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def tier_roster(payload, lane, catalog=None):
    if (not isinstance(payload, dict) or payload.get("queue") != 420
            or not isinstance(payload.get("response"), dict)
            or payload["response"].get("valid") is not True
            or not isinstance(payload.get("tier"), dict)):
        raise SourceValidationError(f"invalid {lane} tier payload")
    result, excluded, seen = [], [], set()
    for tier, bucket in payload["tier"].items():
        if not isinstance(bucket, dict) or not isinstance(bucket.get("lane"), dict):
            raise SourceValidationError("unexpected tier bucket schema")
        lane_bucket = bucket["lane"].get(lane)
        if lane_bucket is None:
            continue
        if not isinstance(lane_bucket, dict) or not isinstance(lane_bucket.get("cid"), dict):
            raise SourceValidationError("unexpected tier lane schema")
        for cid_text, row in lane_bucket["cid"].items():
            if not str(cid_text).isdigit() or not isinstance(row, dict):
                raise SourceValidationError("invalid tier champion row")
            cid = int(cid_text)
            if cid in seen:
                raise SourceValidationError("duplicate tier champion")
            seen.add(cid)
            games = _number(row.get("games"), "tier games", integer=True)
            _number(row.get("wr"), "tier win rate", high=100)
            name = (catalog["champions"].get(str(cid), {}).get("key")
                    if catalog is not None else cm.get_name(cid))
            record = dict(champion=name, champion_id=cid, lane=lane,
                          tier_key=str(tier), games=games, win_rate=row["wr"])
            if str(tier) not in {str(i) for i in range(1, 16)}:
                excluded.append({**record, "reason": "unranked_existing_tier_exclusion"})
            elif games < 100:
                excluded.append({**record, "reason": "below_existing_100_game_candidate_threshold"})
            elif name is None:
                raise SourceValidationError(f"unmapped eligible champion id: {cid}")
            else:
                result.append(record)
    if not result:
        raise SourceValidationError(f"empty eligible {lane} roster")
    return sorted(result, key=lambda row: row["champion"]), excluded


def fetch_manifest(patch, output, catalog=None):
    sources, candidates, excluded = {}, [], []
    directory = output / "roster"
    directory.mkdir()
    for lane in ("bottom", "support"):
        params = dict(ep="tier", v="1", patch=patch, lane=lane,
                      tier="emerald_plus", queue="ranked", region="kr")
        url = "https://a1.lolalytics.com/mega/?" + urlencode(params)
        body, meta = fetch_evidence(url, directory / f"{lane}.json")
        sources[lane] = meta
        if body is None or meta.get("http_status") != 200:
            raise SourceValidationError(f"{lane} roster request failed")
        rows, low = tier_roster(json.loads(body), lane, catalog)
        candidates.extend(rows)
        excluded.extend(low)
    manifest = dict(created_at_utc=_utc_now(), patch=patch, region="kr",
                    tier="emerald_plus", queue="ranked", candidates=candidates,
                    exclusions=excluded, sources=sources,
                    identity_version=catalog["version"] if catalog else "legacy_local_map",
                    definition="Known identity, ranked tier and games >=100; both positions separately")
    save(output / "manifest.json", manifest)
    return manifest


def coverage(case, directory, manifest):
    """Coverage denominator is legal candidate pairs, not all arbitrary heroes."""
    if "base" not in case:
        return None
    bundle = json.loads((directory / "bundle.json").read_text())
    candidate = case["requested_scope"]["champion_id"]
    legal = {lane: {row["champion_id"] for row in manifest["candidates"] if row["lane"] == lane}
             for lane in ("bottom", "support")}
    result = {}
    for lane, rows in bundle["counter_by_enemy_lane"].items():
        wanted = legal[lane] - {candidate}
        observations = {row["champion_id"]: row for row in rows}
        adequate = {cid for cid in wanted if observations.get(cid, {}).get("games", -1) >= 100}
        low = {cid for cid in wanted if cid in observations and observations[cid]["games"] < 100}
        result[f"counter_{lane}"] = dict(possible=len(wanted), observed=len(adequate),
                                            below_100=len(low), absent=len(wanted - observations.keys()))
    comparison = directory / "team_comparison.json"
    if comparison.exists():
        team = json.loads(comparison.read_text())
        wanted = legal[team["partner_lane"]] - {candidate}
        observations = {row["champion_id"]: row for row in team["relation_rows"]}
        result["synergy"] = dict(
            possible=len(wanted), observed=sum(observations.get(cid, {}).get("games", -1) >= 100 for cid in wanted),
            below_100=sum(cid in observations and observations[cid]["games"] < 100 for cid in wanted),
            absent=len(wanted - observations.keys()),
        )
    return result


def summarize_cases(cases, expected_count):
    summary = dict(expected=expected_count, attempted=len(cases),
                   build_validated=sum("base" in case for case in cases),
                   timeline_present=sum(case.get("timeline_status") == "present" for case in cases),
                   population_mismatches=sum(case["status"] == "population_mismatch" for case in cases),
                   failures=sum(case["status"] in ("failed", "team_failed") for case in cases))
    summary["complete"] = summary["attempted"] == expected_count
    summary["coverage"] = {}
    for lane in ("bottom", "support"):
        selected = [case for case in cases if case["requested_scope"]["lane"] == lane]
        summary["coverage"][lane] = {}
        for key in ("counter_bottom", "counter_support", "synergy"):
            totals = {field: sum((case.get("coverage") or {}).get(key, {}).get(field, 0)
                                for case in selected) for field in ("possible", "observed", "below_100", "absent")}
            # A denominator from failed cases is unknown, never a fictitious zero coverage.
            totals["cases_with_coverage"] = sum(key in (case.get("coverage") or {}) for case in selected)
            summary["coverage"][lane][key] = totals
    return summary


def run_round(number, scopes, output, manifest, workers):
    directory = output / f"round-{number}"
    directory.mkdir()
    report = dict(number=number, started_at_utc=_utc_now(), cases=[])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(collect_case, scope, directory): scope for scope in scopes}
        for future in as_completed(futures):
            scope = futures[future]
            case = future.result()
            case["evidence_directory"] = str(directory / f"{scope.champion}_{scope.lane}")
            case["coverage"] = coverage(case, directory / f"{scope.champion}_{scope.lane}", manifest)
            bundle_path = directory / f"{scope.champion}_{scope.lane}" / "bundle.json"
            if bundle_path.exists():
                bundle = json.loads(bundle_path.read_text())
                case["normalized_bundle_sha256"] = hashlib.sha256(
                    json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            report["cases"].append(case)
            if case["status"] in ("failed", "team_failed", "population_mismatch") or len(report["cases"]) % 10 == 0:
                print(f"Round {number}: {len(report['cases'])}/{len(scopes)} {scope.champion}/{scope.lane} {case['status']} {case.get('error', '')}", flush=True)
            save(directory / "report.json", report)
    report["cases"].sort(key=lambda case: (case["requested_scope"]["lane"], case["requested_scope"]["champion"]))
    report.update(completed_at_utc=_utc_now(), summary=summarize_cases(report["cases"], len(scopes)))
    save(directory / "report.json", report)
    return report


def cache_expiry_delay(source, now=None):
    """Use the server's actual cache lifetime; no invented data tolerance."""
    headers = {key.lower(): value for key, value in source.get("response_headers", {}).items()}
    match = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)", headers.get("cache-control", ""), re.I)
    age = headers.get("age")
    if match is None:
        return None
    if age is None:
        # Fresh cache entries can omit Age. Infer cache age only from an
        # explicitly fresh cache status and the server's matching timestamps.
        if headers.get("cf-cache-status", "").upper() not in ("EXPIRED", "MISS", "REVALIDATED"):
            return None
        try:
            date = parsedate_to_datetime(headers["date"])
            modified = parsedate_to_datetime(headers["last-modified"])
            age = (date - modified).total_seconds()
        except (KeyError, ValueError, TypeError):
            return None
        if age < 0 or age > int(match.group(1)):
            return None
    elif not str(age).isdigit():
        return None
    remaining = max(0, int(match.group(1)) - int(age))
    completed = datetime.fromisoformat(source["completed_at_utc"]).timestamp()
    # One second accounts for whole-second Age precision, not sample drift.
    return max(0, completed + remaining + 1 - (time.time() if now is None else now))


def recover_cached_mismatches(report, scopes, output, manifest, workers):
    """One recorded retry after the observed HTML cache expires, then fail closed."""
    number = report["number"]
    directory = output / f"round-{number}"
    save(directory / "report-initial.json", report)
    report["initial_summary"] = report["summary"]
    by_key = {(scope.champion_id, scope.lane): scope for scope in scopes}
    positions = {(case["requested_scope"]["champion_id"], case["requested_scope"]["lane"]): i
                 for i, case in enumerate(report["cases"])}
    tasks, unplanned = [], []
    for case in report["cases"]:
        if case["status"] != "population_mismatch":
            continue
        delay = cache_expiry_delay(case["sources"]["build"])
        key = (case["requested_scope"]["champion_id"], case["requested_scope"]["lane"])
        if delay is None:
            unplanned.append(list(key))
            continue
        tasks.append((time.time() + delay, key))
    report["cache_recovery"] = dict(planned=len(tasks), no_proven_expiry=unplanned,
                                     reason="HTML cache HIT may lag the separately refreshed team endpoint",
                                     max_additional_attempts_per_case=1, attempts=[])
    report["cache_recovery"]["due_at_utc"] = [dict(champion_id=key[0], lane=key[1],
        at=datetime.fromtimestamp(deadline, timezone.utc).isoformat()) for deadline, key in sorted(tasks)]
    save(directory / "report.json", report)
    if not tasks:
        save(directory / "report.json", report)
        return report
    retry_output = directory / "after-cache-expiry"
    retry_output.mkdir()
    pending = sorted(tasks)
    running = {}
    print(f"Round {number}: {len(tasks)} mismatches scheduled once after server-declared cache expiry", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or running:
            # HTTP cache expiry uses real UTC time. A relative timer can pause
            # during laptop sleep and keep an already-expired cache waiting.
            now = time.time()
            while pending and pending[0][0] <= now and len(running) < workers:
                _, key = pending.pop(0)
                running[pool.submit(collect_case, by_key[key], retry_output)] = key
            done = [future for future in running if future.done()]
            for future in done:
                key = running.pop(future)
                case = future.result()
                scope = by_key[key]
                case_dir = retry_output / f"{scope.champion}_{scope.lane}"
                case["evidence_directory"] = str(case_dir)
                case["coverage"] = coverage(case, case_dir, manifest)
                if (case_dir / "bundle.json").exists():
                    bundle = json.loads((case_dir / "bundle.json").read_text())
                    case["normalized_bundle_sha256"] = hashlib.sha256(
                        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                old = report["cases"][positions[key]]
                case["previous_attempt"] = dict(status=old["status"], evidence_directory=old["evidence_directory"],
                                                difference_games=old["team"]["difference_games"])
                report["cases"][positions[key]] = case
                report["cache_recovery"]["attempts"].append(dict(champion=scope.champion, lane=key[1], status=case["status"]))
                save(directory / "report.json", report)
                if len(report["cache_recovery"]["attempts"]) % 10 == 0 or case["status"] != "population_matches_scope_not_echoed":
                    print(f"Round {number} expiry retry: {len(report['cache_recovery']['attempts'])}/{len(tasks)} {key} {case['status']}", flush=True)
            if not done:
                time.sleep(1)
    report.update(completed_at_utc=_utc_now(), summary=summarize_cases(report["cases"], len(scopes)))
    report["cache_recovery"]["recovered"] = sum(case.get("previous_attempt") is not None and
                                                case["status"] == "population_matches_scope_not_echoed" for case in report["cases"])
    save(directory / "report.json", report)
    return report


def compare_rounds(output, completed):
    comparisons = []
    for number in range(2, completed + 1):
        earlier = json.loads((output / f"round-{number-1}" / "report.json").read_text())
        later = json.loads((output / f"round-{number}" / "report.json").read_text())
        keyed = lambda report: {(case["requested_scope"]["champion"], case["requested_scope"]["lane"]): case
                                for case in report["cases"]}
        first, second = keyed(earlier), keyed(later)
        common = set(first) & set(second)
        comparable = [key for key in common if "normalized_bundle_sha256" in first[key] and "normalized_bundle_sha256" in second[key]]
        changed = [key for key in comparable if first[key]["normalized_bundle_sha256"] != second[key]["normalized_bundle_sha256"]]
        regressions = [key for key in comparable if second[key]["base"]["games"] < first[key]["base"]["games"]]
        comparisons.append(dict(from_round=number-1, to_round=number, comparable=len(comparable),
                                changed_data_cases=[list(key) for key in sorted(changed)],
                                unchanged_data_cases=len(comparable)-len(changed),
                                base_game_regressions=[list(key) for key in sorted(regressions)]))
    return comparisons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--round-gap-seconds", type=float, default=60)
    parser.add_argument("--output-dir")
    parser.add_argument("--catalog-file", help="Optional verified official identity catalog for isolated trials")
    parser.add_argument("--retry-after-cache-expiry", action="store_true",
                        help="On population mismatch, retry once after the server-declared page cache expires")
    args = parser.parse_args(argv)
    if args.rounds < 2 or not 1 <= args.workers <= 5 or args.round_gap_seconds < 0:
        parser.error("at least two rounds, 1..5 workers and a nonnegative gap required")
    BuildScope("leona", 89, "support", args.patch)
    output = create_output_dir(args.output_dir)
    report = dict(started_at_utc=_utc_now(), production_accepted=False,
                  planned_rounds=args.rounds, rounds=[], patch=args.patch,
                  environment=dict(python=sys.version, platform=platform.platform(),
                                   github={key: os.environ.get(key) for key in
                                           ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_REF", "RUNNER_OS", "RUNNER_ARCH", "ImageVersion", "ImageOS")}),
                  protected_before=hashes(PROTECTED), implementation_before=hashes(IMPLEMENTATION))
    print(f"Validation output: {output}", flush=True)
    save(output / "report.json", report)
    try:
        catalog = json.loads(Path(args.catalog_file).read_text()) if args.catalog_file else None
        if catalog is not None and not catalog["version"].startswith(args.patch + "."):
            raise SourceValidationError("identity catalog and trial patch differ")
        manifest = fetch_manifest(args.patch, output, catalog)
    except (ValueError, OSError) as exc:
        report.update(completed_at_utc=_utc_now(), exit_code=1,
                      error=f"{type(exc).__name__}: {exc}", stopped_for_investigation=True,
                      protected_after=hashes(PROTECTED))
        report["protected_unchanged"] = report["protected_before"] == report["protected_after"]
        save(output / "report.json", report)
        print(report["error"], flush=True)
        return 1
    scopes = [BuildScope(row["champion"], row["champion_id"], row["lane"], args.patch) for row in manifest["candidates"]]
    print(f"Frozen roster: {len(scopes)} champion-position cases; " + str({lane:sum(s.lane==lane for s in scopes) for lane in ('bottom','support')}), flush=True)
    save(output / "report.json", report)
    for number in range(1, args.rounds + 1):
        round_report = run_round(number, scopes, output, manifest, args.workers)
        if args.retry_after_cache_expiry and round_report["summary"]["population_mismatches"]:
            round_report = recover_cached_mismatches(round_report, scopes, output, manifest, args.workers)
        report["rounds"].append(dict(number=number, started_at_utc=round_report["started_at_utc"],
                                     completed_at_utc=round_report["completed_at_utc"], summary=round_report["summary"],
                                     initial_summary=round_report.get("initial_summary"),
                                     cache_recovery=round_report.get("cache_recovery")))
        save(output / "report.json", report)
        summary = round_report["summary"]
        if summary["failures"] or summary["population_mismatches"]:
            report["stopped_for_investigation"] = True
            break
        if number < args.rounds:
            time.sleep(args.round_gap_seconds)
    report.update(completed_at_utc=_utc_now(), protected_after=hashes(PROTECTED))
    report["implementation_after"] = hashes(IMPLEMENTATION)
    report["implementation_unchanged_during_run"] = report["implementation_before"] == report["implementation_after"]
    report["round_comparisons"] = compare_rounds(output, len(report["rounds"]))
    report["source_count_regression_detected"] = any(
        comparison["base_game_regressions"] for comparison in report["round_comparisons"])
    report["source_statistical_stability"] = (
        "COUNT_REGRESSION_DETECTED_CAUSE_UNCONFIRMED" if report["source_count_regression_detected"]
        else "NO_NEW_REGRESSION_OBSERVED_THIS_RUN; HISTORICAL_CAUSE_NOT_RESOLVED")
    if report["source_count_regression_detected"]:
        report["stopped_for_investigation"] = True
    report["protected_unchanged"] = report["protected_before"] == report["protected_after"]
    report["all_planned_rounds_completed"] = len(report["rounds"]) == args.rounds
    report["exit_code"] = 0 if report["all_planned_rounds_completed"] and not report.get("stopped_for_investigation") and report["protected_unchanged"] and report["implementation_unchanged_during_run"] else 1
    save(output / "report.json", report)
    print(f"Validation report: {output / 'report.json'}; exit={report['exit_code']}", flush=True)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
