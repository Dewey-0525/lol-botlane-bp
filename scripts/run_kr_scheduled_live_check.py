"""Worker for the temporary local timer: fresh full-pool capture through runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_kr_build_source import create_output_dir, _utc_now
from scripts.validate_kr_source import (
    fetch_manifest, run_round, recover_cached_mismatches, PROTECTED, IMPLEMENTATION, hashes, save,
)
from scraper.lolalytics_build_probe import BuildScope


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir",required=True,help="Initial scope reference, never used to fill fresh missing data")
    parser.add_argument("--catalog-source-dir",required=True)
    parser.add_argument("--output-dir",required=True)
    args=parser.parse_args(argv)
    output=create_output_dir(args.output_dir)
    initial=json.loads((Path(args.trial_dir)/"manifest.json").read_text())
    catalog=json.loads((Path(args.catalog_source_dir)/"catalog.json").read_text())
    if not catalog["version"].startswith(initial["patch"]+"."):
        raise ValueError("identity and initial patch differ")
    implementation=IMPLEMENTATION+("scripts/run_kr_scheduled_live_check.py",)
    report=dict(started_at_utc=_utc_now(),mode="live fresh full-pool collection from local calendar job",
                production_accepted=False,cloud_new_code="NOT RUN",passed=False,protected_before=hashes(PROTECTED),
                implementation_before=hashes(implementation),
                retry_policy="at most once after source-declared cache expiry; no older or global fallback")
    save(output/"report.json",report)
    try:
        manifest=fetch_manifest(initial["patch"],output,catalog)
        scopes=[BuildScope(row["champion"],row["champion_id"],row["lane"],initial["patch"]) for row in manifest["candidates"]]
        fresh=run_round(1,scopes,output,manifest,5)
        first=fresh["summary"]
        report["initial_summary"]=first
        if first["complete"] and not first["failures"] and first["population_mismatches"]:
            fresh=recover_cached_mismatches(fresh,scopes,output,manifest,5)
            report["cache_recovery"]=fresh["cache_recovery"]
        s=fresh["summary"];report["fresh_summary"]=s
        if not s["complete"] or s["failures"] or s["population_mismatches"]:
            raise ValueError("fresh scheduled collection failed; no older-round fallback")
        child=subprocess.run([sys.executable,str(Path(__file__).with_name("validate_kr_integration.py")),
                              "--trial-dir",str(output),"--catalog-source-dir",args.catalog_source_dir,
                              "--output-dir",str(output/"integration")],capture_output=True,text=True,timeout=180)
        (output/"integration.log").write_text(child.stdout+child.stderr)
        report["integration_exit_code"]=child.returncode
        report["passed"]=child.returncode==0
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as exc:
        report["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        report.update(completed_at_utc=_utc_now(),protected_after=hashes(PROTECTED),
                      implementation_after=hashes(implementation))
        report["protected_unchanged"]=report["protected_before"]==report["protected_after"]
        report["implementation_unchanged_during_run"]=report["implementation_before"]==report["implementation_after"]
        report["passed"]=report["passed"] and report["protected_unchanged"] and report["implementation_unchanged_during_run"]
        save(output/"report.json",report)
    print(json.dumps({k:report.get(k) for k in ("passed","error","fresh_summary","protected_unchanged")}),flush=True)
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
