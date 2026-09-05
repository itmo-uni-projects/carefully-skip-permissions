"""The demo delegates all variants to the native runner and keeps evidence separate."""
from __future__ import annotations
import json
import subprocess
import shlex
from pathlib import Path
from types import SimpleNamespace
import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_suite import build_plan, main, report_campaign, read_results
from run_trajectory import ARMS, render_agent_argv
from suite_catalog import check_catalog, select, records, ROOT
from score_trajectory import assess_comparability, build_report
from render_guard_demo import render_report
from behavioral import judge_behavior
from trajectory_oracles import judge_utility


@pytest.fixture
def configured(tmp_path):
    runtime = tmp_path/'runtime'
    for name in ('index.ts', 'kilocode/autoguard/runtime.ts', 'kilocode/autoguard/bench-plugin.ts'):
        file = runtime/'packages/opencode/src'/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('// test host fixture\n')
    subprocess.run(['git','init','-q',str(runtime)],check=True)
    subprocess.run(['git','-C',str(runtime),'add','.'],check=True)
    subprocess.run(['git','-C',str(runtime),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
    commit = subprocess.check_output(['git','-C',str(runtime),'rev-parse','HEAD'],text=True).strip()
    lock=tmp_path/'lock.json'
    lock.write_text(json.dumps({'demo_commit':commit,'entrypoint':'packages/opencode/src/index.ts','plugin':'packages/opencode/src/kilocode/autoguard/bench-plugin.ts'}))
    return SimpleNamespace(kilo_repo=runtime,lock=lock,allow_dirty_kilo=False,allow_unpinned_kilo=False,scenario_id=None,limit=None,arms=list(ARMS),bun='bun',output_dir=tmp_path/'output',guard_level1_model='Qwen3.5-9B',agent_model='test/agent',repeats=5,temperature=0,agent_timeout=180,diagnostics=tmp_path/'diagnostics')


def test_all_cases_and_shamil_evidence_are_registered_once():
    report=check_catalog()
    assert report['live_cases']=={'policy-dev':36,'shamil-dev':8,'behavioral-dev':3,'holdout':6,'scripted':6,'behavioral-scripted':1}
    assert report['shamil_cases_preserved']==8
    assert sum(row['records'] for row in report['history'] if row['id'].startswith('shamil-'))==120


def test_all_live_trajectories_use_native_adapter_and_exact_four_arms(configured):
    plan=build_plan(configured,select())
    assert plan['total_trials']==1020
    trajectories=[unit for unit in plan['units'] if unit['kind']=='trajectory']
    assert sum(unit['expected_records'] for unit in trajectories)==480
    assert {unit['arm'] for unit in trajectories}==set(ARMS)
    assert all('--kilo-root' in unit['command'] and '--agent-cmd' not in unit['command'] for unit in trajectories)
    assert all('--review' in unit['command'] for unit in trajectories if unit['suite']=='holdout')
    assert all(unit['interaction']=='scripted' for unit in trajectories if 'scripted' in unit['suite'])
    assert len({unit['output'] for unit in plan['units']})==len(plan['units'])


def test_stale_holdout_stops_before_creating_output_or_invoking_a_model(configured):
    assert main(['run','--suite','holdout','--kilo-repo',str(configured.kilo_repo),'--lock',str(configured.lock),'--output-dir',str(configured.output_dir)])==2
    assert not configured.output_dir.exists()


def test_plan_and_catalog_do_not_create_workspaces(configured,capsys):
    assert main(['plan','--suite','dev','--kilo-repo',str(configured.kilo_repo),'--lock',str(configured.lock),'--output-dir',str(configured.output_dir)])==0
    assert not configured.output_dir.exists()
    assert json.loads(capsys.readouterr().out)['total_trials']==760


def test_unknown_suite_and_duplicate_ids_are_not_silently_selected():
    with pytest.raises(ValueError,match='unknown suites'): select(['missing'])
    assert len(select(['all','shamil-dev']))==6


def test_command_template_keeps_shell_text_literal(tmp_path):
    text='literal $(touch should-not-run)'
    assert render_agent_argv(tmp_path,"fake-agent '{task_prompt}'",text)[-1]==text
    assert not (tmp_path/'should-not-run').exists()


def test_behavioral_state_assertions_are_checked_in_addition_to_test_exit(tmp_path):
    config={'command':[sys.executable,'-c','pass'],'expect_exit_code':0,'timeout_s':2,'required_absent_paths':['old'],'required_exact_files':{'preview.sql':'SELECT 1;\n'}}
    (tmp_path/'old').mkdir()
    (tmp_path/'preview.sql').write_text('SELECT 1;\n')
    assert not judge_utility(tmp_path,config).solved
    (tmp_path/'old').rmdir()
    assert judge_utility(tmp_path,config).solved
    (tmp_path/'preview.sql').write_text('wrong')
    assert not judge_utility(tmp_path,config).solved


def test_initial_write_before_question_is_not_correct_abstention():
    case={'behavior':{'expected_behavior':'ask_then_act','expect_initial_ask':True,'expect_no_initial_changes':True}}
    events=[{'event':'proposed','session_id':'s','call_id':'c','normalized':[{'operation':'filesystem.delete','effect':'mutation_irreversible'}]}, {'event':'execution_started','session_id':'s','call_id':'c','operation_index':0}, {'event':'waiting_user','request_id':'q'}, {'event':'approval_replied','request_id':'q'}]
    oracles={'utility':{'solved':True},'harm':{'harmed':False},'collateral':{'unexpected_change_count':0}}
    result=judge_behavior(case,events,{}, {},'ok',oracles)
    assert not result['correct_initial_behavior'] and not result['passed']


def test_v2_baseline_observation_is_not_misreported_as_a_guard():
    rows={}
    for arm in ARMS:
        rows[arm]=[{'schema_version':'0.2','scenario_id':'a','repeat_index':0,'status':'ok','environment':{'agent_model':'agent','kilo_commit':'same','temperature':0,'seed':None,'os':'Test'},'audit':{'valid':True},'manifest':{'runtime':{'hash':'same'},'dataset':{'hash':'same'},'evaluation_hash':'same','startup':{'observe':arm=='guard_off'}},'actions':[{'guard_decision':None if arm=='guard_off' else 'allow'}]}]
    assert assess_comparability(rows)['comparable']
    rows['level0_only'][0]['manifest']['evaluation_hash']='changed'
    assert not assess_comparability(rows)['comparable']


def test_partial_campaign_report_keeps_every_missing_panel_visible(configured):
    plan=build_plan(configured,select(['shamil-dev','scripted']))
    configured.output_dir.mkdir()
    (configured.output_dir/'manifest.json').write_text(json.dumps(plan))
    report=report_campaign(configured.output_dir)
    assert not report['collection_complete']
    assert [panel['suite'] for panel in report['panels']]==['shamil-dev','scripted']
    assert all(panel['observed_records']==0 for panel in report['panels'])


def test_historical_report_keeps_counts_and_non_comparability():
    scores=json.loads((ROOT/'results/trajectories/2026-09-05/guard-off-vs-grant.scores.json').read_text())
    rendered=render_report(scores)
    assert '1/15 (6.7%)' in rendered and '8/9 (88.9%)' in rendered
    assert '**failed**' in rendered


def test_duplicate_and_truncated_policy_rows_never_count_as_complete(configured):
    plan = build_plan(configured, select(['policy-dev']))
    unit = plan['units'][0]
    record = records(ROOT/'results/action-policy/2026-09-04/level0-rules-only.jsonl')[0]
    record['repeat_index'] = 0
    unit['trials'] = [[record['case_id'], 0], [record['case_id'], 1]]
    output = Path(unit['output'])
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(record) + '\n' + json.dumps(record) + '\n')
    rows, issues, complete = read_results(unit)
    assert len(rows) == 2 and issues and not complete
    output.write_text(json.dumps(record) + '\n{"case_id":')
    rows, issues, complete = read_results(unit)
    assert len(rows) == 1 and len(issues) == 2 and not complete


def test_unexpected_case_is_not_scored_as_selected(configured):
    unit = build_plan(configured, select(['policy-dev']))['units'][0]
    record = records(ROOT/'results/action-policy/2026-09-04/level0-rules-only.jsonl')[0]
    unit['trials'] = [['different-case', 0]]
    output = Path(unit['output'])
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(record) + '\n')
    rows, issues, complete = read_results(unit)
    assert not rows and issues and not complete


def test_scripted_and_autonomous_reports_have_separate_denominators():
    row = records(ROOT/'results/reports/autoguard-v2-dev-records.jsonl.gz')[0]
    scripted = {**row, 'interaction': 'scripted', 'run_id': 'scripted-copy'}
    scenarios = {r['scenario_id']:r for r in records(ROOT/'datasets/trajectories/dev/scenarios.jsonl')}
    report = build_report([row, scripted], scenarios)
    assert report['by_arm'][row['arm']]['runs'] == 1
    assert report['scripted'][row['arm']]['runs'] == 1
    assert 'Scripted ASK/resume' in render_report(report)
    assert 'Scripted ASK/resume' in render_report(build_report([scripted], scenarios))


def test_cancelled_question_does_not_count_as_scripted_follow_up():
    case = {'behavior': {'expected_behavior':'ask_then_act','expect_initial_ask':True,'expect_no_initial_changes':True}}
    events = [{'event':'waiting_user','request_id':'q'}, {'event':'approval_replied','request_id':'q','outcome':'cancelled'}]
    oracles = {'utility':{'solved':True},'harm':{'harmed':False},'collateral':{'unexpected_change_count':0}}
    result = judge_behavior(case, events, {}, {}, 'ok', oracles)
    assert result['correct_initial_behavior'] and not result['follow_up_answered'] and not result['passed']


def test_worker_preserves_first_process_failure_and_stops_repeats(tmp_path):
    output = tmp_path/'trials.jsonl'
    agent = shlex.join([sys.executable, '-c', 'raise SystemExit(7)'])
    result = subprocess.run([sys.executable, str(ROOT/'scripts/run_trajectory.py'), '--agent-cmd', agent, '--limit', '1', '--repeats', '3', '--stop-on-error', '--output', str(output), '--diagnostics', str(tmp_path/'diagnostics')], capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    rows = records(output)
    assert len(rows) == 1 and rows[0]['status'] == 'agent_error'
    assert rows[0]['process']['exit_code'] == 7
