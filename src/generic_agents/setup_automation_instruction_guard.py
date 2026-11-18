"""Keep typed/voice automation notes attached to the original business.

Business names are not immutable identifiers. Until identity-safe migration is
available, block destructive or ambiguous name transitions while notes exist.
"""
from fastapi import HTTPException


def names(value):
    rows=value.get('businesses', [])
    return {row if isinstance(row,str) else row.get('name') for row in rows if isinstance(row,(str,dict))}


def _protected_manual_rename(previous, proposed, intent, before, after, instructions):
    """Accept only an explicit, lossless rename of one current draft business.

    Old note keys remain reserved to prevent a later unrelated business from
    inheriting a historical instruction. Audio receipts remain immutable.
    """
    if not isinstance(intent, dict) or set(intent) != {'from', 'to'}:
        raise HTTPException(409, '业务改名范围无法核对，原说明与业务保留。')
    old, new = intent['from'], intent['to']
    if (not isinstance(old, str) or not isinstance(new, str) or not old or not new
            or old.strip() != old or new.strip() != new or old == new
            or old not in before or new in before or before - {old} != after - {new}
            or old in after or new not in after or not isinstance(instructions.get(old), str)
            or not instructions[old].strip() or new in instructions):
        raise HTTPException(409, '仅可单独改名一个已有业务，且新名称不能复用旧说明。')
    old_aliases, new_aliases = previous.get('businessAliases', {}), proposed.get('businessAliases', {})
    if (not isinstance(old_aliases, dict) or not isinstance(new_aliases, dict)
            or new in old_aliases or new in old_aliases.values()
            or new_aliases != {**old_aliases, new: old}):
        raise HTTPException(409, '业务名称与历史关联冲突，原说明保留。')
    for field in ('flows', 'setupNotes'):
        saved = previous.get(field, {})
        if not isinstance(saved, dict) or new in saved:
            raise HTTPException(409, '新名称已有历史业务资料，请使用其他名称。')
    automation = previous.get('automation', {})
    if not isinstance(automation, dict) or not isinstance(automation.get('policies', {}), dict) or new in automation.get('policies', {}):
        raise HTTPException(409, '新名称已有历史自动化规则，请使用其他名称。')
    if (proposed.get('flows', {}) != previous.get('flows', {})
            or proposed.get('setupNotes', {}) != previous.get('setupNotes', {})
            or proposed.get('automation', {}) != automation):
        raise HTTPException(409, '请先单独保存业务流程与自动化规则，再改名；原记录保留。')
    resources = previous.get('resources', [])
    gaps = previous.get('gaps', [])
    if not isinstance(resources, list) or not isinstance(gaps, list):
        raise HTTPException(409, '原资源或待补充事项无法核对，未改名。')
    expected_resources = []
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get('businesses', []), list):
            raise HTTPException(409, '原资源关联无法核对，未改名。')
        linked = resource.get('businesses', [])
        if any(not isinstance(name, str) for name in linked) or new in linked:
            raise HTTPException(409, '新名称与原资源关联冲突，未改名。')
        expected_resources.append({**resource, 'businesses': list(dict.fromkeys(new if name == old else name for name in linked))})
    expected_gaps = []
    for gap in gaps:
        if not isinstance(gap, dict) or ('business' in gap and not isinstance(gap['business'], str)) or gap.get('business') == new:
            raise HTTPException(409, '新名称与原待补充事项冲突，未改名。')
        expected_gaps.append({**gap, **({'business': new} if gap.get('business') == old else {})})
    if proposed.get('resources', []) != expected_resources or proposed.get('gaps', []) != expected_gaps:
        raise HTTPException(409, '资源或待补充事项与改名前不一致，原关联保留。')
    if (proposed.get('automationInstructions', {}) != {**instructions, new: instructions[old]}
            or proposed.get('voiceInputs', []) != previous.get('voiceInputs', [])):
        raise HTTPException(409, '自动化说明或原语音依据发生变化，请分开修改并重试。')
    return old, new


def protect_automation_instruction_transition(previous,proposed,*,restore=False,explicit_rename=None):
    instructions=previous.get('automationInstructions',{})
    if not isinstance(instructions,dict):
        raise HTTPException(409,'原自动化修改要求无法核对，未修改业务清单。')
    protected={key for key,value in instructions.items() if isinstance(key,str) and isinstance(value,str) and value.strip()}
    if restore and instructions != proposed.get('automationInstructions',{}):
        raise HTTPException(409,'历史版本中的自动化修改要求与当前稿不同，请先核对并保留现有要求，再单独调整。')
    before,after=names(previous),names(proposed)
    accepted = None
    if explicit_rename is not None:
        if restore:
            raise HTTPException(409, '恢复历史版本不能同时改名，请分开操作。')
        accepted = _protected_manual_rename(previous, proposed, explicit_rename, before, after, instructions)
    next_instructions=proposed.get('automationInstructions',{})
    if not isinstance(next_instructions,dict) or any(key in after and key not in next_instructions for key in protected):
        raise HTTPException(409,'已保存的自动化修改要求未包含在本次配置中，原记录保留。请先读取当前配置。')
    if ((before-after)&protected) - ({accepted[0]} if accepted else set()):
        raise HTTPException(409,'该业务有已保存的自动化修改要求。请先复制或清空该要求并保存，再改名或移除；语音依据和历史版本保留。')
    if (after-before)&protected:
        raise HTTPException(409,'该名称有旧的自动化修改要求，暂不能重新用于业务；请先核对历史记录，避免误关联。')
    old=previous.get('businessAliases',{}) or {};new=proposed.get('businessAliases',{}) or {}
    if isinstance(old,dict) and isinstance(new,dict):
        for key in set(old)|set(new):
            if old.get(key)!=new.get(key) and not (accepted and key==accepted[1] and new.get(key)==accepted[0]) and any(item in protected for item in (key,old.get(key),new.get(key)) if isinstance(item,str)):
                raise HTTPException(409,'业务名称关联涉及已保存的自动化修改要求，请先核对并清空原要求。')
