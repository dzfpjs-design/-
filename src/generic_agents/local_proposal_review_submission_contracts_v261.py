"""A local review copy is neither a new vendor answer nor execution authority."""
from uuid import UUID,NAMESPACE_URL,uuid5
from pydantic import Field,StrictBool,StrictInt,StrictStr,field_validator
from .contracts import Contract

SCHEMA='local-proposal-review-submission-v261'
LOOKUP_SCHEMA='local-proposal-review-submission-lookup-v261'
RECEIPT_SCHEMA='local-proposal-review-submission-receipt-v261'
REJECTION_SCHEMA='local-proposal-review-rejection-receipt-v261'
ACTION='setup.local_review.submitted_v261'
REJECTED_ACTION='setup.local_review.submission_rejected_v261'

class SubmitInput(Contract):
    version:StrictInt=Field(ge=1)
    request_id:StrictStr
    value_hash:StrictStr=Field(pattern=r'^[0-9a-f]{64}$')
    revision:StrictInt=Field(ge=1)
    operations:list[dict]=Field(min_length=1,max_length=100)
    confirm_local_changes:StrictBool
    @field_validator('request_id')
    @classmethod
    def nonce(cls,value):
        if str(UUID(value))!=value:raise ValueError('申请编号须为标准UUID')
        return value
    @field_validator('confirm_local_changes')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('请明确另存本地核对建议')
        return value

def submission_id(workspace_id,owner_id,draft_id,version):
    return str(uuid5(NAMESPACE_URL,f'generic-agents/local-review-submission-v261/{workspace_id}/{owner_id}/{draft_id}/{version}'))

def attempt_id(workspace_id,owner_id,request_id):
    return str(uuid5(NAMESPACE_URL,f'generic-agents/local-review-attempt-v261/{workspace_id}/{owner_id}/{request_id}'))

def analysis_id(identity):
    return str(uuid5(NAMESPACE_URL,'generic-agents/local-review-analysis-v261/'+identity))

RECEIPT_KEYS=frozenset({'schema','submission_id','workspace_id','owner_id','draft_id','draft_version',
    'request_id','body_hash','value_hash','source_id','source_raw_hash','source_request_hash',
    'configuration_revision','configuration_hash','operations_hash','policy_hash',
    'derived_analysis_id','derived_result_hash','receipt_hash'})
REJECTION_KEYS=frozenset({'schema','submission_id','workspace_id','owner_id','draft_id','draft_version',
    'request_id','body_hash','code','message','saved_submission_id','receipt_hash'})
