"""Synthetic subprocess: exit after loopback HTTP, before response capture.

Only invoked by the disposable PG test. All mocked sources are synthetic and
network access is restricted by the parent sandbox.
"""
import base64
import copy
import json
import os
from datetime import timedelta
from pathlib import Path
from sqlalchemy import func,select
from generic_agents.security import Principal
from generic_agents import same_goal_advice_start_safety_candidate_v2 as safety
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits


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
    if os.environ.get('GA_SYNTHETIC_CRASH_BOUNDARY')=='before_http':
        dispatch.httpx.Client=lambda *_,**__:os._exit(74)
    else:
        storage.capture=lambda *_:os._exit(73)
    principal=Principal(**payload['principal'])
    dispatch.execute_once(principal,*payload['arguments'])
    os._exit(91)


if __name__=='__main__':main()
