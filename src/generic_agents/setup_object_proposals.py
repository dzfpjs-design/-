"""Local checks and explicit application of document-grounded object suggestions."""
import copy
import re
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract


class ObjectCitation(Contract):
    material_id: str
    block_id: str
    quote: str = Field(min_length=1, max_length=250)


class ProposedField(Contract):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal['文本', '整数', '数值', '是否', '日期']
    required: bool
    unit: str | None = Field(max_length=40)
    citations: list[ObjectCitation] = Field(max_length=4)
    inference_reason: str = Field(max_length=500)

    @model_validator(mode='after')
    def valid(self):
        if not self.label.strip() or self.label != self.label.strip():
            raise ValueError('字段名称不能为空或带有首尾空白')
        if self.unit is not None and (not self.unit.strip() or self.unit != self.unit.strip()):
            raise ValueError('未明确的单位须保存为未知')
        if self.kind not in {'整数', '数值'} and self.unit is not None:
            raise ValueError('仅数值和整数字段可以声明单位')
        return self


class ProposedObject(Contract):
    name: str = Field(min_length=1, max_length=100)
    fields: list[ProposedField] = Field(min_length=1, max_length=40)
    citations: list[ObjectCitation] = Field(max_length=4)
    inference_reason: str = Field(max_length=500)

    @model_validator(mode='after')
    def valid(self):
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError('对象名称不能为空或带有首尾空白')
        if len({f.label for f in self.fields}) != len(self.fields):
            raise ValueError('对象字段名称不能重复')
        if not any(f.kind == '文本' and f.required for f in self.fields):
            raise ValueError('对象需明确一个必填文本识别字段；不足时请在待明确内容提出，不生成不完整对象')
        return self


def unit_declared(label, unit, quote):
    """Accept a positive declaration next to this field, not another row's unit.

    Conservative grammar covers explicit field metadata and table cells. A raw
    measurement, example, negation or alternative is not a schema declaration.
    Unrecognized prose remains unknown pending human review.
    """
    declared=False
    for match in re.finditer(re.escape(label), quote):
        if match.start() and quote[match.start()-1] not in ' \t\n。；;，,|：:':
            continue
        tail=re.split(r'[。；;\n]', quote[match.end():], maxsplit=1)[0]
        uncertainty=tail
        for phrase in ['不必填','非必填','不转换单位','不换算','不做单位转换','不得换算','禁止换算']:
            uncertainty=uncertainty.replace(phrase,'')
        if any(word in uncertainty for word in ['不','未','未知','待','没有','尚无','示例','例如','举例','可能','假设','可为','或者','或']):
            return False
        if re.match(r'\s*[（(]\s*'+re.escape(unit)+r'\s*[）)]',tail):
            declared=True;continue
        declaration=re.search(r'单位\s*(?:为|是|[:：=])?\s*'+re.escape(unit)+r'(?=$|[\s，,|）)])',tail)
        if declaration:
            metadata=tail[:declaration.start()]
            metadata=re.sub(r'不必填|非必填|字段类型|数值|整数|文本|日期|是否|类型|必填|选填|可选|是|否','',metadata)
            if not re.sub(r'[\s:：,，|（）()/]','',metadata):
                declared=True;continue
        if re.search(r'单位\s*(?:为|是|[:：=])?\s*[^\s，,|）)]',tail) and not declaration:
            return False
        if '\t' in tail or '|' in tail:
            cells=[x.strip() for x in re.split(r'[\t|]',tail)]
            if unit in cells and all(x in {'','文本','整数','数值','是否','日期','是','否','必填','非必填','不必填','可选',unit} for x in cells):
                declared=True
    return declared


def validate_objects(values, blocks, previous=None, *, table_snapshot=None):
    result = [ProposedObject.model_validate(x).model_dump() for x in values]
    if len({x['name'] for x in result}) != len(result):
        raise ValueError('对象类型名称重复')
    sources = {}
    for b in blocks:
        sources.setdefault((b['material_id'], b['block_id']), []).append(b['text'])
    for obj in result:
        for item in [obj, *obj['fields']]:
            if not item['citations'] and not item['inference_reason'].strip():
                raise ValueError('对象和每个字段均需原文依据或明确的系统推断')
            for c in item['citations']:
                if not any(c['quote'] in text for text in sources.get((c['material_id'], c['block_id']), [])):
                    raise ValueError('对象字段的引用不匹配已授权原文')
            if item.get('unit') is not None and not any(
                unit_declared(item['label'], item['unit'], c['quote']) and any(
                    unit_declared(item['label'], item['unit'], text)
                    for text in sources.get((c['material_id'], c['block_id']), [])) for c in item['citations']
            ):
                from .setup_ocr_table_units import table_unit_declared
                if table_snapshot is None or not table_unit_declared(item,table_snapshot,sources):
                    raise ValueError('单位需同一原文引用明确对应此字段；缺失或矛盾时保留未知，不能借用其他字段单位')
    current = {x['name']: x for x in result}
    for old in previous or []:
        if old['name'] not in current:
            raise ValueError('累计建议遗漏前批对象，已完成检查点保留')
        fields = {f['label']: f for f in current[old['name']]['fields']}
        for old_field in old['fields']:
            new = fields.get(old_field['label'])
            if new is None:
                raise ValueError('累计建议遗漏前批字段，已完成检查点保留')
            if (any(new[k] != old_field[k] for k in ['kind', 'required'])
                or old_field['unit'] is not None and new['unit'] != old_field['unit']):
                raise ValueError('资料批次的字段类型、必填或单位有冲突，请核对原文；已完成建议与付费响应保留')
    return result


def existing_objects(config):
    if 'instanceObjects' in config:
        return copy.deepcopy(config['instanceObjects'])
    return [{'name': name, 'fields': [{'label': '名称', 'kind': '文本', 'required': True}]}
            for name in dict.fromkeys(config.get('instanceProfile', {}).get('objectCandidates', []))]


def apply_objects(config, proposals, selected, *, analysis_id, result_hash):
    out = copy.deepcopy(config)
    if not selected:
        return out
    candidates = {x['name']: x for x in proposals}
    if len(set(selected)) != len(selected) or any(n not in candidates for n in selected):
        raise ValueError('请选择实际存在且不重复的对象建议')
    existing = existing_objects(out)
    if any(n in {x['name'] for x in existing} for n in selected):
        raise ValueError('同名业务对象已存在；请在检查生成页核对修改，不会覆盖已有人工字段')
    if len(existing) + len(selected) > 40:
        raise ValueError('业务对象类型超过40种，请分实例管理')
    origins = copy.deepcopy(out.get('instanceObjectOrigins', {}))
    for name in selected:
        obj = candidates[name]
        existing.append({'name': name, 'fields': [
            {k: f[k] for k in ['label', 'kind', 'required', 'unit']} for f in obj['fields']]})
        origins[name] = {'analysis_id': analysis_id, 'result_hash': result_hash, 'object': copy.deepcopy(obj)}
    out['instanceObjects'] = existing
    out['instanceObjectOrigins'] = origins
    return out


def origin_material_ids(config, object_names):
    from .report_full_semantic_schema_adoption import origin_material_ids as full_material_ids
    return full_material_ids(config, object_names, 'object') | {c['material_id'] for name in object_names
            for obj in [config.get('instanceObjectOrigins', {}).get(name, {}).get('object', {})]
            for item in [obj, *obj.get('fields', [])] for c in item.get('citations', [])}


def verify_origins(db, p, config, object_names):
    """Do not turn arbitrary draft metadata into document provenance at compilation."""
    from fastapi import HTTPException
    from .models import SetupAnalysis, SetupMaterial
    from .services import scoped
    from .contracts import digest
    from .setup_understanding import ResultV2, request_contract
    for name in object_names:
        origin = config.get('instanceObjectOrigins', {}).get(name)
        if origin is None:
            continue
        if isinstance(origin, dict) and origin.get('schema') in {
                'report-full-semantic-schema-origin-v1', 'report-object-schema-origin-v170'}:
            from .report_full_semantic_schema_adoption import verify_origin
            verify_origin(db, p, config, name, 'object')
            continue
        try:
            if set(origin) != {'analysis_id', 'result_hash', 'object'}:
                raise ValueError()
            row = scoped(db, SetupAnalysis, origin['analysis_id'], p)
            req = row.data['request']
            if (not issubclass(request_contract(req), ResultV2) or row.status not in {'applied', 'undone'}
                or name not in row.data.get('selected_object_names', [])
                or digest(row.data['result']) != row.data['result_hash']
                or origin['result_hash'] != row.data['result_hash']
                or origin['object'] != next(x for x in row.data['result']['objects'] if x['name'] == name)):
                raise ValueError()
            referenced = origin_material_ids(config, [name])
            snapshots = {m['id']: m for m in req['materials']}
            for material_id in referenced:
                m = scoped(db, SetupMaterial, material_id, p)
                original = snapshots[material_id]
                if (m.archived or m.status != 'parsed' or m.version != original['version']
                    or m.sha256 != original['hash'] or digest(m.data.get('blocks')) != original['blocks_hash']):
                    raise ValueError()
        except (HTTPException, ValueError, KeyError, TypeError, StopIteration, AttributeError):
            raise HTTPException(409, '对象字段建议的原文来源或版本已变化，请重新核对；已有配置与记录保留')
