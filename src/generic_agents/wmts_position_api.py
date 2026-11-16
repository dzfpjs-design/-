"""Read-only coordinate lookup against a saved, versioned WMTS catalogue."""
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictFloat, StrictInt
from .contracts import Contract
from .map_services_api import _connection
from .map_services_protocol import wmts_locate
from .setup_api import DB, P

router=APIRouter(prefix='/api/map-services')


class Locate(Contract):
    version:StrictInt=Field(ge=1)
    layer:str=Field(min_length=1,max_length=200)
    matrix_set:str=Field(min_length=1,max_length=200)
    matrix:str=Field(min_length=1,max_length=200)
    longitude:StrictInt|StrictFloat|None=None
    latitude:StrictInt|StrictFloat|None=None
    bounds:tuple[StrictInt|StrictFloat,StrictInt|StrictFloat,StrictInt|StrictFloat,StrictInt|StrictFloat]|None=None


@router.post('/connections/{identity}/locate')
def locate(identity:str,body:Locate,p:P,db:DB):
    row=_connection(db,p,identity)
    if row.data.get('protocol')!='wmts':raise HTTPException(422,'按经纬度选择仅适用于 WMTS 服务')
    if row.version!=body.version:raise HTTPException(409,'连接或目录已有新版本，请刷新后重新选择')
    cat=row.data.get('catalog')
    if not isinstance(cat,dict) or cat.get('protocol')!='wmts':raise HTTPException(409,'请先读取 WMTS 图层目录')
    try:
        result=wmts_locate(cat,body.layer,body.matrix_set,body.matrix,longitude=body.longitude,latitude=body.latitude,bounds=body.bounds)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(422,str(exc) or '地图位置或矩阵无效') from None
    return {'connection_id':row.id,'version':row.version,'catalog_sha256':row.data.get('catalog_sha256'),
            'layer':body.layer,'matrix_set':body.matrix_set,'matrix':body.matrix,**result}
