"""Explicit recognition profiles; the run's original renderer is never replaced."""
import copy
from uuid import UUID

from sqlalchemy import select, tuple_

from .contracts import digest
from .models import MaterialOcrRequest as Request
from . import material_ocr as o

CONTRACT = 'material-ocr-recognition-v1'
AUTH_SCHEMA = 'material-ocr-recognition-authorization-v1'
UNIT_KEYS = {'recognition_engine', 'recognition_authorization'}
FUTURE_KEYS = {'future_recognition_engine', 'future_recognition_authorization', 'recognition_authorizations'}
BASE_TARGET_KEYS = {'actor_id','workspace_id','material_id','run_id','generation','epoch',
                    'source_sha256','engine_manifest_hash','unit_id','image_sha256'}


def identity(value):
    return type(value) is str and str(UUID(value)) == value


@o.integrity
def checked_engine(engine):
    from .material_ocr_runtime import recognition_profile_pairs
    expected = next(((identity, hashed) for identity, hashed in recognition_profile_pairs()
                     if isinstance(engine, dict) and engine.get('manifest_hash') == hashed), None)
    if (expected is None or engine.get('id') != expected[0]
            or not isinstance(engine.get('manifest'), dict)
            or engine.get('summary_hash') != digest(engine['manifest'])
            or engine['manifest'].get('manifest_hash', expected[1]) != expected[1]
            or engine['manifest'].get('id', expected[0]) != expected[0]):
        o.fail('所选识别引擎与固定低内存协议不一致，原稿保留')
    return engine


@o.integrity
def _authorization_claim(run, reference, engine_check=None):
    records = run.data['recognition_authorizations']
    if not isinstance(records, list): o.fail()
    selected = [a for a in records if a.get('authorization_hash') == reference]
    if len(selected) != 1: o.fail('低内存识别授权无法核对，原稿保留')
    auth = selected[0]
    fields = {'schema', 'workspace_id', 'material_id', 'run_id', 'generation', 'source_sha256',
              'renderer_manifest_hash', 'actor_id', 'request_id', 'request_hash', 'epoch',
              'prepared_at', 'rawless_unit_ids', 'engine', 'authorization_hash'}
    if (set(auth) != fields or auth['schema'] != AUTH_SCHEMA
            or auth['authorization_hash'] != digest({k:v for k,v in auth.items() if k != 'authorization_hash'})
            or any(auth[k] != v for k,v in {'workspace_id':run.workspace_id, 'material_id':run.material_id,
                'run_id':run.id, 'generation':run.generation, 'source_sha256':run.data['scope']['source_sha256'],
                'renderer_manifest_hash':run.data['engine']['manifest_hash']}.items())
            or not identity(auth['actor_id']) or not identity(auth['request_id'])
            or type(auth['epoch']) is not int or not 1 <= auth['epoch'] <= run.data['epoch']
            or type(auth['prepared_at']) is not int or not 0 <= auth['prepared_at'] <= run.data['prepared']
            or not isinstance(auth['rawless_unit_ids'], list)
            or len(auth['rawless_unit_ids']) > auth['prepared_at']
            or len(auth['rawless_unit_ids']) != len(set(auth['rawless_unit_ids']))
            or any(not identity(i) for i in auth['rawless_unit_ids'])):
        o.fail()
    (engine_check or checked_engine)(auth['engine'])
    return auth


@o.integrity
def _authorization_receipt(run, auth, saved):
    if (not saved or saved.action != 'resume_with_profile' or saved.state != 'saved'
            or saved.body_hash != auth['request_hash']
            or saved.body_hash != o._request_hash(saved.action, None, saved.data['body'])
            or saved.data.get('target') is not None
            or saved.data.get('record_hash') != digest({k:v for k,v in saved.data.items() if k != 'record_hash'})
            or saved.data.get('result', {}).get('recognition_authorization') != auth
            or saved.data['body'].get('request_id') != auth['request_id']
            or saved.data['body'].get('run_id') != run.id
            or saved.data['body'].get('engine_id') != auth['engine']['id']
            or saved.data['body'].get('engine_manifest_hash') != auth['engine']['manifest_hash']
            or type(saved.data['body'].get('expected_material_version')) is not int
            or type(saved.data['body'].get('expected_run_version')) is not int
            or saved.data['result'].get('run_id') != run.id
            or saved.data['result'].get('material_version') != saved.data['body']['expected_material_version'] + 1
            or saved.data['result'].get('run_version') != saved.data['body']['expected_run_version'] + 1):
        o.fail('原低内存恢复回执无法核对，原稿保留')
    return auth


@o.integrity
def authorization(db, run, reference):
    auth = _authorization_claim(run, reference)
    saved = db.scalar(select(Request).where(Request.workspace_id == run.workspace_id,
        Request.material_id == run.material_id, Request.actor_id == auth['actor_id'],
        Request.request_id == auth['request_id']))
    return _authorization_receipt(run, auth, saved)


def _check_run(run, lookup):
    present = FUTURE_KEYS.intersection(run.data)
    if not present: return
    if present != FUTURE_KEYS: o.fail()
    records = run.data['recognition_authorizations']
    if not isinstance(records, list) or not records: o.fail()
    refs = [a['authorization_hash'] for a in records]
    if len(refs) != len(set(refs)): o.fail()
    for ref in refs: lookup(ref)
    auth = lookup(run.data['future_recognition_authorization'])
    if run.data['future_recognition_engine'] != auth['engine']: o.fail()


@o.integrity
def check_run(db, run):
    return _check_run(run, lambda reference: authorization(db, run, reference))


def _effective_engine(run, unit, lookup, engine_check=None):
    present = UNIT_KEYS.intersection(unit.data)
    if not present:
        return run.data['engine']  # Never use the live future default for an existing page.
    if present != UNIT_KEYS: o.fail()
    engine = (engine_check or checked_engine)(unit.data['recognition_engine'])
    auth = lookup(unit.data['recognition_authorization'])
    if (engine != auth['engine'] or unit.run_id != run.id or unit.material_id != run.material_id
            or unit.workspace_id != run.workspace_id
            or (unit.unit_index <= auth['prepared_at'] and unit.id not in auth['rawless_unit_ids'])
            or (unit.unit_index > auth['prepared_at'] and unit.id in auth['rawless_unit_ids'])):
        o.fail('该页与原低内存恢复授权不一致，原稿保留')
    return engine


@o.integrity
def effective_engine(db, run, unit):
    return _effective_engine(run, unit, lambda reference: authorization(db, run, reference))


def _target_fields(run, unit, engine_check=None):
    present = UNIT_KEYS.intersection(unit.data)
    if not present: return {}
    if present != UNIT_KEYS: o.fail()
    (engine_check or checked_engine)(unit.data['recognition_engine'])
    ref = unit.data['recognition_authorization']
    if type(ref) is not str or len(ref) != 64 or any(c not in '0123456789abcdef' for c in ref): o.fail()
    return {'recognition_contract': CONTRACT,
            'recognition_engine_id': unit.data['recognition_engine']['id'],
            'recognition_engine_manifest_hash': unit.data['recognition_engine']['manifest_hash'],
            'recognition_authorization': unit.data['recognition_authorization']}


@o.integrity
def target_fields(run, unit):
    return _target_fields(run, unit)


def _check_target(run, unit, target, lookup, engine_check=None):
    _effective_engine(run, unit, lookup, engine_check)
    fields = _target_fields(run, unit, engine_check)
    names = {'recognition_contract', 'recognition_engine_id', 'recognition_engine_manifest_hash', 'recognition_authorization'}
    if {k:v for k,v in target.items() if k in names} != fields: o.fail()
    if fields:
        if (set(target) != BASE_TARGET_KEYS | names or target['unit_id'] != unit.id
                or target['image_sha256'] != unit.data['image_sha256']): o.fail()
        auth = lookup(fields['recognition_authorization'])
        if type(target.get('epoch')) is not int or not auth['epoch'] <= target['epoch'] <= run.data['epoch']: o.fail()


@o.integrity
def check_target(db, run, unit, target):
    return _check_target(run, unit, target, lambda reference: authorization(db, run, reference))


class _FreezeRecognition:
    """Private, one-freeze inventory, built only from this run's real rows.

    This is not an authority cache or a public OCR entry point. The caller first
    verifies the current material/run original, retains its normal transaction
    locks/snapshot, and discards this object on return. No caller-supplied row
    map or previously successful result can populate the inventory.
    """
    def __init__(self, db, p, run):
        from .typed_payload_codec_v131 import canonical_json
        if run.workspace_id != p.workspace_id: o.fail()
        self._db = db
        self._actor_id = p.user_id
        self._workspace_id = p.workspace_id
        self.run = run
        self._engines = set()
        self._canonical = canonical_json
        self._authorizations = {}
        present = FUTURE_KEYS.intersection(run.data)
        if not present: return
        if present != FUTURE_KEYS: o.fail()
        records = run.data['recognition_authorizations']
        if not isinstance(records, list) or not records: o.fail()
        refs = [a['authorization_hash'] for a in records]
        if len(refs) != len(set(refs)): o.fail()
        claims = [_authorization_claim(run, ref, self.engine) for ref in refs]
        keys = {(a['actor_id'], a['request_id']) for a in claims}
        rows = list(db.scalars(select(Request).where(Request.workspace_id == p.workspace_id,
            Request.material_id == run.material_id,
            tuple_(Request.actor_id, Request.request_id).in_(sorted(keys)))))
        by_key = {}
        for row in rows:
            key = (row.actor_id, row.request_id)
            if (row.workspace_id != p.workspace_id or row.material_id != run.material_id
                    or key not in keys or key in by_key): o.fail()
            by_key[key] = row
        if set(by_key) != keys: o.fail()
        for auth in claims:
            self._authorizations[auth['authorization_hash']] = _authorization_receipt(
                run, auth, by_key[(auth['actor_id'], auth['request_id'])])

    def engine(self, engine):
        key = self._canonical(engine)
        if key not in self._engines:
            checked_engine(engine)
            self._engines.add(key)
        return engine

    def authorization(self, reference):
        if reference not in self._authorizations: o.fail()
        return self._authorizations[reference]

    def check_run(self):
        return _check_run(self.run, self.authorization)

    def effective_engine(self, unit):
        return _effective_engine(self.run, unit, self.authorization, self.engine)

    def target_fields(self, unit):
        return _target_fields(self.run, unit, self.engine)

    def check_target(self, unit, target):
        return _check_target(self.run, unit, target, self.authorization, self.engine)


@o.integrity
def check_historical_target(db, run, unit, target):
    """Validate a frozen old job against its own choice, not a later unit choice."""
    names = {'recognition_contract', 'recognition_engine_id', 'recognition_engine_manifest_hash', 'recognition_authorization'}
    present = names.intersection(target)
    if not present: return
    if present != names or target['recognition_contract'] != CONTRACT: o.fail()
    if (set(target) != BASE_TARGET_KEYS | names or target['unit_id'] != unit.id
            or target['image_sha256'] != unit.data['image_sha256']): o.fail()
    auth = authorization(db, run, target['recognition_authorization'])
    if (target['recognition_engine_id'] != auth['engine']['id']
            or target['recognition_engine_manifest_hash'] != auth['engine']['manifest_hash']
            or type(target.get('epoch')) is not int or not auth['epoch'] <= target['epoch'] <= run.data['epoch']
            or (unit.unit_index <= auth['prepared_at'] and unit.id not in auth['rawless_unit_ids'])
            or (unit.unit_index > auth['prepared_at'] and unit.id in auth['rawless_unit_ids'])): o.fail()


@o.integrity
def initialize_unit(db, run, unit):
    """Only a genuinely future new page may inherit the persisted future choice."""
    if not FUTURE_KEYS.intersection(run.data): return
    check_run(db, run)
    auth = authorization(db, run, run.data['future_recognition_authorization'])
    if unit.unit_index <= auth['prepared_at'] or unit.data.get('raw_id'): o.fail()
    unit.data = {**unit.data, 'recognition_engine':copy.deepcopy(auth['engine']),
                 'recognition_authorization':auth['authorization_hash']}


@o.integrity
def make_authorization(p, material, run, body, engine, units):
    reason = body.get('reason')
    if type(reason) is not str or not reason.strip() or len(reason) > 2000 or '\x00' in reason: o.fail('请填写有效的恢复说明')
    reason.encode('utf-8')
    engine = copy.deepcopy(engine)
    if not engine.get('ready') or not isinstance(engine.get('manifest'), dict): o.fail('低内存识别条件未就绪')
    summary = digest(engine['manifest'])
    if 'summary_hash' in engine and engine['summary_hash'] != summary: o.fail()
    engine['summary_hash'] = summary; checked_engine(engine)
    if body.get('engine_id') != engine['id'] or body.get('engine_manifest_hash') != engine['manifest_hash']: o.fail('低内存引擎已变化，请重新核对')
    value = {'schema':AUTH_SCHEMA, 'workspace_id':p.workspace_id, 'material_id':material.id,
             'run_id':run.id, 'generation':run.generation, 'source_sha256':material.sha256,
             'renderer_manifest_hash':run.data['engine']['manifest_hash'], 'actor_id':p.user_id,
             'request_id':body['request_id'], 'request_hash':o._request_hash('resume_with_profile', None, body),
             'epoch':run.data['epoch'] + 1, 'prepared_at':run.data['prepared'],
             'rawless_unit_ids':[u.id for u in units if not u.data.get('raw_id')], 'engine':engine}
    return {**value, 'authorization_hash':digest(value)}
