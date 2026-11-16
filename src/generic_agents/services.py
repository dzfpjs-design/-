"""Transactional domain services. Authorization is independent of model output."""
from datetime import datetime, timezone
from sqlalchemy import select
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from .models import Capability, Entity, EntityEvent, Audit, Memory, Issue, Observation, Event, Job, now
from .contracts import Manifest, digest
from .security import require


def scoped(db, model, object_id, principal, lock=False):
    query = select(model).where(model.id == object_id, model.workspace_id == principal.workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row:
        raise HTTPException(404, "记录不存在或不在当前工作区")
    return row


def audit(db, p, action, object_id, details=None):
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action,
                 object_id=object_id, data=details or {}))


def check_version(row, expected):
    if row.version != expected:
        raise HTTPException(409, "记录已更新，请刷新后重新操作")


def as_dict(row):
    from .models import DataRun, Invocation, CloudCall
    if isinstance(row, (DataRun, Job, Invocation, CloudCall, Audit)):
        from .multimodal_question_service_v262 import private_projection
        private = private_projection(row)
        if private is not None:
            return private
    result = {col.name: (val.isoformat() if isinstance(val, datetime) else val)
            for col in row.__table__.columns if not col.info.get('typed_payload_internal')
            and (val := getattr(row, col.name)) is not None}
    if isinstance(row, Job):
        from .catalogue_current_allocation_closure_v260 import public_job_data
        private = public_job_data(row)
        if private is not None:
            result['data'] = private
    return result


def validate(schema, value):
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        raise HTTPException(422, [{"field": ".".join(map(str, e.path)), "message": e.message}
                                  for e in errors[:10]])


def timestamp(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(422, "时间必须包含时区")


def _validate_request_profile(manifest):
    if 'request_profile' not in manifest.execution:
        return
    from .cloud_input_profiles import validate_request_profile
    try:
        if manifest.kind != 'foundation_model':
            raise ValueError('request_profile仅用于基础模型')
        validate_request_profile(manifest.execution)
    except ValueError:
        raise HTTPException(422, 'request_profile须为Qwen3.8非向量基础模型的固定非思考配方') from None


def register(db, p, manifest: Manifest):
    require(p, "admin", "operator")
    _validate_request_profile(manifest)
    value = manifest.model_dump(mode="json")
    from .dji_cloud_tool import applies as dji_tool_applies, validate_manifest as validate_dji_manifest
    if dji_tool_applies(value): validate_dji_manifest(value)
    from .device_gateway_tools_v269 import guard_registration
    guard_registration(db, p, value)
    from .device_gateway_sources_v270 import guard_registration as guard_source_registration
    guard_source_registration(db, p, value)
    from .business_data import is_managed_manifest
    if is_managed_manifest(value):
        raise HTTPException(422, "业务数据来源请通过业务数据连接登记，不能手工注册或复制")
    # Apply only to new ordinary upload sources. Specialized manifests and
    # historical source interpretation keep their original contract and hash.
    if (value["kind"] == "source" and value["execution"].get("adapter") == "upload"
            and set(value["specification"]) <= {"timezone"}):
        value["specification"].setdefault("timezone", "Asia/Shanghai")
    row = Capability(workspace_id=p.workspace_id, namespace=manifest.namespace, name=manifest.name,
                     release_version=manifest.version, kind=manifest.kind, data=value, content_hash=digest(value))
    db.add(row); db.flush()
    audit(db, p, "capability.register", row.id, {"hash": row.content_hash})
    return row


def validate_capability(db, p, cap_id, expected):
    require(p, "admin", "operator")
    cap = scoped(db, Capability, cap_id, p, True); check_version(cap, expected)
    if cap.status != "draft":
        raise HTTPException(409, "只有草稿可以验证")
    manifest = Manifest.model_validate(cap.data)
    _validate_request_profile(manifest)
    from .dji_cloud_tool import applies as dji_tool_applies, validate_manifest as validate_dji_manifest, execute as check_dji_tool
    if dji_tool_applies(cap.data):
        validate_dji_manifest(cap.data); check_dji_tool(db,p,cap,{})
    from .same_goal_continuation_grant_contracts_v313 import applies as continuation_grant_tool
    if continuation_grant_tool(manifest.model_dump(mode='json')):
        from .business_tools import validate_bindings as validate_continuation_grant_tool
        validate_continuation_grant_tool(manifest.model_dump(mode='json'))
    from .multimodal_question_tool_v262 import applies as multimodal_question_tool, validate_manifest as validate_multimodal_question_tool
    if multimodal_question_tool(manifest.model_dump(mode='json')):
        validate_multimodal_question_tool(manifest.model_dump(mode='json'))
    if (manifest.execution.get('handler') == 'task_sound_receipt_status_v261'
            or manifest.namespace == 'task_sound_receipt_status_v261'
            or manifest.name == 'task_sound_receipt_status_v261'):
        from .workflow_sound_receipt_status_v261 import validate_manifest
        validate_manifest(manifest)
    if manifest.execution.get('handler') == 'workflow_task_quality_archive_v220':
        from .workflow_quality_archive_v220 import validate_manifest
        validate_manifest(manifest.model_dump(mode='json'))
    if manifest.execution.get('handler') in {'workflow_task_originals_quality_v219', 'workflow_task_originals_dedup_v219'}:
        from .workflow_data_quality_v219 import validate_manifest
        validate_manifest(manifest.model_dump(mode='json'))
    if ('native_schema_variant' in manifest.execution
            and manifest.kind != 'foundation_model'):
        raise HTTPException(422, '原生Schema变体只能明确登记在基础模型能力中')
    if cap.namespace == 'platform_numeric_rules' or 'managed_numeric_rule' in manifest.specification:
        from .local_rules_api import validate_managed_manifest
        validate_managed_manifest(db, p, cap)
    if manifest.kind == "domain_package":
        schemas = manifest.specification.get("entity_schemas", {})
        if not schemas:
            raise HTTPException(422, "业务包必须定义对象类型")
        for schema in schemas.values():
            Draft202012Validator.check_schema(schema)
            # Apply external-ref prohibition to package schemas too.
            Manifest.model_validate({**cap.data, "input_schema": schema})
        from .relations import validate_types
        validate_types(manifest.specification)
        tools=manifest.specification.get('data_tool_ids',[])
        if not isinstance(tools,list) or len(tools)>12 or not all(isinstance(x,str) for x in tools) or len(set(tools))!=len(tools):
            raise HTTPException(422,'业务包查询工具目录须为最多12项唯一依赖')
        for identifier in tools:
            tool=published(db,p,identifier)
            from .data_tools import validate_manifest
            if identifier not in manifest.dependencies or not tool.data['execution'].get('query_tool'):
                raise HTTPException(422,'查询工具必须声明已发布查询能力依赖')
            validate_manifest(tool.data)
        for identifier in manifest.specification.get('business_tool_ids',[]):
            tool=published(db,p,identifier)
            if tool.data['execution'].get('query_tool'):raise HTTPException(422,'数据查询工具须通过data_tool_ids声明')
            if tool.kind not in {'tool','specialist_model'} or identifier not in manifest.dependencies:
                raise HTTPException(422,'业务工具必须声明已发布工具/专业模型的依赖')
    if manifest.kind == "source" and manifest.specification.get("observation_capability") is not None:
        from .sensing_selection import SourceObservation
        try:SourceObservation.model_validate(manifest.specification["observation_capability"])
        except ValueError:raise HTTPException(422,"观测能力声明不符合字段、模态、动作、回执契约")
    if manifest.kind == "foundation_model":
        execution = manifest.execution
        if execution.get('task')=='embedding':
            from .retrieval import embedding_profile
            embedding_profile(cap)
        if execution.get("provider") not in {"qwen", "deepseek"} or not execution.get("model"):
            raise HTTPException(422, "需指定Qwen或DeepSeek模型")
        if not execution.get("secret_ref") or not manifest.data_policy.get("cloud_allowed"):
            raise HTTPException(422, "需配置密钥引用并明确云端数据授权")
        expected_secret = {"qwen": "DASHSCOPE_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}[execution["provider"]]
        if type(execution.get('report_evidence_output')) is dict and execution['report_evidence_output'].get('schema') == 'report-evidence-output-v5':
            expected_secret = 'QWEN_TOKENPLAN_API_KEY'
        if execution["secret_ref"] != expected_secret:
            raise HTTPException(422, "密钥引用不属于指定模型供应商")
        if not 128 <= int(execution.get("max_tokens", 2048)) <= 8192:
            raise HTTPException(422, "模型输出预算无效")
        if 'thinking_mode' in execution:
            if execution.get('provider')!='deepseek' or execution.get('task')=='embedding' or not isinstance(execution['thinking_mode'],str) or execution['thinking_mode'] not in {'enabled','disabled'}:
                raise HTTPException(422,'thinking_mode仅适用于DeepSeek非向量模型，且必须明确enabled或disabled')
        if 'report_evidence_output' in execution or 'native_schema_variant' in execution:
            from .cloud_evidence_profiles import validate_evidence_output
            try:
                validate_evidence_output(execution)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
    if manifest.kind in {"tool", "specialist_model", "source"}:
        if manifest.execution.get("adapter") not in {"builtin", "http", "mcp", "upload", "cloud_extractor", "cloud_retrieval", "local_specialist", "local_geospatial", "geo_quality_scl_v351", "local_geo_pair", "local_learned_geo_pair", "local_pixel_change", "local_audio", "local_environment_sound", "local_sound_observation", "audio_stream_source", "local_speech_activity", "local_segmented_audio", "local_task_segmented_audio", "media_source", "wms_source", "saved_gateway_map", "saved_feature_data", "mqtt_source", "business_data_source", "workflow_service", "business_reasoning", "business_decision_v276", "business_decision_v284", "business_goal_ready_v288", "same_goal_advice_v302", "task_archive", "task_evidence_packet", "runtime_document_packet", "public_observation_v1"}:
            raise HTTPException(422, "不支持的执行适配器")
        if manifest.execution.get('adapter') == 'public_observation_v1':
            from .public_observations import SOURCES, manifest as public_manifest
            provider_id=manifest.execution.get('provider_id')
            if manifest.kind!='source' or provider_id not in SOURCES or manifest.model_dump(mode='json')!=public_manifest(provider_id).model_dump(mode='json'):
                raise HTTPException(422,'公开观测来源须使用服务端固定只读契约')
        if manifest.execution.get('adapter') == 'business_data_source':
            from .business_data import validate_managed_capability
            validate_managed_capability(db, p, cap)
        if manifest.execution.get('adapter') == 'workflow_service':
            from .workflow_service_catalog_v197 import validate_manifest
            validate_manifest(manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter') in {'business_reasoning', 'business_decision_v276', 'business_decision_v284','business_goal_ready_v288'}:
            from .business_reasoning_protocols_v276 import validate_manifest
            validate_manifest(db, p, manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter')=='same_goal_advice_v302':
            from .same_goal_advice_registration_v304 import validate_manifest
            validate_manifest(db,p,manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter') == 'task_archive':
            from .workflow_task_archive_v198 import validate_manifest
            validate_manifest(manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter') == 'task_evidence_packet':
            from .workflow_task_packet_v199 import validate_manifest
            validate_manifest(manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter') == 'runtime_document_packet':
            from .workflow_document_packet_v207 import validate_manifest
            validate_manifest(manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter') == 'builtin' and manifest.execution.get('handler') == 'workflow_task_context_v196':
            from .workflow_task_context_v196 import validate_manifest
            validate_manifest(manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter') == 'builtin' and manifest.execution.get('handler') == 'require_observation_context_v197':
            from .workflow_context_required_v197 import validate_manifest
            validate_manifest(manifest.model_dump(mode='json'))
        if manifest.execution.get('adapter')=='media_source':
            from .media import validated_channel,channel_manifest
            channel,gateway=validated_channel(db,p,manifest.execution.get('channel_id'))
            if manifest.model_dump()!=channel_manifest(channel,gateway).model_dump():
                raise HTTPException(422,'媒体来源必须使用已验证通道的固定契约，不授予控制或外发权限')
        if manifest.execution.get('adapter')=='mqtt_source':
            from .mqtt_observations import validate_source_manifest
            validate_source_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='wms_source':
            from .wms_source import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='saved_gateway_map':
            from .map_saved_source import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='saved_feature_data':
            from .feature_gateway_api import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter') == 'local_specialist':
            from .local_specialists import ready_install
            from .specialist_api import capability_manifest
            row = ready_install(db, p, manifest.execution.get('installation_id'))
            if manifest.model_dump() != capability_manifest(row).model_dump():
                raise HTTPException(422, '本地专业能力必须使用已安装版本的固定契约；不授予云发送或设备操作权限')
        if manifest.execution.get('adapter')=='local_geospatial':
            from .workflow_scenes import spatial_manifest
            from .local_specialists import ready_install
            row=ready_install(db,p,manifest.execution.get('installation_id'))
            if manifest.model_dump()!=spatial_manifest(row).model_dump():raise HTTPException(422,'空间能力必须使用已安装本地版本的固定契约，不授予外发或设备操作权限')
        if manifest.execution.get('adapter')=='geo_quality_scl_v351':
            from .geo_quality_v351 import manifest as quality_manifest
            if manifest.model_dump(mode='json') != quality_manifest().model_dump(mode='json'):
                raise HTTPException(422, 'SCL质量适配器必须使用已核验的本地固定契约')
        if manifest.execution.get('adapter')=='local_geo_pair':
            from .workflow_geo_pairs import validate_manifest
            validate_manifest(manifest)
        if manifest.execution.get('adapter')=='local_learned_geo_pair':
            from .workflow_learned_geo_pairs import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='local_pixel_change':
            from .workflow_pixel_manifest_v250 import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='local_task_segmented_audio':
            from .workflow_segmented_audio import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='local_segmented_audio':
            from .segmented_audio_capability import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='local_speech_activity':
            from .local_vad_install import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='local_environment_sound':
            from .workflow_sound import validate_manifest
            validate_manifest(manifest)
            from .local_sound import checked_trace
            checked_trace(db,p,manifest.execution.get('model_trace'),live=True)
        if manifest.execution.get('adapter')=='local_sound_observation':
            from .audio_observation_model import validate_manifest
            validate_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='audio_stream_source':
            from .audio_streams import validate_source_manifest
            validate_source_manifest(db,p,manifest)
        if manifest.execution.get('adapter')=='local_audio':
            from .workflow_audio import validate_manifest
            validate_manifest(manifest)
        if manifest.execution.get('adapter')=='cloud_retrieval':
            from .retrieval import embedding_profile
            model=published(db,p,manifest.execution.get('model_profile_id'),'foundation_model');embedding_profile(model)
            if model.id not in manifest.dependencies or not manifest.execution.get('query_tool'):
                raise HTTPException(422,'语义检索须锁定已发布向量模型依赖及查询工具声明')
        if manifest.execution.get("adapter") == "cloud_extractor":
            model = published(db, p, manifest.execution.get("model_profile_id"), "foundation_model")
            if not manifest.data_policy.get("cloud_allowed") or not manifest.specification.get("extraction_instruction"):
                raise HTTPException(422, "云提取器需要出域授权和提取任务说明")
            if model.id not in manifest.dependencies or "perception" not in manifest.allowed_agent_roles:
                raise HTTPException(422, "云提取器必须声明模型依赖和感知角色权限")
            if not {"attributes", "anomalies"}.issubset(manifest.output_schema.get("properties", {})):
                raise HTTPException(422, "提取器输出必须定义attributes和anomalies")
        if manifest.execution.get("adapter") in {"http", "mcp"}:
            from .gateway import validate_endpoint
            from .device_gateway_tools_v269 import applies as managed_device, validate_tool
            from .device_gateway_sources_v270 import managed as managed_source, validate_source
            if managed_source(db, p, cap):
                validate_source(db, p, cap, purpose='prepare')
            elif managed_device(cap.data):
                validate_tool(db, p, cap, purpose='prepare')
            else:
                validate_endpoint(manifest.execution)
                if manifest.execution.get('receipt_endpoint'):
                    validate_endpoint({'endpoint':manifest.execution['receipt_endpoint']})
            if manifest.kind in {'tool','specialist_model'} and manifest.execution.get('side_effect'):
                if manifest.execution.get('approval_policy')!='admin' or not manifest.execution.get('receipt_endpoint'):
                    raise HTTPException(422,'业务外部写工具需要管理员批准策略和只读回执查询地址')
            from .business_tools import validate_bindings
            validate_bindings(cap.data)
            if manifest.execution.get('adapter')=='mcp':
                if not manifest.execution.get('tool_name'):
                    raise HTTPException(422,'MCP适配器必须登记工具名称')
                from .connectors import MCPClient
                try:
                    with MCPClient(manifest.execution) as connector:connector.check_tool(cap.data)
                except HTTPException:raise
                except Exception:raise HTTPException(409,'MCP服务或工具契约核验失败，未发布')
        if manifest.execution.get("adapter") == "builtin":
            if manifest.execution.get("handler") not in {"numeric_rules","aggregate_values","data_record_aggregate","data_keyword_filter","data_fulltext_search","workflow_task_context_v196","require_observation_context_v197","workflow_task_originals_quality_v219","workflow_task_originals_dedup_v219","workflow_task_quality_archive_v220","pixel_machine_query_v252","task_sound_receipt_status_v261","multimodal_question_v262","task_pixel_receipt_status_v263","dji_cloud_dock_status_v1"}:
                raise HTTPException(422, "不存在该内置处理器")
            from .business_tools import validate_bindings
            validate_bindings(cap.data)
            if manifest.execution.get('handler') in {'data_record_aggregate','data_keyword_filter','data_fulltext_search'} and not manifest.execution.get('query_tool'):
                raise HTTPException(422,'数据处理器必须登记为只读查询工具')
            for rule in manifest.specification.get("rules", []):
                if not {"id", "field", "operator", "threshold"}.issubset(rule) or rule["operator"] not in {"gt", "lt"}:
                    raise HTTPException(422, "数值规则格式无效")
                if not isinstance(rule["threshold"], (int, float)) or isinstance(rule["threshold"], bool):
                    raise HTTPException(422, "规则阈值必须为数值")
        if manifest.kind == "source":
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(manifest.specification.get("timezone", "UTC"))
            except ZoneInfoNotFoundError:
                raise HTTPException(422, "感知源时区无效")
    for dependency in manifest.dependencies:
        dep = scoped(db, Capability, dependency, p)
        if dep.status != "published":
            raise HTTPException(422, "依赖必须先发布")
    cap.validated_hash = cap.content_hash; cap.status = "validated"; cap.version += 1
    audit(db, p, "capability.validate", cap.id, {"hash": cap.content_hash})
    return cap


def publish(db, p, cap_id, expected, reason):
    require(p, "admin")
    cap = scoped(db, Capability, cap_id, p, True); check_version(cap, expected)
    if cap.status != "validated" or cap.validated_hash != digest(cap.data):
        raise HTTPException(409, "需先验证当前版本")
    from .dji_cloud_tool import applies as dji_tool_applies, execute as check_dji_tool
    if dji_tool_applies(cap.data): check_dji_tool(db,p,cap,{})
    from .device_gateway_tools_v269 import applies as managed_device, validate_tool
    from .device_gateway_sources_v270 import managed as managed_source, validate_source
    if managed_source(db, p, cap):
        validate_source(db, p, cap, purpose='prepare')
    if managed_device(cap.data):
        validate_tool(db, p, cap, purpose='prepare')
    if cap.namespace == 'platform_numeric_rules' or 'managed_numeric_rule' in cap.data.get('specification', {}):
        from .local_rules_api import require_current_check
        require_current_check(db, p, cap)
    cap.status = "published"; cap.version += 1
    audit(db, p, "capability.publish", cap.id, {"reason": reason, "hash": cap.content_hash})
    return cap


def published(db, p, cap_id, kind=None):
    cap = scoped(db, Capability, cap_id, p)
    from .business_data import is_managed_manifest
    if is_managed_manifest(cap.data):
        raise HTTPException(409, '业务数据来源只允许通过已登记连接读取，不能用于普通观测或工具执行')
    if cap.status != "published" or (kind and cap.kind != kind):
        raise HTTPException(409, "能力尚未发布或类型不匹配")
    if cap.content_hash != digest(cap.data) or cap.validated_hash != cap.content_hash:
        raise HTTPException(409,'已发布能力内容发生变化，请核对登记版本')
    return cap


def create_entity(db, p, body):
    require(p, "admin", "operator")
    package = published(db, p, body.package_id, "domain_package")
    schema = package.data["specification"]["entity_schemas"].get(body.entity_type)
    if not schema:
        raise HTTPException(422, "业务包未定义该对象类型")
    validate(schema, body.attributes)
    row = Entity(workspace_id=p.workspace_id, namespace=package.namespace, entity_type=body.entity_type,
                 external_id=body.external_id, schema_capability_id=package.id, data=body.attributes,
                 observed_at=timestamp(body.observed_at), valid_until=timestamp(body.valid_until))
    db.add(row); db.flush()
    db.add(EntityEvent(workspace_id=p.workspace_id, entity_id=row.id, actor_id=p.user_id,
                       data={"operation": "create", "after": row.data, "entity_version": 1}))
    audit(db, p, "entity.create", row.id)
    from .quality import changed
    changed(db,p,package.id,'entity:'+row.id+':1',row.id)
    return row


def update_entity(db, p, entity_id, attributes, expected, reason):
    require(p, "admin", "operator")
    row = scoped(db, Entity, entity_id, p, True); check_version(row, expected)
    package = published(db, p, row.schema_capability_id, "domain_package")
    validate(package.data["specification"]["entity_schemas"][row.entity_type], attributes)
    before = row.data; row.data = attributes; row.version += 1
    from .retrieval import invalidate_entity
    invalidate_entity(db,p,row.id)
    db.add(EntityEvent(workspace_id=p.workspace_id, entity_id=row.id, actor_id=p.user_id,
                       data={"before": before, "after": attributes, "reason": reason,
                             "entity_version": row.version, "operation": "update"}))
    audit(db, p, "entity.update", row.id, {"reason": reason, "version": row.version})
    from .quality import changed
    changed(db,p,package.id,'entity:'+row.id+':'+str(row.version),row.id)
    return row


def query_evidence(db, p, entity_ids, fields, *, learning=True, context="", route_key=None, requirement=None, fixed_window=None, relation_query=None, include_trusted_cases=True, _goal_evaluation_cut=None):
    if _goal_evaluation_cut is not None:
        from .goal_evaluation_query_v288 import _query_clock, need
        need(relation_query is None and requirement is not None and learning is False and include_trusted_cases is False)
        _query_clock(db,p,entity_ids,fields,requirement,fixed_window,route_key,_goal_evaluation_cut)
    from .instance_data_receipts import query_context,observation_receipt
    query_context(db,p,entity_ids)
    if relation_query:
        from .relations import query
        return query(db,p,entity_ids,fields,relation_query,learning=learning,context=context,route_key=route_key,
            requirement=requirement,fixed_window=fixed_window,include_trusted_cases=include_trusted_cases)
    if requirement is not None:
        from .data_requirements import query
        return query(db,p,entity_ids,fields,requirement,fixed_window=fixed_window,learning=learning,context=context,route_key=route_key,include_trusted_cases=include_trusted_cases,
            **({'_goal_evaluation_cut':_goal_evaluation_cut} if _goal_evaluation_cut is not None else {}))
    entities = [scoped(db, Entity, entity_id, p) for entity_id in entity_ids]
    evidence, observation_evidence, gaps, conflicts = [], [], [], []
    for row in entities:
        missing = [field for field in fields if field not in row.data or row.data[field] is None]
        stale = bool(row.valid_until and row.valid_until < now())
        if missing or stale:
            gaps.append({"entity_id": row.id, "missing_fields": missing, "stale": stale,
                         "route": "perception", "reason": "需补充观测或核实有效期"})
        observations = db.scalars(select(Observation).where(
            Observation.workspace_id == p.workspace_id, Observation.entity_id == row.id,
            Observation.status == "processed",Observation.observed_at.is_not(None),
            Observation.data['candidate_only'].as_boolean().is_distinct_from(True)).order_by(Observation.observed_at.desc()).limit(20)).all()
        alternatives = {}
        for obs in observations:
            if obs.data.get('candidate_only') or obs.observed_at is None:continue
            reception=observation_receipt(db,p,obs)
            extracted = obs.data.get("extracted", {})
            verification=obs.data.get('human_verification',{})
            review_valid=verification.get('input_hash')==digest({'extracted':extracted,
                'evidence_ids':obs.data.get('evidence_ids',[]),'invocation_id':obs.data.get('invocation_id')})
            if review_valid and verification.get('decision')=='rejected':continue
            selected_attributes = {key: value for key, value in extracted.get("attributes", {}).items()
                                   if not fields or key in fields}
            if selected_attributes or extracted.get("anomalies"):
                observation_evidence.append({"observation_id": obs.id, "entity_id": row.id,
                    "version": obs.version, "source_id": obs.source_id,
                    "observed_at": obs.observed_at.isoformat(), "attributes": selected_attributes,
                    "anomalies": extracted.get("anomalies", []),
                    "evidence_ids": obs.data.get("evidence_ids", []),
                    "invocation_id": obs.data.get("invocation_id"),
                    "verification": "confirmed" if review_valid and verification.get('decision')=='confirmed' else "unverified_extraction", "source": "processed_observation",
                    **reception})
            for field, val in obs.data.get("extracted", {}).get("attributes", {}).items():
                alternatives.setdefault((field, obs.observed_at.isoformat()), set()).add(digest(val))
        for (field, observed_time), values in alternatives.items():
            if len(values) > 1:
                conflicts.append({"entity_id": row.id, "field": field,
                                  "observation_ids": [o.id for o in observations if o.observed_at.isoformat() == observed_time],
                                  "reason": "同一时间的多源观测存在不同值，需要核实来源"})
        evidence.append({"entity_id": row.id, "version": row.version,
                         "attributes": {key: val for key, val in row.data.items() if not fields or key in fields},
                         "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                         "valid_until": row.valid_until.isoformat() if row.valid_until else None,
                         "source": "resource_entity", "observation_ids": [o.id for o in observations]})
    packages = {row.schema_capability_id for row in entities}
    status = "insufficient" if gaps or not evidence else "conflict" if conflicts else "sufficient"
    from .learning import select_cases, eligible
    if not include_trusted_cases:
        memories, decisions = [], []
    elif learning:
        memories, decisions = select_cases(db, p, packages, fields, context, route_key, status, entity_ids)
    else:
        rows = [m for package_id in sorted(packages) for m in eligible(db, p, package_id)][:10]
        memories = [{"id":m.id,"text":m.text,"provenance":m.data} for m in rows]; decisions = []
    return {"status": status,
            "evidence": evidence, "observation_evidence": observation_evidence,
            "gaps": gaps, "conflicts": conflicts,
            "trusted_cases": memories, "learning_decisions": decisions,
            "evaluated_at": now().isoformat()}


def diagnose(db, p):
    from .quality import diagnose as check_quality
    return check_quality(db,p)


def enqueue_event(db, p, kind, key, data):
    from sqlalchemy.dialects.postgresql import insert
    statement = insert(Event).values(workspace_id=p.workspace_id, kind=kind,
             idempotency_key=key, data=data).on_conflict_do_nothing(
             index_elements=["workspace_id", "idempotency_key"])
    db.execute(statement)
    return db.scalar(select(Event).where(Event.workspace_id == p.workspace_id, Event.idempotency_key == key))
