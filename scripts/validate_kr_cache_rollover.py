"""One full frozen-pool check after proven natural cache expiry; never publish."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_kr_build_source import create_output_dir, _utc_now
from scripts.validate_kr_source import cache_expiry_delay, run_round, hashes, PROTECTED, save
from scraper.lolalytics_build_probe import BuildScope


def counter_values(bundle):
    return {lane: {row["champion_id"]: (row["games"], row["win_rate"]) for row in rows}
            for lane, rows in bundle["counter_by_enemy_lane"].items()}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir",required=True)
    parser.add_argument("--catalog-source-dir",required=True)
    parser.add_argument("--output-dir")
    args=parser.parse_args(argv)
    source=Path(args.trial_dir).resolve()
    output=create_output_dir(args.output_dir)
    manifest=json.loads((source/"manifest.json").read_text())
    first=json.loads((source/"round-1/report.json").read_text())
    summary=first["summary"]
    if not summary["complete"] or summary["failures"] or summary["population_mismatches"]:
        raise ValueError("initial full round did not pass")
    for name in ("manifest.json","roster"):
        if (source/name).is_dir():shutil.copytree(source/name,output/name)
        else:shutil.copy2(source/name,output/name)
    now=time.time()
    delays=[cache_expiry_delay(case["sources"]["build"], now=now) for case in first["cases"]]
    if any(delay is None for delay in delays):
        raise ValueError("cannot prove expiry for every first-round page; no guessed wait")
    deadline=now+max(delays)
    scopes=[BuildScope(row["champion"],row["champion_id"],row["lane"],manifest["patch"]) for row in manifest["candidates"]]
    implementation={str(path.relative_to(Path(__file__).resolve().parents[1])):hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [Path(__file__),Path(__file__).with_name("kr_trial_dataset.py"),Path(__file__).with_name("validate_kr_source.py")]}
    report=dict(started_at_utc=_utc_now(),source_trial=str(source),patch=manifest["patch"],
                production_accepted=False,protected_before=hashes(PROTECTED),
                implementation=implementation,state="waiting_for_declared_cache_expiry",
                not_before_utc=datetime.fromtimestamp(deadline,timezone.utc).isoformat(),passed=False)
    save(output/"report.json",report)
    print(f"Rollover evidence: {output}; not before {report['not_before_utc']}",flush=True)
    while time.time()<deadline:
        remaining=deadline-time.time()
        print(f"Waiting for actual cache expiry: {remaining:.0f} seconds",flush=True)
        time.sleep(min(45,remaining))
    report["state"]="collecting";save(output/"report.json",report)
    try:
        last=run_round(1,scopes,output,manifest,5)
        report["summary"]=last["summary"]
        changes={key:0 for key in ("base_games","base_win_rate","counter_values","cache_generation")}
        before={(case["requested_scope"]["champion_id"],case["requested_scope"]["lane"]):case for case in first["cases"]}
        for case in last["cases"]:
            if "base" not in case:continue
            key=(case["requested_scope"]["champion_id"],case["requested_scope"]["lane"])
            name=next(scope.champion for scope in scopes if (scope.champion_id,scope.lane)==key)
            old=json.loads((source/f"round-1/{name}_{key[1]}/bundle.json").read_text())
            new=json.loads((output/f"round-1/{name}_{key[1]}/bundle.json").read_text())
            changes["base_games"]+=new["hero_stats"]["games"]!=old["hero_stats"]["games"]
            changes["base_win_rate"]+=new["hero_stats"]["win_rate"]!=old["hero_stats"]["win_rate"]
            changes["counter_values"]+=counter_values(new)!=counter_values(old)
            headers=lambda row:{k.lower():v for k,v in row["sources"]["build"]["response_headers"].items()}
            changes["cache_generation"]+=headers(case).get("last-modified")!=headers(before[key]).get("last-modified")
        report["changes"]=changes
        s=last["summary"]
        if not s["complete"] or s["failures"] or s["population_mismatches"]:
            raise ValueError("updated full round failed; investigate original source evidence")
        child=subprocess.run([sys.executable,str(Path(__file__).with_name("validate_kr_integration.py")),
                              "--trial-dir",str(output),"--catalog-source-dir",args.catalog_source_dir,
                              "--output-dir",str(output/"integration")],capture_output=True,text=True,timeout=180)
        (output/"integration.log").write_text(child.stdout+child.stderr)
        report["integration_exit_code"]=child.returncode
        report["passed"]=child.returncode==0 and any(changes[k] for k in ("base_games","base_win_rate","counter_values"))
        if not report["passed"]:raise ValueError("integration failed or actual statistic update not observed")
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as exc:
        report["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        report.update(completed_at_utc=_utc_now(),state="completed",protected_after=hashes(PROTECTED))
        report["protected_unchanged"]=report["protected_before"]==report["protected_after"]
        report["passed"]=report["passed"] and report["protected_unchanged"]
        save(output/"report.json",report)
    print(json.dumps({k:report.get(k) for k in ("passed","changes","error","protected_unchanged")}),flush=True)
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
