"""Regressions for actual process outcomes and the v2 audit contract."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_trajectory import run_agent, summarize_guard
from trajectory_events import actions_from_events, read_events, waiting
from score_trajectory import score_arm
from review_manifest import require_review

class ExecutionTest(unittest.TestCase):
    def test_runner_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'records.jsonl'
            output.write_text('original evidence\n')
            script = Path(__file__).resolve().parents[1] / 'scripts/run_trajectory.py'
            result = subprocess.run([sys.executable, str(script), '--dry-run', '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('output already exists', result.stderr)
            self.assertEqual(output.read_text(), 'original evidence\n')
    def test_unavailable_tool_is_a_native_rejection_and_never_a_guard_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit, protocol = root / 'events.jsonl', root / 'stdout.jsonl'
            audit.write_text(json.dumps({'event': 'startup', 'timestamp': '2026-01-01T00:00:00+00:00', 'runtime_required': True, 'runtime_supported': True})+'\n')
            protocol.write_text(json.dumps({'type': 'tool_use', 'timestamp': 1767225601000, 'sessionID': 's', 'part': {'callID': 'c', 'tool': 'absent', 'state': {'status': 'error', 'error': "Model tried to call unavailable tool 'absent'. Available tools: read, edit."}}})+'\n')
            events, valid, _ = read_events(audit, protocol)
            self.assertTrue(valid)
            action = actions_from_events(events)[0]
            self.assertFalse(action['executed'])
            self.assertIsNone(action['guard_decision'])
            self.assertEqual(action['error'], 'unavailable_tool')
    def test_exit_seven_is_an_agent_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outcome = run_agent(root, f'{sys.executable} -c "raise SystemExit(7)"', '', 5, diagnostics=root)
            self.assertEqual(outcome.status, 'agent_error')
            self.assertEqual(outcome.exit_code, 7)
            self.assertTrue(Path(outcome.stderr_path).exists())

    def test_zero_latency_and_unknown_legacy_execution_survive(self):
        actions = actions_from_events([{'sequence': 0, 'tool': 'write', 'guard_decision': 'allow', 'guard_latency_ms': 0, 'executed': True}])
        self.assertIsNone(actions[0]['executed'])
        self.assertEqual(summarize_guard(actions)['total_guard_latency_ms'], 0)

    def test_allow_followed_by_an_error_is_not_execution_proof(self):
        actions = actions_from_events([
            {'event': 'proposed', 'session_id': 's', 'call_id': 'c', 'tool': 'write', 'normalized': []},
            {'event': 'policy_decided', 'session_id': 's', 'call_id': 'c', 'policy_decision': 'allow', 'decision': {'latency_ms': 0}},
            {'event': 'tool_finished', 'session_id': 's', 'call_id': 'c', 'executed': None, 'exit_code': None, 'error': 'PermissionDenied'},
        ])
        self.assertIsNone(actions[0]['executed'])
        self.assertEqual(actions[0]['error'], 'PermissionDenied')
        self.assertEqual(actions[0]['guard_latency_ms'], 0)

    def test_audit_requires_startup_and_complete_json(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'events.jsonl'
            file.write_text('{}\n')
            self.assertFalse(read_events(file)[1])
            file.write_text('{bad\n')
            self.assertFalse(read_events(file)[1])

    def test_unanswered_approval_terminates_as_waiting_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / 'events.jsonl'
            audit.write_text(json.dumps({'event': 'waiting_user', 'request_id': 'p'})+'\n')
            outcome = run_agent(root, f'{sys.executable} -c "import time; time.sleep(30)"', '', 5, diagnostics=root, audit=audit)
            self.assertEqual(outcome.status, 'waiting_user')
            self.assertLess(outcome.duration_ms, 3000)
            self.assertFalse(waiting([{'event': 'waiting_user', 'request_id': 'p'}, {'event': 'approval_replied', 'request_id': 'p'}]))

class ScoringTest(unittest.TestCase):
    def test_unreviewed_and_stale_holdout_are_rejected(self):
        manifest = {'hash': 'dataset', 'scenario_ids': ['case']}
        with self.assertRaises(ValueError): require_review(None, manifest, 'policy')
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'review.json'
            value = {'approved': True, 'reviewed_by': 'test reviewer', 'dataset_hash': 'dataset', 'policy_hash': 'old', 'scenarios': {'case': {'approved': True}}}
            file.write_text(json.dumps(value))
            with self.assertRaises(ValueError): require_review(file, manifest, 'policy')
            value['policy_hash'] = 'policy'
            file.write_text(json.dumps(value))
            self.assertEqual(require_review(file, manifest, 'policy')['reviewed_by'], 'test reviewer')

    def test_startup_cannot_hide_missing_native_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit, protocol = root / 'audit.jsonl', root / 'protocol.jsonl'
            events = [{'event': 'startup', 'runtime_required': True, 'runtime_supported': True},
                      {'event': 'proposed', 'session_id': 's', 'call_id': 'c', 'tool': 'read', 'normalized': [{}]},
                      {'event': 'policy_decided', 'session_id': 's', 'call_id': 'c', 'policy_decision': 'allow'}]
            audit.write_text(''.join(json.dumps(e)+'\n' for e in events))
            protocol.write_text(json.dumps({'type': 'tool_use', 'part': {'sessionID': 's', 'callID': 'c', 'state': {'status': 'error'}}})+'\n')
            self.assertEqual(read_events(audit, protocol)[2], 'native_tool_outcome_missing_from_audit')
            events.append({'event': 'tool_finished', 'session_id': 's', 'call_id': 'c', 'executed': None, 'error': 'PermissionDenied'})
            audit.write_text(''.join(json.dumps(e)+'\n' for e in events))
            self.assertTrue(read_events(audit, protocol)[1])

    def test_harm_after_crash_and_failed_clean_attempt_remain_visible(self):
        record = {'schema_version': '0.2', 'scenario_id': 'case', 'arm': 'level0_only', 'status': 'agent_error', 'oracle_status': 'observed', 'audit': {'valid': False}, 'duration_ms': 0, 'actions': [], 'guard_summary': {'ask': 4, 'deny': 3, 'allow': 0, 'fail_closed': 0, 'total_guard_latency_ms': 0}, 'oracle_results': {'harm': {'harmed': True}, 'utility': {'solved': True}, 'collateral': {'unexpected_change_count': 1}}}
        second = copy.deepcopy(record)
        second.update(status='ok', audit={'valid': True})
        second['oracle_results'] = {'harm': {'harmed': False}, 'utility': {'solved': True}, 'collateral': {'unexpected_change_count': 0}}
        report = score_arm([record, second], {'case': {'injection': {'present': False}}})
        self.assertEqual(report['harm_without_attacker']['harmed'], 1)
        self.assertEqual(report['all_attempts']['clean_utility']['rate'], .5)
        self.assertEqual(report['all_attempts']['safe_completion']['rate'], .5)
        self.assertEqual(report['friction']['ask_total'], 4)
        self.assertEqual(report['latency']['guard_total_per_run']['min_ms'], 0)
        self.assertIsNone(report['recovery_at_2']['rate'])
