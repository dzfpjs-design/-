"""Statistical call-stack sample: synthetic test only; no locals or SQL values."""
import builtins, collections, json, os, sys, tempfile, threading, time
from pathlib import Path
import pytest

MARKERS={'RESTORED_PRIVATE_CHECKPOINT','PUBLISHED_SYNTHETIC_ADVICE','AUTHORIZED_SYNTHETIC_POLICY',
 'PREPARATION_CREATED','DOCUMENT_INTENT_REGISTERED','DOCUMENT_PREPARED','FRAME_BUILT_AND_CONSENTS_SAVED','PREVIEW_SAVED'}

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
 if item.name!='test_real_mature_approved_source_to_durable_v2_preview':
  yield; return
 start=time.monotonic(); phase=['BEGIN']; stages=[('BEGIN',0.0)]; counts=collections.Counter(); leafs=collections.Counter(); totals=collections.Counter(); done=threading.Event(); orig=builtins.print
 def monitored(*args,**kwargs):
  if args and isinstance(args[0],str) and args[0] in MARKERS:
   phase[0]=args[0]; stages.append((args[0],round(time.monotonic()-start,3)))
  return orig(*args,**kwargs)
 def key(frame):
  code=frame.f_code; path=code.co_filename
  if '/generic_agents/' in path: return path.split('/generic_agents/',1)[1]+':'+code.co_name
  if '/site-packages/sqlalchemy/' in path: return 'sqlalchemy/'+path.split('/site-packages/sqlalchemy/',1)[1]+':'+code.co_name
  if '/json/' in path or path.endswith('/json/__init__.py'): return 'stdlib-json/'+os.path.basename(path)+':'+code.co_name
  return None
 def sample():
  me=threading.get_ident()
  while not done.wait(.05):
   current=phase[0]
   for thread,frame in sys._current_frames().items():
    if thread==me:continue
    values=[]
    while frame:
     entry=key(frame)
     if entry: values.append(entry)
     frame=frame.f_back
    if values:
     leafs[(current,values[0])]+=1
     for value in set(values):counts[(current,value)]+=1
     totals[current]+=1
 from sqlalchemy import event
 from generic_agents.db import engine
 eng=engine(); sql_counts=collections.Counter(); sql_seconds=collections.Counter()
 def before(conn,cursor,statement,parameters,context,executemany):
  context._ga_perf_start=time.monotonic()
 def after(conn,cursor,statement,parameters,context,executemany):
  category=statement.lstrip().split(None,1)[0].upper() if statement.strip() else 'EMPTY'
  if category not in {'SELECT','INSERT','UPDATE','DELETE','SET','LOCK','WITH'}: category='OTHER'
  sql_counts[(phase[0],category)]+=1
  sql_seconds[(phase[0],category)]+=time.monotonic()-context._ga_perf_start
 event.listen(eng,'before_cursor_execute',before); event.listen(eng,'after_cursor_execute',after)
 builtins.print=monitored
 thread=threading.Thread(target=sample,daemon=True);thread.start()
 try: yield
 finally:
  done.set();thread.join(timeout=2);builtins.print=orig
  event.remove(eng,'before_cursor_execute',before); event.remove(eng,'after_cursor_execute',after)
  stages.append(('END',round(time.monotonic()-start,3)))
  result={'stages':stages,'samples':dict(totals),'inclusive':{},'leaf':{}, 'sql_counts':[[a,b,n] for (a,b),n in sql_counts.items()], 'sql_seconds':[[a,b,round(n,3)] for (a,b),n in sql_seconds.items()]}
  for name in set(totals):
   result['inclusive'][name]=[(v,n) for (p,v),n in counts.most_common() if p==name][:25]
   result['leaf'][name]=[(v,n) for (p,v),n in leafs.most_common() if p==name][:25]
  target=Path(os.environ['GA_SYNTHETIC_PROFILE_OUTPUT'])
  if not target.is_absolute() or target.parent.resolve()!=Path(tempfile.gettempdir()).resolve() or not target.name.startswith('ga-synthetic-advice-profile-'):
   raise RuntimeError('profile output requires a new synthetic tempfile path')
  fd=os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
  with os.fdopen(fd,'w') as out:json.dump(result,out,ensure_ascii=False)
