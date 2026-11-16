"""Bounded WMS catalog/requests; all public coordinates use east/north order."""
import math
from io import BytesIO
from urllib.parse import urlsplit,urlunsplit,parse_qsl
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring,iterparse

CRS={'EPSG:4326','CRS:84','EPSG:3857'}
VERSIONS={'1.1.1','1.3.0'}
XML_LIMIT=4*1024*1024
XML_NODE_LIMIT=40000
LAYER_LIMIT=2000
MAP_LIMIT=16*1024*1024


def normalize_endpoint(value):
    if not isinstance(value,str) or len(value)>1800 or any(ord(c)<33 for c in value):raise ValueError('服务地址无效')
    parsed=urlsplit(value)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('服务地址需要HTTP/HTTPS，不允许内嵌凭证或片段')
    try:parsed.port
    except ValueError:raise ValueError('服务端口无效') from None
    pairs=parse_qsl(parsed.query,keep_blank_values=True)
    if len({k.lower() for k,_ in pairs})!=len(pairs) or any(
        k.lower() not in {'service','request','version'} or
        k.lower()=='service' and v.upper()!='WMS' or
        k.lower()=='request' and v.lower()!='getcapabilities' or
        k.lower()=='version' and v not in VERSIONS for k,v in pairs):
        raise ValueError('地址只可包含WMS目录参数，凭证请使用服务端密钥引用')
    return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'',''))


def bbox(value,crs):
    if crs not in CRS or not isinstance(value,(list,tuple)) or len(value)!=4 or any(type(v) not in {float,int} or not math.isfinite(v) for v in value):
        raise ValueError('坐标系或范围无效')
    x0,y0,x1,y1=map(float,value);maximum=20037508.35
    if x0>=x1 or y0>=y1 or (crs in {'EPSG:4326','CRS:84'} and not (-180<=x0<x1<=180 and -90<=y0<y1<=90)) or (crs=='EPSG:3857' and any(abs(v)>maximum for v in value)):
        raise ValueError('范围需要西小于东、南小于北，且在坐标系允许范围内')
    return [x0,y0,x1,y1]


def within(value,allowed):return all((value[i]>=allowed[i] if i<2 else value[i]<=allowed[i]) for i in range(4))
def axis(value,crs,version):return [value[1],value[0],value[3],value[2]] if version=='1.3.0' and crs=='EPSG:4326' else list(value)
def tag(element):return element.tag.rsplit('}',1)[-1]
def child(element,name):return next((e for e in element if tag(e)==name),None)
def text(element,name):
    value=child(element,name)
    return value.text.strip() if value is not None and value.text else ''


def catalog(content,version):
    if version not in VERSIONS or not content or len(content)>XML_LIMIT:raise ValueError('WMS目录版本或大小不支持')
    # Legacy 1.1.1 commonly declares an external DTD. Never resolve it or entities.
    try:
        # Bound the tree before constructing it. A compact, malicious 4 MiB
        # XML can contain far more elements than a legitimate catalog.
        nodes=0;depth=-1
        for event,element in iterparse(BytesIO(content),events=('start','end'),forbid_dtd=False,forbid_entities=True,forbid_external=True):
            if event=='start':
                nodes+=1;depth+=1
                if depth>32 or nodes>XML_NODE_LIMIT:raise ValueError('WMS目录层级或元素过多')
            else:
                element.clear();depth-=1
        root=fromstring(content,forbid_dtd=False,forbid_entities=True,forbid_external=True)
    except (ParseError,DefusedXmlException):raise ValueError('WMS目录XML无效，禁止实体及外部引用解析') from None
    if tag(root) not in {'WMS_Capabilities','WMT_MS_Capabilities'} or root.get('version')!=version:
        raise ValueError('目录不是选定版本的WMS，不能静默切换协议版本')
    capability=child(root,'Capability');request=child(capability,'Request') if capability is not None else None
    operation=child(request,'GetMap') if request is not None else None
    if operation is None or not any(tag(e)=='Format' and (e.text or '').strip()=='image/png' for e in operation):
        raise ValueError('此WMS未声明PNG地图读取能力')
    layers=[];names=set()
    def visit(layer,inherited_crs,inherited_boxes,inherited_styles):
        systems=set(inherited_crs);boxes=dict(inherited_boxes);styles=set(inherited_styles);geographic=None;explicit_boxes=set()
        for e in layer:
            kind=tag(e)
            if kind in {'CRS','SRS'}:systems.update((e.text or '').upper().split())
            elif kind=='BoundingBox':
                code=(e.get('CRS') or e.get('SRS') or '').upper()
                if code in CRS:
                    explicit_boxes.add(code)
                    try:boxes[code]=bbox(axis([float(e.get(k)) for k in ('minx','miny','maxx','maxy')],code,version),code)
                    except (ValueError,TypeError):raise ValueError('图层声明了无效的空间范围') from None
            elif kind in {'LatLonBoundingBox','EX_GeographicBoundingBox'}:
                try:
                    values=[float(e.get(k)) for k in ('minx','miny','maxx','maxy')] if kind=='LatLonBoundingBox' else [float(text(e,k)) for k in ('westBoundLongitude','southBoundLatitude','eastBoundLongitude','northBoundLatitude')]
                    geographic=bbox(values,'EPSG:4326')
                except (ValueError,TypeError):raise ValueError('图层的经纬度范围无效') from None
            elif kind=='Style':
                name=text(e,'Name')
                if name and len(name)<=200:styles.add(name)
        if geographic:
            for code in ('EPSG:4326','CRS:84'):
                if code not in explicit_boxes:boxes[code]=geographic
        name=text(layer,'Name')
        if name:
            if len(name)>200 or name in names or len(layers)>=LAYER_LIMIT:raise ValueError(f'图层名重复、过长或目录超过{LAYER_LIMIT}图层')
            names.add(name)
            usable={c:boxes[c] for c in sorted(systems&CRS) if c in boxes}
            layers.append({'name':name,'title':text(layer,'Title')[:300] or name,'bounds':usable,'styles':sorted(styles),
                           'time_dimension_declared':any(tag(e) in {'Dimension','Extent'} and e.get('name','').lower()=='time' for e in layer)})
        for e in layer:
            if tag(e)=='Layer':visit(e,systems,boxes,styles)
    if capability is not None:
        for e in capability:
            if tag(e)=='Layer':visit(e,set(),{},set())
    if not layers:raise ValueError('目录没有可命名图层')
    return {'version':version,'title':text(child(root,'Service'),'Title')[:300] if child(root,'Service') is not None else 'WMS地图服务',
            'layers':layers,'format':'image/png','catalog_links_ignored':True,'rendered_map':True,'source_capture_time_known':False}


def map_params(version,scope,window,width,height):
    window=bbox(window,scope['crs'])
    if not within(window,scope['bbox']):raise ValueError('读取范围超出管理员确认的图层范围')
    if type(width) is not int or type(height) is not int or not (32<=width<=2048 and 32<=height<=2048):raise ValueError('地图宽高需要32到2048像素')
    return {'SERVICE':'WMS','VERSION':version,'REQUEST':'GetMap','LAYERS':scope['layer'],'STYLES':scope['style'],
            'CRS' if version=='1.3.0' else 'SRS':scope['crs'],'BBOX':','.join(str(v) for v in axis(window,scope['crs'],version)),
            'WIDTH':str(width),'HEIGHT':str(height),'FORMAT':'image/png','TRANSPARENT':'TRUE'}
