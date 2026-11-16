"""Local projections of sanitized, visible saved records; never field truth."""
import json,math

VERSION='saved-advice-basis-v1'
REVIEW={'uncertain':'尚不能判断','abnormal':'属于业务异常','not_abnormal':'不属于业务异常','insufficient':'尚不能判断','insufficient_evidence':'证据不足'}
STATES={'workflow_completed':'流程已完成','workflow_waiting':'等待当前步骤处理','workflow_queued':'等待本地处理','workflow_running':'正在处理','workflow_blocked':'处理受阻','cancelled':'已取消','failed':'处理失败'}
METRICS={'candidate_masks':'候选区域数','candidate_pixels':'差异候选像素数','unknown_pixels':'未知像素数','saved_frames':'保存画面数','saved_asr_segments':'保存转写段数','vad_intervals':'语音候选区间数','whole_asr_performed':'已进行整片转写'}

def scalar(value):
    return type(value) in {str,int,bool} or type(value) is float and math.isfinite(value)

def build(references):
    # Only complete visible excerpts count. The private source catalog may hold
    # more, but it cannot silently become extra evidence beyond cloud consent.
    usable={};omitted=[]
    for ref in references:
        if ref.get('excerpt_truncated'):omitted.append(ref['id']);continue
        try:value=json.loads(ref['text'])
        except (ValueError,TypeError):omitted.append(ref['id']);continue
        if isinstance(value,dict):usable[ref['id']]=value
    signal=usable.get('signal',{});progress=usable.get('original-task');review=signal.get('business_review') or {}
    original=None
    if progress:
        receipts=progress.get('completed_steps') or []
        original={'reference_id':'original-task','title':progress.get('title','原分析任务'),
            'status_label':STATES.get(progress.get('status'),'请核对原任务状态'),
            'completed':progress.get('status')=='workflow_completed',
            'saved_steps':[{'name':v.get('name','已保存步骤'),'note':str(v.get('note',''))[:400],'note_excerpt':len(str(v.get('note','')))>400} for v in receipts if isinstance(v,dict)],
            'limit':'这些步骤及说明已经保存，不需要再次询问是否已完成；原任务不应被新建议覆盖。'}
    saved_review=None
    if isinstance(review,dict) and review.get('decision') in REVIEW:
        saved_review={'reference_id':'signal','decision':REVIEW[review['decision']],
            'note':str(review.get('note',''))[:400],'note_excerpt':len(str(review.get('note','')))>400,
            'limit':'该判断已在原线索中保存；不需要询问是否存在保存记录，不等于领域结论经过额外验证。'}
    records=[]
    for ref in references:
        value=usable.get(ref['id'])
        if not ref['id'].startswith('record:') or not value:continue
        verified=value.get('integrity_verified') is True
        completed=value.get('status')=='succeeded'
        metrics=[{'label':METRICS[k],'value':v,'origin':'保存的处理结果'} for k,v in (value.get('metrics') or {}).items() if k in METRICS and scalar(v) and v is not None] if verified and completed else []
        fields=[{'label':'原登记字段：'+str(v.get('name','未命名字段'))[:100],'value':v['value'],'origin':'保存的登记字段，未证明为独立测量'} for v in (value.get('reported_fields') or []) if isinstance(v,dict) and 'value' in v and scalar(v['value']) and v.get('name') and len(str(v['value']))<=200] if verified else []
        group=value.get('source_group') if verified and value.get('original_identity_basis')=='verified_original_byte_set' else None
        records.append({'reference_id':ref['id'],'name':value.get('name') or value.get('task_title') or '已保存资料',
            'kind':value.get('kind'),'processing_status':value.get('status'),'source_group':group,'integrity_verified':verified,'values':(metrics+fields)[:6],
            'values_omitted':max(0,len(metrics+fields)-6),
            'measurement_comparable':False,'time_verified':value.get('source_time_verified') is True,
            'limit':'只能核对保存记录；来源关联、人工核查及行业效果不能由文件完整性替代。' if verified else '原记录未通过完整性核对，不把其中的数值显示为事实。'})
    comparisons=[]
    for i,left in enumerate(records):
        for right in records[i+1:]:
            if left['kind']=='business_archive' or right['kind']=='business_archive':continue
            if not any(type(v['value']) in {int,float} for v in left['values']) or not any(type(v['value']) in {int,float} for v in right['values']):continue
            a,b=left['source_group'],right['source_group']
            relation='same_originals' if a and a==b else 'different_originals' if a and b else 'identity_unverified'
            explanation={'same_originals':'原件字节集合相同，不证明采集事件相同或观测独立；不能仅凭处理次数视为新增独立观测，仍需核对方法、参数、范围、时间和审核版本。',
                'different_originals':'原件字节集合不同，不能仅凭候选数量差异认定冲突、变化或现场异常。',
                'identity_unverified':'至少一方原件身份未核实，登记字段与处理结果不能直接当作同一测量。'}[relation]
            comparisons.append({'left_reference_id':left['reference_id'],'right_reference_id':right['reference_id'],
                'relation':relation,'direct_comparison_supported':False,'explanation':explanation})
    return {'version':VERSION,'scope':'本地核对已保存资料，不代表现场状态或模型判断已验证',
        'original_task':original,'saved_business_review':saved_review,'records':records,'comparisons':comparisons,
        'omitted_references':omitted,'limit':'不同原件及测量条件未核实的数值，不能仅凭差值称为冲突；人工判断和处理归集不是新的独立观测。'}

VALIDATION_VERSION='frozen-audit-fields-v1'
def matches_frozen_quote(quote,cloud_input):
    """Strict typed JSON equality with one complete authorized audit object."""
    from .contracts import digest
    audit=cloud_input.get('basis_audit')
    if not audit or audit.get('version')!=VERSION or digest(audit)!=digest(build(cloud_input['references'])):return False
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('duplicate JSON field')
            value[key]=item
        return value
    try:value=json.loads(quote,object_pairs_hook=unique)
    except (ValueError,TypeError):return False
    if not isinstance(value,dict):return False
    allowed=[audit.get('original_task'),audit.get('saved_business_review'),*audit['records'],*audit['comparisons']]
    return any(isinstance(v,dict) and digest(value)==digest(v) for v in allowed)
