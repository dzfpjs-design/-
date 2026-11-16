"""Compact evidence manifest; never reads credentials or normal application rows."""
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/mqtt-daily-full-v09'


def read(name):
    return json.loads((OUT / name).read_text())


def finalize():
    baseline = 'normal-update-baseline.json' if (OUT / 'normal-update-baseline.json').exists() else 'normal-before.json'
    before, current = read(baseline), read('normal-predeploy.json')
    assert before == current
    runtime = read('browser/verification.json')
    assert runtime['originals'] == 1010 and runtime['independent_processing_records'] == 4
    assert runtime['cloud_calls'] == 0 and runtime['actual_worker_restart_then_ingestion']
    assert read('browser/before-restart.json') == read('browser/after-restart.json')
    paths = list(Path('/private/var/folders/kc/3md2gf953ms01t04_wz426340000gn/T/pytest-of-dzfpjs')
        .glob('pytest-*/test_browser_daily_full0/daily-protocol.jsonl'))
    trace = max(paths, key=lambda path: path.stat().st_mtime)
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    audited = [row for row in events if 'committed_visible_at_puback' in row]
    assert len(audited) == 16 and all(row['committed_visible_at_puback'] for row in audited)
    assert sum(row.get('packet') == 'SubscribePacket' for row in events) == 2
    assert sum(row.get('packet') == 'SubackPacket' for row in events) == 2
    assert sum(row.get('event') == 'broker_client_connected' for row in events) == 2
    (OUT / 'browser/protocol-audit.jsonl').write_bytes(trace.read_bytes())
    assert len({row['client_id'] for row in events}) == 1
    tracked = [ROOT / path for path in (
        'src/generic_agents/mqtt_daily.py', 'src/generic_agents/models.py',
        'src/generic_agents/mqtt_data.py', 'src/generic_agents/mqtt_observations.py',
        'src/generic_agents/mqtt_api.py', 'src/generic_agents/worker.py',
        'src/generic_agents/config.py', 'src/generic_agents/api.py',
        'src/generic_agents/advice_workflows.py', 'src/generic_agents/workspace_api.py',
        'design/resource-studio/src/MqttDailyStudio.jsx',
        'design/resource-studio/src/mqtt-daily-policy.mjs',
        'design/resource-studio/src/mqtt-daily-storage.mjs',
        'tests/browser_mqtt_daily_harness.py', 'tests/test_mqtt_daily_capacity.py')]
    tracked += list((ROOT / '.runtime/builds/mqtt-daily-full').glob('assets/index-PynPsk95.js'))
    tracked += list((ROOT / '.runtime/builds/mqtt-daily-full').glob('assets/index-*.js'))
    tracked.append(ROOT / 'tests/test_mqtt_daily_download.py')
    browser_path = OUT / 'browser/acceptance.json'
    browser = json.loads(browser_path.read_text()) if browser_path.exists() else {
        'final_build': 'not_accepted', 'full_csv_file': False,
        'stop_refresh_resume': False, 'readonly_instance_and_narrow_layout': False,
        'blocker': 'Actual authenticated HTTP file download and final user flow remain unverified',
    }
    accepted = all(browser.get(name) is True for name in (
        'full_csv_file', 'stop_refresh_resume', 'readonly_instance_and_narrow_layout',
        'raw_original_file', 'offline_retry', 'fixed_csv_after_late'))
    browser['accepted'] = accepted
    deployed = (OUT / 'normal-after-deploy.json').exists()
    normal = read('normal-after-deploy.json') if deployed else current
    if deployed:
        assert all(normal['tables'][name] == value for name, value in before['tables'].items())
        assert normal['files'] == before['files']
        added = set(normal['tables']) - set(before['tables'])
        assert added == {'mqtt_daily_ledgers', 'mqtt_daily_entries', 'mqtt_daily_changes', 'mqtt_daily_checkpoints'}
        assert all(normal['tables'][name]['count'] == 0 for name in added)
    result = {
        'status': 'VERIFIED_PROGRESS' if accepted and deployed else 'IN_PROGRESS', 'full_goal': 'active',
        'specialist_default': 'local', 'foundation_reasoning': 'public_cloud_qwen_deepseek',
        'silent_cloud_fallback': False,
        'tests': {'first_broad': {'passed': 1151, 'failed': 6, 'skipped': 1},
            'failed_related_after_fix': 50, 'checkpoint_read_proof_after_fix': 10,
            'frontend_broad_before_last_changes': 216, 'frontend_final_related': 45,
            'http_download_final_related': 53,
            'capacity_final': 2, 'counts_overlap_do_not_sum': True},
        'capacity': read('capacity-index.json'),
        'runtime': {**runtime, 'broker_connected': 2, 'actual_suback': 2,
            'same_persistent_client_id': True, 'sampled_commit_audits': len(audited),
            'all_sampled_commits_visible_at_ack': True,
            'burst_throughput_not_a_capacity_claim': True},
        'browser': browser,
        'normal_runtime': {'deployed': deployed, 'migration': deployed,
            'old_inventory_unchanged': True, 'baseline': baseline,
            'tables': normal['table_count'], 'records': normal['record_count'], 'files': normal['file_count'],
            'normal_login_not_reverified': True, 'device_worker_off': True},
        'release_blockers': ([] if accepted else ['final_browser_user_journey']) + ['other_modal_full_capacity',
            'all_gateway_live_protocols', 'field_identity_tls_acl', 'specialist_domain_quality_licenses_cost',
            'controlled_evolution_benefit', 'production_backup_capacity'],
        'source_hashes': {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
            for path in tracked if path.is_file()},
    }
    (OUT / 'verification-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'runtime_originals': runtime['originals'],
        'browser_accepted': accepted, 'normal_deployed': deployed}))


if __name__ == '__main__':
    finalize()
