"""Pure, complete UTF-8 presentation of a caller-verified Task evidence snapshot.

This module grants no source, verification, IAM, dispatch or model permission.
The collector supplies public labels and declared attributes, keeping internal
identities and private source proofs elsewhere. Input order is frozen; mapping
order never changes the output, and no entry is selected, merged or summarized.
"""
import copy
import math
import re
from datetime import datetime

from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'task-evidence-text-snapshot-v199'
MAX_BYTES = 20000
ERROR = '任务证据正文无法完整核对或超过20000字节；原记录保留，未截断'
_KEYS = {'schema', 'task_title', 'simulation_only', 'evidence_status', 'reference_at',
    'required_fields', 'objects', 'gaps', 'issues'}
_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,12})?(?:Z|[+-]\d{2}:\d{2})')
_STATUS = {'sufficient': '原查询资料充足（不等于事实已核实）',
    'conflict': '原查询存在资料冲突，仍须核对',
    'insufficient': '原查询资料不足，仍有缺项或待补充内容'}


class PacketTextError(ValueError):
    """Safe refusal without including private source values or partial text."""


def _need(value):
    if not value:
        raise PacketTextError(ERROR)


def _text(value, *, empty=False, limit=20000):
    _need(type(value) is str and '\x00' not in value and (empty or bool(value.strip())))
    _need(len(value.encode('utf-8', errors='strict')) <= limit)


def _keys(value, expected):
    _need(type(value) is dict and set(value) == expected)


def _list(value, limit=2000):
    _need(type(value) is list and len(value) <= limit)


def _safe(value):
    """Bound recursion before the typed codec, without number coercion."""
    count = 0
    def visit(item, parents=()):
        nonlocal count
        count += 1
        _need(count <= 50000 and len(parents) <= 20)
        kind = type(item)
        if item is None or kind in (bool, int):
            return
        if kind is float:
            _need(math.isfinite(item)); return
        if kind is str:
            _text(item, empty=True); return
        _need(kind in (list, dict) and not any(item is parent for parent in parents))
        if kind is dict:
            for key, child in item.items():
                _text(key, empty=True); visit(child, (*parents, item))
        else:
            for child in item:
                visit(child, (*parents, item))
    visit(value)


def _time(value):
    if value is None:
        return
    _text(value, limit=80)
    _need(_TIME.fullmatch(value) is not None)
    parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None)


def _attributes(value, fields):
    _need(type(value) is dict and set(value) <= fields)


def validate_snapshot(snapshot):
    """Validate the closed public DTO and return an unmodified detached copy.

    candidate/human_confirmed are collector assertions, never established by
    this shape check. It neither repairs legacy numeric loss nor checks files.
    """
    try:
        _safe(snapshot); _keys(snapshot, _KEYS)
        _need(snapshot['schema'] == SCHEMA and type(snapshot['simulation_only']) is bool)
        _text(snapshot['task_title'], limit=2000)
        _need(type(snapshot['evidence_status']) is str and snapshot['evidence_status'] in _STATUS)
        _time(snapshot['reference_at'])
        fields = set()
        _list(snapshot['required_fields'], 1000)
        for row in snapshot['required_fields']:
            _keys(row, {'key', 'label', 'unit'})
            _text(row['key'], limit=200); _text(row['label'], limit=2000)
            _need(row['key'] not in fields)
            if row['unit'] is not None:
                _text(row['unit'], limit=200)
            fields.add(row['key'])
        _list(snapshot['objects'], 1000)
        refs, simulation_found = set(), False
        for index, row in enumerate(snapshot['objects'], 1):
            _keys(row, {'object_ref', 'label', 'archive_attributes', 'observations'})
            _need(row['object_ref'] == '对象' + str(index)); refs.add(row['object_ref'])
            _text(row['label'], limit=2000); _attributes(row['archive_attributes'], fields)
            _list(row['observations'])
            for observation in row['observations']:
                _keys(observation, {'source_label', 'observed_at', 'attributes', 'anomalies',
                    'verification', 'simulation_only'})
                _text(observation['source_label'], limit=2000); _time(observation['observed_at'])
                _attributes(observation['attributes'], fields)
                _need(type(observation['verification']) is str
                    and observation['verification'] in {'candidate', 'human_confirmed'}
                    and type(observation['simulation_only']) is bool)
                simulation_found = simulation_found or observation['simulation_only']
                _list(observation['anomalies'])
                for anomaly in observation['anomalies']:
                    _keys(anomaly, {'field', 'value', 'severity'})
                    _need(type(anomaly['field']) is str and anomaly['field'] in fields)
                    _text(anomaly['severity'], limit=200)
        _need(not simulation_found or snapshot['simulation_only'])
        _list(snapshot['gaps'])
        for gap in snapshot['gaps']:
            _keys(gap, {'object_ref', 'fields', 'status'})
            _need(type(gap['object_ref']) is str and gap['object_ref'] in refs)
            _text(gap['status'], limit=1000); _list(gap['fields'], 1000)
            _need(all(type(key) is str and key in fields for key in gap['fields']))
        _list(snapshot['issues'])
        for issue in snapshot['issues']:
            _text(issue)
        # The same typed JSON spelling is used for nested values in the body.
        canonical_json(snapshot)
        return copy.deepcopy(snapshot)
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        raise PacketTextError(ERROR) from None


def render_packet(snapshot):
    """Return the whole text/plain UTF-8 body, or refuse it without truncation."""
    value = validate_snapshot(snapshot)
    lines, size = [], 0
    def add(line=''):
        nonlocal size
        size += len((line + '\n').encode('utf-8'))
        _need(size <= MAX_BYTES)
        lines.append(line)
    def shown(item):
        return canonical_json(item) + ('（明确为空）' if item is None else '')
    def time_text(item):
        return canonical_json(item) if item is not None else '未提供（时间未知；未补用当前时钟）'
    def unit_text(field):
        return canonical_json(field['unit']) if field['unit'] is not None else '未声明（不推测单位）'
    def attributes(items):
        for field in value['required_fields']:
            present = field['key'] in items
            content = shown(items[field['key']]) if present else '未提供（缺项；未补用默认值）'
            add('  ' + canonical_json(field['label']) + '：' + content + '；单位：' + unit_text(field))
        if not value['required_fields']:
            add('  未声明需要核对的字段；不据此判断资料完整。')
    fields = {row['key']: row for row in value['required_fields']}
    add('本任务证据资料')
    add('任务：' + canonical_json(value['task_title']))
    add('任务标记：' + ('仅工程模拟（SIM），不能作为生产或现场验证。' if value['simulation_only']
        else '本包未标记为SIM；不据此证明现场事实。'))
    add('证据状态：' + _STATUS[value['evidence_status']])
    add('原参考时刻：' + time_text(value['reference_at']))
    add('说明：档案记录、观测候选和异常候选均不等于已核实事实；人工确认标记仅描述该条来源记录，不授予执行许可。')
    add('范围：仅列本包冻结的记录，不表示全部历史或完整样本；未提供的值、时间和单位不会自动补成零或当前值。')
    for row in value['objects']:
        add(); add(row['object_ref'] + '：' + canonical_json(row['label']))
        add('档案记录（与本次观测分列）：'); attributes(row['archive_attributes'])
        for index, observation in enumerate(row['observations'], 1):
            add('观测记录 ' + str(index) + '：' + canonical_json(observation['source_label']))
            add('  原观测时刻：' + time_text(observation['observed_at']))
            add('  核对标记：' + ('观测候选，未经人工确认。' if observation['verification'] == 'candidate'
                else '该条来源标记为人工已确认；不扩大为整体事实或执行授权。'))
            add('  本条来源标记：' + ('SIM模拟观测，非现场实测证明。' if observation['simulation_only']
                else '未标记SIM；仍须核对来源与事实。'))
            attributes(observation['attributes'])
            if observation['anomalies']:
                add('  异常候选（不是已经确认的事件）：')
                for anomaly in observation['anomalies']:
                    field = fields[anomaly['field']]
                    add('    ' + canonical_json(field['label']) + '；值：' + shown(anomaly['value'])
                        + '；单位：' + unit_text(field) + '；原级别：' + canonical_json(anomaly['severity']))
            else:
                add('  本条没有列出异常候选；不等于没有异常。')
        if not row['observations']:
            add('观测记录：本包未提供；不拿档案值冒充新观测。')
    if not value['objects']:
        add(); add('本包未提供对象记录。')
    add(); add('缺项与处理缺口：')
    for index, gap in enumerate(value['gaps'], 1):
        labels = '、'.join(canonical_json(fields[key]['label']) for key in gap['fields']) or '未列出字段'
        add(str(index) + '. ' + gap['object_ref'] + '；字段：' + labels + '；原状态：' + canonical_json(gap['status']))
    if not value['gaps']:
        add('本包未列出缺口；不据此声称所有要求已满足。')
    add(); add('冲突与其他待核问题：')
    for index, issue in enumerate(value['issues'], 1):
        add(str(index) + '. ' + canonical_json(issue))
    if not value['issues']:
        add('本包未列出问题；不据此作业务质量或现场正确性结论。')
    return ('\n'.join(lines) + '\n').encode('utf-8')
