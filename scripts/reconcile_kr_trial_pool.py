"""Close a growing pool with explicit supplemental evidence, never fake full rounds.

Previously verified captures remain immutable. New positions receive three real
captures. The final composed snapshot states its capture window and provenance;
it is not described as a simultaneous or independently collected full round.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_kr_build_source import REPO_ROOT, create_output_dir, _utc_now
from scripts.validate_kr_source import fetch_manifest, run_round, hashes, PROTECTED, save
from scripts.kr_trial_catalog import identity_name
from scraper.lolalytics_build_probe import BuildScope


def load(path):
    return json.loads(Path(path).read_text())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeated-trial-dir",required=True)
    parser.add_argument("--updated-trial-dir",required=True)
    parser.add_argument("--catalog-source-dir",required=True)
    parser.add_argument("--output-dir")
    args=parser.parse_args(argv)
    output=create_output_dir(args.output_dir)
    repeated=Path(args.repeated_trial_dir).resolve()
    updated=Path(args.updated_trial_dir).resolve()
    catalog_source=Path(args.catalog_source_dir).resolve()
    catalog=load(catalog_source/"catalog.json")
    initial=load(repeated/"manifest.json")
    if not catalog["version"].startswith(initial["patch"]+"."):
        raise ValueError("identity and reference patch differ")
    original=load(repeated/"report.json")
    if original.get("exit_code")!=0 or not original.get("all_planned_rounds_completed"):
        raise ValueError("original repeated full-pool trial did not pass")
    counts={}
    for round_record in original["rounds"]:
        rr=load(repeated/f"round-{round_record['number']}/report.json")
        for case in rr["cases"]:
            if case["status"]!="population_matches_scope_not_echoed":
                raise ValueError("a reference capture failed")
            key=(case["requested_scope"]["champion_id"],case["requested_scope"]["lane"])
            counts[key]=counts.get(key,0)+1
    update=load(updated/"report.json")
    if update.get("passed") is not True:
        raise ValueError("reference cache update did not pass")
    captures={}
    for case in load(updated/"round-1/report.json")["cases"]:
        key=(case["requested_scope"]["champion_id"],case["requested_scope"]["lane"])
        if counts.get(key,0)<3 or case["status"]!="population_matches_scope_not_echoed":
            raise ValueError("updated position lacks three passing reference captures")
        name=identity_name(catalog,key[0])
        captures[key]=(case,updated/f"round-1/{name}_{key[1]}")
        counts[key]+=1
    report=dict(started_at_utc=_utc_now(),production_accepted=False,passed=False,
                protected_before=hashes(PROTECTED),supplemental_cycles=[],
                definition="Timestamped composed snapshot: each current position has >=3 actual captures; added positions do not inherit cache-update proof")
    print(f"Pool reconciliation evidence: {output}",flush=True)
    save(output/"report.json",report)
    try:
        manifest=None
        for cycle in range(1,4):
            discovery=output/f"discovery-{cycle}";discovery.mkdir()
            manifest=fetch_manifest(initial["patch"],discovery,catalog)
            wanted={(row["champion_id"],row["lane"]):row for row in manifest["candidates"]}
            missing=sorted(wanted.keys()-captures.keys())
            if not missing:
                report["closed_at_utc"]=_utc_now()
                break
            supplement=output/f"supplement-{cycle}";supplement.mkdir()
            scopes=[BuildScope(wanted[key]["champion"],key[0],key[1],initial["patch"]) for key in missing]
            cycle_record=dict(positions=[dict(champion=scope.champion,lane=scope.lane) for scope in scopes],rounds=[])
            report["supplemental_cycles"].append(cycle_record)
            for number in (1,2,3):
                rr=run_round(number,scopes,supplement,manifest,5)
                cycle_record["rounds"].append(rr["summary"])
                save(output/"report.json",report)
                s=rr["summary"]
                if not s["complete"] or s["failures"] or s["population_mismatches"]:
                    raise ValueError("supplemental capture failed; no reference/global fallback")
                for case in rr["cases"]:
                    key=(case["requested_scope"]["champion_id"],case["requested_scope"]["lane"])
                    name=identity_name(catalog,key[0])
                    captures[key]=(case,supplement/f"round-{number}/{name}_{key[1]}")
                    counts[key]=counts.get(key,0)+1
                if number<3:time.sleep(60)
        else:
            raise ValueError("pool still expanded after bounded reconciliation; no complete-pool claim")
        if any(counts.get(key,0)<3 for key in wanted):
            raise ValueError("current position lacks repeated evidence")
        save(output/"manifest.json",manifest)
        combined=output/"round-1";combined.mkdir()
        cases=[]
        starts=[];ends=[]
        for key,row in sorted(wanted.items()):
            case,directory=captures[key]
            new=copy.deepcopy(case)
            target=combined/f"{row['champion']}_{row['lane']}"
            shutil.copytree(directory,target)
            new["origin_evidence_directory"]=str(directory)
            new["evidence_directory"]=str(target)
            new["actual_capture_count"]=counts[key]
            cases.append(new)
            starts.extend(source["requested_at_utc"] for source in case["sources"].values())
            ends.extend(source["completed_at_utc"] for source in case["sources"].values())
        save(combined/"report.json",dict(kind="composed verified snapshot, NOT an independently collected full round",
             started_at_utc=min(starts),completed_at_utc=max(ends),cases=cases))
        report.update(candidate_positions=len(wanted),minimum_actual_captures=min(counts[key] for key in wanted),
                      capture_window_utc=[min(starts),max(ends)],
                      positions_with_verified_prior_cache_update=len(wanted.keys()&set((r['champion_id'],r['lane']) for r in initial['candidates'])),
                      supplemental_positions_without_cache_rollover_proof=[dict(champion=wanted[key]['champion'],lane=key[1]) for key in wanted if key not in {(r['champion_id'],r['lane']) for r in initial['candidates']}])
        child=subprocess.run([sys.executable,str(REPO_ROOT/"scripts/validate_kr_integration.py"),"--trial-dir",str(output),
                              "--catalog-source-dir",str(catalog_source),"--output-dir",str(output/"integration")],capture_output=True,text=True,timeout=180)
        (output/"integration.log").write_text(child.stdout+child.stderr)
        report["integration_exit_code"]=child.returncode
        report["passed"]=child.returncode==0
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as exc:
        report["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        report.update(completed_at_utc=_utc_now(),protected_after=hashes(PROTECTED))
        report["protected_unchanged"]=report["protected_before"]==report["protected_after"]
        report["passed"]=report["passed"] and report["protected_unchanged"]
        save(output/"report.json",report)
    print(json.dumps({key:report.get(key) for key in ("passed","candidate_positions","minimum_actual_captures","error","protected_unchanged")}),flush=True)
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
