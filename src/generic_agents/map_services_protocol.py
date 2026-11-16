"""Bounded, read-only WMTS KVP and ArcGIS MapServer metadata.

Links advertised by remote catalogues are deliberately ignored.  Requests are
constructed only against the administrator-authorised endpoint.
"""
import json, math
from io import BytesIO
from defusedxml.ElementTree import iterparse, fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError
from .wms_protocol import bbox
from .wmts_geospatial import apply_matrix_limits, matrix_geometry, locate_point, locate_bounds

LIMIT=4*1024*1024

def _tag(e): return e.tag.rsplit('}',1)[-1]
def _children(e,name): return [x for x in e if _tag(x)==name]
def _text(e,name):
    nodes=_children(e,name)
    return (nodes[0].text or '').strip() if nodes else ''

def wmts_catalog(raw):
    if not raw or len(raw)>LIMIT: raise ValueError('WMTS 目录为空或超过大小限制')
    try:
        depth=-1;count=0
        for event,element in iterparse(BytesIO(raw),events=('start','end'),forbid_dtd=True,forbid_entities=True,forbid_external=True):
            if event=='start':
                depth+=1;count+=1
                if depth>32 or count>40000: raise ValueError('WMTS 目录层级或元素过多')
            else: element.clear();depth-=1
        root=fromstring(raw,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    except (ParseError,DefusedXmlException): raise ValueError('WMTS 目录 XML 无效或包含不允许的引用') from None
    if _tag(root)!='Capabilities' or root.get('version')!='1.0.0': raise ValueError('需要 WMTS 1.0.0 目录')
    content=next(iter(_children(root,'Contents')),None)
    if content is None: raise ValueError('WMTS 没有图层目录')
    sets={}
    for ts in _children(content,'TileMatrixSet'):
        name=_text(ts,'Identifier');crs=_text(ts,'SupportedCRS'); matrices=[]
        if not name or len(name)>200 or name in sets: raise ValueError('WMTS 矩阵集标识无效或重复')
        for tm in _children(ts,'TileMatrix'):
            ident=_text(tm,'Identifier')
            try:
                w,h=int(_text(tm,'MatrixWidth')),int(_text(tm,'MatrixHeight'))
                tw,th=int(_text(tm,'TileWidth')),int(_text(tm,'TileHeight'))
            except ValueError: raise ValueError('WMTS 瓦片矩阵参数无效') from None
            if not ident or len(ident)>200 or not (1<=w<=100000000 and 1<=h<=100000000 and 1<=tw<=1024 and 1<=th<=1024): raise ValueError('WMTS 瓦片矩阵超出支持范围')
            matrix={'id':ident,'width':w,'height':h,'tile_width':tw,'tile_height':th}
            geometry=matrix_geometry(tm,crs)
            if geometry is not None:matrix['geometry']=geometry
            matrices.append(matrix)
        if len(matrices)>100 or len({m['id'] for m in matrices})!=len(matrices): raise ValueError('WMTS 矩阵过多或名称重复')
        sets[name]={'id':name,'crs':crs[:200],'matrices':matrices}
    layers=[];seen=set()
    for layer in _children(content,'Layer'):
        ident=_text(layer,'Identifier');formats=[(x.text or '').strip().lower() for x in _children(layer,'Format')]
        if not ident or len(ident)>200 or ident in seen: raise ValueError('WMTS 图层标识无效或重复')
        seen.add(ident)
        if 'image/png' not in formats or _children(layer,'Dimension'): continue
        styles=[]
        for style in _children(layer,'Style'):
            s=_text(style,'Identifier')
            if s and len(s)<=200: styles.append(s)
        links=[];link_names=set()
        for link in _children(layer,'TileMatrixSetLink'):
            name=_text(link,'TileMatrixSet')
            # Ambiguous duplicate links must never let an unrestricted entry
            # shadow a restricted entry for the same layer and matrix set.
            if name in link_names:raise ValueError('WMTS 图层重复声明同一矩阵集，无法安全判断读取范围')
            link_names.add(name)
            if name in sets and sets[name]['matrices']:
                # A restriction is per layer link, not a property of the
                # shared matrix set. Invalid restrictions fail closed.
                try:links.append(apply_matrix_limits(link,sets[name]))
                except ValueError:continue
        if links and styles: layers.append({'id':ident,'title':_text(layer,'Title')[:300] or ident,'styles':styles,'matrix_sets':links})
    if not layers or len(layers)>2000: raise ValueError('WMTS 未提供可读取的 PNG 图层和有效矩阵集')
    return {'protocol':'wmts','layers':layers,'ignored_catalog_links':True}

def arcgis_catalog(raw):
    if not raw or len(raw)>LIMIT: raise ValueError('ArcGIS 目录为空或超过大小限制')
    try: data=json.loads(raw)
    except (UnicodeError,ValueError): raise ValueError('ArcGIS 服务目录 JSON 无效') from None
    if not isinstance(data,dict) or 'error' in data or not isinstance(data.get('layers'),list): raise ValueError('ArcGIS MapServer 服务目录不可用')
    extent=data.get('fullExtent') or data.get('initialExtent') or {}; sr=extent.get('spatialReference') or {}; wkid=sr.get('latestWkid',sr.get('wkid'))
    if wkid in (102100,102113): wkid=3857
    if wkid not in (4326,3857): raise ValueError('当前仅支持 EPSG:4326 / 3857 的 ArcGIS 服务范围')
    try: bounds=bbox([extent[k] for k in ('xmin','ymin','xmax','ymax')],f'EPSG:{wkid}')
    except (KeyError,ValueError,TypeError): raise ValueError('ArcGIS 服务范围无效') from None
    layers=[];ids=set()
    for entry in data['layers']:
        if not isinstance(entry,dict) or type(entry.get('id')) is not int or entry['id']<0 or entry['id'] in ids: raise ValueError('ArcGIS 图层标识无效或重复')
        ids.add(entry['id']); title=entry.get('name')
        if not isinstance(title,str) or not title.strip(): title=f"图层 {entry['id']}"
        layers.append({'id':str(entry['id']),'title':title[:300]})
    if not layers or len(layers)>2000: raise ValueError('ArcGIS 未提供可选图层或图层过多')
    return {'protocol':'arcgis','layers':layers,'crs':f'EPSG:{wkid}','bounds':bounds,'ignored_catalog_links':True}

def arcgis_image_catalog(raw):
    if not raw or len(raw)>LIMIT:raise ValueError('ArcGIS 影像目录为空或超过大小限制')
    try:data=json.loads(raw)
    except (UnicodeError,ValueError):raise ValueError('ArcGIS 影像目录 JSON 无效') from None
    if not isinstance(data,dict) or 'error' in data:raise ValueError('ArcGIS ImageServer 服务目录不可用')
    extent=data.get('fullExtent') or data.get('initialExtent') or {};sr=extent.get('spatialReference') or {};wkid=sr.get('latestWkid',sr.get('wkid'))
    if wkid in (102100,102113):wkid=3857
    if wkid not in (4326,3857):raise ValueError('当前仅支持 EPSG:4326 / 3857 的影像服务')
    try:bounds=bbox([extent[k] for k in ('xmin','ymin','xmax','ymax')],f'EPSG:{wkid}')
    except (KeyError,ValueError,TypeError):raise ValueError('ArcGIS 影像服务范围无效') from None
    return {'protocol':'arcgis_image','layers':[{'id':'image','title':'服务影像'}],'crs':f'EPSG:{wkid}','bounds':bounds,'ignored_catalog_links':True}

def wmts_params(catalog,selection):
    layer=next((x for x in catalog['layers'] if x['id']==selection.get('layer')),None)
    ts=next((x for x in layer['matrix_sets'] if x['id']==selection.get('matrix_set')),None) if layer else None
    mat=next((x for x in ts['matrices'] if x['id']==selection.get('matrix')),None) if ts else None
    style=selection.get('style');row=selection.get('row');col=selection.get('col')
    if not mat or style not in layer['styles'] or type(row) is not int or type(col) is not int or not (0<=row<mat['height'] and 0<=col<mat['width']): raise ValueError('瓦片选择或行列号超出服务声明范围')
    limits=mat.get('limits')
    if limits is not None:
        try:
            if not (limits['min_row']<=row<=limits['max_row'] and limits['min_col']<=col<=limits['max_col']):raise ValueError('瓦片行列号超出图层允许读取的范围')
        except (TypeError,KeyError):raise ValueError('瓦片范围记录无效，请重新读取目录') from None
    return {'SERVICE':'WMTS','REQUEST':'GetTile','VERSION':'1.0.0','LAYER':layer['id'],'STYLE':style,'FORMAT':'image/png','TILEMATRIXSET':ts['id'],'TILEMATRIX':mat['id'],'TILEROW':str(row),'TILECOL':str(col)}


def wmts_locate(catalog, layer_id, matrix_set_id, matrix_id, *, longitude=None, latitude=None, bounds=None):
    """Locate a point or extent against the exact saved layer/matrix metadata."""
    layer=next((x for x in catalog.get('layers',[]) if x.get('id')==layer_id),None)
    matrix_set=next((x for x in layer.get('matrix_sets',[]) if x.get('id')==matrix_set_id),None) if layer else None
    matrix=next((x for x in matrix_set.get('matrices',[]) if x.get('id')==matrix_id),None) if matrix_set else None
    if not matrix:raise ValueError('所选图层或瓦片矩阵不在已保存的目录中')
    if bounds is not None:
        if longitude is not None or latitude is not None or not isinstance(bounds,(list,tuple)) or len(bounds)!=4:raise ValueError('请提供一组经纬度位置或一组范围')
        return {'kind':'bounds',**locate_bounds(matrix,*bounds)}
    if longitude is None or latitude is None:raise ValueError('请填写经纬度')
    return {'kind':'point',**locate_point(matrix,longitude,latitude)}

def arcgis_params(catalog,selection):
    layer=selection.get('layer')
    if layer not in [x['id'] for x in catalog['layers']]: raise ValueError('ArcGIS 图层不在已保存目录中')
    area=bbox(selection.get('bbox'),catalog['crs']);scope=catalog['bounds']
    if any(area[i]<scope[i] for i in (0,1)) or any(area[i]>scope[i] for i in (2,3)): raise ValueError('读取范围超出服务已声明范围')
    params={'f':'image','format':'png32','transparent':'true','bbox':','.join(map(str,area)), 'bboxSR':catalog['crs'].split(':')[1], 'imageSR':catalog['crs'].split(':')[1], 'size':'512,512'}
    if catalog.get('protocol')!='arcgis_image':params['layers']='show:'+layer
    return params
