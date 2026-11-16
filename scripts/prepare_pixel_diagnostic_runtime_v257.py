"""Install a separate pinned processor or inspect an existing one, never overwrite."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PINS={'numpy':'2.5.3','pydantic':'2.13.5','Pillow':'12.3.0'}


def inspect(python):
    code="import numpy,pydantic,PIL,json;print(json.dumps({'numpy':numpy.__version__,'pydantic':pydantic.__version__,'Pillow':PIL.__version__}))"
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    result=subprocess.run([str(python),'-c',code],env=env,capture_output=True,text=True,timeout=30)
    if result.returncode or json.loads(result.stdout)!=PINS:
        raise RuntimeError('Local diagnostic runtime does not load the pinned packages')
    return json.loads(result.stdout)


def main():
    p=argparse.ArgumentParser();p.add_argument('--target',type=Path,default=ROOT/'.runtime/venvs/pixel-diagnostic-v257')
    p.add_argument('--check-existing',action='store_true');args=p.parse_args();target=args.target.absolute();python=target/'bin/python'
    if args.check_existing:
        inspect(python);print('Pinned diagnostic runtime imports verified; no files changed');return
    if target.exists():raise RuntimeError('Refusing to overwrite an existing environment; use --check-existing')
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    state_path=target.parent/(target.name+'-preparation.json');log_path=target.parent/(target.name+'-preparation.log')
    if state_path.exists() or log_path.exists():raise RuntimeError('Preparation evidence already exists; inspect it before retrying')
    state={'phase':'creating','target':str(target),'pins':PINS,'existing_environments_modified':False}
    def save():
        temporary=state_path.with_suffix('.tmp');temporary.write_text(json.dumps(state));temporary.chmod(0o600);temporary.replace(state_path)
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PIP_INDEX_URL='https://pypi.org/simple',PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_NO_INPUT='1')
    save()
    try:
        with log_path.open('wb') as log:
            log_path.chmod(0o600)
            def run(command):
                result=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,timeout=900)
                if result.returncode:raise RuntimeError('Preparation failed; inspect the protected preparation log')
            run([sys.executable,'-m','venv',str(target)])
            state['phase']='installing';save()
            run([str(python),'-m','pip','install','--requirement',str(Path(__file__).with_name('pixel_diagnostic_requirements_v257.txt'))])
        state['phase']='checking_imports';save();actual=inspect(python)
        state.update(phase='ready',actual_imports=actual,python=str(python));save()
    except Exception:
        state['phase']='failed';save();raise
    print('Separate pinned diagnostic runtime prepared; configure GA_PIXEL_DIAGNOSTIC_PYTHON to its bin/python')


if __name__=='__main__':main()
