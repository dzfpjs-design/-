"""Fixed local query tool; published workspace registration grants no source read."""
from fastapi import HTTPException
from sqlalchemy import select

from .contracts import Manifest, digest
from .models import Capability
from .services import published
from . import multimodal_question_contracts_v262 as c

HANDLER = 'multimodal_question_v262'
NAMESPACE = 'platform_multimodal_questions'
NAME = 'multimodal_registered_results_query'


def manifest():
    return Manifest.model_validate({'contract_version': '1.0', 'kind': 'tool', 'namespace': NAMESPACE,
        'name': NAME, 'version': '1.0.0', 'owner': 'platform',
        'description': '只读核对已登记像素和环境声音成果；不采集、不改对象、不判断现场真值',
        'input_schema': c.tool_input_schema(), 'output_schema': c.answer_schema(), 'allowed_agent_roles': ['data'],
        'execution': {'adapter': 'builtin', 'handler': HANDLER, 'side_effect': False, 'query_tool': True,
            'input_bindings': {'query_id': 'request_key'}}, 'data_policy': {'cloud_allowed': False},
        'budget': {'max_attempts': 1, 'max_concurrency': 4}, 'dependencies': [],
        'specification': {'schema': 'multimodal-question-tool-v262', 'method': 'multimodal_registered_results_query',
            'max_source_records': 1000, 'max_combined_source_bytes': 16 * 1024 * 1024,
            'facets': ['pixel', 'task_sound'], 'source_authority': 'saved_owner_query_and_independent_source_audit'}})


def applies(value):
    return (value.get('namespace') == NAMESPACE or value.get('execution', {}).get('handler') == HANDLER
        or value.get('specification', {}).get('schema') == 'multimodal-question-tool-v262')


def validate_manifest(value):
    if digest(value) != digest(manifest().model_dump(mode='json')):
        raise HTTPException(422, '统一成果问答只能使用固定本机只读合同，不能扩展来源、角色或处理器')


def catalog(db, p, *, live=True, pin=None):
    """Never register/validate/publish from GET or operator query execution."""
    rows = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == NAMESPACE, Capability.name == NAME, Capability.release_version == '1.0.0').limit(2)))
    if not rows: return None
    if len(rows) != 1: raise HTTPException(409, '统一成果问答工具登记无法唯一核对')
    row = published(db, p, rows[0].id, 'tool') if live else rows[0]
    validate_manifest(row.data)
    actual = {'id': row.id, 'hash': row.content_hash, 'version': row.release_version}
    if row.kind != 'tool' or digest(row.data) != row.content_hash or row.validated_hash != row.content_hash or (pin is not None and actual != pin):
        raise HTTPException(409, '原统一成果问答工具版本或固定内容无法核对')
    return row
