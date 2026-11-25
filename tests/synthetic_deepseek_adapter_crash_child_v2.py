"""Exit after synthetic first-answer commit, before independent settlement."""
import base64,copy,json,os
from datetime import timedelta
from pathlib import Path
from sqlalchemy import func,select
from generic_agents.security import Principal
from generic_agents import same_goal_advice_start_safety_candidate_v2 as safety
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
from generic_agents import same_goal_advice_deepseek_adapter_candidate_v2 as adapter
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as reconcile


def main():
    payload=json.loads(Path(os.environ['GA_SYNTHETIC_CRASH_CASE']).read_text())
    frame=payload['frame'];resolved=payload['resolved']
    resolved['content']=base64.b64decode(resolved['content'],validate=True)
    safety._lock_approved_sources=lambda *_:None
    safety.check_freshness=lambda *_:None
    permits._source_expiry=lambda db,*_:db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5)
    preview._verify_evaluation=lambda *_:None
    preview.frame_builder.build_current=lambda *_,**__:copy.deepcopy(frame)
    document.sources.resolve_documents=lambda *_:copy.deepcopy(resolved)
    reconcile.reconcile=lambda *_:os._exit(76)
    principal=Principal(**payload['principal'])
    adapter.execute_synthetic_test_only(principal,*payload['arguments'])
    os._exit(91)


if __name__=='__main__':main()
