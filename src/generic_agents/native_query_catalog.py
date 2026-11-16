"""Generation-time local data capabilities; no separate data-agent setup."""
import json
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Manifest, digest
from .models import Capability
from .services import published, register, validate_capability, publish

SCHEMA = 'native-query-catalog-v1'
METHODS = ['keyword', 'aggregate', 'fulltext']
NAMES = ['data_keyword', 'data_aggregate', 'data_fulltext']
LABELS = {'value': '计算结果', 'sample_count': '参与计算的证据记录数',
    'field': '计算字段', 'operation': '计算方式', 'score': '检索匹配分数'}


def manifests():
    base = Path(__file__).resolve().parents[2]/'model-packs'
    return [Manifest.model_validate(json.loads((base/('native-'+name+'.json')).read_text())) for name in NAMES]


def ensure(db, p):
    """Only instance generation calls this; preview/read never installs or publishes."""
    tools = []
    for manifest in manifests():
        row = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
            Capability.namespace == manifest.namespace, Capability.name == manifest.name,
            Capability.release_version == manifest.version).with_for_update())
        if row:
            row = published(db, p, row.id)
            if row.content_hash != digest(manifest.model_dump(mode='json')):
                raise HTTPException(409, '平台内置查询登记与固定实现不一致，不能替换或静默升级')
        else:
            row = register(db, p, manifest)
            validate_capability(db, p, row.id, row.version)
            publish(db, p, row.id, row.version, '根据确认业务版本自动配备本机只读数据查询，不外发、不控制设备')
        tools.append(row)
    return tools


def pins(tools):
    return {'schema': SCHEMA, 'tools': [{'id': row.id, 'hash': row.content_hash,
        'version': row.release_version, 'method': row.data['specification']['method']} for row in tools]}


def catalog(db, p, package):
    fixed = package.data['specification'].get('native_query_catalog')
    if fixed is None:
        return []
    expected = {m.specification['method']: digest(m.model_dump(mode='json')) for m in manifests()}
    if fixed.get('schema') != SCHEMA or not isinstance(fixed.get('tools'), list) or len(fixed['tools']) != 3:
        raise HTTPException(409, '原业务版本的本地查询目录无法核对')
    dependencies = set(package.data['dependencies'])
    allowed = set(package.data['specification'].get('data_tool_ids', []))
    result = []
    seen = set()
    for pin in fixed['tools']:
        method = pin.get('method')
        if method in seen or method not in expected or pin.get('hash') != expected[method] or pin.get('id') not in dependencies & allowed:
            raise HTTPException(409, '原业务版本的查询能力未按固定实现和依赖登记')
        row = published(db, p, pin['id'])
        if row.content_hash != pin['hash'] or row.release_version != pin['version'] or digest(row.data) != row.content_hash:
            raise HTTPException(409, '原本地查询能力版本或内容已变化')
        result.append(row)
        seen.add(method)
    return result


def catalog_for_task(db, p, task, *, lock_sources=True):
    from .workspace_api import saved_execution
    _, release, business = saved_execution(db, p, task.id)
    if any(key in task.data for key in ('object_definition_bindings','object_definition_bindings_hash')):
        from .entity_release_compat import verify_task_objects
        verify_task_objects(db,p,task,release,lock_sources=lock_sources)
    package = published(db, p, release.package_id, 'domain_package')
    if digest(release.data['snapshot']) != release.configuration_hash or package.data['specification'].get('configuration_hash') != release.configuration_hash:
        raise HTTPException(409, '原业务版本与查询目录关联无法核对')
    if not business['available']:
        raise HTTPException(409, '原业务不可执行，不能使用查询能力')
    return catalog(db, p, package)


def metadata(db, p, package):
    tools = []
    from .business_tools import arguments_schema
    source_fields={}
    for obj in package.data['specification'].get('object_catalog',[]):
        schema=package.data['specification']['entity_schemas'][obj['key']]
        for field in obj['fields']:
            prop=schema['properties'][field['key']]
            kind=prop.get('type')
            if isinstance(kind,list):kind=next((x for x in kind if x!='null'),None)
            if kind in {'string','boolean','number','integer'}:
                source_fields[field['key']]={'key':field['key'],'label':field['label'],'kind':kind,'unit':field.get('unit')}
    for row in catalog(db, p, package):
        fields = []
        properties = row.data['output_schema']['properties']['records']['items']['properties']['values'].get('properties', {})
        for key, prop in properties.items():
            kind = prop.get('type')
            if isinstance(kind, list):
                choices = [x for x in kind if x != 'null']
                kind = choices[0] if len(choices) == 1 else None
            if kind not in {'number', 'integer', 'string', 'boolean'}:
                continue
            fields.append({'key': key, 'label': prop.get('title') or LABELS.get(key) or key,
                'kind': kind, 'unit': '条证据记录' if key == 'sample_count' else None})
        tools.append({'id': row.id, 'name': row.data['description'], 'version': row.release_version,
            'content_hash': row.content_hash, 'method': row.data['specification']['method'],
            'external': False, 'parameters_schema': arguments_schema(row),
            'preserves_input_fields':row.data['specification']['method'] in {'keyword','fulltext'},
            'output_fields':list(source_fields.values()) if row.data['specification']['method'] in {'keyword','fulltext'} else fields})
    return {'query_tools': tools, 'query_tools_error': None}


def output_schema_for_steps(db,p,task,spec,*,lock_sources=True):
    """Typed frames follow each explicit input edge, never all prior branches."""
    from .models import Entity
    has_proof=any(key in task.data for key in ('object_definition_bindings','object_definition_bindings_hash'))
    if has_proof:
        from .entity_release_compat import verify_task_objects
        verify_task_objects(db,p,task,lock_sources=lock_sources)
    package=published(db,p,task.package_id,'domain_package')
    source={}
    for identity in spec['entity_ids']:
        obj=db.get(Entity,identity)
        if (not obj or obj.workspace_id!=p.workspace_id
            or (identity not in task.data['entity_ids'] if has_proof else obj.schema_capability_id!=package.id)):
            raise HTTPException(422,'查询对象不属于当前业务范围')
        source.update({key:prop for key,prop in package.data['specification']['entity_schemas'][obj.entity_type]['properties'].items() if key in spec['required_fields']})
    frames={'evidence':source};available={cap.id:cap for cap in catalog_for_task(db,p,task,lock_sources=lock_sources)}
    for step in spec['data_query']['tool_steps']:
        inputs=frames.get(step['input_from']);cap=available.get(step['capability_id'])
        if inputs is None or cap is None:
            raise HTTPException(422,'查询步骤只能使用原资料或明确前序结果的字段')
        field=step['parameters'].get('field');prop=inputs.get(field)
        if not prop:
            raise HTTPException(422,'查询字段不属于该步骤选择的输入结果')
        kind=prop.get('type')
        if isinstance(kind,list):kind=next((x for x in kind if x!='null'),None)
        method=cap.data['specification']['method']
        if method in {'keyword','fulltext'} and kind!='string':
            raise HTTPException(422,'关键词筛选和词项检索需要已声明的文字字段')
        if method=='aggregate' and step['parameters'].get('operation')!='count' and kind not in {'integer','number'}:
            raise HTTPException(422,'均值、合计和最小最大值需要已声明的数值字段；记录计数可选择其他字段')
        frames[step['id']]=(cap.data['output_schema']['properties']['records']['items']['properties']['values']['properties'] if method=='aggregate' else inputs)
    return frames[spec['data_query']['tool_steps'][-1]['id']]
