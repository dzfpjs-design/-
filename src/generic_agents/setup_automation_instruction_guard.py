"""Keep typed/voice automation notes attached to the original business.

Business names are not immutable identifiers. Until identity-safe migration is
available, block destructive or ambiguous name transitions while notes exist.
"""
from fastapi import HTTPException


def names(value):
    rows=value.get('businesses', [])
    return {row if isinstance(row,str) else row.get('name') for row in rows if isinstance(row,(str,dict))}


def protect_automation_instruction_transition(previous,proposed,*,restore=False):
    instructions=previous.get('automationInstructions',{})
    if not isinstance(instructions,dict):
        raise HTTPException(409,'原自动化修改要求无法核对，未修改业务清单。')
    protected={key for key,value in instructions.items() if isinstance(key,str) and isinstance(value,str) and value.strip()}
    if restore and instructions != proposed.get('automationInstructions',{}):
        raise HTTPException(409,'历史版本中的自动化修改要求与当前稿不同，请先核对并保留现有要求，再单独调整。')
    before,after=names(previous),names(proposed)
    next_instructions=proposed.get('automationInstructions',{})
    if not isinstance(next_instructions,dict) or any(key in after and key not in next_instructions for key in protected):
        raise HTTPException(409,'已保存的自动化修改要求未包含在本次配置中，原记录保留。请先读取当前配置。')
    if (before-after)&protected:
        raise HTTPException(409,'该业务有已保存的自动化修改要求。请先复制或清空该要求并保存，再改名或移除；语音依据和历史版本保留。')
    if (after-before)&protected:
        raise HTTPException(409,'该名称有旧的自动化修改要求，暂不能重新用于业务；请先核对历史记录，避免误关联。')
    old=previous.get('businessAliases',{}) or {};new=proposed.get('businessAliases',{}) or {}
    if isinstance(old,dict) and isinstance(new,dict):
        for key in set(old)|set(new):
            if old.get(key)!=new.get(key) and any(item in protected for item in (key,old.get(key),new.get(key)) if isinstance(item,str)):
                raise HTTPException(409,'业务名称关联涉及已保存的自动化修改要求，请先核对并清空原要求。')
