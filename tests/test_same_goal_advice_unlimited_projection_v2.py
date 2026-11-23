"""Pure projections only; real source and policy authority stay in DB gates."""
import copy
import hashlib
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_projection_v307 as old
from generic_agents import same_goal_advice_unlimited_contracts_v2 as contracts
from generic_agents import same_goal_advice_unlimited_projection_v2 as v2
from tests.test_same_goal_advice_projection_v307 import scene, change_document_text


def prepare_v2(value):
    result = copy.deepcopy(value)
    prep = result['preparation']; prep['schema'] = contracts.PREPARATION
    budget = prep['budget']; budget['policy_ref'] = budget.pop('quota_policy_ref')
    budget.pop('max_cost_minor_units'); budget.pop('currency')
    budget.update(cost_policy='unlimited', allocation_basis='per_plan')
    prep['record_hash'] = c.record_hash(prep)
    return result


def project(value):
    return v2.derive(value['frame'], value['preparation'],
        evidence_parts=value['evidence_parts'], evidence_pins=value['evidence_pins'])


def test_rederives_all_policy_ids_as_private_and_does_not_mutate_local_input(scene):
    value = prepare_v2(scene)
    policy_id = value['preparation']['budget']['policy_ref']['id']
    value['frame']['question'] += ' 原政策编号 ' + policy_id
    before = copy.deepcopy(value)
    result = project(value)
    assert value == before and result == project(value)
    assert policy_id in result['aliases']
    assert policy_id not in old.c.base._json(result['cloud_request'])
    assert policy_id not in old.c.base._json(old.messages(result))


def test_projection_does_not_cross_accept_v1_v2_or_changed_evidence(scene):
    value = prepare_v2(scene)
    with pytest.raises(HTTPException):
        old.derive(value['frame'], value['preparation'],
            evidence_parts=value['evidence_parts'], evidence_pins=value['evidence_pins'])
    with pytest.raises(HTTPException):
        v2.derive(scene['frame'], scene['preparation'],
            evidence_parts=scene['evidence_parts'], evidence_pins=scene['evidence_pins'])
    altered = copy.deepcopy(value)
    altered['evidence_parts'][-1]['text'] += ' changed'
    with pytest.raises(HTTPException):
        project(altered)


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_messages_rejects_a_rehashed_projection_with_known_compact_id(scene, version):
    value = copy.deepcopy(scene) if version == 'v1' else prepare_v2(scene)
    result = (old.derive(value['frame'], value['preparation'], evidence_parts=value['evidence_parts'],
        evidence_pins=value['evidence_pins']) if version == 'v1' else project(value))
    result['cloud_request']['question'] += value['preparation']['scope']['task_id'].replace('-', '')
    result['projection_hash'] = old.digest({key: item for key, item in result.items() if key != 'projection_hash'})
    with pytest.raises(HTTPException) as error:
        old.messages(result)
    assert '编号与摘要' in str(error.value.detail)


@pytest.mark.parametrize('version', ['v1', 'v2'])
@pytest.mark.parametrize('location', ['question', 'evidence'])
def test_known_compact_internal_id_fails_closed_without_rewriting_original(scene, version, location):
    value = copy.deepcopy(scene) if version == 'v1' else prepare_v2(scene)
    compact = value['preparation']['scope']['task_id'].replace('-', '').upper()
    if location == 'question':
        value['frame']['question'] += ' 原任务编号 ' + compact
    else:
        # The fixture updates the actual text hash, evidence reference, pin and
        # source record together; this is an otherwise coherent source change.
        change_document_text(value, '合成文档中的编号 ' + compact)
    before = copy.deepcopy(value)
    with pytest.raises(HTTPException) as error:
        if version == 'v1':
            old.derive(value['frame'], value['preparation'],
                evidence_parts=value['evidence_parts'], evidence_pins=value['evidence_pins'])
        else:
            project(value)
    assert '编号与摘要' in str(error.value.detail) and '未发送' in str(error.value.detail)
    assert value == before


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_unknown_compact_text_is_not_claimed_as_anonymized(scene, version):
    value = copy.deepcopy(scene) if version == 'v1' else prepare_v2(scene)
    unknown = uuid4().hex
    value['frame']['question'] += ' 其他文本 ' + unknown
    result = (old.derive(value['frame'], value['preparation'], evidence_parts=value['evidence_parts'],
        evidence_pins=value['evidence_pins']) if version == 'v1' else project(value))
    assert unknown in result['cloud_request']['question']


def test_noncolliding_real_sha_is_preserved_but_colliding_tokens_fail_closed():
    identity = str(uuid4()); compact = identity.replace('-', '')
    digest_value = compact + 'b' * 32
    aliases = {identity: str(uuid4())}
    valid_sha = hashlib.sha256(b'ordinary synthetic evidence').hexdigest()
    assert compact not in valid_sha
    original = {'sha256': valid_sha, 'text': 'SHA256=' + valid_sha}
    before = copy.deepcopy(original)
    old._reject_known_compact_ids(original, aliases=aliases)
    assert original == before
    # A collision in either structured data or free text is ambiguous; the
    # request is stopped rather than modifying the original SHA or evidence.
    with pytest.raises(HTTPException):
        old._reject_known_compact_ids({'sha256': digest_value}, aliases=aliases)
    with pytest.raises(HTTPException):
        old._reject_known_compact_ids({'text': 'SHA256=' + digest_value}, aliases=aliases)
    # Adjacent hex does not let a bare known identifier slip through.
    with pytest.raises(HTTPException):
        old._reject_known_compact_ids({'text': 'a' + compact + 'f'}, aliases=aliases)


@pytest.mark.parametrize('version', ['v1', 'v2'])
@pytest.mark.parametrize('location', ['question', 'evidence'])
def test_real_sha256_prefix_chosen_as_request_uuid_fails_closed(scene, version, location):
    # No cryptographic preimage is fabricated: find a real SHA-256 whose first
    # 32 hex chars have RFC 4122 v4/variant bits, then choose that valid UUID.
    for index in range(4096):
        actual = hashlib.sha256(('synthetic-digest-' + str(index)).encode()).hexdigest()
        candidate = UUID(actual[:32])
        if candidate.version == 4:
            break
    else:
        pytest.fail('未找到符合 UUID 版本的合成摘要')
    value = copy.deepcopy(scene) if version == 'v1' else prepare_v2(scene)
    prep = value['preparation']
    prep['request']['request_id'] = str(candidate)
    prep['request_hash'] = old.digest(prep['request'])
    # This digest is explicitly present in a structured inventory hash field;
    # its appearance in free text must still be treated as ambiguous.
    prep['mature_head']['report_hash'] = actual
    value['frame']['source_record']['mature_head']['report_hash'] = actual
    prep['record_hash'] = c.record_hash(prep)
    value['frame']['source_record']['record_hash'] = c.record_hash(value['frame']['source_record'])
    if location == 'question':
        value['frame']['question'] += ' 资料 SHA256 ' + actual
    else:
        change_document_text(value, '合成资料 SHA256 ' + actual)
    before = copy.deepcopy(value)
    with pytest.raises(HTTPException) as error:
        if version == 'v1':
            old.derive(value['frame'], prep, evidence_parts=value['evidence_parts'], evidence_pins=value['evidence_pins'])
        else:
            project(value)
    assert '编号与摘要' in str(error.value.detail) and '未发送' in str(error.value.detail)
    assert value == before


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_real_structured_source_sha_with_known_id_prefix_is_never_sent(scene, version):
    for index in range(4096):
        original_text = 'synthetic-evidence-' + str(index)
        actual = hashlib.sha256(original_text.encode()).hexdigest()
        candidate = UUID(actual[:32])
        if candidate.version == 4:
            break
    else:
        pytest.fail('未找到符合 UUID 版本的合成摘要')
    value = copy.deepcopy(scene) if version == 'v1' else prepare_v2(scene)
    prep = value['preparation']
    prep['request']['request_id'] = str(candidate)
    prep['request_hash'] = old.digest(prep['request'])
    prep['record_hash'] = c.record_hash(prep)
    change_document_text(value, original_text)
    before = copy.deepcopy(value)
    assert value['frame']['source_record']['document']['evidence_ref']['sha256'] == actual
    with pytest.raises(HTTPException) as error:
        if version == 'v1':
            old.derive(value['frame'], prep, evidence_parts=value['evidence_parts'], evidence_pins=value['evidence_pins'])
        else:
            project(value)
    assert '编号与摘要' in str(error.value.detail) and '未发送' in str(error.value.detail)
    assert value == before
