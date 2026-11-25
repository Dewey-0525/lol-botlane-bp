import ssl
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

from scripts.validate_kr_source import tier_roster, summarize_cases, cache_expiry_delay, recover_cached_mismatches
from scripts.probe_kr_build_source import fetch_evidence
from scraper.lolalytics_build_probe import SourceValidationError, BuildScope


def tier_payload(rows):
    return dict(queue=420, response={"valid": True},
                tier={"1": {"lane": {"bottom": {"cid": rows}}}})


class FullValidationTests(unittest.TestCase):
    def test_existing_candidate_threshold_preserved(self):
        rows, low = tier_roster(tier_payload({"22": {"games": 100, "wr": 50},
                                             "30": {"games": 99, "wr": 50}}), "bottom")
        self.assertEqual([row["champion"] for row in rows], ["ashe"])
        self.assertEqual(low[0]["games"], 99)

    def test_unmapped_eligible_candidate_is_not_silently_skipped(self):
        with self.assertRaises(SourceValidationError):
            tier_roster(tier_payload({"999999": {"games": 100, "wr": 50}}), "bottom")

    def test_official_identity_support_does_not_change_ranked_or_100_game_rule(self):
        catalog = {"champions": {"805": {"key": "locke"}}}
        rows, low = tier_roster(tier_payload({"805": {"games": 100, "wr": 50}}), "bottom", catalog)
        self.assertEqual(rows[0]["champion"], "locke")
        payload = tier_payload({"805": {"games": 99, "wr": 50}})
        # Keep at least one eligible row: an empty full lane is itself a failure.
        catalog["champions"]["22"] = {"key": "ashe"}
        payload["tier"]["1"]["lane"]["bottom"]["cid"]["22"] = {"games": 100, "wr": 50}
        rows, low = tier_roster(payload, "bottom", catalog)
        self.assertEqual([row["champion"] for row in rows], ["ashe"])
        self.assertEqual(low[0]["reason"], "below_existing_100_game_candidate_threshold")
        payload["tier"]["?"] = {"lane": {"bottom": {"cid": {"805": {"games": 500, "wr": 50}}}}}
        del payload["tier"]["1"]["lane"]["bottom"]["cid"]["805"]
        rows, low = tier_roster(payload, "bottom", catalog)
        self.assertEqual([row["champion"] for row in rows], ["ashe"])
        self.assertEqual(low[0]["reason"], "unranked_existing_tier_exclusion")

    def test_invalid_roster_numbers_rejected(self):
        for row in ({"games": -100, "wr": 50}, {"games": 100, "wr": 999}):
            with self.assertRaises(SourceValidationError):
                tier_roster(tier_payload({"22": row}), "bottom")

    def test_incomplete_round_cannot_be_reported_complete(self):
        summary = summarize_cases([], 138)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["build_validated"], 0)
        self.assertEqual(summary["coverage"]["bottom"]["synergy"]["cases_with_coverage"], 0)

    def test_transport_keeps_certificate_and_hostname_verification(self):
        with tempfile.TemporaryDirectory() as output:
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as call:
                fetch_evidence("https://lolalytics.com/", Path(output) / "body")
            context = call.call_args.kwargs["context"]
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_retry_uses_remaining_declared_cache_lifetime(self):
        meta = dict(completed_at_utc="2026-11-02T15:00:00+00:00",
                    response_headers={"Cache-Control": "max-age=1800", "Age": "1200"})
        from datetime import datetime
        stamp = datetime.fromisoformat(meta["completed_at_utc"]).timestamp()
        self.assertEqual(cache_expiry_delay(meta, now=stamp), 601)
        self.assertEqual(cache_expiry_delay(meta, now=stamp+700), 0)
        del meta["response_headers"]["Age"]
        self.assertIsNone(cache_expiry_delay(meta, now=stamp))

    def test_cache_retry_keeps_initial_failure_and_can_recover(self):
        self._exercise_recovery("population_matches_scope_not_echoed", True)

    def test_fresh_cache_without_age_requires_explicit_timestamps(self):
        from datetime import datetime
        meta = dict(completed_at_utc="2026-11-02T15:00:00+00:00", response_headers={
            "Cache-Control": "max-age=1800", "cf-cache-status": "EXPIRED",
            "Date": "Mon, 02 Nov 2026 15:00:00 GMT", "last-modified": "Mon, 02 Nov 2026 15:00:00 GMT"})
        stamp = datetime.fromisoformat(meta["completed_at_utc"]).timestamp()
        self.assertEqual(cache_expiry_delay(meta, now=stamp), 1801)
        meta["response_headers"]["cf-cache-status"] = "DYNAMIC"
        self.assertIsNone(cache_expiry_delay(meta, now=stamp))

    def test_cache_retry_does_not_accept_a_second_mismatch(self):
        self._exercise_recovery("population_mismatch", False)

    def _exercise_recovery(self, final_status, succeeds):
        scope = BuildScope("leona", 89, "support", "16.21")
        case = dict(requested_scope=scope.conditions(), status="population_mismatch",
                    base={"games": 10}, evidence_directory="original",
                    team={"difference_games": 1}, sources={"build": {
                        "completed_at_utc": "2026-11-02T15:00:00+00:00",
                        "response_headers": {"Cache-Control": "max-age=1800", "Age": "1200"}}})
        report = dict(number=1, cases=[case], summary=summarize_cases([case], 1))
        retry = dict(requested_scope=scope.conditions(), status=final_status,
                     base={"games": 11}, sources={})
        with tempfile.TemporaryDirectory() as output:
            directory = Path(output) / "round-1"
            directory.mkdir()
            with patch("scripts.validate_kr_source.collect_case", return_value=retry) as collect:
                with patch("scripts.validate_kr_source.coverage", return_value=None):
                    with patch("scripts.validate_kr_source.time.monotonic", side_effect=AssertionError("HTTP expiry must use UTC, not a suspend-paused timer")):
                        result = recover_cached_mismatches(report, [scope], Path(output), {}, 1)
            self.assertEqual(collect.call_count, 1)
            initial = json.loads((directory / "report-initial.json").read_text())
            self.assertEqual(initial["cases"][0]["status"], "population_mismatch")
            self.assertEqual(result["cache_recovery"]["recovered"], int(succeeds))
            self.assertEqual(result["summary"]["population_mismatches"], int(not succeeds))
            self.assertEqual(result["cases"][0]["previous_attempt"]["difference_games"], 1)

    def test_missing_expiry_metadata_never_triggers_guessed_retry(self):
        scope = BuildScope("leona", 89, "support", "16.21")
        case = dict(requested_scope=scope.conditions(), status="population_mismatch", base={"games": 10},
                    sources={"build": {"response_headers": {}}})
        report = dict(number=1, cases=[case], summary=summarize_cases([case], 1))
        with tempfile.TemporaryDirectory() as output:
            (Path(output) / "round-1").mkdir()
            with patch("scripts.validate_kr_source.collect_case") as collect:
                result = recover_cached_mismatches(report, [scope], Path(output), {}, 1)
            collect.assert_not_called()
            self.assertEqual(result["cache_recovery"]["planned"], 0)
            self.assertEqual(result["summary"]["population_mismatches"], 1)

    def test_recovery_uses_numeric_identity_for_site_slug_alias(self):
        scope = BuildScope("monkeyking", 62, "support", "16.21")
        self.assertEqual(scope.conditions()["champion"], "wukong")
        case = dict(requested_scope=scope.conditions(), status="population_mismatch", base={"games": 10},
                    evidence_directory="original", team={"difference_games": 1}, sources={"build": {
                        "completed_at_utc": "2026-11-02T15:00:00+00:00",
                        "response_headers": {"Cache-Control": "max-age=1800", "Age": "1800"}}})
        retry = dict(requested_scope=scope.conditions(), status="population_matches_scope_not_echoed",
                     base={"games": 11}, sources={})
        report = dict(number=1, cases=[case], summary=summarize_cases([case], 1))
        with tempfile.TemporaryDirectory() as output:
            (Path(output) / "round-1").mkdir()
            with patch("scripts.validate_kr_source.collect_case", return_value=retry) as collect:
                with patch("scripts.validate_kr_source.coverage", return_value=None):
                    result = recover_cached_mismatches(report, [scope], Path(output), {}, 1)
            self.assertEqual(collect.call_args.args[0].champion, "monkeyking")
            self.assertTrue(result["cases"][0]["evidence_directory"].endswith("monkeyking_support"))
            self.assertEqual(result["cache_recovery"]["recovered"], 1)


if __name__ == "__main__":
    unittest.main()
