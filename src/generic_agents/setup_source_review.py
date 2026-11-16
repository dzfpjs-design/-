"""Source-grounded review over the existing durable typed-edit pipeline."""
import copy

from fastapi import HTTPException

SOURCE_REVIEW_PREFIX = '【对照原文复核】'
SOURCE_REVIEW_INSTRUCTION = (SOURCE_REVIEW_PREFIX +
    '请逐项对照本次选择的原文，核查当前已保存业务流程的触发、步骤、条件、顺序、负责角色和输出。'
    '原文有明确要求而当前字段与之不一致时，必须提出对应字段修改并逐项引用原文；'
    '原文未明确或无法判断的内容列为待确认，不要把空结果当作全面核实；'
    '不要猜测或扩展设备、账号和执行授权。')


def is_source_review(instruction):
    return isinstance(instruction, str) and instruction.startswith(SOURCE_REVIEW_PREFIX)


def check_source_review_request(body, capability):
    if not is_source_review(body.instruction):
        return
    if body.contract_version != 6 or not body.material_ids:
        raise HTTPException(422, '原文复核必须选择至少一份已解析的资料，并使用逐项修改模式')
    if capability.data.get('execution', {}).get('provider') != 'deepseek':
        raise HTTPException(409, '原文复核当前仅支持已批准的 DeepSeek 模型')


def remove_ungrounded_proposals(value):
    """Move uncited proposals into questions; the paid raw response is retained.

    Exact quotation and edit-scope checks still run after this filter. The
    semantic relevance of an actual quotation remains a human decision.
    """
    from .setup_edit_operations import EditResult
    result = EditResult.model_validate(value).model_dump()
    operations = result['operations']
    removed = {op['id'] for op in operations if not op['citations']}
    while True:
        dependent = {op['id'] for op in operations if any(x in removed for x in op['dependencies'])}
        new = removed | dependent
        if new == removed:
            break
        removed = new
    if not removed:
        return result
    questions = copy.deepcopy(result['questions'])
    for index, op in enumerate(operations, 1):
        if op['id'] in removed:
            detail = op.get('value') or op.get('name') or (op.get('step') or {}).get('name') or ''
            detail = '（' + detail[:80] + '）' if isinstance(detail, str) and detail.strip() else ''
            questions.append(f'第{index}项建议{detail}缺少原文引用或依赖未获原文支持，未纳入可应用修改，请人工核查。')
    if len(questions) > 30:
        raise ValueError('缺少原文依据的建议过多，已保留原答，请缩小复核范围')
    return {'operations': [op for op in operations if op['id'] not in removed], 'questions': questions}
