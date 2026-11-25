"""Capture official, patch-pinned champion identity for an isolated validation."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.kr_trial_catalog import build_catalog
from scripts.probe_kr_build_source import REPO_ROOT, create_output_dir, fetch_evidence
from scripts.validate_kr_source import save
from scraper.chinese_getchampion.hero_id_mapping import HERO_ID_MAPPING


def capture_missing_avatars(output, catalog):
    output = Path(output)
    directory = output / "avatars"
    directory.mkdir(exist_ok=False)
    for row in catalog["champions"].values():
        if (REPO_ROOT / f"static/avatars/{row['champion_id']}.png").is_file():
            continue
        url = f"https://ddragon.leagueoflegends.com/cdn/{catalog['version']}/img/champion/{row['asset_id']}.png"
        body, meta = fetch_evidence(url, directory / f"{row['champion_id']}.png")
        if body is None or meta.get("http_status") != 200 or not body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("missing official portrait could not be captured; no production asset changes")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version",required=True)
    parser.add_argument("--output-dir",required=True)
    args=parser.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+\.\d+",args.version):
        parser.error("explicit numbered official data version required")
    output=create_output_dir(args.output_dir)
    payloads=[]
    for locale in ("en_US","zh_CN"):
        body,meta=fetch_evidence(f"https://ddragon.leagueoflegends.com/cdn/{args.version}/data/{locale}/champion.json",
                                 output/f"champion-{locale}.json")
        if body is None or meta.get("http_status")!=200:
            raise ValueError("official identity capture failed; no legacy or other-patch fallback")
        payload=json.loads(body)
        if payload.get("version")!=args.version:
            raise ValueError("returned identity data version differs")
        payloads.append(payload)
    names=Path(__file__).resolve().parents[1]/"scraper/chinese_getchampion"
    catalog=build_catalog(*payloads,HERO_ID_MAPPING,(names/"hero_names.txt").read_text().splitlines(),
                          (names/"英雄名字.txt").read_text().splitlines())
    save(output/"catalog.json",catalog)
    capture_missing_avatars(output,catalog)
    print(f"Captured {len(catalog['champions'])} identities, version {catalog['version']}; trial only")
    return 0


if __name__=="__main__":raise SystemExit(main())
