#!/usr/bin/env python3
"""Resume a fully captured failed trial, preserving original evidence separately.

Only completed build captures with verified raw hashes are imported. This avoids
redownloading 138 coherent cases merely to retry two mismatches after expiry.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_kr_source import (
    PROTECTED, IMPLEMENTATION, hashes, save, run_round, recover_cached_mismatches, compare_rounds,
)
from scripts.probe_kr_build_source import create_output_dir, _utc_now
from scraper.lolalytics_build_probe import BuildScope, parse_build_page, compare_team_payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--round-gap-seconds", type=float, default=60)
    parser.add_argument("--output-dir", help="New evidence directory outside the repository")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 5 or args.round_gap_seconds < 0:
        parser.error("invalid workers or interval")
    source = args.source.resolve()
    original = json.loads((source / "report.json").read_text())
    if original.get("exit_code") != 1 or not original.get("stopped_for_investigation"):
        parser.error("source must be a completed trial stopped for investigation")
    if original["protected_after"] != hashes(PROTECTED):
        parser.error("protected files changed since the source trial")
    manifest = json.loads((source / "manifest.json").read_text())
    scopes = [BuildScope(row["champion"], row["champion_id"], row["lane"], manifest["patch"])
              for row in manifest["candidates"]]
    output = create_output_dir(args.output_dir)
    shutil.copytree(source / "roster", output / "roster")
    shutil.copy2(source / "manifest.json", output / "manifest.json")
    print(f"Resumed validation output: {output}", flush=True)
    implementation_paths = IMPLEMENTATION + ("scripts/resume_kr_source_validation.py",)
    report = dict(started_at_utc=original["started_at_utc"], resumed_at_utc=_utc_now(),
                  imported_from=str(source), imported_report_sha256=hashlib.sha256((source / "report.json").read_bytes()).hexdigest(),
                  production_accepted=False, planned_rounds=original["planned_rounds"], rounds=[],
                  patch=manifest["patch"], environment=original["environment"],
                  protected_before=hashes(PROTECTED), implementation_before=hashes(implementation_paths))
    save(output / "report.json", report)
    by_identity = {(scope.champion_id, scope.lane): scope for scope in scopes}
    for entry in original["rounds"]:
        number = entry["number"]
        directory = output / f"round-{number}"
        shutil.copytree(source / f"round-{number}", directory)
        round_report = json.loads((directory / "report.json").read_text())
        for case in round_report["cases"]:
            req = case["requested_scope"]
            scope = by_identity[(req["champion_id"], req["lane"])]
            case_dir = directory / f"{scope.champion}_{scope.lane}"
            for label, name in (("build", "build.html"), ("team", "team.json")):
                raw = (case_dir / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != case["sources"][label]["sha256"]:
                    raise ValueError("imported raw source hash mismatch")
            bundle = parse_build_page((case_dir / "build.html").read_text(), scope)
            if bundle != json.loads((case_dir / "bundle.json").read_text()):
                raise ValueError("imported build no longer reproduces under current parser")
            team = compare_team_payload(json.loads((case_dir / "team.json").read_text()), bundle, scope)
            if team != json.loads((case_dir / "team_comparison.json").read_text()):
                raise ValueError("imported team comparison no longer reproduces")
            case["evidence_directory"] = str(case_dir)
        round_report["imported_capture"] = True
        save(directory / "report.json", round_report)
        if round_report["summary"]["population_mismatches"]:
            round_report = recover_cached_mismatches(round_report, scopes, output, manifest, args.workers)
        report["rounds"].append({key: round_report.get(key) for key in
                                 ("number", "started_at_utc", "completed_at_utc", "summary", "initial_summary", "cache_recovery", "imported_capture")})
        save(output / "report.json", report)
        if round_report["summary"]["failures"] or round_report["summary"]["population_mismatches"]:
            report["stopped_for_investigation"] = True
            break
    for number in range(len(report["rounds"]) + 1, report["planned_rounds"] + 1):
        if report.get("stopped_for_investigation"):
            break
        time.sleep(args.round_gap_seconds)
        round_report = run_round(number, scopes, output, manifest, args.workers)
        if round_report["summary"]["population_mismatches"]:
            round_report = recover_cached_mismatches(round_report, scopes, output, manifest, args.workers)
        report["rounds"].append({key: round_report.get(key) for key in
                                 ("number", "started_at_utc", "completed_at_utc", "summary", "initial_summary", "cache_recovery")})
        save(output / "report.json", report)
        if round_report["summary"]["failures"] or round_report["summary"]["population_mismatches"]:
            report["stopped_for_investigation"] = True
    report.update(completed_at_utc=_utc_now(), protected_after=hashes(PROTECTED),
                  implementation_after=hashes(implementation_paths), round_comparisons=compare_rounds(output, len(report["rounds"])))
    report["protected_unchanged"] = report["protected_before"] == report["protected_after"]
    report["implementation_unchanged_during_run"] = report["implementation_before"] == report["implementation_after"]
    report["all_planned_rounds_completed"] = len(report["rounds"]) == report["planned_rounds"]
    report["exit_code"] = 0 if (report["all_planned_rounds_completed"] and not report.get("stopped_for_investigation")
                                and report["protected_unchanged"] and report["implementation_unchanged_during_run"]) else 1
    save(output / "report.json", report)
    print(f"Resumed report: {output / 'report.json'}; exit={report['exit_code']}", flush=True)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
