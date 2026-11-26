"""Sanitize synthetic cProfile stats to a fixed function whitelist and numbers."""
from types import CodeType

_APP = {
    'same_goal_advice_setup_api_candidate_v2': {'read_context', '_enabled'},
    'same_goal_advice_context_v310': {'_released', '_approved_sources', '_model'},
    'same_goal_advice_store_v302': {'_reader', '_rows', '_fresh'},
    'same_goal_advice_read_session_v2': {'_check_loaded_state', '_check_pending', 'isolated'},
    'goal_evaluation_provider_v288': {'binding', '_head', '_record', '_q0', '_definition_graph', '_fresh'},
    'goal_evaluation_append_store_v297': {'_mature_head', '_mature_value', '_reader', '_task', '_fresh'},
    'goal_evaluation_query_v288': {'_reader', '_rows', '_fresh', '_query'},
    'goal_ack_origin_mqtt_v297': {'checked_origin', 'handles', '_row', '_rows', '_deliveries', '_legacy_receipts'},
    'mapped_acquisition_origin_v177': {'guard', '_guard', '_get', '_rows', '_origin', '_parent'},
    'original_acquisition_reception_v127': {'_context', 'context', '_row', '_links'},
    'instance_data_receipts': {'verify_release', '_verify_release', '_receipt', '_source_release', '_destination'},
    'mqtt_observations': {'checked_packet', 'guard', 'verify_request', 'reconcile'},
    'authored_workflow_store_v208': {'check_binding', 'verify_materials', '_pins', '_roles'},
    'workspace_api': {'saved_execution', '_task', '_reader'},
    'evidence': {'evidence_bytes', 'authorize_cloud'},
    'services': {'scoped', 'published', 'validate', 'as_dict'},
    'contracts': {'digest', 'canonical_json'},
    'typed_payload_orm_v131': {'process_result_value', 'column_expression', 'compare_values'},
    'typed_payload_codec_v131': {'decode_payload', '_decode_payload_uncached', '_verified_decode_json',
        '_parse', '_json_value', '_string', '_dump_validated', '_projection_parse', '_same_projection',
        'canonical_json', 'validate_owner', 'validate_marker'},
}
_LIB = {
    ('/json/decoder.py', 'decode'): 'json.decode',
    ('/json/decoder.py', 'raw_decode'): 'json.raw_decode',
    ('/json/encoder.py', 'encode'): 'json.encode',
    ('/json/encoder.py', 'iterencode'): 'json.iterencode',
    ('/json/__init__.py', 'loads'): 'json.loads',
    ('/json/__init__.py', 'dumps'): 'json.dumps',
    ('/sqlalchemy/engine/base.py', 'execute'): 'sqlalchemy.connection_execute',
    ('/sqlalchemy/orm/session.py', 'execute'): 'sqlalchemy.session_execute',
}
_NATIVE = {
    '<built-in method _json.scanstring>': 'json.scanstring',
    '<built-in method _json.make_scanner>': 'json.make_scanner',
    '<built-in method _json.encode_basestring_ascii>': 'json.encode_basestring_ascii',
    '<built-in method _hashlib.openssl_sha256>': 'hashlib.sha256',
    '<method \'encode\' of \'str\' objects>': 'native.string_encode',
    '<method \'items\' of \'dict\' objects>': 'native.dict_items',
    '<method \'get\' of \'dict\' objects>': 'native.dict_get',
    '<method \'add\' of \'set\' objects>': 'native.set_add',
    '<method \'remove\' of \'set\' objects>': 'native.set_remove',
    '<method \'is_finite\' of \'decimal.Decimal\' objects>': 'native.decimal_finite',
    '<built-in method builtins.all>': 'native.all',
    '<built-in method builtins.any>': 'native.any',
    '<built-in method builtins.id>': 'native.id',
    '<built-in method builtins.len>': 'native.len',
    '<built-in method builtins.isinstance>': 'native.isinstance',
    '<built-in method builtins.getattr>': 'native.getattr',
    '<built-in method builtins.hasattr>': 'native.hasattr',
    '<built-in method builtins.sorted>': 'native.sorted',
    '<method \'validate_python\' of \'pydantic_core._pydantic_core.SchemaValidator\' objects>': 'native.pydantic_validate',
}


def summarize(profile):
    result = {}
    other = {key: 0.0 for key in ('application', 'sqlalchemy', 'pydantic', 'psycopg', 'json', 'other_python', 'native')}
    for entry in profile.getstats():
        code = entry.code
        label = None
        if isinstance(code, CodeType):
            filename = code.co_filename.replace('\\', '/')
            for module, names in _APP.items():
                if filename.endswith('/src/generic_agents/' + module + '.py') and code.co_name in names:
                    label = module + '.' + code.co_name
                    break
            if label is None:
                for (suffix, function), name in _LIB.items():
                    if filename.endswith(suffix) and code.co_name == function:
                        label = name
                        break
        elif isinstance(code, str):
            label = _NATIVE.get(code)
        if label is None:
            if not isinstance(code, CodeType):
                category = 'native'
            elif '/src/generic_agents/' in filename:
                category = 'application'
            elif '/sqlalchemy/' in filename:
                category = 'sqlalchemy'
            elif '/pydantic/' in filename:
                category = 'pydantic'
            elif '/psycopg/' in filename:
                category = 'psycopg'
            elif '/json/' in filename:
                category = 'json'
            else:
                category = 'other_python'
            other[category] += entry.inlinetime
            continue
        item = result.setdefault(label, {'calls': 0, 'self_ns': 0, 'inclusive_ns': 0})
        item['calls'] += entry.callcount
        item['self_ns'] += round(entry.inlinetime * 1e9)
        item['inclusive_ns'] += round(entry.totaltime * 1e9)
    return {'functions': result, 'other_self_ns': {key: round(value * 1e9) for key, value in other.items()}}
