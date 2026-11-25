"""Identity, position and evidence rejection tests for the isolated runtime."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_kr_build_probe import fixture, page
from scraper.lolalytics_build_probe import BuildScope, SourceValidationError, parse_build_page, compare_team_payload
from scripts.kr_trial_catalog import build_catalog, app_metadata
from scripts.kr_trial_dataset import build_dataset, existing_admission_checks
from scripts.prepare_kr_trial_runtime import patched_sources, prepare_runtime
from scripts.probe_kr_build_source import REPO_ROOT
import bp_engine as original_engine


def catalog_fixture():
    rows = [(22, "Ashe", "艾希"), (30, "Karthus", "卡尔萨斯"),
            (89, "Leona", "蕾欧娜"), (805, "Locke", "洛克")]
    en = dict(version="16.21.1", data={key:dict(key=str(cid), id=key, name=key, title=key + " title") for cid,key,cn in rows})
    cn = dict(version="16.21.1", data={key:dict(key=str(cid), id=key, name=cn, title=cn + "称号") for cid,key,cn in rows})
    return en, cn


def make_catalog():
    return build_catalog(*catalog_fixture(), {"leona":89,"ashe":22,"karthus":30},
                         ["Ashe","Karthus","Leona"], ["寒冰射手","死亡颂唱者","曙光女神"])


def make_trial(directory, impossible_rates=False, low_candidate_base=False):
    candidates, cases = [], []
    for name,cid,lane in (("leona",89,"support"),("ashe",22,"bottom")):
        scope = BuildScope(name,cid,lane,"16.21")
        nav, data = fixture()
        nav.update(champion=name,cid=cid,lane=lane)
        data["header"].update(cid=cid,lane=lane,n=200)
        data["analysed"] = 2000
        data["enemy"]["bottom"] = [[22,50,0,0,0,100],[30,49.49,0,0,0,99],[805,100,0,0,0,1]]
        data["enemy"]["support"] = [[89,50,0,0,0,200]]
        if impossible_rates:
            data["enemy"]["support"][0][1] = 49
        data["sidebar"]["time"] = {"time":{"1":200},"timeWin":{"1":100}}
        if low_candidate_base:
            data["header"].update(n=99,wr=50.51)
            data["enemy"]={"bottom":[[22,50.51,0,0,0,99]],"support":[[89,50.51,0,0,0,99]]}
            data["sidebar"]["time"]={"time":{"1":99},"timeWin":{"1":50}}
        body = page(nav,data).encode()
        bundle = parse_build_page(body.decode(),scope)
        partner_lane = "bottom" if lane == "support" else "support"
        team = dict(response={"valid":True},team_h=["id","wr","d1","d2","pr","n"],
                    team={partner_lane: data["enemy"][partner_lane]})
        team_body = json.dumps(team).encode()
        comparison = compare_team_payload(team,bundle,scope)
        target = directory / "round-1" / f"{name}_{lane}"
        target.mkdir(parents=True)
        for file,value in (("bundle.json",bundle),("team_comparison.json",comparison)):
            (target/file).write_text(json.dumps(value))
        sources = {}
        for label,raw,url in (("build",body,scope.build_url()),("team",team_body,scope.team_url())):
            (target / ("build.html" if label == "build" else "team.json")).write_bytes(raw)
            sources[label] = dict(http_status=200,sha256=hashlib.sha256(raw).hexdigest(),url=url,final_url=url)
        candidates.append(dict(champion=name,champion_id=cid,lane=lane,tier_key="5",games=200))
        cases.append(dict(requested_scope=scope.conditions(),status="population_matches_scope_not_echoed",sources=sources))
    manifest = dict(patch="16.21",region="kr",tier="emerald_plus",queue="ranked",candidates=candidates)
    report = dict(cases=cases,completed_at_utc="2026-11-03T12:00:00+00:00")
    (directory/"manifest.json").write_text(json.dumps(manifest))
    (directory/"round-1/report.json").write_text(json.dumps(report))
    return manifest, report


class IdentityTests(unittest.TestCase):
    def test_identity_join_survives_mapping_and_locale_reordering(self):
        catalog = make_catalog()
        self.assertEqual(catalog["champions"]["22"]["cn_name"],"寒冰射手")
        self.assertEqual(catalog["champions"]["89"]["cn_name"],"曙光女神")
        self.assertEqual(catalog["champions"]["805"]["key"],"locke")

    def test_truncated_names_and_official_identity_conflicts_fail(self):
        for en,cn,names in [(*catalog_fixture(), ["Ashe"]), (*catalog_fixture(), ["Ashe","Leona"])]:
            cn["data"]["Ashe"]["key"] = "89"
            with self.assertRaises(SourceValidationError):
                build_catalog(en,cn,{"ashe":22,"leona":89},names,["艾希","蕾欧娜"])
        en,cn = catalog_fixture()
        with self.assertRaises(SourceValidationError):
            build_catalog(en,cn,{"ashe":22},["Ashe"],[])

    def test_identity_version_mismatch_fails(self):
        en,cn = catalog_fixture(); cn["version"] = "16.20.1"
        with self.assertRaises(SourceValidationError):
            build_catalog(en,cn,{"ashe":22},["Ashe"],["寒冰"])

    def test_same_length_but_reordered_chinese_labels_are_rejected(self):
        with self.assertRaisesRegex(SourceValidationError,"Chinese label conflicts"):
            build_catalog(*catalog_fixture(),{"ashe":22,"leona":89},["Ashe","Leona"],["曙光女神","寒冰射手"])

    def test_official_english_display_alias_is_searchable(self):
        from app import build_search_tokens
        catalog=make_catalog()
        catalog["champions"]["62"]=dict(champion_id=62,key="monkeyking",name="Wukong",cn_name="齐天大圣",
                                         localized_name="孙悟空",localized_title="齐天大圣")
        meta=app_metadata(catalog,{},build_search_tokens)
        self.assertIn("wukong",meta["monkeyking"]["search_text"])
        self.assertEqual(meta["monkeyking"]["champion_id"],62)


class DatasetTests(unittest.TestCase):
    def test_duplicate_positions_cannot_inflate_existing_flat_coverage_floors(self):
        db=dict(meta={"fetch_errors_count":0},tiers={"bottom":{},"support":{}},
                hero_stats={"bottom":{},"support":{}},synergy_by_lane={},counter_by_lane={})
        for lane in ("bottom","support"):
            db["synergy_by_lane"][lane]={"same_hero":{str(i):{} for i in range(5)}}
            db["counter_by_lane"][lane]={"same_hero":{"vs_adc":{str(i):{} for i in range(30)},"vs_sup":{str(i):{} for i in range(20)}}}
        result=existing_admission_checks(db)
        self.assertFalse(result["checks"]["synergy_rows"])
        self.assertFalse(result["checks"]["counter_adc_rows"])
        self.assertFalse(result["checks"]["counter_support_rows"])
        self.assertEqual(result["counts"]["synergy_rows"],5)

    def test_real_replay_contract_and_100_game_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);make_trial(directory)
            db,audit=build_dataset(directory,1,make_catalog())
            self.assertEqual(db["counter_by_lane"]["support"]["leona"]["vs_adc"],{"ashe":{"win_rate":50,"games":100}})
            self.assertNotIn("karthus",db["synergy_by_lane"]["support"]["leona"])
            self.assertEqual(db["counter"],{})
            self.assertEqual(db["synergy"],{})
            self.assertEqual(audit["raw_responses_verified"],4)
            self.assertTrue(db["meta"]["trial_only"])
            self.assertFalse(db["meta"]["production_accepted"])

    def test_raw_tampering_or_derived_tampering_is_rejected(self):
        for name in ("build.html","team.json","bundle.json","team_comparison.json"):
            with self.subTest(file=name),tempfile.TemporaryDirectory() as temp:
                directory=Path(temp);make_trial(directory)
                (directory/"round-1/leona_support"/name).write_text("{}")
                with self.assertRaises(SourceValidationError):
                    build_dataset(directory,1,make_catalog())

    def test_partial_or_failed_collection_never_becomes_dataset(self):
        for failure in ("missing","mismatch","duplicate"):
            with tempfile.TemporaryDirectory() as temp:
                directory=Path(temp);manifest,report=make_trial(directory)
                if failure=="missing":report["cases"].pop()
                if failure=="mismatch":report["cases"][0]["status"]="population_mismatch"
                if failure=="duplicate":report["cases"].append(copy.deepcopy(report["cases"][0]))
                (directory/"round-1/report.json").write_text(json.dumps(report))
                with self.assertRaises(SourceValidationError):
                    build_dataset(directory,1,make_catalog())

    def test_wrong_scope_and_unknown_relation_identity_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);manifest,report=make_trial(directory)
            catalog=make_catalog();del catalog["champions"]["805"]
            with self.assertRaises(SourceValidationError):build_dataset(directory,1,catalog)
            manifest["region"]="all"
            (directory/"manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(SourceValidationError):build_dataset(directory,1,make_catalog())

    def test_same_game_totals_do_not_hide_inconsistent_win_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);make_trial(directory,impossible_rates=True)
            with self.assertRaisesRegex(SourceValidationError,"partition wins"):
                build_dataset(directory,1,make_catalog())

    def test_eligible_tier_cannot_hide_a_cached_base_below_100_games(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);make_trial(directory,low_candidate_base=True)
            with self.assertRaisesRegex(SourceValidationError,"same-page candidate base"):
                build_dataset(directory,1,make_catalog())


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources=patched_sources()
        cls.engine=types.ModuleType("kr_trial_test_engine")
        exec(compile(cls.sources["bp_engine.py"],"trial/bp_engine.py","exec"),cls.engine.__dict__)

    def setUp(self):
        self.db=dict(meta={"positions_required":True}, tiers={"bottom":{"ashe":"A"},"support":{"ashe":"B","leona":"A"}},
                     hero_stats={"bottom":{},"support":{}},synergy={"ashe":{"leona":{"win_rate":99,"games":9999}}},counter={},
                     synergy_by_lane={"support":{"ashe":{"leona":{"win_rate":10,"games":999}},"leona":{"ashe":{"win_rate":60,"games":123}}},"bottom":{"ashe":{"leona":{"win_rate":55,"games":111}}}},
                     counter_by_lane={"bottom":{"ashe":{"vs_adc":{},"vs_sup":{"leona":{"win_rate":40,"games":222}}}},"support":{"ashe":{"vs_adc":{},"vs_sup":{"leona":{"win_rate":80,"games":333}}},"leona":{"vs_adc":{"ashe":{"win_rate":60,"games":444}},"vs_sup":{"ashe":{"win_rate":20,"games":555}}}}})

    def test_reproduced_reversed_synergy_uses_correct_candidate_position(self):
        old=original_engine.calculate_synergy_bonus(self.db,"leona","ashe",0,0,"support")
        fixed=self.engine.calculate_synergy_bonus(self.db,"leona","ashe",0,0,"support")
        self.assertEqual(old["duo_games"],999)
        self.assertEqual(fixed["duo_games"],123)
        # This output is the existing smoothed value, not raw 60 percent.
        self.assertAlmostEqual(fixed["actual_duo_absolute_winrate"],(.60*123+.5*1000)/(123+1000))

    def test_missing_correct_position_never_uses_reverse_or_legacy(self):
        del self.db["synergy_by_lane"]["support"]["leona"]["ashe"]
        fixed=self.engine.calculate_synergy_bonus(self.db,"leona","ashe",0,0,"support")
        self.assertEqual(fixed["duo_games"],0)
        self.assertIsNone(fixed["actual_duo_absolute_winrate"])
        self.db["synergy_by_lane"]["support"]={}
        self.assertEqual(self.engine.get_lane_relation_matrix(self.db,"synergy","support"),{})

    def test_explicit_enemy_position_reaches_both_counter_directions(self):
        for lane,forward,reverse in (("bottom",444,222),("support",555,333)):
            rows=self.engine.find_counter_picks("ashe","support",self.db,top_n=None,enemy_lane=lane)
            row=next(row for row in rows if row["name"]=="leona")
            self.assertEqual(row["forward_games"],forward)
            self.assertEqual(row["reverse_games"],reverse)
        with self.assertRaises(ValueError):self.engine.find_counter_picks("ashe","support",self.db)

    def test_score_constants_are_unchanged(self):
        for name,value in vars(original_engine).items():
            if name.isupper():self.assertEqual(getattr(self.engine,name),value,name)

    def test_app_routes_reject_ambiguous_position_and_accept_explicit_position(self):
        module=types.ModuleType("kr_trial_test_app");module.__file__=str(REPO_ROOT/"app.py")
        with patch.dict(sys.modules,{"bp_engine":self.engine,"kr_trial_test_app":module}):
            exec(compile(self.sources["app.py"],"trial/app.py","exec"),module.__dict__)
        module.DB=self.db
        module.CHAMPION_META=app_metadata(make_catalog(),{},module.build_search_tokens)
        client=module.app.test_client()
        for endpoint in ("synergy","counter","matchup"):
            self.assertEqual(client.get(f"/api/{endpoint}/ashe").status_code,400)
            self.assertEqual(client.get(f"/api/{endpoint}/ashe?lane=middle").status_code,400)
            response=client.get(f"/api/{endpoint}/ashe?lane=bottom")
            self.assertEqual(response.status_code,200,response.get_json())
            self.assertEqual(response.get_json()["lane"],"bottom")
        self.assertIn('id="counter_lane"',self.sources["templates/index.html"])
        self.assertIn('id="synergy_lane"',self.sources["templates/index.html"])

    def test_preparation_never_overwrites_repository_or_existing_directory(self):
        with self.assertRaises(ValueError):prepare_runtime(REPO_ROOT/"trial-must-not-exist")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileExistsError):prepare_runtime(Path(temp))


if __name__=="__main__":unittest.main()
