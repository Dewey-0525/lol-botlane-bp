import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_kr_scheduled_live_check as worker


class ScheduledWorkerTests(unittest.TestCase):
    def exercise_rejected_source(self, failures):
        summary = dict(complete=True, failures=failures, population_mismatches=1)
        initial = dict(summary=summary)
        recovered = dict(summary=copy.deepcopy(summary), cache_recovery=dict(planned=1, recovered=0))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reference").mkdir()
            (root / "reference/manifest.json").write_text(json.dumps({"patch": "16.22"}))
            (root / "catalog").mkdir()
            (root / "catalog/catalog.json").write_text(json.dumps({"version": "16.22.1"}))
            manifest = dict(candidates=[dict(champion="ashe", champion_id=22, lane="bottom")])
            with patch.object(worker, "fetch_manifest", return_value=manifest), \
                    patch.object(worker, "run_round", return_value=initial), \
                    patch.object(worker, "recover_cached_mismatches", return_value=recovered) as retry, \
                    patch.object(worker.subprocess, "run") as application:
                status = worker.main(["--trial-dir", str(root / "reference"),
                                      "--catalog-source-dir", str(root / "catalog"),
                                      "--output-dir", str(root / "output")])
            report = json.loads((root / "output/report.json").read_text())
            self.assertEqual(status, 1)
            self.assertFalse(report["passed"])
            self.assertTrue(report["protected_unchanged"])
            self.assertTrue(report["implementation_unchanged_during_run"])
            application.assert_not_called()
            self.assertEqual(retry.call_count, 0 if failures else 1)
            self.assertFalse((root / "output/integration").exists())

    def test_timer_never_runs_application_after_expiry_retry_still_mismatches(self):
        self.exercise_rejected_source(failures=0)

    def test_timer_does_not_wait_for_cache_when_other_source_failures_already_exist(self):
        self.exercise_rejected_source(failures=1)


if __name__ == "__main__":
    unittest.main()
