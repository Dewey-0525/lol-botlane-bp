"""Synthetic failure checks for the isolated experiment, not recommendation tests."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from scraper.lolalytics_build_probe import (
    BuildScope, SourceValidationError, parse_build_page, compare_team_payload,
)
from scripts.probe_kr_build_source import REPO_ROOT, create_output_dir, collect_case, fetch_evidence


SCOPE = BuildScope("leona", 89, "support", "16.21")


def fixture():
    nav = dict(page="build", champion="leona", cid=89, lane="support", patch="16.21",
               tier="emerald_plus", region="kr", mode="ranked")
    data = dict(header=dict(cid=89, lane="support", patch="16.21", queue=420, n=10, wr=50),
                analysed=100, avgWr=51.66, response={"valid": True},
                enemy_h=["id", "wr", "d1", "d2", "pr", "n"],
                enemy={"bottom": [[222, 50, 0, 0, 60, 6], [51, 75, 0, 0, 40, 4]],
                       "support": [[412, 50, 0, 0, 100, 10]]},
                sidebar={"time": {"time": dict(zip("1234567", [1, 1, 2, 2, 2, 1, 1])),
                                  "timeWin": dict(zip("1234567", [1, 0, 1, 1, 1, 0, 1]))}})
    return nav, data


def page(nav, data):
    objects = ["unrelated entry", {"request_url": "2"}, "/?lane=support&region=kr"]

    def encode(value):
        index = len(objects)
        objects.append(None)
        if isinstance(value, dict):
            value = {key: encode(item) for key, item in value.items()}
        elif isinstance(value, list):
            value = [encode(item) for item in value]
        objects[index] = value
        chars, ref = "0123456789abcdefghijklmnopqrstuvwxyz", ""
        while index:
            ref = chars[index % 36] + ref
            index //= 36
        return ref or "0"

    encode(nav)
    encode(data)
    return '<script type="qwik/json">' + json.dumps({"objs": objects}) + '</script>'


class BuildPageTests(unittest.TestCase):
    def test_one_response_preserves_roles_timeline_and_low_samples(self):
        nav, data = fixture()
        result = parse_build_page(page(nav, data), SCOPE)
        self.assertEqual(result["hero_stats"]["games"], 10)
        self.assertEqual(result["hero_stats"]["stats_by_time"][0], dict(label="0-20", games=2, wins=1))
        self.assertEqual(result["counter_by_enemy_lane"]["bottom"][0], dict(champion_id=222, win_rate=50, games=6))
        self.assertEqual(result["counter_by_enemy_lane"]["support"][0]["champion_id"], 412)
        self.assertEqual(result["population"]["rank_avg_win_rate"], 51.66)
        self.assertFalse(result["production_accepted"])

    def test_wrong_page_scope_rejected(self):
        for key, value in [("region", "all"), ("tier", "emerald"), ("patch", "16.20"),
                           ("lane", "bottom"), ("champion", "ashe"), ("cid", 22), ("mode", "aram")]:
            with self.subTest(key=key):
                nav, data = fixture()
                nav[key] = value
                with self.assertRaises(SourceValidationError):
                    parse_build_page(page(nav, data), SCOPE)

    def test_wrong_header_scope_rejected(self):
        for key, value in [("cid", 22), ("lane", "bottom"), ("patch", "16.20"), ("queue", 440)]:
            with self.subTest(key=key):
                nav, data = fixture()
                data["header"][key] = value
                with self.assertRaises(SourceValidationError):
                    parse_build_page(page(nav, data), SCOPE)

    def test_invalid_base_and_relation_numbers_rejected(self):
        for field, value in [("games", -1), ("games", 11), ("games", 1.5),
                             ("wr", 999), ("wr", float("nan")), ("games", True)]:
            with self.subTest(field=field, value=value):
                nav, data = fixture()
                data["enemy"]["bottom"][0][5 if field == "games" else 1] = value
                with self.assertRaises(SourceValidationError):
                    parse_build_page(page(nav, data), SCOPE)
        nav, data = fixture()
        data["header"]["n"] = -100
        with self.assertRaises(SourceValidationError):
            parse_build_page(page(nav, data), SCOPE)

    def test_changed_schema_missing_position_and_duplicate_rows_rejected(self):
        nav, original = fixture()
        variants = []
        data = copy.deepcopy(original)
        data["enemy_h"][1] = "vsWr"
        variants.append(data)
        data = copy.deepcopy(original)
        del data["enemy"]["support"]
        variants.append(data)
        data = copy.deepcopy(original)
        data["enemy"]["bottom"][1][0] = 222
        variants.append(data)
        for data in variants:
            with self.assertRaises(SourceValidationError):
                parse_build_page(page(nav, data), SCOPE)

    def test_population_and_timeline_mismatch_rejected(self):
        for kind in ("counter_sum", "timeline_sum", "wins_over_games"):
            with self.subTest(kind=kind):
                nav, data = fixture()
                if kind == "counter_sum":
                    data["enemy"]["bottom"][0][5] = 5
                elif kind == "timeline_sum":
                    data["sidebar"]["time"]["time"]["1"] = 0
                else:
                    data["sidebar"]["time"]["timeWin"]["1"] = 2
                with self.assertRaises(SourceValidationError):
                    parse_build_page(page(nav, data), SCOPE)

    def test_missing_timeline_is_missing_not_zero(self):
        nav, data = fixture()
        del data["sidebar"]
        result = parse_build_page(page(nav, data), SCOPE)
        self.assertIsNone(result["hero_stats"]["stats_by_time"])
        self.assertEqual(result["timeline_status"], "missing")

    def test_sparse_zero_game_bucket_requires_exhaustive_population(self):
        nav, data = fixture()
        data["sidebar"]["time"]["time"]["1"] = 0
        data["sidebar"]["time"]["timeWin"]["1"] = 0
        data["sidebar"]["time"]["time"]["2"] += 1
        del data["sidebar"]["time"]["time"]["1"]
        del data["sidebar"]["time"]["timeWin"]["1"]
        result = parse_build_page(page(nav, data), SCOPE)
        first = result["hero_stats"]["stats_by_time"][0]
        self.assertEqual(first["games"], 2)
        self.assertEqual(first["source_omitted_zero_game_buckets"], ["1"])
        # The same omission must fail if the remaining buckets do not cover base.
        data["sidebar"]["time"]["time"]["2"] -= 1
        with self.assertRaises(SourceValidationError):
            parse_build_page(page(nav, data), SCOPE)

    def test_sparse_wins_must_be_uniquely_zero_not_unknown(self):
        nav, data = fixture()
        data["sidebar"]["time"]["timeWin"]["1"] = 0
        data["sidebar"]["time"]["timeWin"]["3"] = 2
        del data["sidebar"]["time"]["timeWin"]["1"]
        result = parse_build_page(page(nav, data), SCOPE)
        self.assertEqual(result["hero_stats"]["stats_by_time"][0]["source_omitted_zero_win_buckets"], ["1"])
        data["header"]["wr"] = 60
        with self.assertRaises(SourceValidationError):
            parse_build_page(page(nav, data), SCOPE)
        # A rounded base rate cannot prove an omitted win count in a large pool.
        data["header"].update(n=100000, wr=50)
        data["analysed"] = 100000
        data["enemy"]["bottom"] = [[222, 50, 0, 0, 100, 100000]]
        data["enemy"]["support"][0][5] = 100000
        data["sidebar"]["time"]["time"] = {"1": 1, "2": 99999}
        data["sidebar"]["time"]["timeWin"] = {"2": 50000}
        with self.assertRaises(SourceValidationError):
            parse_build_page(page(nav, data), SCOPE)

    def test_exact_rounding_tie_can_uniquely_prove_zero_omitted_wins(self):
        nav, data = fixture()
        data["header"].update(n=160, wr=48.13)
        data["analysed"] = 160
        data["enemy"]["bottom"] = [[222, 48.13, 0, 0, 100, 160]]
        data["enemy"]["support"] = [[412, 48.13, 0, 0, 100, 160]]
        data["sidebar"]["time"] = {
            "time": dict(zip("1234567", [1, 25, 27, 49, 35, 19, 4])),
            "timeWin": dict(zip("234567", [19, 19, 17, 14, 5, 3])),
        }
        result = parse_build_page(page(nav, data), SCOPE)
        timeline = result["hero_stats"]["stats_by_time"]
        self.assertEqual(sum(row["wins"] for row in timeline), 77)
        self.assertEqual(timeline[0]["source_omitted_zero_win_buckets"], ["1"])

    def test_rounding_tie_still_rejects_an_additional_possible_win(self):
        nav, data = fixture()
        data["header"].update(n=20000, wr=50)
        data["analysed"] = 20000
        data["enemy"]["bottom"] = [[222, 50, 0, 0, 100, 20000]]
        data["enemy"]["support"] = [[412, 50, 0, 0, 100, 20000]]
        data["sidebar"]["time"] = {
            "time": {"1": 2, "2": 19998},
            "timeWin": {"2": 10000},
        }
        # 10001 wins is exactly the upper rounding boundary, so zero is
        # not the only possible value for the omitted bucket.
        with self.assertRaises(SourceValidationError):
            parse_build_page(page(nav, data), SCOPE)

    def test_blocked_page_and_cyclic_reference_rejected(self):
        with self.assertRaises(SourceValidationError):
            parse_build_page("<html>Cloudflare challenge</html>", SCOPE)
        nav, data = fixture()
        payload = json.loads(page(nav, data).split(">", 1)[1].split("</script>")[0])
        target = next(obj for obj in payload["objs"] if isinstance(obj, dict) and "enemy_h" in obj)
        target["enemy"] = "1"
        payload["objs"][1] = {"self": "1"}
        with self.assertRaises(SourceValidationError):
            parse_build_page('<script type="qwik/json">' + json.dumps(payload) + '</script>', SCOPE)

    def test_team_mismatch_reported_without_allowance_or_scope_upgrade(self):
        nav, data = fixture()
        bundle = parse_build_page(page(nav, data), SCOPE)
        team = dict(response={"valid": True}, team_h=["id", "wr", "d1", "d2", "pr", "n"],
                    team={"bottom": [[145, 50, 0, 0, 100, 9]]})
        result = compare_team_payload(team, bundle, SCOPE)
        self.assertEqual(result["difference_games"], -1)
        self.assertEqual(result["status"], "population_mismatch")
        team["team"]["bottom"][0][5] = 10
        result = compare_team_payload(team, bundle, SCOPE)
        self.assertEqual(result["status"], "population_matches_scope_not_echoed")
        self.assertFalse(result["production_accepted"])

    def test_no_repository_output_and_no_implicit_patch(self):
        with self.assertRaises(ValueError):
            create_output_dir(str(REPO_ROOT / "data" / "trial"))
        with tempfile.TemporaryDirectory() as parent:
            path = create_output_dir(str(Path(parent) / "trial"))
            with self.assertRaises(FileExistsError):
                create_output_dir(str(path))
        for patch in ("", "7", "14", "current"):
            with self.assertRaises(ValueError):
                BuildScope("leona", 89, "support", patch)

    def test_invalid_response_metadata_rejected(self):
        nav, data = fixture()
        data["response"] = []
        with self.assertRaises(SourceValidationError):
            parse_build_page(page(nav, data), SCOPE)
        nav, data = fixture()
        bundle = parse_build_page(page(nav, data), SCOPE)
        with self.assertRaises(SourceValidationError):
            compare_team_payload({"response": []}, bundle, SCOPE)

    def test_transport_failure_keeps_evidence_without_trying_other_source(self):
        with tempfile.TemporaryDirectory() as output:
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as request:
                result = collect_case(SCOPE, Path(output))
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result["status"], "failed")
            directory = Path(output) / "leona_support"
            self.assertTrue((directory / "build.html.request.json").exists())
            self.assertFalse((directory / "bundle.json").exists())

    def test_http_failure_body_is_preserved(self):
        error = urllib.error.HTTPError(SCOPE.build_url(), 403, "blocked", {}, io.BytesIO(b"challenge"))
        with tempfile.TemporaryDirectory() as output:
            destination = Path(output) / "build.html"
            with patch("urllib.request.urlopen", side_effect=error):
                body, meta = fetch_evidence(SCOPE.build_url(), destination)
            self.assertEqual(meta["http_status"], 403)
            self.assertEqual(body, b"challenge")
            self.assertEqual(destination.read_bytes(), b"challenge")

    def test_team_failure_is_not_reported_as_complete_sample(self):
        nav, data = fixture()
        responses = [(page(nav, data).encode(), {"http_status": 200}), (None, {"error": "offline"})]
        with tempfile.TemporaryDirectory() as output:
            with patch("scripts.probe_kr_build_source.fetch_evidence", side_effect=responses):
                with patch("scripts.probe_kr_build_source.time.sleep"):
                    result = collect_case(SCOPE, Path(output))
            self.assertEqual(result["status"], "team_failed")
            self.assertEqual(result["base"]["games"], 10)
            self.assertFalse("team" in result)


if __name__ == "__main__":
    unittest.main()
