"""Explicit instance-scoped originals for candidate-only local image workflows."""
from fastapi import HTTPException
from datetime import UTC
from .models import Evidence
from .services import scoped
from .evidence import evidence_bytes


def view(row):
    return {'id':row.id,'name':row.data['name'],'sha256':row.sha256,
        'media_type':row.media_type,'size_bytes':row.size_bytes,
        'width':row.data['width'],'height':row.data['height'],'created_at':row.created_at.astimezone(UTC).isoformat()}


def snapshots(db,p,instance_id,identities):
    if len(set(identities))!=len(identities):raise HTTPException(422,'不能重复选择同一图片')
    result=[]
    for identity in identities:
        row,_=evidence_bytes(db,p,identity)
        if row.data.get('purpose')!='workflow_input_image' or row.data.get('instance_id')!=instance_id:
            raise HTTPException(409,'图片不属于当前实例的任务原件，请重新选择')
        if row.media_type not in {'image/png','image/jpeg'} or row.data.get('modality')!='image':
            raise HTTPException(409,'任务图片需要经过原件格式核对')
        result.append(view(row))
    return result


def verify(db,p,task):
    saved=task.data.get('image_snapshots',[])
    current=snapshots(db,p,task.data['instance_id'],[x['id'] for x in saved])
    if current!=saved:raise HTTPException(409,'任务原图与保存摘要不一致，请恢复原件；处理记录保留')


def local_inputs(db,p,task,step,inputs):
    if step['execution']['adapter']!='local_specialist':return
    source=step['execution']['bindings']['evidence_ids']['source']
    if source=='task_image_ids':allowed=[x['id'] for x in task.data.get('image_snapshots',[])]
    else:
        value=task.data.get('perception_event') or {}
        allowed=[value['frame_evidence_id']] if value.get('frame_evidence_id') else []
    if len(allowed)!=1 or inputs.get('evidence_ids')!=allowed:
        raise HTTPException(409,'此本地检测节点需要一张明确选择的原图；请拆分任务或核对保存画面')
    row,_=evidence_bytes(db,p,allowed[0])
    if row.media_type not in {'image/png','image/jpeg'} or row.data.get('modality')!='image':
        raise HTTPException(409,'本地检测只能读取已核对的PNG或JPEG图片')


def local_result(db,p,step,inputs,output):
    if step['execution']['adapter']!='local_specialist':return
    row,_=evidence_bytes(db,p,inputs['evidence_ids'][0])
    detection=output.get('detection') or output.get('segmentation',{})
    if (detection.get('image_sha256')!=row.sha256 or
        (detection.get('width'),detection.get('height'))!=(row.data['width'],row.data['height']) or
        detection.get('cloud_calls')!=0):
        raise HTTPException(409,'本地模型结果与任务原图不一致，未接受为步骤结果')

    if detection.get('schema') == 'local-segmentation-v1':
        from .segmentation import mask_bytes
        for mask in detection['masks']:
            mask_bytes(db, p, detection, mask['mask_evidence_id'], row.id)


def verify_masks(db,p,task):
    from .segmentation import mask_bytes
    for receipt in task.data['workflow_runtime'].get('receipts',[]):
        result=receipt.get('output',{}).get('segmentation')
        if not receipt.get('candidate_only') or not result:continue
        ids=receipt.get('input_snapshot',{}).get('evidence_ids',[])
        if len(ids)!=1:raise HTTPException(409,'掩膜核查依据缺少固定原图')
        for mask in result['masks']:
            mask_bytes(db,p,result,mask['mask_evidence_id'],ids[0])
