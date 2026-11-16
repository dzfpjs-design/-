"""Register only the fixed derivative of an actual published business tool.

Publication describes the contract; it never grants a direct call, source
access, cloud consent or spending. The Plan-only execution gate stays separate.
"""
from fastapi import HTTPException

from .contracts import digest
from .services import published
from . import same_goal_advice_contracts_v302 as c


def validate_manifest(db,p,manifest):
    try:
        declared=manifest['specification']['source_business_capability_ref']
        source=published(db,p,declared['id'],'tool')
        actual={'id':source.id,'content_hash':source.content_hash,'release_version':source.release_version}
        if (declared!=actual or source.validated_hash!=source.content_hash
                or digest(source.data)!=source.content_hash):raise ValueError()
        c.validate_manifest(manifest,source_manifest=source.data,source_ref=actual)
        from .business_goal_ready_runtime_v288 import validate_manifest as original_model
        return original_model(db,p,source.data)
    except HTTPException:raise
    except (KeyError,TypeError,ValueError,AttributeError,RecursionError):
        raise HTTPException(422,'同目标建议能力必须来自当前已发布且原件相符的业务能力；未获得调用或外发权限') from None
