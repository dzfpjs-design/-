"""Read-only published rules and shared source budget preview."""
from fastapi import APIRouter,Query,HTTPException
from .setup_api import P,DB
from .services import scoped
from .models import MediaChannel,MediaGateway
from .media_monitor import clock
from .video_read_boundary import authority,usage,available

router=APIRouter(prefix='/api/media/channels')

@router.get('/{identity}/read-boundary')
def preview(identity:str,p:P,db:DB,seconds:int=Query(6,ge=4,le=12)):
 channel=scoped(db,MediaChannel,identity,p);gateway=scoped(db,MediaGateway,channel.gateway_id,p);current=clock(db)
 try:budget=usage(db,channel,gateway,current);budget_error=None
 except HTTPException as error:budget=None;budget_error=str(error.detail)
 modes={}
 for mode in ['manual','automatic']:
  try:
   rules=authority(db,p,channel,gateway,mode)
   try:available(db,p,channel,gateway,mode,seconds,current);issue=None
   except HTTPException as error:issue=str(error.detail)
   modes[mode]={'rules':rules,'allowed_now':issue is None,'issue':issue}
  except HTTPException as error:modes[mode]={'rules':None,'allowed_now':False,'issue':str(error.detail)}
 return {'channel_id':identity,'checked_at':current.isoformat(),'usage':budget,'budget_error':budget_error,'modes':modes,
  'count_unit':'clip_request_or_continuous_connection','camera_time_window_required':False,
  'source_identity_limit':'按已配置网关地址与流路径核对；不同网络地址的物理设备身份仍需独立确认',
  'execution_rechecks':True,'cloud_calls_created':0}
