"""Rescan the actual KR candidate pool; never call a changed pool stable."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_kr_build_source import REPO_ROOT, _utc_now
from scripts.validate_kr_source import fetch_manifest, save


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir", required=True)
    parser.add_argument("--catalog-file", required=True)
    args = parser.parse_args(argv)
    trial = Path(args.trial_dir).resolve()
    if trial == REPO_ROOT or REPO_ROOT in trial.parents:
        raise ValueError("trial evidence and new roster output must be outside the repository")
    initial = json.loads((trial / "manifest.json").read_text())
    catalog = json.loads(Path(args.catalog_file).read_text())
    if not catalog["version"].startswith(initial["patch"] + "."):
        raise ValueError("identity patch differs from initial manifest")
    output = trial / "end-roster-check"
    output.mkdir(exist_ok=False)
    latest = fetch_manifest(initial["patch"], output, catalog)
    keyed = lambda manifest: {(r["champion_id"], r["lane"]): r["champion"] for r in manifest["candidates"]}
    before, after = keyed(initial), keyed(latest)
    result = dict(checked_at_utc=_utc_now(), patch=initial["patch"], start_count=len(before),
                  end_count=len(after), added=[dict(champion=after[key], champion_id=key[0], lane=key[1]) for key in sorted(after.keys()-before.keys())],
                  removed=[dict(champion=before[key], champion_id=key[0], lane=key[1]) for key in sorted(before.keys()-after.keys())],
                  pool_unchanged=before == after, production_accepted=False,
                  limitation="Changed upstream candidate eligibility is preserved, never silently skipped")
    save(trial / "roster-stability.json", result)
    print(json.dumps(result))
    return 0 if result["pool_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
