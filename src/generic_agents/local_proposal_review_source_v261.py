"""Owner-gated display of the original saved reply and its frozen authorized blocks."""
import copy,json
from fastapi import HTTPException
from .contracts import digest
from .models import SetupAnalysis
from .services import scoped
from . import local_proposal_review_drafts_v261 as drafts


def source_for_draft(db,p,draft_id):
    # Ownership is checked before any source content is loaded. A broken
    # original blocks this view, while the separate private-draft GET survives.
    own=drafts.get_draft(db,p,draft_id)
    expected=own['value']['source']
    source=scoped(db,SetupAnalysis,expected['analysis_id'],p)
    def need(ok):
        if not ok:raise HTTPException(409,'已保存的人稿仍保留，但原建议或授权原文快照暂不能核对')
    need(type(source.data) is dict)
    raw=source.data.get('raw_response');request=source.data.get('request',{})
    need(type(request) is dict)
    need(type(raw) is dict and 'value' in raw
         and digest({k:v for k,v in raw.items() if k!='hash'})==raw.get('hash')==expected['raw_hash'])
    need(type(request.get('contract_version')) is int
         and request['contract_version']==expected['contract_version'] and request['contract_version'] in {4,5})
    value=raw['value'];need(type(value) is dict and len(json.dumps(value,ensure_ascii=False).encode())<=2*1024*1024)
    need(type(request.get('materials')) is list and type(request.get('chunks')) is list)
    materials={m['id']:str(m.get('filename') or m.get('name') or '已上传资料') for m in request['materials'] if type(m) is dict and type(m.get('id')) is str}
    blocks=[];seen=set();ordinals={}
    for chunk in request.get('chunks',[]):
        need(type(chunk) is list)
        for block in chunk:
            need(type(block) is dict and all(type(block.get(k)) is str for k in ('material_id','block_id','text')))
            key=(block['material_id'],block['block_id']);need(key not in seen);seen.add(key)
            ordinal=ordinals.get(key[0],0)+1;ordinals[key[0]]=ordinal
            label=materials.get(key[0],'已上传资料').replace('\\','/').split('/')[-1][:200]
            blocks.append({'material_id':key[0],'block_id':key[1],'material_name':label,
                           'ordinal':ordinal,'text':block['text']})
    need(bool(blocks) and len(blocks)<=5000)
    items={};citations={}
    def collect(item,path):
        need(type(item) is dict)
        references=item.get('citations',[]);need(type(references) is list)
        for index,citation in enumerate(references):
            need(type(citation) is dict)
            citations[path+'/citations/'+str(index)]=digest(citation)
    for collection in ('businesses','objects','relationships'):
        if collection=='relationships' and request['contract_version']==4:continue
        rows=value.get(collection,[]);need(type(rows) is list)
        for index,item in enumerate(rows):
            path='/'+collection+'/'+str(index);need(type(item) is dict)
            items[path]=digest(item);collect(item,path)
            nested='steps' if collection=='businesses' else 'fields' if collection=='objects' else None
            if nested:
                children=item.get(nested,[]);need(type(children) is list)
                for child_index,child in enumerate(children):collect(child,path+'/'+nested+'/'+str(child_index))
    return {'analysis_id':source.id,'source_version':expected['source_version'],
        'contract_version':request['contract_version'],'raw_hash':raw['hash'],
        'raw_candidate':copy.deepcopy(value),'source_blocks':blocks,
        'item_hashes':items,'citation_hashes':citations}
