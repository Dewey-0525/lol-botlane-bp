"""Real updater/Flask boundary tests with injected collection, never real writes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import app as service
import update_data as updater


REPO = Path(__file__).resolve().parents[1]


class UpdateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.active = self.directory / 'botlane_dataset.json'
        self.original = json.loads((REPO / 'data/botlane_dataset.json').read_text())
        self.original['meta']['transaction_fixture'] = 'old'
        self.active.write_text(json.dumps(self.original))
        self.old_bytes = self.active.read_bytes()
        for target, name, value in (
            (updater, 'DATA_PATH', str(self.active)),
            (updater, 'BACKUP_DIR', str(self.directory / 'backups')),
            (service, 'DB_PATH', str(self.active)),
            (service, 'DB', None),
        ):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)
        original_validation = updater.validate_dataset
        validation = patch.object(updater, 'validate_dataset',
                                  side_effect=lambda path=None: original_validation(path or str(self.active)))
        validation.start()
        self.addCleanup(validation.stop)

    def collector(self, candidate):
        def run(args, **kwargs):
            self.assertIn('pre_fetch.py', args)
            destination = Path(args[args.index('--output') + 1]) if '--output' in args else self.active
            destination.write_text(json.dumps(candidate))
            self.destination = destination
            self.observed_during_collection = service.app.test_client().get('/api/health').get_json()
        return run

    def test_invalid_candidate_never_enters_running_application(self):
        bad = copy.deepcopy(self.original)
        bad['meta']['transaction_fixture'] = 'invalid'
        bad['tiers'] = {'bottom': {}, 'support': {}}
        with patch.object(updater, 'run_command', side_effect=self.collector(bad)), \
                patch.object(updater, 'run_api_tests') as api:
            with self.assertRaises(ValueError):
                updater.update_data()
        self.assertEqual(self.observed_during_collection['data_meta']['transaction_fixture'], 'old')
        self.assertEqual(self.active.read_bytes(), self.old_bytes)
        self.assertEqual(service.get_db()['meta']['transaction_fixture'], 'old')
        api.assert_not_called()

    def test_api_failure_preserves_original_bytes(self):
        candidate = copy.deepcopy(self.original)
        candidate['meta']['transaction_fixture'] = 'candidate'
        with patch.object(updater, 'run_command', side_effect=self.collector(candidate)), \
                patch.object(updater, 'run_api_tests', side_effect=ValueError('injected API failure')):
            with self.assertRaisesRegex(ValueError, 'injected API failure'):
                updater.update_data()
        self.assertEqual(self.observed_during_collection['data_meta']['transaction_fixture'], 'old')
        self.assertEqual(self.active.read_bytes(), self.old_bytes)

    def test_real_api_child_checks_candidate_before_publication(self):
        candidate = copy.deepcopy(self.original)
        candidate['meta']['transaction_fixture'] = 'candidate'
        observed = {}
        real_command = updater.run_command

        def command(args, **kwargs):
            if 'pre_fetch.py' in args:
                return self.collector(candidate)(args, **kwargs)
            self.assertIn('test_api.py', args)
            observed['active_before_api'] = self.active.read_bytes()
            observed['candidate_path'] = kwargs.get('env', {}).get('BOTLANE_DATA_PATH')
            self.assertEqual(Path(observed['candidate_path']), self.destination)
            # Runs the existing complete API regression in a real child process.
            return real_command(args, **kwargs)

        with patch.object(updater, 'run_command', side_effect=command):
            updater.update_data()
        self.assertEqual(observed['active_before_api'], self.old_bytes)
        self.assertEqual(json.loads(self.active.read_text())['meta']['transaction_fixture'], 'candidate')
        self.assertNotEqual(self.destination, self.active)
        self.assertFalse(self.destination.exists())
        # The deployment owns process replacement; do not silently hot-reload.
        self.assertEqual(service.get_db()['meta']['transaction_fixture'], 'old')

    def test_failed_update_does_not_restore_over_an_external_change(self):
        candidate = copy.deepcopy(self.original)
        external = copy.deepcopy(self.original)
        external['meta']['transaction_fixture'] = 'external'

        def api_failure(*_args, **_kwargs):
            self.active.write_text(json.dumps(external))
            raise ValueError('injected API failure after external change')

        with patch.object(updater, 'run_command', side_effect=self.collector(candidate)), \
                patch.object(updater, 'run_api_tests', side_effect=api_failure):
            with self.assertRaises(ValueError):
                updater.update_data()
        self.assertEqual(json.loads(self.active.read_text())['meta']['transaction_fixture'], 'external')

    def test_abrupt_updater_process_exit_never_replaces_active_file(self):
        # Real child exit, after the simulated collector writes its output.
        code = '''
import json, os, pathlib, sys
sys.dont_write_bytecode = True
import update_data as updater
active = pathlib.Path(sys.argv[1])
updater.DATA_PATH = str(active)
updater.BACKUP_DIR = str(active.parent / 'crash-backups')
def interrupted(args, **kwargs):
    output = pathlib.Path(args[args.index('--output')+1]) if '--output' in args else active
    output.write_text(json.dumps({'meta': {'transaction_fixture': 'invalid'}}))
    os._exit(73)
updater.run_command = interrupted
updater.update_data()
'''
        child = subprocess.run([sys.executable, '-B', '-c', code, str(self.active)],
                               cwd=REPO, capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertEqual(self.active.read_bytes(), self.old_bytes)

    def test_external_change_after_successful_api_validation_is_not_overwritten(self):
        candidate = copy.deepcopy(self.original)
        external = copy.deepcopy(self.original)
        external['meta']['transaction_fixture'] = 'external'
        with patch.object(updater, 'run_command', side_effect=self.collector(candidate)), \
                patch.object(updater, 'run_api_tests', side_effect=lambda *_: self.active.write_text(json.dumps(external))):
            with self.assertRaisesRegex(RuntimeError, '其他程序修改'):
                updater.update_data()
        self.assertEqual(json.loads(self.active.read_text())['meta']['transaction_fixture'], 'external')

    def test_concurrent_update_is_rejected_without_collection(self):
        with updater.dataset_lock(), patch.object(updater, 'run_command') as collect:
            with self.assertRaisesRegex(RuntimeError, '正在执行'):
                updater.update_data()
        collect.assert_not_called()
        self.assertEqual(self.active.read_bytes(), self.old_bytes)

    def test_backup_restores_exact_bytes_with_atomic_file_switch(self):
        backup = updater.backup_dataset()
        self.active.write_text('{}')
        updater.restore_backup(backup)
        self.assertEqual(self.active.read_bytes(), self.old_bytes)

    def test_prefetch_output_option_keeps_real_dataset_unchanged(self):
        import pre_fetch
        formal = (REPO / 'data/botlane_dataset.json').read_bytes()
        candidate = self.directory / 'collector-candidate.json'
        db = self.original
        with patch.object(sys, 'argv', ['pre_fetch.py', '--output', str(candidate)]), \
                patch.object(pre_fetch, 'fetch_tiers', return_value=(db['tiers'], db['hero_stats'], [], {})), \
                patch.object(pre_fetch, 'fetch_matrix', return_value=(db['synergy'], db['counter'], db['synergy_by_lane'], db['counter_by_lane'], [], 1)):
            pre_fetch.main()
        self.assertTrue(candidate.is_file())
        self.assertEqual(json.loads(candidate.read_text())['tiers'], db['tiers'])
        self.assertEqual((REPO / 'data/botlane_dataset.json').read_bytes(), formal)


if __name__ == '__main__':
    unittest.main()
