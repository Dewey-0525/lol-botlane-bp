"""Temporary macOS launchd replay check, cleaned up in finally. Not a cloud test."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_kr_build_source import REPO_ROOT, create_output_dir, _utc_now
from scripts.validate_kr_source import save


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir",required=True)
    parser.add_argument("--catalog-source-dir",required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--live",action="store_true",help="Run fresh full-pool collection, not only saved-source replay")
    args=parser.parse_args(argv)
    if sys.platform!="darwin":parser.error("this check is specifically for macOS, not GitHub Linux")
    output=create_output_dir(args.output_dir)
    label="com.lol-botlane-bp.isolated-validation."+uuid.uuid4().hex
    domain=f"gui/{os.getuid()}"
    target=datetime.now().astimezone().replace(second=0,microsecond=0)+timedelta(minutes=1)
    if target.timestamp()-time.time()<15:target+=timedelta(minutes=1)
    script="run_kr_scheduled_live_check.py" if args.live else "validate_kr_integration.py"
    arguments=[sys.executable,str(REPO_ROOT/"scripts"/script),
               "--trial-dir",str(Path(args.trial_dir).resolve()),
               "--catalog-source-dir",str(Path(args.catalog_source_dir).resolve()),
               "--output-dir",str(output/"integration")]
    config=dict(Label=label,ProgramArguments=arguments,WorkingDirectory=str(REPO_ROOT),
                RunAtLoad=False,StartCalendarInterval=dict(Month=target.month,Day=target.day,Hour=target.hour,Minute=target.minute),
                StandardOutPath=str(output/"launchd.stdout.log"),StandardErrorPath=str(output/"launchd.stderr.log"))
    plist=output/"validation.plist"
    plist.write_bytes(plistlib.dumps(config))
    report=dict(started_at_utc=_utc_now(),scheduled_at_utc=target.astimezone(timezone.utc).isoformat(),
                label=label,environment="macOS launchd calendar event, noninteractive",
                mode="live fresh full-pool collection through application" if args.live else "saved real-source replay",
                cloud_new_code="NOT RUN; remote writes declined",production_accepted=False,passed=False)
    save(output/"report.json",report)
    loaded=False
    print(f"Local scheduler evidence: {output}; scheduled {report['scheduled_at_utc']}",flush=True)
    try:
        child=subprocess.run(["launchctl","bootstrap",domain,str(plist)],capture_output=True,text=True)
        if child.returncode:raise ValueError("launchd bootstrap failed: "+child.stderr.strip())
        loaded=True
        # A live mismatch may wait for one server-declared 30-minute cache
        # lifetime. Bound the whole observation, including collection and replay.
        deadline=target.timestamp()+(3000 if args.live else 120)
        while time.time()<deadline:
            status=subprocess.run(["launchctl","print",domain+"/"+label],capture_output=True,text=True)
            worker=output/"integration/report.json"
            match=re.search(r"last exit code = (\d+)",status.stdout)
            if match and int(match.group(1)) != 0 and not worker.exists():
                report["launchd_last_exit_code"]=int(match.group(1))
                raise ValueError("scheduled worker exited before creating its evidence report")
            if worker.exists():
                data=json.loads(worker.read_text())
                if match:
                    report.update(worker_started_at_utc=data["started_at_utc"],worker_completed_at_utc=data["completed_at_utc"],
                                  worker_passed=data["passed"],launchd_last_exit_code=int(match.group(1)),
                                  triggered_at_or_after_calendar_time=datetime.fromisoformat(data["started_at_utc"]).timestamp()>=target.timestamp())
                    report["passed"]=data["passed"] and int(match.group(1))==0 and report["triggered_at_or_after_calendar_time"]
                    break
            time.sleep(2)
        else:raise ValueError("scheduled replay did not complete within the bounded observation window")
    except (ValueError,OSError,KeyError) as exc:
        report["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        if loaded:
            cleanup=subprocess.run(["launchctl","bootout",domain+"/"+label],capture_output=True,text=True)
            report["cleanup_exit_code"]=cleanup.returncode
            check=subprocess.run(["launchctl","print",domain+"/"+label],capture_output=True,text=True)
            report["temporary_job_removed"]=cleanup.returncode==0 and check.returncode!=0
        else:report["temporary_job_removed"]=True
        report["passed"]=report["passed"] and report["temporary_job_removed"]
        report["completed_at_utc"]=_utc_now()
        save(output/"report.json",report)
    print(json.dumps(report),flush=True)
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
