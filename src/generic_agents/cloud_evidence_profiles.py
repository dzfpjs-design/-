"""Explicit evidence-call output profiles; legacy input policies are unchanged.

These are platform ceilings, not a claim that every allowed schema fits or that
an answer is complete/correct. Generation/preview must freeze the selected
profile and actual options; the gateway still reserves before one HTTP call.
Official output limits were independently checked by root on 2026-10-17.
This module performs no IO, model call, credential lookup or budget mutation.
"""
import copy


MAX_RESPONSE_BYTES = 256 * 1024
_STAGES = {'map-v3': 180, 'reduce-v2': 600, 'map-v4': 180,
    'map-v5': 180, 'reduce-v3': 600, 'map-v6': 180, 'reduce-v4': 600}
STRICT_MAP_TRANSPORT = 'deepseek-strict-map-tool-v1'
STRUCTURED_REPORT_TRANSPORT = 'deepseek-strict-report-tool-v2'
REF_ARM_REPORT_TRANSPORT = 'deepseek-strict-report-tool-v3'
NATIVE_REPORT_TRANSPORT = 'qwen-tokenplan-native-json-schema-v1'
NATIVE_ENDPOINT = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'
CLAIM_NATIVE_TRANSPORT = 'qwen-public-claim-native-v168'
CLAIM_NATIVE_ENDPOINT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
CLAIM_ENUM_NATIVE_TRANSPORT = 'qwen-public-claim-enum-native-v172'
_MODELS = {
    'deepseek': {'deepseek-flash', 'deepseek-v4-pro'},
    'qwen': {'qwen-plus', 'qwen-plus-2025-12-01'},
}
_OUTPUT_CEILINGS = {'deepseek': 131072, 'qwen': 32768}
_NO_NATIVE_STAGE = object()


def _native_schema_variant_guard(execution, stage=_NO_NATIVE_STAGE):
    """Only an explicit allocation registration selects a provider dialect.

    Missing keys keep every legacy registration/policy unchanged. This selector
    is server configuration, never a provider request option or an implicit
    upgrade based on a model name.
    """
    if type(execution) is dict and 'native_schema_variant' in execution:
        if stage is not _NO_NATIVE_STAGE and stage != 'catalogue-allocation-v213':
            raise ValueError('原生Schema变体仅用于明确登记的小批归属阶段')
        from .report_business_catalogue_allocation_native_v213 import schema_variant
        return schema_variant(execution)
    return None


def evidence_policy(execution, stage):
    """Return the exact stage policy for an explicitly registered output limit."""
    _native_schema_variant_guard(execution, stage)
    if type(execution) is dict and type(stage) is str and stage == 'catalogue-allocation-v213':
        from .report_business_catalogue_allocation_native_v213 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'catalogue-v211':
        from .report_business_catalogue_native_v211 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v193':
        from .report_business_flow_native_v193 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v190':
        from .report_business_flow_native_v190 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v187':
        from .report_business_flow_native_v187 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v185':
        from .report_business_flow_native_v185 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v184':
        from .report_business_flow_native_v184 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v183':
        from .report_business_flow_native_v183 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'business-flow-v182':
        from .report_business_flow_native_v182 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'reduce-scope-v181':
        from .report_scoped_design_native_v181 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'reduce-v179':
        from .report_design_grouped_native_v179 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'reduce-v178':
        from .report_design_grouped_native_v178 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage == 'reduce-v176':
        from .report_design_grouped_native_v176 import input_policy
        return input_policy(execution)
    if type(execution) is dict and type(stage) is str and stage in {'map-v172', 'reduce-v172'}:
        return _claim_enum_native_policy(execution, stage)
    if type(execution) is dict and type(stage) is str and stage in {'map-v168', 'reduce-v168'}:
        return _claim_native_policy(execution, stage)
    if type(execution) is dict and type(stage) is str and stage in {'map-v7', 'reduce-v5'}:
        return _native_policy(execution, stage)
    if type(execution) is not dict or type(stage) is not str or stage not in _STAGES:
        raise ValueError('未登记的证据读取阶段，未发送')
    provider, model = execution.get('provider'), execution.get('model')
    if (type(provider) is not str or type(model) is not str
            or provider not in _MODELS or model not in _MODELS[provider]
            or execution.get('task') == 'embedding'):
        raise ValueError('此模型未登记证据长文本输出配置，未发送')
    if provider == 'deepseek' and execution.get('thinking_mode') != 'disabled':
        raise ValueError('证据读取的DeepSeek调用必须明确关闭思考，未发送')
    if provider == 'qwen' and 'thinking_mode' in execution:
        raise ValueError('Qwen证据配置不能使用DeepSeek思考选项，未发送')
    generic = execution.get('max_tokens')
    if type(generic) is not int or not 128 <= generic <= 8192:
        raise ValueError('通用输出预算仍须是128至8192的明确整数，未发送')
    configured = generic
    if 'report_evidence_output' in execution:
        declaration = execution['report_evidence_output']
        ref_arms = type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v4'
        structured = type(declaration) is dict and declaration.get('schema') in {'report-evidence-output-v3', 'report-evidence-output-v4'}
        if structured:
            stages = {'map-v6', 'reduce-v4'} if ref_arms else {'map-v5', 'reduce-v3'}
            transport = REF_ARM_REPORT_TRANSPORT if ref_arms else STRUCTURED_REPORT_TRANSPORT
            if (set(declaration) != {'schema', 'max_tokens', 'map_transport', 'reduce_transport'}
                    or provider != 'deepseek' or stage not in stages
                    or declaration['map_transport'] != transport
                    or declaration['reduce_transport'] != transport
                    or type(declaration['max_tokens']) is not int
                    or not 128 <= declaration['max_tokens'] <= _OUTPUT_CEILINGS[provider]):
                raise ValueError('有界读取与严格汇总必须按新闭合协议同时明确登记；未降级或发送')
            configured = declaration['max_tokens']
            output = min(configured, 8192) if stage in {'map-v5', 'map-v6'} else configured
            return {'version': 'cloud-evidence-2026-10-17/' + stage, 'stage': stage,
                'max_input_bytes': 900000, 'transport_timeout_seconds': _STAGES[stage],
                'max_output_tokens': output, 'max_images': 0, 'context_token_bound': 1000000,
                'full_report': True, 'long_context_verified': True}
        if stage in {'map-v5', 'reduce-v3', 'map-v6', 'reduce-v4'}:
            raise ValueError('此模型未显式登记新的有界读取与严格汇总；未发送')
        strict_map = type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v2'
        fields = {'schema', 'max_tokens', 'map_transport'} if strict_map else {'schema', 'max_tokens'}
        if (type(declaration) is not dict or set(declaration) != fields
                or declaration['schema'] != ('report-evidence-output-v2' if strict_map else 'report-evidence-output-v1')
                or type(declaration['max_tokens']) is not int
                or not 128 <= declaration['max_tokens'] <= _OUTPUT_CEILINGS[provider]):
            raise ValueError('证据专用输出预算未按当前平台闭合协议登记，未发送')
        if strict_map:
            if provider != 'deepseek' or declaration['map_transport'] != STRICT_MAP_TRANSPORT or stage != 'map-v4':
                raise ValueError('此声明仅允许已登记的DeepSeek严格Map读取；未降级或发送汇总')
        elif stage == 'map-v4':
            raise ValueError('此模型未显式登记严格Map交付方式，未发送')
        configured = declaration['max_tokens']
    elif stage in {'map-v4', 'map-v5', 'reduce-v3', 'map-v6', 'reduce-v4'}:
        raise ValueError('此模型未显式登记严格Map交付方式，未发送')
    output = min(configured, 8192) if stage in {'map-v3', 'map-v4'} else configured
    return {'version': 'cloud-evidence-2026-10-17/' + stage, 'stage': stage,
        'max_input_bytes': 900000, 'transport_timeout_seconds': _STAGES[stage],
        'max_output_tokens': output, 'max_images': 0, 'context_token_bound': 1000000,
        'full_report': True, 'long_context_verified': True}


def validate_evidence_output(execution):
    """Validate only an explicit new registration; never widen legacy options."""
    if type(execution) is not dict:
        raise ValueError('执行配置必须是对象')
    _native_schema_variant_guard(execution)
    if 'report_evidence_output' in execution:
        declaration = execution['report_evidence_output']
        if type(declaration) is dict and declaration.get('schema') == 'report-business-catalogue-allocation-native-output-v213':
            evidence_policy(execution, 'catalogue-allocation-v213')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-catalogue-native-output-v211':
            evidence_policy(execution, 'catalogue-v211')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v193':
            evidence_policy(execution, 'business-flow-v193')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v190':
            evidence_policy(execution, 'business-flow-v190')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v187':
            evidence_policy(execution, 'business-flow-v187')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v185':
            evidence_policy(execution, 'business-flow-v185')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v184':
            evidence_policy(execution, 'business-flow-v184')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v183':
            evidence_policy(execution, 'business-flow-v183')
        elif type(declaration) is dict and declaration.get('schema') == 'report-business-flow-output-v182':
            evidence_policy(execution, 'business-flow-v182')
        elif type(declaration) is dict and declaration.get('schema') == 'report-scoped-design-output-v181':
            evidence_policy(execution, 'reduce-scope-v181')
        elif type(declaration) is dict and declaration.get('schema') == 'report-indexed-design-output-v179':
            evidence_policy(execution, 'reduce-v179')
        elif type(declaration) is dict and declaration.get('schema') == 'report-indexed-design-output-v178':
            evidence_policy(execution, 'reduce-v178')
        elif type(declaration) is dict and declaration.get('schema') == 'report-saved-map-design-output-v176':
            evidence_policy(execution, 'reduce-v176')
        elif type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v172':
            evidence_policy(execution, 'map-v172')
            evidence_policy(execution, 'reduce-v172')
        elif type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v168':
            evidence_policy(execution, 'map-v168')
            evidence_policy(execution, 'reduce-v168')
        elif type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v5':
            evidence_policy(execution, 'map-v7')
            evidence_policy(execution, 'reduce-v5')
        elif type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v4':
            evidence_policy(execution, 'map-v6')
            evidence_policy(execution, 'reduce-v4')
        elif type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v3':
            evidence_policy(execution, 'map-v5')
            evidence_policy(execution, 'reduce-v3')
        elif type(declaration) is dict and declaration.get('schema') == 'report-evidence-output-v2':
            evidence_policy(execution, 'map-v4')
        else:
            evidence_policy(execution, 'map-v3')
            evidence_policy(execution, 'reduce-v2')
    return execution


def declared_map_transport(execution):
    """Only a published manifest's explicit V2 declaration opts into V4."""
    if type(execution) is not dict:
        raise ValueError('执行配置必须是对象')
    _native_schema_variant_guard(execution)
    declaration = execution.get('report_evidence_output')
    if declaration is None and 'report_evidence_output' not in execution:
        return None
    if type(declaration) is not dict:
        raise ValueError('证据专用输出声明必须是对象')
    if declaration.get('schema') == 'report-business-catalogue-allocation-native-output-v213':
        evidence_policy(execution, 'catalogue-allocation-v213')
        raise ValueError('固定小批归属仅允许独立阶段，不能进入原报告Map或完整设计流程')
    if declaration.get('schema') == 'report-business-catalogue-native-output-v211':
        evidence_policy(execution, 'catalogue-v211')
        raise ValueError('业务目录仅允许独立目录阶段，不能进入原报告Map或完整设计流程')
    if declaration.get('schema') == 'report-business-flow-output-v193':
        evidence_policy(execution, 'business-flow-v193')
        raise ValueError('递归因果规划仅允许独立阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-business-flow-output-v190':
        evidence_policy(execution, 'business-flow-v190')
        raise ValueError('因果合同规划仅允许独立阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-business-flow-output-v187':
        evidence_policy(execution, 'business-flow-v187')
        raise ValueError('源驱动业务规划仅允许独立推理阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-business-flow-output-v185':
        evidence_policy(execution, 'business-flow-v185')
        raise ValueError('流程来源复核与修订仅允许其独立阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-business-flow-output-v184':
        evidence_policy(execution, 'business-flow-v184')
        raise ValueError('结构化流程计划仅允许其独立阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-business-flow-output-v183':
        evidence_policy(execution, 'business-flow-v183')
        raise ValueError('业务流程设计仅允许独立流程阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-business-flow-output-v182':
        evidence_policy(execution, 'business-flow-v182')
        raise ValueError('业务流程设计仅允许独立流程阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-scoped-design-output-v181':
        evidence_policy(execution, 'reduce-scope-v181')
        raise ValueError('单业务设计仅允许其独立阶段，不能进入整报告读取或采用流程')
    if declaration.get('schema') == 'report-indexed-design-output-v179':
        evidence_policy(execution, 'reduce-v179')
        raise ValueError('编号声明方案只有独立汇总阶段，不能进入原报告Map流程')
    if declaration.get('schema') == 'report-indexed-design-output-v178':
        evidence_policy(execution, 'reduce-v178')
        raise ValueError('编号声明方案只有独立汇总阶段，不能进入原报告Map流程')
    if declaration.get('schema') == 'report-saved-map-design-output-v176':
        evidence_policy(execution, 'reduce-v176')
        raise ValueError('已保存声明的独立方案只有汇总阶段，不能进入原报告Map流程')
    if declaration.get('schema') == 'report-evidence-output-v172':
        evidence_policy(execution, 'map-v172')
        evidence_policy(execution, 'reduce-v172')
        return CLAIM_ENUM_NATIVE_TRANSPORT
    if declaration.get('schema') == 'report-evidence-output-v168':
        evidence_policy(execution, 'map-v168')
        evidence_policy(execution, 'reduce-v168')
        return CLAIM_NATIVE_TRANSPORT
    if declaration.get('schema') == 'report-evidence-output-v5':
        evidence_policy(execution, 'map-v7')
        evidence_policy(execution, 'reduce-v5')
        return NATIVE_REPORT_TRANSPORT
    if declaration.get('schema') == 'report-evidence-output-v4':
        evidence_policy(execution, 'map-v6')
        evidence_policy(execution, 'reduce-v4')
        return REF_ARM_REPORT_TRANSPORT
    if declaration.get('schema') == 'report-evidence-output-v3':
        evidence_policy(execution, 'map-v5')
        evidence_policy(execution, 'reduce-v3')
        return STRUCTURED_REPORT_TRANSPORT
    if declaration.get('schema') == 'report-evidence-output-v2':
        evidence_policy(execution, 'map-v4')
        return STRICT_MAP_TRANSPORT
    if declaration.get('schema') != 'report-evidence-output-v1':
        raise ValueError('未登记的证据专用输出声明，未发送')
    return None


def evidence_options(execution, base_options, stage):
    """Copy old provider options, selecting only the registered stage output."""
    policy = evidence_policy(execution, stage)
    if stage == 'catalogue-allocation-v213':
        from .report_business_catalogue_allocation_native_v213 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'catalogue-v211':
        from .report_business_catalogue_native_v211 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v193':
        from .report_business_flow_native_v193 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v190':
        from .report_business_flow_native_v190 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v187':
        from .report_business_flow_native_v187 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v185':
        from .report_business_flow_native_v185 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v184':
        from .report_business_flow_native_v184 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v183':
        from .report_business_flow_native_v183 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'business-flow-v182':
        from .report_business_flow_native_v182 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'reduce-scope-v181':
        from .report_scoped_design_native_v181 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'reduce-v179':
        from .report_design_grouped_native_v179 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'reduce-v178':
        from .report_design_grouped_native_v178 import dispatch_options
        return dispatch_options(execution, base_options)
    if stage == 'reduce-v176':
        from .report_design_grouped_native_v176 import dispatch_options
        return dispatch_options(execution, base_options)
    if (type(base_options) is not dict or base_options.get('model') != execution['model']
            or type(base_options.get('max_tokens')) is not int
            or base_options['max_tokens'] != execution['max_tokens']):
        raise ValueError('证据调用选项与已登记模型或输出预算不一致，未发送')
    if execution['provider'] == 'deepseek' and base_options.get('thinking') != {'type': 'disabled'}:
        raise ValueError('证据调用选项没有保留明确关闭的思考模式，未发送')
    result = copy.deepcopy(base_options)
    result['max_tokens'] = policy['max_output_tokens']
    if stage in {'map-v7', 'reduce-v5', 'map-v168', 'reduce-v168', 'map-v172', 'reduce-v172'}:
        if set(base_options) != {'model', 'max_tokens', 'response_format'} or base_options['response_format'] != {'type': 'json_object'}:
            raise ValueError('原生结构化选项与登记预算不一致，未发送')
        return {'model': execution['model'], 'max_tokens': policy['max_output_tokens'],
            'enable_thinking': False, 'stream': False, 'n': 1}
    if stage in {'map-v4', 'map-v5', 'reduce-v3', 'map-v6', 'reduce-v4'}:
        result.pop('response_format', None)
        result['stream'] = False
    if execution['provider'] == 'qwen':
        result['enable_thinking'] = False
    return result


def _native_policy(execution, stage):
    declaration = execution.get('report_evidence_output')
    if (execution.get('provider') != 'qwen' or execution.get('model') != 'qwen3.8-flash'
            or execution.get('task') == 'embedding' or 'thinking_mode' in execution
            or type(execution.get('max_tokens')) is not int or not 128 <= execution['max_tokens'] <= 8192
            or type(declaration) is not dict
            or set(declaration) != {'schema', 'max_tokens', 'map_transport', 'reduce_transport', 'endpoint'}
            or declaration['schema'] != 'report-evidence-output-v5'
            or declaration['map_transport'] != NATIVE_REPORT_TRANSPORT
            or declaration['reduce_transport'] != NATIVE_REPORT_TRANSPORT
            or declaration['endpoint'] != NATIVE_ENDPOINT
            or type(declaration['max_tokens']) is not int or not 128 <= declaration['max_tokens'] <= 131072):
        raise ValueError('原生结构化读取须显式登记完整Qwen TokenPlan配置，未降级或发送')
    return {'version': 'cloud-evidence-2026-10-17/' + stage, 'stage': stage,
        'max_input_bytes': 900000, 'transport_timeout_seconds': 180 if stage == 'map-v7' else 600,
        'max_output_tokens': min(declaration['max_tokens'], 8192) if stage == 'map-v7' else declaration['max_tokens'],
        'max_images': 0, 'context_token_bound': 1000000,
        'full_report': True, 'long_context_verified': True}


def _claim_native_policy(execution, stage):
    declaration = execution.get('report_evidence_output')
    if (execution.get('provider') != 'qwen' or execution.get('model') != 'qwen3.8-max-0902'
            or execution.get('task') == 'embedding' or 'thinking_mode' in execution
            or type(execution.get('max_tokens')) is not int or not 128 <= execution['max_tokens'] <= 8192
            or type(declaration) is not dict
            or set(declaration) != {'schema', 'max_tokens', 'map_transport', 'reduce_transport', 'endpoint'}
            or declaration['schema'] != 'report-evidence-output-v168'
            or declaration['map_transport'] != CLAIM_NATIVE_TRANSPORT
            or declaration['reduce_transport'] != CLAIM_NATIVE_TRANSPORT
            or declaration['endpoint'] != CLAIM_NATIVE_ENDPOINT
            or type(declaration['max_tokens']) is not int or not 128 <= declaration['max_tokens'] <= 32768):
        raise ValueError('声明数组报告须登记完整公共Qwen原生配置，未回退或发送')
    return {'version': 'cloud-evidence-2026-10-21/' + stage, 'stage': stage,
        'max_input_bytes': 900000, 'transport_timeout_seconds': 300 if stage == 'map-v168' else 600,
        'max_output_tokens': declaration['max_tokens'], 'max_images': 0, 'context_token_bound': 1000000,
        'full_report': True, 'long_context_verified': True}
def _claim_enum_native_policy(execution, stage):
    declaration = execution.get('report_evidence_output')
    if (stage not in {'map-v172', 'reduce-v172'}
            or execution.get('provider') != 'qwen' or execution.get('model') != 'qwen3.8-max-0902'
            or execution.get('task') == 'embedding' or 'thinking_mode' in execution
            or type(execution.get('max_tokens')) is not int or not 128 <= execution['max_tokens'] <= 8192
            or type(declaration) is not dict
            or set(declaration) != {'schema', 'max_tokens', 'map_transport', 'reduce_transport', 'endpoint'}
            or declaration['schema'] != 'report-evidence-output-v172'
            or declaration['map_transport'] != CLAIM_ENUM_NATIVE_TRANSPORT
            or declaration['reduce_transport'] != CLAIM_ENUM_NATIVE_TRANSPORT
            or declaration['endpoint'] != CLAIM_NATIVE_ENDPOINT
            or type(declaration['max_tokens']) is not int or not 128 <= declaration['max_tokens'] <= 32768):
        raise ValueError('逐组引用枚举须登记独立公共Qwen原生配置，未回退或发送')
    return {'version': 'cloud-evidence-2026-10-22/' + stage, 'stage': stage,
        'max_input_bytes': 900000, 'transport_timeout_seconds': 300 if stage == 'map-v172' else 600,
        'max_output_tokens': declaration['max_tokens'], 'max_images': 0, 'context_token_bound': 1000000,
        'full_report': True, 'long_context_verified': True}
