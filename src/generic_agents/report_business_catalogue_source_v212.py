"""Read an explicitly selected partial suggestion from one failed paid answer.

This does not settle, repair, recover a journal, transition a consumer, dispatch
a Job, or reinterpret the original failure as a successful model answer.
"""
import copy
import hashlib

from sqlalchemy import select

from . import models as m
from . import report_business_catalogue_native_v211 as native
from . import report_design_saved_source_v174 as saved_source
from . import report_saved_map_design_consumer_v176 as consumer
from .report_saved_map_design_preparation_v176 import _source as checked_source


MODE = 'failed_catalogue_partial_v212'
VIEW = 'report-business-catalogue-partial-view-v212'


def load_failed_partial_source(db, p, consumer_id):
    """Fail closed on incomplete transport/source/receipt or any different call."""
    from . import report_business_catalogue_partial_v212 as partial

    consumer._current_reader(db, p)
    rows, _, calls = consumer._inventory(db, p)
    row = consumer._owned(rows, p, consumer.identity(consumer_id))
    consumer.need(consumer._family(row.data['preview']['preparation']).id == 'catalogue-v211'
        and row.status == 'failed' and row.data['projection'] is None
        and row.data['manual_value'] is None and row.data['dispatch_started'] is True,
        '只可核对这次已失败目录回答中的部分建议；原失败状态不会改变')
    call = calls.get(row.id)
    job = db.scalar(select(m.Job).where(m.Job.id == row.job_id,
        m.Job.workspace_id == p.workspace_id))
    consumer.need(call is not None and call.id == row.reserved_call_id and call.status == 'failed'
        and job is not None and job.id == row.job_id and job.kind == consumer.JOB
        and job.idempotency_key == 'saved-map-design:' + row.request_id
        and job.data.get('actor_id') == p.user_id and job.data.get('consumer_id') == row.id,
        '原失败调用或后台任务身份无法核对，未采用部分回答')
    actual = consumer.runtime().read_response(db, p, row, recover_saved=False)
    consumer.need(type(actual) is dict and type(actual.get('body')) is bytes,
        '原完整回答或接收记录尚无法核对，未采用部分回答')
    response = row.data['response']
    consumer.need(type(response) is dict
        and consumer.same(actual['binding'], consumer.journal_binding(row))
        and all(consumer.same(actual[key], response[key])
            for key in ('receipt', 'capture', 'descriptor', 'binding'))
        and consumer.same(call.data.get('receipt'), actual['receipt'])
        and response['body_sha256'] == hashlib.sha256(actual['body']).hexdigest())
    capture, receipt = actual['capture'], actual['receipt']
    consumer.need(capture.get('response_complete') is True and capture.get('ready') is True
        and capture.get('content_encoding_safe') is True and capture.get('http_status') == 200
        and capture.get('body_sha256') == response['body_sha256']
        and receipt.get('call_id') == call.id and receipt.get('provider') == 'qwen'
        and receipt.get('model') == native.MODEL
        and receipt.get('capture_hash') == capture.get('capture_hash'))
    inspected = native.inspect_response(actual['body'], capture['http_status'], native.STAGE,
        response_complete=True, http_content_encoding=capture['http_content_encoding'])
    consumer.need(inspected['metadata']['envelope_valid'] is True
        and inspected['metadata']['finish_reason'] == 'stop'
        and all(consumer.same(receipt.get(key), value)
            for key, value in inspected['metadata'].items()),
        '原回答未完整正常终止，不能作为部分建议')
    source = row.data['preview']['source_context']
    current_source = saved_source.load_historical_source_context(db, p, row.producer_id)
    consumer.need(consumer.same(current_source, source),
        '原报告、提取结果或引用资料已有变化；原回答保留，不能据此新建或确认目录')
    snapshot, claims, _ = checked_source(source)
    consumer.need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id)
    prep = row.data['preview']['preparation']
    raw_arguments = inspected['arguments']
    consumer.need(hashlib.sha256(raw_arguments.encode('utf-8')).hexdigest()
        == response['arguments_sha256'])
    projection = partial.build_partial_projection(raw_arguments.encode('utf-8'),
        indexed=prep['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=snapshot['snapshot_hash'], catalog=source['catalog'])
    return {'row': row, 'call': call, 'job': job, 'actual': actual, 'source': source,
        'prep': prep, 'raw_arguments': raw_arguments, 'projection': projection}


def partial_view(db, p, consumer_id):
    value = load_failed_partial_source(db, p, consumer_id)
    row, call = value['row'], value['call']
    return {**consumer.view(row), 'schema': VIEW, 'origin_mode': MODE,
        'call_id': call.id, 'call_status': call.status,
        'catalogue_partial': copy.deepcopy(value['projection']),
        'model_answer_valid': False, 'allocation_complete': False,
        'dispatch_allowed': False, 'human_confirmed': False}
