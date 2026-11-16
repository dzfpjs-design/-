"""Registered deterministic local material tools; never free-form AI reasoning."""
import copy
import re
from .source_query_contracts import need

TOOL = {'id': 'local_material_status', 'version': '1', 'read_only': True, 'cloud_calls': 0}
INTENTS = ('receipt_overview', 'incomplete_capture', 'processing_status', 'time_provenance',
    'evidence_lookup', 'document_conditions', 'unsupported')
CAPTURE_LABELS = {'partial':'仅保存部分视频','captured':'已保存请求片段','failed':'采集失败，未确认完整有效视频',
    'queued':'等待采集','running':'正在采集','connecting':'正在连接','capturing':'正在采集',
    'cancelled':'已停止采集','blocked':'采集受阻'}
PROCESS_LABELS = {'succeeded':'已完成本地处理','completed':'核查已完成','parsed':'正文已解析',
    'queued':'等待处理','running':'正在本地处理','failed':'处理失败','cancelled':'已停止处理','blocked':'处理受阻'}


def intent(question):
    value = question.casefold()
    # Explicit supported vocabulary is a bounded intent hint, not authority or
    # a general natural-language answer. Unrecognized requests stay explicit.
    if value.strip().startswith(('删除','修改','执行','打开','关闭','转动','移动','替我删除','帮我删除')): return 'unsupported'
    if any(word in value for word in ('报告','文档','正文','条款')) and any(word in value for word in ('要求','条件','条款','规定','正文')):
        return 'document_conditions'
    if any(word in value for word in ('现在着火','是否着火','着火了吗','长势如何','树种是什么','物种数量','种群数量','是否有病虫害')) or (
        '现在' in value and any(word in value for word in ('火灾','着火','长势','病虫害'))):
        return 'unsupported'
    if any(word in value for word in ('拍摄时间', '拍摄日期', '现场时间', '哪天拍', '哪年', '时效',
        '今天拍','当天拍','什么时候拍')): return 'time_provenance'
    if '资料' in value and any(word in value for word in ('完整','收齐','清单','概况','状况','人工核对')) and not any(
        word in value for word in ('补采','片段','采集时长')): return 'receipt_overview'
    if any(word in value for word in ('补采','片段','采集时长')): return 'incomplete_capture'
    if any(word in value for word in ('处理', '核查', '质检', '进度', '运行')): return 'processing_status'
    if any(word in value for word in ('证据', '依据', '原件', '出处', '引用')): return 'evidence_lookup'
    if any(word in value for word in ('部分', '不完整', '完整', '秒', '未完成', '为什么失败',
        '没读满','未读满','读满了吗')): return 'incomplete_capture'
    if any(word in value for word in ('报告', '文档', '正文', '要求', '条件', '条款')): return 'document_conditions'
    if any(word in value for word in ('资料', '收到', '接收', '收齐', '有什么', '有哪些', '到数')): return 'receipt_overview'
    return 'unsupported'


def _document_terms(question):
    """A conservative literal filter, not segmentation or semantic matching.

    No inferred synonym or concept is allowed. A specific remaining phrase
    must occur literally in an adopted search keyword or excerpt. Questions
    with only framing words can display selected excerpts, never all-report
    requirements. The user's original question is never changed.
    """
    value=question.casefold()
    quoted=re.findall(r'[“「"]([^”」"]+)[”」"]',value)
    if quoted:return [item.strip() for item in quoted if item.strip()]
    framing=('请问','告诉我','帮我','所选','本次','这份','该份','可行性研究报告','初步设计报告','设计报告',
        '可研报告','报告','文档','正文','搜索','检索','结果',
        '里面','当中','有关','相关','关于','针对','有哪些','有什么','是什么','如何','是否','什么','怎样',
        '条款','规定','条件','要求','内容','说明','节选','资料','中的','里的','的','中','里','对','请')
    for term in framing:value=value.replace(term,' ')
    return [item for item in re.findall(r'[\w\u4e00-\u9fff]+',value) if len(item)>=2]


def _document_matches(record,terms,bundle):
    keywords=[]
    for entry in bundle.get('snapshot',{}).get('entries',[]):
        if entry.get('provider_id')=='document_searches' and entry.get('record',{}).get('key')==record['key']:
            value=entry.get('source_ref',{}).get('keyword')
            if type(value) is str:keywords.append(value.casefold())
    text=(record.get('text') or '').casefold()
    return all(term in text or any(term in keyword for keyword in keywords) for term in terms)


def answer(question, bundle, answered_at):
    need(bundle['schema'] == 'material-source-bundle-v2' and type(bundle['records']) is list
        and bundle['coverage']['expected_sources_verified'] is False)
    selected = intent(question); findings = []; references = {}; records = bundle['records']; relevant = []; capture_records=[]
    document_terms=_document_terms(question) if selected=='document_conditions' else []
    adopted_documents=[item for item in records if item['kind']=='document_excerpt']
    def add(code, label, text, severity, record):
        refs = record['references']; need(type(refs) is list and refs)
        keys = []
        for reference in refs:
            key = reference['key']
            if key in references: need(references[key] == reference)
            references[key] = copy.deepcopy(reference); keys.append(key)
        findings.append({'code': code, 'label': label, 'text': text, 'severity': severity, 'reference_keys': keys})
    def group(code,label,text,items):
        refs={ref['key']:ref for item in items for ref in item['references']}
        if refs:add(code,label,text,'gap',{'references':list(refs.values())})
    number=lambda value:format(value,'g') if type(value) in (int,float) else str(value)
    if selected != 'unsupported':
        for record in records:
            need(record['kind'] in {'capture', 'day_record', 'document_excerpt', 'assessment'})
            if selected == 'document_conditions' and record['kind'] != 'document_excerpt': continue
            if selected == 'time_provenance':
                if record['scene_time_trust'] == 'verified' and record['scene_observed_at']:
                    text = '原来源记录核实的采集时间为'+str(record['scene_observed_at'])+'；仅适用于这份来源。'
                elif record['kind']=='document_excerpt':
                    text = '文档版本登记日期为'+record['batch_day']+'；这不是文档形成日期或现场拍摄时间。'
                else: text = '这份资料的现场拍摄时间未核实；原资料记录日期'+record['batch_day']+'和平台接收时间不能代替拍摄时间。'
                add('scene_time_boundary', record['label'], text, 'gap' if record['scene_time_trust'] != 'verified' else 'info', record)
                continue
            if record['kind'] == 'document_excerpt':
                if selected=='document_conditions' and not _document_matches(record,document_terms,bundle):continue
                if selected in {'document_conditions','evidence_lookup','receipt_overview'}:
                    add('saved_document_excerpt', record['label'], record['text'] or '正式搜索没有可引用的正文片段。', 'info', record)
                continue
            capture = record['capture']; processing = record['processing']; delivery = record['delivery']['status']
            if capture:capture_records.append(record)
            if selected=='incomplete_capture':
                if not capture:continue
                requested,actual=capture['requested_seconds'],capture['actual_seconds']
                short=actual is not None and requested is not None and actual < requested-.1
                if capture['status']=='captured' and not short and actual is not None and requested is not None:continue
            if selected=='processing_status' and not processing and delivery=='not_applicable':continue
            relevant.append(record)
            if selected=='evidence_lookup':
                add('fixed_source',record['label'],'已列出这份资料的固定来源及版本引用；引用不会自动跳到后来版本。',
                    'info' if all(ref['metadata_verified'] for ref in record['references']) else 'error',record)
                continue
            if capture and selected in {'receipt_overview','incomplete_capture'}:
                requested, actual = capture['requested_seconds'], capture['actual_seconds']
                duration = ('请求'+number(requested)+'秒；' if requested is not None else '请求时长未核对；')
                duration += '实际保存'+number(actual)+'秒。' if actual is not None else '没有可核实的实际保存时长。'
                text = CAPTURE_LABELS.get(capture['status'],'采集状态待核对')+'。'+duration
                if capture['reason']:
                    reason=capture['reason']
                    text += ' '+({'capture_deadline':'原读取已达本次等待上限，未取得请求的完整片段。',
                        'source_unavailable':'原来源当时无法读取。'}.get(reason,reason if any('\u4e00'<=char<='\u9fff' for char in reason) else '原回执保存了未完成原因，可查看原资料记录。'))
                add('capture_status', record['label'], text, 'gap' if capture['status'] != 'captured' else 'info', record)
            if processing and selected in {'receipt_overview','processing_status'}:
                count = processing['frames_saved']
                text = PROCESS_LABELS.get(processing['status'],'处理状态待核对')+'。'
                text += '已保存'+number(count)+'张画面。' if count is not None else '本次没有可核实的已保存画面数。'
                add('processing_status', record['label'], text, 'info' if processing['status'] in {'succeeded', 'completed'} else 'gap', record)
            add('independent_receipt', record['label'], {'received':'这份资料已有独立接收证明；不等同于完整补采成功或现场状态已证实。',
                'pending':'仍在等待独立接收，处理完成不能替代接收。', 'blocked':'这份资料的独立接收受阻，保留原回执。',
                'unknown':'未核实独立接收，不能按已收到统计。', 'not_applicable':'此来源不适用媒体独立收件。'}[delivery],
                'info' if delivery in {'received', 'not_applicable'} else 'gap', record)
        if selected=='incomplete_capture' and not relevant and capture_records:
            group('no_incomplete_marker','所选片段核对范围','已核所选记录中未发现未读满标记；预期来源清单未知，未全扫时也不能保证全量完整。',capture_records)
        if selected not in {'unsupported','document_conditions','time_provenance'}:
            group('scene_time_boundary','仍未核实的拍摄时间','批次日和接收时间不能认证拍摄时间；这些来源的现场时效仍未核实。',
                [record for record in relevant if record['scene_time_trust']!='verified'])
            group('human_review_boundary','仍需人工核对的边界','仍需核对时效、覆盖与业务用途；技术处理、画面数量或独立收件不等于人工业务结论，本次目录未证明人工核查已全部完成。',relevant)
    limitations = list(bundle['limitations']) + ['这是本地有界资料状况答复，不是云模型推理或对象现场结论。',
        '没有完整应交来源清单，不提供全量收齐率；无记录不代表无异常。']
    if selected == 'unsupported':
        status = 'unsupported'; summary = '当前仅支持所选实例资料的接收、部分采集、处理、时间依据、引用和已完成正文搜索；请明确这些范围。'
    elif not findings:
        status = 'needs_clarification' if selected == 'document_conditions' else 'partial'
        summary = (('所选正式搜索未覆盖问题中的明确字词；请先完成相应正文检索。这不表示报告没有这些内容。' if adopted_documents else
            '本次没有可引用的正式已完成正文搜索；尚未覆盖该报告，不能据此说文档没有内容。') if selected == 'document_conditions' else
            '所选范围没有可核实记录；不能据此认定没有资料、没有异常或设备均已交齐。')
    else:
        partial = bundle['coverage']['truncated'] or not bundle['coverage']['scan_complete'] or any(item['severity'] != 'info' for item in findings) or (selected=='document_conditions' and not document_terms)
        status = 'partial' if partial else 'answered'
        summary = {'receipt_overview':'已按所选范围汇总本批资料；部分采集、处理、独立收件和待核对项分别列出。',
            'incomplete_capture':'已核对所选片段的读取完整性；实际时长与请求时长分开显示，不将独立收件当作读满。',
            'processing_status':'已列出所选资料的处理和独立收件进度。','evidence_lookup':'已列出本次采用的固定来源与版本引用。',
            'document_conditions':'以下为所选正式搜索的原文节选，请按所选搜索范围核对；不等于完整报告条款解析。',
            'time_provenance':'已核对所选资料的时间依据；平台接收与现场采集时间分别说明。'}[selected]
    return {'schema': 'material-answer-v2', 'intent': selected, 'answer_status': status,
        'summary': summary, 'findings': findings, 'references': list(references.values()),
        'coverage': copy.deepcopy(bundle['coverage']), 'limitations': list(dict.fromkeys(limitations)), 'answered_at': answered_at}


# Fixed server registration: adapters cannot import arbitrary module paths or
# replace handlers through request parameters. Future tools need a versioned
# code contract or normal separately authorized capability registration.
REGISTRY = {TOOL['id']: {'version': TOOL['version'], 'handler': answer}}


def execute_registered(tool, question, bundle, answered_at):
    registered = REGISTRY.get(tool['id'])
    need(registered is not None and registered['version'] == tool['version'] and tool == TOOL)
    return registered['handler'](question, bundle, answered_at)
