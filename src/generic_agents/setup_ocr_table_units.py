"""确认过的字段元数据表格单位关系；不拼接、修改或补造引用原文。"""
import math
import re

HEADERS={
    'field':{'字段名称','字段名','字段','属性名称'},
    'type':{'数据类型','字段类型','类型'},
    'required':{'是否必填','必填要求','必填'},
    'unit':{'单位要求','单位'},
}
HASH=re.compile(r'^[a-f0-9]{64}$')


def same_row(a,b):
    x,y,w,h=a['box'];bx,by,bw,bh=b['box']
    return (max(h,bh)<=min(h,bh)*1.8
        and min(y+h,by+bh)-max(y,by)>=min(h,bh)*.75
        and abs(y+h/2-by-bh/2)<=max(h,bh)*.3)


def sources(snapshot):
    groups={}
    if snapshot.get('version')!='setup-source-snapshot-v1':return {}
    for material in snapshot['materials']:
        confirmation=material.get('confirmation')
        if not confirmation:continue
        for block in material['blocks']:
            if block.get('kind')!='ocr_confirmed':continue
            location=block['location'];ocr=location['ocr'];box=ocr['box']
            if (set(location) not in ({'ocr','page'},{'ocr','body_index'})
                or any(type(location[k]) is not int or location[k]<0 for k in location if k!='ocr')
                or set(ocr)!={'confirmation_id','confirmation_hash','run_id','unit_id','revision_id','revision_hash','image_sha256','region_id','box'}
                or any(not isinstance(ocr[k],str) or not ocr[k] for k in ocr if k!='box')
                or any(not HASH.fullmatch(ocr[k]) for k in ('confirmation_hash','revision_hash','image_sha256'))
                or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box)
                or box[0]<0 or box[1]<0 or box[2]<=0 or box[3]<=0
                or ocr['confirmation_id']!=confirmation['id'] or ocr['confirmation_hash']!=confirmation['hash']
                or ocr['run_id']!=confirmation['run_id']):return {}
            key=(material['id'],*(ocr[k] for k in ('confirmation_id','confirmation_hash','run_id','unit_id','revision_id','revision_hash','image_sha256')),
                 tuple((k,location[k]) for k in ('page','body_index') if k in location))
            groups.setdefault(key,[]).append({'material_id':material['id'],'id':block['id'],'text':block['text'].strip(),'box':box})
    return groups


def header(rows):
    found=[]
    for candidate in rows:
        if candidate['text'] not in HEADERS['field']:continue
        aligned=sorted([b for b in rows if same_row(candidate,b)],key=lambda b:b['box'][0])
        keys=[]
        for b in aligned:
            matches=[key for key,words in HEADERS.items() if b['text'] in words]
            keys.append(matches[0] if len(matches)==1 else None)
        if keys in (['field','type','unit'],['field','type','required','unit']):
            # 表头文本顺序不能替代列几何；仅容许较窄框宽2%的OCR边缘噪声。
            if any(a['box'][0]+a['box'][2]-b['box'][0]>min(a['box'][2],b['box'][2])*.02
                   or a['box'][0]+a['box'][2]/2>=b['box'][0]+b['box'][2]/2
                   for a,b in zip(aligned,aligned[1:])):return None
            found.append((aligned,keys))
    # 本片不自动选两个表格/重复表头之一，也不处理合并或多行单元格。
    return found[0] if len(found)==1 else None


def declared_unit(text,unit):
    # 单位表头下的独立单位值，或完整正向声明；测量/例子/否定/待核均不是配置。
    if (not isinstance(unit,str) or not unit.strip() or unit!=unit.strip()
        or unit.casefold() in {'n/a','na','unknown','none','null','-','—','--','?','？'}
        or any(word in unit for word in ('不','未','未知','待','尚','无','没有','禁止','示例','例如','举例','假设','可能','或者','可为','或'))):return False
    return text==unit or re.fullmatch(r'单位\s*(?:为|是|[:：=])\s*'+re.escape(unit),text) is not None


def unit_conflict(label,unit,text):
    if label not in text:return False
    tail=text.replace(label,'')
    for phrase in ('不必填','非必填','不转换单位','不换算','不做单位转换','不得换算','禁止换算'):
        tail=tail.replace(phrase,'')
    if '单位' not in tail and unit not in tail:return False
    return any(word in tail for word in ('未知','未','待','尚','不','没有','无单位','并非','禁止','不得'))


def table_unit_declared(field,snapshot,cloud_sources):
    try:
        groups=sources(snapshot)
        if not groups:return False
        matches=[]
        for rows in groups.values():
            fields=[b for b in rows if b['text']==field['label']]
            if not fields:continue
            if len(fields)!=1:return False
            h=header(rows)
            if h is None:return False
            heads,keys=h;label=fields[0]
            if label['box'][1]<=max(b['box'][1]+b['box'][3] for b in heads):return False
            if any(any(word in b['text'] for word in ('示例','例如','举例','假设')) for b in rows):return False
            # 同页存在针对该字段的未知/否定声明时，不用几何表格覆盖矛盾。
            if any(b is not label and unit_conflict(field['label'],field['unit'],b['text']) for b in rows):return False
            aligned=sorted([b for b in rows if same_row(label,b)],key=lambda b:b['box'][0])
            if len(aligned)!=len(heads) or aligned[0]!=label:return False
            if any(a['box'][0]+a['box'][2]>=b['box'][0] for a,b in zip(aligned,aligned[1:])):return False
            centers=[b['box'][0]+b['box'][2]/2 for b in heads]
            boundaries=[heads[0]['box'][0]-heads[0]['box'][2]/2,*[(a+b)/2 for a,b in zip(centers,centers[1:])],heads[-1]['box'][0]+heads[-1]['box'][2]*1.5]
            if any(not boundaries[n]<b['box'][0]+b['box'][2]/2<boundaries[n+1] for n,b in enumerate(aligned)):return False
            row=dict(zip(keys,aligned));unit=row['unit']
            if row['type']['text']!=field['kind'] or field['kind'] not in {'数值','整数'}:return False
            if 'required' in row and row['required']['text'] not in {'必填','非必填','不必填','选填','可选','是','否'}:return False
            if not declared_unit(unit['text'],field['unit']):return False
            for b in (label,unit):
                text=cloud_sources.get((b['material_id'],b['id']),[])
                if len(text)!=1 or text[0].strip()!=b['text'] or not any(
                    c['material_id']==b['material_id'] and c['block_id']==b['id'] and c['quote'].strip()==b['text'] for c in field['citations']):return False
            matches.append((label['material_id'],label['id'],unit['id']))
        return len(matches)==1
    except (KeyError,TypeError,ValueError,IndexError):
        return False
