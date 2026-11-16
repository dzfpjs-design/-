"""Same fixed Mandarin inputs/labels as M37; local native Qwen candidate only."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from benchmark_local_asr import digest,file_hash,save,load_checkpoint
from benchmark_mandarin_asr import validate_records,report,CONFIGURATION
from generic_agents.asr_character_quality import character_score,CHARACTER_PROTOCOL
from generic_agents.local_qwen_audio import call,ready,RECIPE,RECIPE_HASH,validate
from generic_agents.local_specialists import node_lock

SOURCE=ROOT/'docs/evidence/mandarin-asr-quality-v06'
MANIFEST_SHA='6b7017e781e218d51fba256e94a6055c7ede32215efabe8fc2fb62f54820728a'


def fingerprint(manifest):
    return {'manifest_sha256':digest(manifest),'model_pack':RECIPE['pack_id'],'model_pack_version':RECIPE['version'],
        'recipe_sha256':RECIPE_HASH,'configuration':CONFIGURATION,'protocol':CHARACTER_PROTOCOL,
        'model_runner_sha256':file_hash(ROOT/'src/generic_agents/local_qwen_audio_runner.py'),
        'adapter_sha256':file_hash(ROOT/'src/generic_agents/local_qwen_audio.py'),
        'decoder_sha256':RECIPE['decoder_sha256'],
        'metric_support_sha256':file_hash(ROOT/'src/generic_agents/asr_quality.py'),
        'character_metric_sha256':file_hash(ROOT/'src/generic_agents/asr_character_quality.py'),
        'checkpoint_support_sha256':file_hash(ROOT/'scripts/benchmark_local_asr.py'),
        'benchmark_support_sha256':file_hash(ROOT/'scripts/benchmark_mandarin_asr.py'),
        'benchmark_code_sha256':file_hash(Path(__file__))}


def run(out,max_new,retry_failed):
    manifest=json.loads((SOURCE/'manifest.json').read_text())
    if digest(manifest)!=MANIFEST_SHA or manifest['configuration']!=CONFIGURATION or manifest['protocol']!=CHARACTER_PROTOCOL:
        raise ValueError('Original M37 dataset, labels or scoring protocol changed')
    original=json.loads((SOURCE/'report.json').read_text())
    proof=json.loads((SOURCE/'verification.json').read_text())
    if file_hash(SOURCE/'report.json')!=proof['report_sha256'] or file_hash(SOURCE/'checkpoint.json')!=proof['checkpoint_sha256'] or not original['complete']:
        raise ValueError('Original baseline is not complete or differs from verified record')
    for sample in manifest['samples']:
        path=SOURCE/sample['path']
        if path.is_symlink() or path.resolve().parent!=(SOURCE/'samples').resolve() or file_hash(path)!=sample['sha256']:
            raise ValueError('Original corpus recording changed')
    checkpoint=load_checkpoint(out,fingerprint(manifest));validate_records(manifest,checkpoint);ready()
    for row in checkpoint['records']:
        if row['status']=='succeeded':validate(row['result'],(SOURCE/next(s['path'] for s in manifest['samples'] if s['id']==row['id'])).read_bytes(),CONFIGURATION)
    save(out/'manifest.json',manifest);processed=0
    for sample in manifest['samples']:
        old=next((r for r in checkpoint['records'] if r['id']==sample['id']),None)
        if old and (old['status']=='succeeded' or old['status']=='failed' and not retry_failed):continue
        if max_new is not None and processed>=max_new:break
        attempts=[] if old is None else old.get('previous_attempts',[])+[{k:v for k,v in old.items() if k!='previous_attempts'}]
        if old:checkpoint['records'].remove(old)
        row={'id':sample['id'],'input_sha256':sample['sha256'],'status':'running','started_unix':time.time(),'previous_attempts':attempts}
        checkpoint['records'].append(row);save(out/'checkpoint.json',checkpoint);start=time.monotonic()
        try:
            with node_lock():result=call((SOURCE/sample['path']).read_bytes(),CONFIGURATION)
            if result['audio']['sample_rate']!=16000 or abs(result['audio']['duration_seconds']-sample['duration_seconds'])>1e-6:
                raise ValueError('Decoded duration differs from original author label')
            row.update(status='succeeded',result=result,result_sha256=digest(result),score=character_score(sample['reference_text'],result['transcription']['text']))
        except Exception as error:row.update(status='failed',error=str(error)[:800])
        finally:
            row['wall_seconds']=time.monotonic()-start;save(out/'checkpoint.json',checkpoint);save(out/'report.json',report(manifest,checkpoint))
        processed+=1
        print(json.dumps({'id':row['id'],'status':row['status'],'completed':sum(r['status']=='succeeded' for r in checkpoint['records']),'wall_seconds':round(row['wall_seconds'],3)}),flush=True)
    validate_records(manifest,checkpoint);summary=report(manifest,checkpoint);save(out/'report.json',summary)
    print(json.dumps({k:summary[k] for k in ['planned','succeeded','failed','not_started','complete','cloud_calls']}),flush=True)
    return 1 if summary['failed'] else 0


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--max-new',type=int);p.add_argument('--retry-failed',action='store_true');a=p.parse_args()
    if a.max_new is not None and a.max_new<0:p.error('--max-new must be nonnegative')
    sys.exit(run(a.out,a.max_new,a.retry_failed))
