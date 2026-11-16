"""Trusted offline native scorer. Receives bounded files, never account secrets."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();source=Path(args.input);target=Path(args.out)
    value=None
    try:
        if source.stat().st_size>131072:raise ValueError('input too large')
        query=json.loads(source.read_text())
        # Actual imports, not distribution metadata alone, establish readiness.
        import numpy
        import PIL.Image
        import pydantic
        from . import pixel_diagnostic_protocol_v257 as protocol
        operation=query['operation']
        if operation=='probe':
            result=protocol._current_evaluator()
        elif operation=='freeze':
            result=protocol.frozen_manifest(query['binding'],query['baseline_threshold_text'],query['candidate_threshold_text'])
        elif operation=='evaluate':
            if query['manifest']['evaluator']!=protocol._current_evaluator():
                value={'ok':False,'code':'evaluator_changed','message':'本地计算版本已改变；保留原方案，明确另存新方案后再计算。'}
                result=None
            else:
                root=source.parent
                files=[]
                for name in ['probability.npy','labels.png','valid.png']:
                    path=root/name
                    if not path.is_file() or path.stat().st_size>64_004_096:raise ValueError('invalid input file budget')
                    files.append(path.read_bytes())
                result=protocol.evaluate_frozen(query['manifest'],*files)
        else:raise ValueError('unknown operation')
        if value is None:value={'ok':True,'value':result}
    except (ImportError,ModuleNotFoundError):
        value={'ok':False,'code':'runtime_unavailable','message':'本地诊断计算环境尚未就绪，请管理员检查部署；原方案保留。'}
    except Exception:
        value={'ok':False,'code':'scoring_failed','message':'原始分数、标注或冻结计算版本未通过核对；本次未保存评估，原方案保留。'}
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>131072:raise ValueError('output budget exceeded')
    target.write_bytes(raw);target.chmod(0o600)


if __name__=='__main__':main()
