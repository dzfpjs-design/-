"""Engineering-only local HTTP WMS publisher of synthetic, non-field data.

Never used as a product fallback. No arbitrary files, outbound requests or secrets.
"""
import argparse,io,json,os
from datetime import UTC,datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
CHECKPOINT=ROOT/'.runtime/wms-reference.json'
RECORDS=ROOT/'.runtime/wms-reference-requests.jsonl'

def capabilities(version):
    tag='CRS' if version=='1.3.0' else 'SRS'
    root='WMS_Capabilities' if version=='1.3.0' else 'WMT_MS_Capabilities'
    bounds='minx="20" miny="100" maxx="30" maxy="120"' if version=='1.3.0' else 'minx="100" miny="20" maxx="120" maxy="30"'
    return f'''<?xml version="1.0"?><{root} version="{version}">
    <Service><Title>公开合成参考地图（非实测）</Title></Service>
    <Capability><Request><GetMap><Format>image/png</Format></GetMap></Request>
    <Layer><Title>合成参考图层</Title><{tag}>EPSG:4326</{tag}>
    <BoundingBox {tag}="EPSG:4326" {bounds}/><Style><Name>baseline</Name><Title>合成地图样式</Title></Style>
    <Layer><Name>public:render</Name><Title>公开合成渲染图（不是卫星原始波段）</Title></Layer>
    </Layer></Capability></{root}>'''.encode()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        parsed=urlsplit(self.path);q={k.upper():v for k,v in parse_qs(parsed.query).items()}
        if parsed.path!='/wms' or q.get('SERVICE')!=['WMS']:
            self.send_error(404);return
        version=q.get('VERSION',['1.3.0'])[0]
        if version not in {'1.1.1','1.3.0'}:self.send_error(400);return
        if q.get('REQUEST')==['GetCapabilities']:content=capabilities(version);mime='application/xml'
        elif q.get('REQUEST')==['GetMap']:
            try:
                width=int(q['WIDTH'][0]);height=int(q['HEIGHT'][0])
                if not 32<=width<=2048 or not 32<=height<=2048 or q['LAYERS']!=['public:render'] or q['FORMAT']!=['image/png']:raise ValueError()
            except (KeyError,ValueError):self.send_error(400);return
            image=Image.new('RGBA',(width,height),(46,98,69,255));draw=ImageDraw.Draw(image)
            draw.rectangle((width//3,height//4,width*2//3,height*3//4),fill=(170,138,77,255))
            draw.line((0,height//2,width,height//3),fill=(85,140,182,255),width=max(2,width//24))
            draw.rectangle((0,0,width-1,1),fill=(0,0,0,0))
            data=io.BytesIO();image.save(data,format='PNG');content=data.getvalue();mime='image/png'
        else:self.send_error(400);return
        # Only synthetic request parameters; never retain headers or credentials.
        with RECORDS.open('a') as target:target.write(json.dumps({'request':q,'at':datetime.now(UTC).isoformat()})+'\n')
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)));self.end_headers()
        try:self.wfile.write(content)
        except (BrokenPipeError,ConnectionResetError):pass

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    value={'pid':os.getpid(),'endpoint':f'http://127.0.0.1:{server.server_port}/wms','synthetic_only':True,'field_quality_verified':False}
    with os.fdopen(os.open(CHECKPOINT,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600),'w') as target:json.dump(value,target)
    RECORDS.touch(mode=0o600,exist_ok=True)
    try:server.serve_forever()
    finally:server.server_close()

if __name__=='__main__':main()
