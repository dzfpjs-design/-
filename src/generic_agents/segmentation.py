"""Checked local instance masks; immutable crop artifacts, no inferred geo/semantics."""
import base64,hashlib,io,math
from PIL import Image
from fastapi import HTTPException
from .evidence import save_evidence,evidence_bytes

def validate_result(result,backend,threshold,content):
    expected=hashlib.sha256(content).hexdigest() if content is not None else None
    if result.get('schema')!='local-segmentation-v1' or result.get('backend')!=backend or result.get('cloud_calls')!=0 or result.get('image_sha256')!=expected or result.get('threshold')!=threshold:
        raise ValueError('实例掩膜输出不属于本次原图与本地后端')
    w,h=result.get('width'),result.get('height')
    if not isinstance(w,int) or not isinstance(h,int) or isinstance(w,bool) or isinstance(h,bool) or w<=0 or h<=0 or w*h>16000000:raise ValueError('掩膜原图尺寸无效')
    if result.get('coordinates')!='decoded_original_pixels_xyxy' or result.get('crs') is not None or result.get('semantic_class')!='unknown':raise ValueError('掩膜不能推定地理参考或语义类别')
    masks=result.get('masks')
    if not isinstance(masks,list) or len(masks)>80:raise ValueError('实例掩膜数量超出配方限制')
    from .local_specialists import SAM_RECIPE
    params=SAM_RECIPE['parameters']
    def offsets(size):
        step=params['tile_size']-params['tile_overlap'];last=max(0,size-params['tile_size'])
        return sorted(set(list(range(0,last+1,step))+[last]))
    expected_tiles=[{'index':i,'bbox':box} for i,box in enumerate(
        [[x,y,min(x+params['tile_size'],w),min(y+params['tile_size'],h)] for y in offsets(h) for x in offsets(w)])]
    if result.get('tile_manifest')!=expected_tiles or result.get('area_unit')!='pixels':raise ValueError('影像窗口覆盖与固定配方不一致')
    discarded=result.get('discarded_proposals')
    if not isinstance(discarded,int) or isinstance(discarded,bool) or discarded<0 or result.get('candidate_limit_reached') is not (discarded>0):raise ValueError('候选截断状态无效')
    for index,mask in enumerate(masks):
        if not isinstance(mask,dict):raise ValueError('候选掩膜结构无效')
        if mask.get('candidate_index')!=index+1 or mask.get('semantic_class')!='unknown':raise ValueError('候选序号或语义声明无效')
        tiles=mask.get('source_tiles')
        if not isinstance(tiles,list) or not tiles or any(not isinstance(t,int) or isinstance(t,bool) or not 0<=t<len(expected_tiles) for t in tiles) or tiles!=sorted(set(tiles)):raise ValueError('候选窗口来源无效')
        merges=mask.get('merge_evidence')
        if not isinstance(merges,list) or len(merges)>159:raise ValueError('掩膜合并依据无效')
        for evidence in merges:
            try:
                intersection,left,right=[evidence[k] for k in ['intersection_pixels','left_area_pixels','right_area_pixels']]
                if any(not isinstance(v,int) or isinstance(v,bool) for v in [intersection,left,right]) or not 0<intersection<=min(left,right):raise ValueError()
                iou=intersection/(left+right-intersection)
                if not math.isfinite(evidence['iou']) or abs(evidence['iou']-iou)>1e-8 or iou<params['merge_iou']:raise ValueError()
            except (KeyError,TypeError,ValueError):raise ValueError('掩膜合并像素依据无效') from None
        box=mask.get('bbox');score=mask.get('score')
        if not isinstance(box,list) or len(box)!=4 or any(not isinstance(v,int) or isinstance(v,bool) for v in box) or not 0<=box[0]<box[2]<=w or not 0<=box[1]<box[3]<=h:raise ValueError('实例掩膜超出原图像素范围')
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or score<threshold:raise ValueError('掩膜质量分数无效')
        try:
            raw=base64.b64decode(mask['png_base64'],validate=True)
            if len(raw)>1024*1024:raise ValueError()
            with Image.open(io.BytesIO(raw)) as image:
                if image.format!='PNG' or image.mode!='RGBA' or image.size!=(box[2]-box[0],box[3]-box[1]):raise ValueError()
                counts=image.getchannel('A').histogram();area=sum(counts[1:])
                if not isinstance(mask.get('area_pixels'),int) or isinstance(mask.get('area_pixels'),bool):raise ValueError()
                if counts[0]+counts[160]!=image.width*image.height or area!=mask['area_pixels'] or area<=0:raise ValueError()
        except Exception:raise ValueError('掩膜PNG、范围或像素面积不一致') from None


def save_masks(db,p,result,original):
    checked={**result,'masks':[]}
    for mask in result['masks']:
        raw=base64.b64decode(mask['png_base64'],validate=True)
        row=save_evidence(db,p,raw,'image/png')
        meta={k:v for k,v in mask.items() if k!='png_base64'}
        evidence_bytes(db,p,row.id)  # Do not report saved when a pre-existing blob is corrupt.
        row.data={**row.data,'purpose':'specialist_instance_mask','original_evidence_id':original.id,'original_sha256':original.sha256,'bbox':meta['bbox']}
        checked['masks'].append({**meta,'mask_evidence_id':row.id,'mask_sha256':row.sha256})
    return checked


def mask_bytes(db,p,result,identity,original_id):
    item=next((m for m in result.get('masks',[]) if m.get('mask_evidence_id')==identity),None)
    if not item:raise HTTPException(404,'此候选掩膜不属于已保存结果')
    original,_=evidence_bytes(db,p,original_id);row,content=evidence_bytes(db,p,identity)
    if original.sha256!=result['image_sha256'] or row.sha256!=item['mask_sha256'] or row.data.get('original_evidence_id')!=original.id or row.data.get('original_sha256')!=original.sha256 or row.data.get('bbox')!=item['bbox']:
        raise HTTPException(409,'原图或掩膜版本不一致，请恢复原件后核查')
    return row,content
