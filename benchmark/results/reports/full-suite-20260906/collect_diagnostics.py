import json
import sys
from collections import Counter
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('Usage: collect_diagnostics.py EXPANDED_CAMPAIGN_DIRECTORY (private stdout logs are optional)')
ROOT = Path(sys.argv[1]).resolve()
out = {'panels': []}
for path in sorted(ROOT.glob('*/*.jsonl')):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if not rows or 'oracle_results' not in rows[0]:
        continue
    item = {'suite': path.parent.name, 'arm': path.stem, 'runs': len(rows)}
    item['cases'] = []
    for sid in sorted({r['scenario_id'] for r in rows}):
        rr = [r for r in rows if r['scenario_id'] == sid]
        item['cases'].append({
            'id': sid, 'runs': len(rr),
            'status': dict(Counter(r['status'] for r in rr)),
            'utility': sum(r['oracle_results']['utility']['solved'] for r in rr),
            'harm': sum(r['oracle_results']['harm']['harmed'] for r in rr),
            'behavior_pass': sum(bool(r.get('behavior') and r['behavior']['passed']) for r in rr),
            'empty_final': sum(r['process'].get('protocol_status') == 'empty_final_response' for r in rr),
        })
    actions = [a for r in rows for a in r['actions']]
    item['decisions'] = dict(Counter(str(a.get('guard_decision')) for a in actions))
    item['decision_levels'] = dict(Counter(str(a.get('guard_level')) for a in actions))
    item['execution_evidence'] = dict(Counter(a['execution_evidence'] for a in actions))
    item['unobserved_tools'] = dict(Counter(a['tool'] for a in actions if a['executed'] is None))
    item['exit_codes'] = dict(Counter(str(a.get('exit_code')) for a in actions))
    events = [e for r in rows for e in r['events']]
    decisions = [e for e in events if e['event'] == 'policy_decided']
    item['missing_facts'] = dict(Counter(f for e in decisions for f in (e.get('decision') or {}).get('missing_facts', [])))
    item['l1_results'] = dict(Counter(str(o['level1'].get('verdict')) + ':' + str(o['level1'].get('reason_code')) for e in decisions for o in e.get('operation_results', []) if o.get('level1')))
    item['questions'] = dict(Counter(e['event'] for e in events if any(word in e['event'] for word in ('question', 'answer', 'approval', 'clarification')) or e['event'] == 'waiting_user'))
    item['approval_flows'] = []
    for r in rows:
        for i, event in enumerate(r['events']):
            if event['event'] != 'approval_replied':
                continue
            same = [e for e in r['events'][i + 1:] if e.get('call_id') == event.get('call_id')]
            waiting = [e for e in r['events'][:i] if e['event'] == 'waiting_user' and e.get('request_id') == event.get('request_id') and e.get('call_id') == event.get('call_id')]
            item['approval_flows'].append({
                'run_id': r['run_id'], 'call_id': event.get('call_id'),
                'request_id': event.get('request_id'), 'approved': event.get('approved'),
                'actor': event.get('actor'), 'matched_pending': bool(waiting),
                'rechecked_allow': any(e['event'] == 'policy_decided' and e.get('policy_decision') == 'allow' and e.get('contract_version') == event.get('contract_version') for e in same),
                'execution_after': any(e['event'] == 'execution_started' for e in same),
                'execution_before': any(e['event'] == 'execution_started' and e.get('call_id') == event.get('call_id') for e in r['events'][:i]),
            })
    item['recovery'] = [{'run_id': r['run_id'], 'checkpoints': r['recovery_checkpoints']} for r in rows if r['recovery_checkpoints']]
    item['import_failure_runs'] = []
    item['empty_final_protocol'] = []
    item['private_protocol_logs_available'] = 0
    for r in rows:
        raw = Path(r['process']['stdout_path'])
        if not raw.exists():
            continue
        item['private_protocol_logs_available'] += 1
        stream = []
        for line in raw.read_text().splitlines():
            try:
                stream.append(json.loads(line))
            except ValueError:
                pass
        tools = [e.get('part', {}) for e in stream if e.get('type') == 'tool_use']
        outputs = [str(t.get('state', {}).get('output', '')) for t in tools]
        if any('ModuleNotFoundError' in text for text in outputs):
            item['import_failure_runs'].append(r['run_id'])
        if r['process'].get('protocol_status') == 'empty_final_response':
            finishes = [e.get('part', {}) for e in stream if e.get('type') == 'step_finish']
            last = finishes[-1] if finishes else {}
            item['empty_final_protocol'].append({
                'run_id': r['run_id'], 'final_reason': last.get('reason'),
                'last_step_tokens': last.get('tokens'), 'tool_calls': len(tools),
                'utility': r['oracle_results']['utility']['solved'],
            })
    out['panels'].append(item)
(ROOT / 'diagnostics.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'panels': len(out['panels']), 'records': sum(x['runs'] for x in out['panels']), 'import_failures': sum(len(x['import_failure_runs']) for x in out['panels'])}))
