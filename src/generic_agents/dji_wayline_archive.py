"""Bounded DJI KMZ/WPML inspection; structural inspection is not flight approval.

Only metadata is interpreted, and unknown fields remain in the original file.
This does not validate airspace, geofencing, weather, obstacle clearance, model
compatibility, or the full DJI schema. No device commands are issued here.
"""
from __future__ import annotations
from hashlib import md5,sha256
from io import BytesIO
import math,re,stat,zipfile
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from fastapi import HTTPException

MAX_ARCHIVE_BYTES=20*1024*1024
MAX_TOTAL_UNPACKED=50*1024*1024
MAX_XML_BYTES=5*1024*1024
MAX_ENTRIES=200
MAX_POINTS=3000
KML='http://www.opengis.net/kml/2.2'
WPML=re.compile(r'^http://www\.dji\.com/wpmz/1\.0\.[0-9]+$')
NOTICE='仅核对文件结构和部分航点，不代表航线合法、设备兼容或可以安全飞行；地形、障碍、空域、天气及起降条件仍须人工核验。'


def _bad(msg):raise HTTPException(422,msg)


def _safe_xml(raw):
    try:
        return ElementTree.fromstring(raw,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    except (DefusedXmlException,ElementTree.ParseError,ValueError,TypeError,RecursionError):
        _bad('航线 XML 无效或包含不允许的实体声明')


def _number(text,lo,hi,label):
    try:value=float(text)
    except (TypeError,ValueError,OverflowError):_bad(label+'不是有效数值')
    if not math.isfinite(value) or not lo<=value<=hi:_bad(label+'超出有效范围')
    return value


def _namespace(tag):
    return tag[1:].split('}',1)[0] if isinstance(tag,str) and tag.startswith('{') else ''


def _one(parent,tag,*,optional=False):
    values=parent.findall(tag)
    if len(values)>1 or (not values and not optional):_bad('航线 XML 缺少或重复必要字段')
    return values[0] if values else None


def inspect_kmz(content):
    """Return bounded summary of official WPML fields, without validating safety."""
    if not isinstance(content,bytes) or not 0<len(content)<=MAX_ARCHIVE_BYTES:
        raise HTTPException(413,'航线文件为空或超过 20 MB 上限')
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries=archive.infolist()
            if not 1<=len(entries)<=MAX_ENTRIES:_bad('航线压缩包包含过多文件')
            total=0;names={}
            for info in entries:
                name=info.filename
                parts=name.split('/')
                if (not name or name.startswith('/') or '\\' in name or '\x00' in name
                    or any(part in ('.','..') for part in parts) or re.match(r'^[A-Za-z]:',name)
                    or (info.external_attr>>16)&0o170000==stat.S_IFLNK
                    or info.flag_bits&1 or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)):
                    _bad('航线压缩包包含不安全路径、链接或不支持的文件')
                lowered=name.casefold()
                if lowered in names:_bad('航线压缩包包含重复文件名')
                names[lowered]=info
                total+=info.file_size
                if total>MAX_TOTAL_UNPACKED or (info.file_size>1024*1024 and info.file_size>200*max(info.compress_size,1)):
                    _bad('航线压缩包解压大小或压缩比例超过上限')
            required=('wpmz/template.kml','wpmz/waylines.wpml')
            if not all(x in names and names[x].filename==x for x in required):_bad('请提供包含 wpmz/template.kml 和 wpmz/waylines.wpml 的 DJI 航线包')
            if any(names[x].file_size>MAX_XML_BYTES for x in required):_bad('航线 XML 文件超过大小上限')
            template=_safe_xml(archive.read(names[required[0]]));waylines=_safe_xml(archive.read(names[required[1]]))
    except (zipfile.BadZipFile,zipfile.LargeZipFile,EOFError,OSError,RuntimeError,NotImplementedError):
        _bad('航线压缩包损坏或无法读取')
    if template.tag!='{'+KML+'}kml' or waylines.tag!='{'+KML+'}kml':_bad('航线 XML 不是 KML 格式')
    ns={_namespace(e.tag) for e in waylines.iter() if WPML.fullmatch(_namespace(e.tag))}
    if len(ns)!=1:_bad('航线执行文件的 WPML 命名空间缺失或不一致')
    wp='{'+next(iter(ns))+'}';km='{'+KML+'}'
    if _one(template,km+'Document',optional=True) is None or _one(waylines,km+'Document',optional=True) is None:
        _bad('航线 XML 缺少 Document 元素')
    if len(template.findall('.//'+wp+'missionConfig'))!=1:
        _bad('航线模板缺少或重复任务配置，或 WPML 命名空间不一致')
    folders=waylines.findall('.//'+km+'Folder')
    if not folders or len(folders)>100:_bad('航线执行文件缺少航线或包含过多航线')
    routes=[];total_points=0
    for folder in folders:
        points=folder.findall(km+'Placemark')
        if not points:continue
        route_id_el=_one(folder,wp+'waylineId',optional=True);mode_el=_one(folder,wp+'executeHeightMode',optional=True)
        route_id=(route_id_el.text or '').strip() if route_id_el is not None else None
        mode=(mode_el.text or '').strip() if mode_el is not None else None
        if route_id is not None and (not route_id.isdecimal() or len(route_id)>12):_bad('航线编号格式无效')
        if mode is not None and (len(mode)>80 or any(ord(c)<32 for c in mode)):_bad('高度模式格式无效')
        result=[];seen=set()
        for point in points:
            total_points+=1
            if total_points>MAX_POINTS:_bad('航点数量超过上限')
            index_el=_one(point,wp+'index');index=(index_el.text or '').strip()
            if not index.isdecimal() or len(index)>9 or int(index) in seen:_bad('航点编号无效或重复')
            seen.add(int(index))
            coordinates=_one(_one(point,km+'Point'),km+'coordinates');raw=(coordinates.text or '').strip().split(',')
            if len(raw) not in (2,3):_bad('航点坐标格式无效')
            longitude=_number(raw[0],-180,180,'航点经度');latitude=_number(raw[1],-90,90,'航点纬度')
            coordinate_height=_number(raw[2],-12000,100000,'航点坐标高度') if len(raw)==3 else None
            height_el=_one(point,wp+'executeHeight',optional=True)
            height=_number((height_el.text or '').strip(),-12000,100000,'执行高度') if height_el is not None else None
            result.append({'index':int(index),'longitude':longitude,'latitude':latitude,
                           'coordinate_height':coordinate_height,'execute_height':height})
        routes.append({'wayline_id':int(route_id) if route_id is not None else None,'height_mode':mode,'points':result})
    if not routes:_bad('未找到可识别的航点，测绘或特殊航线需使用专用解析器')
    return {'format':'dji-wpml-kmz','sha256':sha256(content).hexdigest(),
            'md5':md5(content,usedforsecurity=False).hexdigest(),'bytes':len(content),
            'wpml_namespace':next(iter(ns)),'route_count':len(routes),'point_count':total_points,
            'routes':routes,'notice':NOTICE,'flight_approved':False}
