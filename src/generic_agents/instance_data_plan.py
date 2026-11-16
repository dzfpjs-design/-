"""Derive a human-readable plan from confirmed shapes; no pretend policy execution."""

RESOURCE_NAMES={'camera':'视频','video':'音视频通道','drone':'无人机资料','maps':'地图/影像','sensor':'测量数据',
                'documents':'文档','data':'业务数据','models':'专业模型','tools':'工具','control':'设备控制'}

def derive_plan(catalog,businesses,resources):
    rows=[]
    for obj in catalog:
        fields=obj['fields'];required=[f['label'] for f in fields if f['required']]
        numeric=[f for f in fields if f['kind'] in {'数值','整数'}]
        units='；'.join(f['label']+'：'+(f.get('unit') or '单位未明确') for f in numeric)
        rows.append({'name':obj['name']+'对象记录','storage':'PostgreSQL对象记录及变更历史',
            'status':'schema_validation_available','object_type':obj['key'],
            'details':f'按确认的{len(fields)}个字段校验类型，必填：'+('、'.join(required) or '无')+
                ('。数值单位：'+units if units else '')+'。保存原记录与修改历史；单位声明不自动转换采集数据，也不证明现场状态。'})
    rows.append({'name':'流程任务、输出与审批','storage':'PostgreSQL任务、步骤回执与审计',
        'status':'versioned_storage_available','details':f'本次{len(businesses)}项业务中，'+
            f'{sum(bool(b["available"]) for b in businesses)}项执行条件已齐全；任务固定实例/流程版本，保留负责人、证据与结果。'})
    categories=sorted({r.get('category') for r in resources if r.get('category') in RESOURCE_NAMES})
    rows.append({'name':'资料与采集来源','storage':'本地原件存储、摘要和观测记录',
        'status':'source_configuration_only','source_categories':categories,
        'details':('当前配置来源：'+ '、'.join(RESOURCE_NAMES[x] for x in categories) if categories else '当前未配置资源来源')+
            '。原件/引用按实际接入保存，未知采集时间与单位保留未知；本页来源清单不代表连接已验证或设备已授权。'})
    rows.append({'name':'反馈与改进候选','storage':'数据管理Memory','status':'review_required',
        'details':'保留人工结果与来源；改进须审核、评估，未验收候选不自动发布或改变权限。'})
    rows.append({'name':'本地筛选、检索与统计','storage':'本机只读登记能力及有来源的查询记录','status':'generation_prepared',
        'details':'生成业务版本时自动固定关键词筛选、词项检索和数值汇总，无需另配数据管理智能体。任务可明确选择查询条件；只使用任务已选对象与字段，保留逐步输入、结果和来源，不外发、不改变原记录。统计单位为证据记录，不能当作现场对象数量。'})
    rows.append({'name':'后续数据策略','storage':'待业务规模与运行验证后确认','status':'not_enabled',
        'details':'自动保留期、删除、专用索引和跨来源主动质检尚未由本配置启用；系统不会据此删除资料或宣称治理完成。'})
    return rows
