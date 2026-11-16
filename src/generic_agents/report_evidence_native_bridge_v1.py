"""Native-only capture/journal bridge; no provider or automatic retry.

The authenticated pipeline supplies frozen rows and verifies the CloudCall.
Read helpers never repair files. Explicit recovery first checks ALL original
captures, semantic projections and existing journal fragments; only then may
write_missing_journal fill absent files for that SAME reserved call. Existing
bytes, including partial/conflicting files, are never overwritten or removed.
"""
import copy
import hashlib
import os
from pathlib import Path

from .contracts import digest
from . import report_evidence_http_capture_v1 as capture


def root():
    from .config import settings
    return Path(settings().storage_dir).absolute() / 'report-evidence-native-http-v1'


def context(pipeline, row, key):
    stage = pipeline._stage(row, key)
    selected = pipeline._family(row.data['preview']) if hasattr(pipeline, '_family') else pipeline.v5
    if not selected.native(row.data['preview']) or not stage['reserved_call_id']:
        raise ValueError('Native capture requires its original reserved stage')
    spec, authorized = pipeline._spec(row, key), pipeline._authorized(row, key)
    policy = copy.deepcopy(spec['input_policy'])
    if policy.pop('single_batch', None) is not True:
        raise ValueError('Native capture requires single-response authorization')
    return {'binding': pipeline.journal_binding(row, key, stage['reserved_call_id']),
        'request_hash': authorized['request_hash'], 'endpoint': spec['endpoint'],
        'delivery': spec['delivery'], 'schema_proof_hash': digest(spec['schema_proof']),
        'profile_hash': digest(policy), 'token_bound': authorized['input_summary']['token_bound'],
        'max_bytes': capture.REDUCE_LIMIT if key == 'reduce' else capture.MAP_LIMIT}


def load_verified(pipeline, row, key, expected_hash=None):
    state = capture.load(root(), context(pipeline, row, key))
    if expected_hash is not None and state['capture_hash'] != expected_hash:
        raise ValueError('Native capture hash differs')
    return state


def response(pipeline, row, key):
    """Read a complete identity-encoded original body; never promote files."""
    ctx = context(pipeline, row, key)
    policy = copy.deepcopy(pipeline._spec(row, key)['input_policy']); policy.pop('single_batch')
    return response_context(ctx, row.data['preview']['provider'], row.data['preview']['model'], policy,
        (pipeline._family(row.data['preview']) if hasattr(pipeline, '_family') else pipeline.v5)._stage_name(key, row.data['preview']))


def response_context(ctx, provider, model, policy, stage):
    """Pure-scope FS read, usable before touching any callback ORM state."""
    from .gateway import _strict_evidence_usage
    from . import report_evidence_qwen_json_schema_v1 as tool
    if type(policy) is dict and 'native_schema_variant' in policy:
        from . import report_business_catalogue_allocation_native_v213 as allocation
        if (stage != allocation.STAGE or provider != 'qwen' or model != allocation.MODEL
                or policy['native_schema_variant'] != allocation.NATIVE_SCHEMA_VARIANT):
            raise ValueError('Batch allocation capture schema variant differs')
    if stage == 'catalogue-allocation-v213':
        from . import report_business_catalogue_allocation_native_v213 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or ctx['binding']['stage'] != 'reduce'
                or digest(policy) != ctx['profile_hash']):
            raise ValueError('Batch allocation capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-catalogue-allocation-native-v213':
        raise ValueError('Batch allocation capture requires its independent stage')
    elif stage == 'catalogue-v211':
        from . import report_business_catalogue_native_v211 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or ctx['binding']['stage'] != 'reduce'
                or digest(policy) != ctx['profile_hash']):
            raise ValueError('Business catalogue capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-business-catalogue-native-v211':
        raise ValueError('Business catalogue capture requires its independent stage')
    elif stage == 'business-flow-v193':
        from . import report_business_flow_native_v193 as tool
        if (tool.PROVIDER != 'deepseek' or provider != tool.PROVIDER
                or tool.MODEL != 'deepseek-v4-pro' or model != tool.MODEL
                or tool.DELIVERY != 'deepseek-public-business-flow-native-v193' or ctx['delivery'] != tool.DELIVERY
                or tool.ENDPOINT != 'https://api.deepseek.com/chat/completions' or ctx['endpoint'] != tool.ENDPOINT
                or ctx['binding']['stage'] != 'reduce' or digest(policy) != ctx['profile_hash']):
            raise ValueError('Nested causal business flow capture profile differs')
    elif ctx.get('delivery') == 'deepseek-public-business-flow-native-v193':
        raise ValueError('Nested causal business flow capture requires its independent stage')
    elif stage == 'business-flow-v190':
        from . import report_business_flow_native_v190 as tool
        if (tool.PROVIDER != 'deepseek' or provider != tool.PROVIDER
                or tool.MODEL != 'deepseek-v4-pro' or model != tool.MODEL
                or tool.DELIVERY != 'deepseek-public-business-flow-native-v190' or ctx['delivery'] != tool.DELIVERY
                or tool.ENDPOINT != 'https://api.deepseek.com/chat/completions' or ctx['endpoint'] != tool.ENDPOINT
                or ctx['binding']['stage'] != 'reduce' or digest(policy) != ctx['profile_hash']):
            raise ValueError('Causal business flow capture profile differs')
    elif ctx.get('delivery') == 'deepseek-public-business-flow-native-v190':
        raise ValueError('Causal business flow capture requires its independent stage')
    elif stage == 'business-flow-v187':
        from . import report_business_flow_native_v187 as tool
        if (tool.PROVIDER != 'deepseek' or provider != tool.PROVIDER
                or tool.MODEL != 'deepseek-v4-pro' or model != tool.MODEL
                or tool.DELIVERY != 'deepseek-public-business-flow-native-v187' or ctx['delivery'] != tool.DELIVERY
                or tool.ENDPOINT != 'https://api.deepseek.com/chat/completions' or ctx['endpoint'] != tool.ENDPOINT
                or ctx['binding']['stage'] != 'reduce' or digest(policy) != ctx['profile_hash']):
            raise ValueError('Reasoned business flow capture profile differs')
    elif ctx.get('delivery') == 'deepseek-public-business-flow-native-v187':
        raise ValueError('Reasoned business flow capture requires its independent stage')
    elif stage == 'business-flow-v185':
        from . import report_business_flow_native_v185 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Business flow revision capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-business-flow-native-v185':
        raise ValueError('Business flow revision capture requires its independent stage')
    elif stage == 'business-flow-v184':
        from . import report_business_flow_native_v184 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Structured flow plan capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-business-flow-native-v184':
        raise ValueError('Structured flow plan capture requires its independent stage')
    elif stage == 'business-flow-v183':
        from . import report_business_flow_native_v183 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Compatible business flow capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-business-flow-native-v183':
        raise ValueError('Compatible business flow capture requires its independent stage')
    elif stage == 'business-flow-v182':
        from . import report_business_flow_native_v182 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Business flow capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-business-flow-native-v182':
        raise ValueError('Business flow capture requires its independent stage')
    elif stage == 'reduce-scope-v181':
        from . import report_scoped_design_native_v181 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Scoped design capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-scoped-design-native-v181':
        raise ValueError('Scoped capture requires its independent scope stage')
    elif stage == 'reduce-v179':
        from . import report_design_grouped_native_v179 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Compatible indexed design capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-indexed-design-native-v179':
        raise ValueError('Compatible indexed capture requires its independent reduce stage')
    elif stage == 'reduce-v178':
        from . import report_design_grouped_native_v178 as tool
        if (provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY
                or ctx['endpoint'] != tool.ENDPOINT or digest(policy) != ctx['profile_hash']):
            raise ValueError('Indexed design capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-indexed-design-native-v178':
        raise ValueError('Indexed design capture requires its independent reduce stage')
    elif stage == 'reduce-v176':
        from . import report_design_grouped_native_v176 as tool
        if provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY or ctx['endpoint'] != tool.ENDPOINT:
            raise ValueError('Saved Map design capture profile differs')
    elif ctx.get('delivery') == 'qwen-public-saved-map-design-native-v176':
        raise ValueError('Saved Map design capture requires its independent reduce stage')
    elif stage in {'map-v172', 'reduce-v172'}:
        from . import report_claim_enum_native_v172 as tool
        if provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY or ctx['endpoint'] != tool.ENDPOINT:
            raise ValueError('Public enum claim capture profile differs')
    elif stage in {'map-v168', 'reduce-v168'}:
        from . import report_claim_native_v168 as tool
        if provider != 'qwen' or model != tool.MODEL or ctx['delivery'] != tool.DELIVERY or ctx['endpoint'] != tool.ENDPOINT:
            raise ValueError('Public claim capture profile differs')
    state = capture.load(root(), ctx)
    if not state['ready'] or not state['content_encoding_safe'] or state['response_complete'] is not True:
        raise ValueError('Native capture is incomplete or quarantined')
    body = capture.read_body(root(), ctx, state['capture_hash'])
    inspected = tool.inspect_response(body, state['http_status'], stage,
        response_complete=True, http_content_encoding=state['http_content_encoding'])
    usage = _strict_evidence_usage(inspected['usage'], present=inspected['metadata']['usage_present'])
    receipt = {**copy.deepcopy(inspected['metadata']), **usage, 'usage_available': not usage['usage_unavailable'],
        'provider': provider, 'model': model,
        'request_id': state['request_id'], 'call_id': ctx['binding']['reserved_call_id'],
        'evidence_profile': policy, 'response_complete': True,
        'http_content_encoding': state['http_content_encoding'], 'capture_hash': state['capture_hash']}
    return body.decode('utf-8', errors='surrogateescape'), receipt, state


def _expected(adapter, binding, wire, receipt):
    binding = adapter._binding(binding)
    if type(wire) is not str:
        raise ValueError('Native journal requires original lossless text')
    content = wire.encode('utf-8', errors='surrogatepass')
    status, raw_receipt = adapter._receipt(receipt)
    if status != 'saved' or receipt.get('call_id') != binding['reserved_call_id']:
        raise ValueError('Native receipt is not safely serializable')
    try:
        content.decode('utf-8', errors='strict'); utf8 = True
    except UnicodeError:
        utf8 = False
    reasons = adapter._reasons(len(content), utf8, status, True)
    envelope = {'schema': adapter._ENVELOPE, 'binding': binding, 'binding_hash': digest(binding),
        'wire_kind': 'text', 'encoding': 'utf-8-surrogatepass-v1', 'wire_bytes': len(content),
        'wire_sha256': hashlib.sha256(content).hexdigest(), 'receipt_status': status,
        'receipt_bytes': len(raw_receipt), 'receipt_sha256': hashlib.sha256(raw_receipt).hexdigest(),
        'consumable': not reasons, 'quarantine_reasons': reasons}
    raw_envelope = adapter._json_bytes(envelope)
    descriptor = {**envelope, 'schema': adapter._DESCRIPTOR, 'envelope_bytes': len(raw_envelope),
        'envelope_sha256': hashlib.sha256(raw_envelope).hexdigest()}
    descriptor['descriptor_hash'] = digest(descriptor)
    return binding, {'wire.bin': content, 'receipt.bin': raw_receipt, 'envelope.json': raw_envelope}, descriptor


def check_missing_journal(adapter, storage_root, binding, wire, receipt):
    """Read-only conflict preflight, returning a prospective exact descriptor."""
    binding, files, descriptor = _expected(adapter, binding, wire, receipt)
    try:
        with adapter._stage(storage_root, binding) as stage:
            names = set(os.listdir(stage))
            if names - {'slot.json', binding['reserved_call_id']}:
                raise ValueError('Native journal stage contains conflicting files')
            if 'slot.json' in names and adapter._read_file(stage, 'slot.json') != adapter._json_bytes(adapter._slot_value(binding)):
                raise ValueError('Native journal slot differs')
            if binding['reserved_call_id'] in names:
                directory = adapter._directory(stage, binding['reserved_call_id'])
                try:
                    existing = set(os.listdir(directory))
                    if existing - set(files):
                        raise ValueError('Native journal contains unknown files')
                    for name in existing:
                        if adapter._read_file(directory, name, max(len(files[name]), 8192)) != files[name]:
                            raise ValueError('Native journal fragment conflicts with original capture')
                finally:
                    os.close(directory)
    except FileNotFoundError:
        pass
    return descriptor


def write_missing_journal(adapter, storage_root, binding, wire, receipt):
    """Explicit native promotion only; same identity and absent files only."""
    expected = check_missing_journal(adapter, storage_root, binding, wire, receipt)
    binding, files, _ = _expected(adapter, binding, wire, receipt)
    with adapter._stage(storage_root, binding, True) as stage:
        slot = adapter._json_bytes(adapter._slot_value(binding))
        try:
            adapter._new_file(stage, 'slot.json', slot)
        except FileExistsError:
            if adapter._read_file(stage, 'slot.json') != slot:
                raise ValueError('Native journal slot conflict')
        directory = adapter._directory(stage, binding['reserved_call_id'], True)
        try:
            for name, raw in files.items():
                try:
                    adapter._new_file(directory, name, raw)
                except FileExistsError:
                    if adapter._read_file(directory, name, max(len(raw), 8192)) != raw:
                        raise ValueError('Native journal response conflict')
            actual = adapter._bundle(directory, binding)
            if actual != expected:
                raise ValueError('Native journal rebuilt descriptor differs')
            return actual
        finally:
            os.close(directory)


def promote_response(pipeline, row, key, wire, receipt):
    expected_wire, expected_receipt, _ = response(pipeline, row, key)
    if wire != expected_wire or receipt != expected_receipt:
        raise ValueError('Native callback differs from original capture')
    return write_missing_journal(pipeline._journal_for(row, key), pipeline._stage_journal_root(row, key),
        context(pipeline, row, key)['binding'], wire, receipt)
