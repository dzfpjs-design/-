from . import ftp_gateway_api,ftp_transport
from .remote_geo_gateway import make_router
router=make_router('ftp',ftp_transport,ftp_gateway_api)
