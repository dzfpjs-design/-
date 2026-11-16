from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import setup_source_review as review
from generic_agents import setup_understanding as understanding
from generic_agents.setup_edit_operations import EditResult, project
from tests.test_setup_edit_operations_pure import blocks, config, operation


def body(**override):
    data = {'instruction': review.SOURCE_REVIEW_INSTRUCTION, 'contract_version': 6,
            'material_ids': ['A']}
    data.update(override)
    return SimpleNamespace(**data)


def capability(provider):
    return SimpleNamespace(data={'execution': {'provider': provider}})


def test_review_requires_original_source_edit_mode_and_deepseek():
    review.check_source_review_request(body(), capability('deepseek'))
    for invalid, status in [(body(material_ids=[]), 422), (body(contract_version=5), 422),
                            (body(), 409)]:
        with pytest.raises(HTTPException) as error:
            review.check_source_review_request(invalid, capability('qwen' if status == 409 else 'deepseek'))
        assert error.value.status_code == status
    review.check_source_review_request(body(instruction='ordinary edit', material_ids=[]), capability('qwen'))


def test_review_retains_verified_quote_and_removes_unsubstantiated_edit():
    cfg=config(); _,handles=project(cfg,blocks(),scrub_text=lambda x:x)
    cited=operation(target='s_3',field='role',value='复核员');cited['citations']=[{'material_id':'A','block_id':'p1','quote':'只修改第三步负责人'}]
    unsupported=operation(target='s_1',field='role',value='新负责人');unsupported['id']='edit_2'
    req={'contract_version':6,'edit_handles':handles,'instruction':review.SOURCE_REVIEW_INSTRUCTION}
    result=understanding.validate_result({'operations':[cited,unsupported],'questions':[]},blocks(),cfg,EditResult,request=req)
    assert [x['id'] for x in result['operations']]==['edit_1']
    assert '新负责人' in result['questions'][0] and '未纳入' in result['questions'][0]
    ordinary=understanding.validate_result({'operations':[unsupported],'questions':[]},blocks(),cfg,EditResult,request={**req,'instruction':'普通修改'})
    assert len(ordinary['operations'])==1


def test_review_drops_transitive_dependencies_and_rejects_fake_quotes():
    first=operation(target='s_3',field='role',value='负责人')
    second=operation(target='s_2',field='role',value='审核员');second.update(id='edit_2',dependencies=['edit_1'],citations=[{'material_id':'A','block_id':'p1','quote':'负责人'}])
    result=review.remove_ungrounded_proposals({'operations':[first,second],'questions':[]})
    assert result['operations']==[] and len(result['questions'])==2
    cfg=config();_,handles=project(cfg,blocks(),scrub_text=lambda x:x)
    second['dependencies']=[];second['citations'][0]['quote']='原文中不存在的内容'
    with pytest.raises(ValueError,match='不匹配'):
        understanding.validate_result({'operations':[second],'questions':[]},blocks(),cfg,EditResult,request={'contract_version':6,'edit_handles':handles,'instruction':review.SOURCE_REVIEW_INSTRUCTION})


def test_review_marker_survives_privacy_projection():
    assert understanding.scrub(review.SOURCE_REVIEW_INSTRUCTION).startswith(review.SOURCE_REVIEW_PREFIX)
