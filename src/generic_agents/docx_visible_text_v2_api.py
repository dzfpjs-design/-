"""Read-only, versioned comparison for DOCX run-hyphen handling.

The stored source bytes remain the authority.  This route never modifies a
material, a historical report snapshot, a configuration or a cloud task.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .contracts import digest
from .db import session
from .document_parsers import ParseProblem
from .native_report_reading import read_docx, read_docx_visible_v2
from .report_full_reading_source import MaterialRef, _evidence, _material
from .security import Principal, authenticated, require

router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
MAX_CHANGED_PARAGRAPHS = 500
MAX_CHANGED_CHARACTERS = 300000


def _fail(code, message, status=409):
    raise HTTPException(status, {'code': code, 'message': message},
                        headers={'Cache-Control': 'private, no-store'})


def _only_added_hyphens(before, after):
    """Allow preserved source hyphens and boundary spaces newly exposed by them."""
    i = 0
    for char in after:
        if i < len(before) and char == before[i]:
            i += 1
        elif char in {'\u2011', '\u00ad'}:
            continue
        elif char.isspace() and (i == 0 or i == len(before)):
            continue
        else:
            return False
    return i == len(before)


def compare_docx_views(content):
    """Compare by normalized paragraph position, never by re-numbered block ID."""
    newer = read_docx_visible_v2(content)
    try:
        older = read_docx(content)
    except ParseProblem as error:
        if str(error) != '未找到可读取正文，不能生成待核业务草案':
            raise
        older = None
    old_by_paragraph = {b['location']['paragraph_index']: b for b in (older or {}).get('blocks', [])}
    new_by_paragraph = {b['location']['paragraph_index']: b for b in newer['blocks']}
    if len(old_by_paragraph) != len((older or {}).get('blocks', [])) or len(new_by_paragraph) != len(newer['blocks']):
        _fail('docx_visible_alignment', '原文位置无法唯一核对，未生成对照')
    changes = []
    chars = 0
    for paragraph in sorted(old_by_paragraph.keys() | new_by_paragraph.keys()):
        old, new = old_by_paragraph.get(paragraph), new_by_paragraph.get(paragraph)
        before, after = old['text'] if old else '', new['text'] if new else ''
        if before == after:
            continue
        # New parser must only add the two described characters.  This also
        # catches accidental drift in the legacy reader or source alignment.
        if not _only_added_hyphens(before, after):
            _fail('docx_visible_alignment', '新旧文字无法逐段对齐，未生成对照')
        chars += len(before) + len(after)
        if len(changes) >= MAX_CHANGED_PARAGRAPHS or chars > MAX_CHANGED_CHARACTERS:
            _fail('docx_visible_capacity', '需要核对的文字过多，未截断或生成不完整对照', 413)
        changes.append({'paragraph_index': paragraph,
                        'body_index': (new or old)['location']['body_index'],
                        'legacy_id': old['id'] if old else None,
                        'visible_id': new['id'] if new else None,
                        'before': before, 'after': after,
                        'nonbreaking_hyphens': after.count('\u2011') - before.count('\u2011'),
                        'optional_hyphens': after.count('\u00ad') - before.count('\u00ad')})
    return older, newer, changes


@router.get('/api/setup/materials/{identity}/docx-visible-text-v2')
def read_visible_docx(identity: UUID, response: Response, p: P, db: DB,
                      version: int = Query(..., ge=1), source_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
    require(p, 'admin', 'operator')
    from .map_access import require_config
    require_config(db, p)
    ref = MaterialRef(id=identity, version=version, hash=source_hash)
    material = _material(db, p, ref, lock=False, suffix='.docx')
    _, content = _evidence(db, p, material)
    try:
        older, newer, changes = compare_docx_views(content)
    except ParseProblem:
        _fail('docx_visible_parse', '原文无法安全生成字符对照；原件及已有读取结果保留', 422)
    value = {'schema': 'docx-visible-text-comparison-v2', 'actor_id': p.user_id,
             'workspace_id': p.workspace_id,
             'material': {'id': material.id, 'version': material.version, 'hash': material.sha256},
             'legacy_reading_hash': digest(older) if older is not None else None,
             'visible_reading_hash': digest(newer), 'visible_text_policy': newer['text_policy'],
             'paragraph_count': len(newer['blocks']), 'changes': changes,
             'legacy_preserved': True, 'saved_new_view': False, 'can_apply': False, 'can_send': False}
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return {**value, 'comparison_hash': digest(value)}
