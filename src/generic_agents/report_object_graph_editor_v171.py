"""Read-only complete new-object graph evidence, without granting execution."""
import copy
from uuid import UUID
from fastapi import APIRouter
from .contracts import digest
from . import report_full_semantic_graph_editor as old
from . import report_full_semantic_graph_editor_v4 as events
from .report_object_graph_binding_v171 import required_owner_fields

router = APIRouter()
SCHEMA = 'report-object-graph-editor-context-v171'
PREFIX = '/full-semantic-object-graphs-v171/{identity}'


@router.get(PREFIX + '/editor-context')
def get_context(identity: UUID, p: old.review.pipeline.ReadP, db: old.review.pipeline.DB):
    configuration, record, heads, manifest, _, _ = old.review.context(db, p, identity)
    design = record['design']; active = str(identity) in heads
    old._need(design.get('schema') == 'report-object-semantic-design-v169'
        and manifest.get('schema') == 'report-object-source-manifest-v170',
        '此入口只接受已确认的新对象完整设计及其独立来源清单')
    owners = required_owner_fields(design)
    choices, actor_available = events._source_choices(db, p)
    available = active and actor_available and any(row['available'] for row in choices)
    reason = (None if available else events.INACTIVE if not active
        else events.NO_PERMISSION if not actor_available else events.NO_SOURCES)
    result = {'schema': SCHEMA, 'handoff_id': str(identity), 'active': active,
        'design': copy.deepcopy(design), 'manifest': copy.deepcopy(manifest),
        'configuration': {'revision': configuration.version, 'hash': digest(configuration.data)},
        'owners': copy.deepcopy(owners), 'proofs': old._proofs(db, p, identity, active, manifest),
        'schema_origins': old._schema_origins(db, p, identity, configuration.data, owners,
            origin_schema='report-object-schema-origin-v170'),
        'adoptions': old._adoptions(db, p, identity, configuration, design, active),
        'event_wait': {'available': available, 'reason': reason,
            'policy': copy.deepcopy(events.sources.POLICY), 'sources': choices}}
    return old.review.bounded(result)
