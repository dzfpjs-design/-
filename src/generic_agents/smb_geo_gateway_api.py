from . import smb_gateway_api,smb_transport
from .remote_geo_gateway import make_router
router=make_router('smb',smb_transport,smb_gateway_api)
