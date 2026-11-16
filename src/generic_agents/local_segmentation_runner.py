"""Offline pinned SAM2 RGB tile proposals. Only executed in specialist venv."""
import argparse,base64,hashlib,io,json,os,time
from pathlib import Path
from importlib.metadata import version
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTORCH_ENABLE_MPS_FALLBACK='0')

def windows(size,tile,overlap):
    return list(range(0,max(1,size-tile+1),tile-overlap))+([max(0,size-tile)] if size>tile and (size-tile)%(tile-overlap) else [])

def merge(proposals,params):
    import numpy as np
    kept=[]
    for proposal in sorted(proposals,key=lambda x:x['score'],reverse=True):
        proposal={**proposal,'merge_evidence':[]}
        restart=True
        while restart:
            restart=False
            for index,other in enumerate(kept):
                a,b=proposal['bbox'],other['bbox'];x0,y0=max(a[0],b[0]),max(a[1],b[1]);x1,y1=min(a[2],b[2]),min(a[3],b[3])
                if x0>=x1 or y0>=y1:continue
                intersection=int(np.logical_and(proposal['bitmap'][y0-a[1]:y1-a[1],x0-a[0]:x1-a[0]],other['bitmap'][y0-b[1]:y1-b[1],x0-b[0]:x1-b[0]]).sum())
                pa,oa=int(proposal['bitmap'].sum()),int(other['bitmap'].sum())
                iou=intersection/(pa+oa-intersection)
                if iou<params['merge_iou']:continue
                c=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])];merged=np.zeros((c[3]-c[1],c[2]-c[0]),dtype=bool)
                for row in [proposal,other]:
                    q=row['bbox'];merged[q[1]-c[1]:q[3]-c[1],q[0]-c[0]:q[2]-c[0]]|=row['bitmap']
                evidence={'intersection_pixels':intersection,'left_area_pixels':pa,'right_area_pixels':oa,'iou':iou}
                proposal.update(bbox=c,bitmap=merged,source_tiles=sorted(set(other['source_tiles']+proposal['source_tiles'])),
                    merge_evidence=proposal['merge_evidence']+other['merge_evidence']+[evidence])
                kept.pop(index);restart=True;break
        kept.append(proposal)
    return kept

def main():
    p=argparse.ArgumentParser();p.add_argument('--weights',required=True);p.add_argument('--image');p.add_argument('--backend',choices=['cpu','mps'],default='cpu');p.add_argument('--threshold',type=float,default=.5);a=p.parse_args()
    if not .01<=a.threshold<=.99:raise ValueError('模型分数门槛需在0.01到0.99之间')
    recipe=json.loads((Path(__file__).resolve().parents[2]/'model-packs/sam2-1-tiny.json').read_text());params=recipe['parameters']
    if {k:version(k) for k in recipe['environment']}!=recipe['environment']:raise ValueError('隔离环境与固定配方不一致')
    import torch,numpy as np
    from PIL import Image
    from transformers import Sam2Model,Sam2Processor,pipeline
    torch.set_num_threads(4)
    if a.backend=='mps' and not torch.backends.mps.is_available():raise ValueError('MPS is unavailable')
    start=time.monotonic();model=Sam2Model.from_pretrained(a.weights,local_files_only=True,use_safetensors=True,trust_remote_code=False).eval().to(a.backend)
    processor=Sam2Processor.from_pretrained(a.weights,local_files_only=True,trust_remote_code=False)
    generator=pipeline('mask-generation',model=model,image_processor=processor.image_processor,device=torch.device(a.backend));loaded=round((time.monotonic()-start)*1000)
    raw=Path(a.image).read_bytes() if a.image else None
    with Image.open(io.BytesIO(raw)) if raw else Image.new('RGB',(128,128),'white') as original:
        image=original.convert('RGB');orientation=original.getexif().get(274,1)
    if image.width*image.height>16000000:raise ValueError('影像超过1600万像素')
    proposals=[];tiles=[];discarded=0;start=time.monotonic()
    for y in windows(image.height,params['tile_size'],params['tile_overlap']):
        for x in windows(image.width,params['tile_size'],params['tile_overlap']):
            box=[x,y,min(x+params['tile_size'],image.width),min(y+params['tile_size'],image.height)];tile=image.crop(box);index=len(tiles);tiles.append({'index':index,'bbox':box})
            out=generator(tile,points_per_batch=params['points_per_batch'],points_per_crop=params['points_per_crop'],pred_iou_thresh=a.threshold,stability_score_thresh=params['stability_score_thresh'])
            for mask,score in zip(out['masks'],out['scores']):
                bitmap=np.asarray(mask,dtype=bool);yy,xx=np.nonzero(bitmap)
                if not len(xx):continue
                b=[int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1]
                proposals.append({'bbox':[b[0]+x,b[1]+y,b[2]+x,b[3]+y],'bitmap':bitmap[b[1]:b[3],b[0]:b[2]],'score':float(score),'source_tiles':[index]})
            if len(proposals)>160:
                discarded+=len(proposals)-160
                proposals=sorted(proposals,key=lambda p:p['score'],reverse=True)[:160]
    masks=merge(proposals,params);discarded+=max(0,len(masks)-params['max_candidates']);masks=masks[:params['max_candidates']];encoded=[]
    for i,row in enumerate(masks):
        bitmap=row.pop('bitmap');rgba=np.zeros((*bitmap.shape,4),dtype=np.uint8);rgba[bitmap]=[39,104,240,160];buf=io.BytesIO();Image.fromarray(rgba).save(buf,format='PNG')
        encoded.append({**row,'candidate_index':i+1,'area_pixels':int(bitmap.sum()),'semantic_class':'unknown','png_base64':base64.b64encode(buf.getvalue()).decode()})
    if a.backend=='mps':torch.mps.synchronize()
    result={'schema':'local-segmentation-v1','backend':a.backend,'threshold':a.threshold,'image_sha256':hashlib.sha256(raw).hexdigest() if raw else None,'width':image.width,'height':image.height,
        'coordinates':'decoded_original_pixels_xyxy','exif_orientation':orientation,'semantic_class':'unknown','crs':None,'area_unit':'pixels','tile_manifest':tiles,
        'masks':encoded,'candidate_limit_reached':discarded>0,'discarded_proposals':discarded,'model_load_ms':loaded,'inference_ms':round((time.monotonic()-start)*1000),'cloud_calls':0}
    output=json.dumps(result,ensure_ascii=False)
    if len(output.encode())>recipe['max_output_bytes']:raise ValueError('候选输出超出本地额度')
    print(output)
if __name__=='__main__':main()
