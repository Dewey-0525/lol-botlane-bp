"""Replay captured real data into a fresh isolated application and verify routes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.kr_trial_catalog import build_catalog
from scripts.kr_trial_dataset import build_dataset, read_json, existing_admission_checks
from scripts.prepare_kr_trial_runtime import prepare_runtime
from scripts.probe_kr_build_source import REPO_ROOT, create_output_dir, _utc_now
from scripts.validate_kr_source import PROTECTED, hashes, save
from scraper.chinese_getchampion.hero_id_mapping import HERO_ID_MAPPING


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir", required=True)
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--catalog-source-dir", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    output = create_output_dir(args.output_dir)
    report = dict(started_at_utc=_utc_now(), output=str(output), production_accepted=False,
                  mode="saved_real_source_replay", cloud_new_code="NOT RUN",
                  protected_before=hashes(PROTECTED), passed=False)
    print(f"Integration evidence: {output}", flush=True)
    try:
        source = Path(args.catalog_source_dir)
        locales = []
        for locale in ("en_US", "zh_CN"):
            path = source / f"champion-{locale}.json"
            meta = read_json(path.with_suffix(".json.request.json"))
            if meta.get("http_status") != 200 or meta.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError("official identity source failed evidence verification")
            locales.append(read_json(path))
        names_dir = REPO_ROOT / "scraper/chinese_getchampion"
        catalog = build_catalog(*locales, HERO_ID_MAPPING,
                                (names_dir / "hero_names.txt").read_text().splitlines(),
                                (names_dir / "英雄名字.txt").read_text().splitlines())
        report["identity_count"] = len(catalog["champions"])
        dataset, audit = build_dataset(args.trial_dir, args.round, catalog)
        report["dataset_audit"] = audit
        report["existing_admission_checks"] = existing_admission_checks(dataset)
        if not report["existing_admission_checks"]["passed"]:
            raise ValueError("trial data does not meet existing production coverage floors")
        runtime = prepare_runtime(output / "runtime")
        portraits = 0
        for row in catalog["champions"].values():
            target = runtime / f"static/avatars/{row['champion_id']}.png"
            if target.is_file():
                continue
            asset = source / f"avatars/{row['champion_id']}.png"
            meta = read_json(asset.with_suffix(".png.request.json"))
            raw = asset.read_bytes()
            url = f"https://ddragon.leagueoflegends.com/cdn/{catalog['version']}/img/champion/{row['asset_id']}.png"
            if (meta.get("http_status") != 200 or meta.get("url") != url or meta.get("final_url") != url
                    or meta.get("sha256") != hashlib.sha256(raw).hexdigest()
                    or not raw.startswith(b"\x89PNG\r\n\x1a\n")):
                raise ValueError("official portrait evidence failed verification")
            shutil.copyfile(asset, target)
            portraits += 1
        report["official_portraits_added_to_trial_only"] = portraits
        save(runtime / "data/trial_catalog.json", catalog)
        save(runtime / "data/botlane_dataset.json", dataset)
        child = subprocess.run([sys.executable, "scripts/check_kr_trial_runtime.py"],
                               cwd=runtime, capture_output=True, text=True, timeout=180)
        (output / "runtime.stdout.log").write_text(child.stdout)
        (output / "runtime.stderr.log").write_text(child.stderr)
        if child.returncode:
            raise ValueError(f"isolated application validation failed: {child.stderr[-1400:]}")
        report["runtime"] = read_json(runtime / "runtime-check.json")
        report["patch_sha256"] = hashlib.sha256((runtime / "runtime.patch").read_bytes()).hexdigest()
        report["passed"] = report["runtime"]["passed"]
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report.update(completed_at_utc=_utc_now(), protected_after=hashes(PROTECTED))
        report["protected_unchanged"] = report["protected_before"] == report["protected_after"]
        report["passed"] = report["passed"] and report["protected_unchanged"]
        save(output / "report.json", report)
    print(json.dumps({key:report.get(key) for key in ("passed", "identity_count", "runtime", "error", "protected_unchanged")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
