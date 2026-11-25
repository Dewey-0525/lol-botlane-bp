"""A coherent source response must not hide a same-patch count regression."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import validate_kr_source as validation


class CountRegressionTests(unittest.TestCase):
    def exercise(self, last_games):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'evidence'
            manifest = dict(candidates=[dict(champion='leona', champion_id=89, lane='support')])

            def round_result(number, _scopes, directory, _manifest, _workers):
                target = directory / f'round-{number}'
                target.mkdir()
                report = dict(number=number, started_at_utc='2026-11-25T13:00:00+00:00',
                              completed_at_utc='2026-11-25T13:00:01+00:00',
                              cases=[dict(requested_scope=dict(champion='leona', lane='support'),
                                          base=dict(games=1000 if number == 1 else last_games),
                                          normalized_bundle_sha256=f'different-round-{number}')],
                              summary=dict(complete=True, failures=0, population_mismatches=0))
                (target / 'report.json').write_text(json.dumps(report))
                return report

            with patch.object(validation, 'fetch_manifest', return_value=manifest), \
                    patch.object(validation, 'run_round', side_effect=round_result):
                status = validation.main(['--patch', '16.23', '--rounds', '2',
                                          '--round-gap-seconds', '0', '--output-dir', str(output)])
            return status, json.loads((output / 'report.json').read_text())

    def test_same_patch_source_decrease_fails_the_full_run(self):
        status, report = self.exercise(999)
        self.assertEqual(status, 1)
        self.assertTrue(report['source_count_regression_detected'])
        self.assertTrue(report['stopped_for_investigation'])
        self.assertEqual(report['source_statistical_stability'], 'COUNT_REGRESSION_DETECTED_CAUSE_UNCONFIRMED')

    def test_growth_does_not_claim_the_historical_cause_was_resolved(self):
        status, report = self.exercise(1001)
        self.assertEqual(status, 0)
        self.assertFalse(report['source_count_regression_detected'])
        self.assertIn('HISTORICAL_CAUSE_NOT_RESOLVED', report['source_statistical_stability'])
        self.assertFalse(report['production_accepted'])


if __name__ == '__main__':
    unittest.main()
