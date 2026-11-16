"""Disposable loopback FTP fixture. Run only with a disposable backend and test workspace."""
import argparse,json,os,tempfile,shutil
from pathlib import Path
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

parser=argparse.ArgumentParser()
parser.add_argument('--workspace-id',required=True)
parser.add_argument('--environment-file',required=True,help='write fixture environment JSON here')
parser.add_argument('--geotiff',help='optional synthetic TIFF to add to fixture directory')
args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='ga-ftp-fixture-') as directory:
    Path(directory,'测试业务资料.txt').write_text('巡护人员发现异常后进行现场核查并形成记录。\n',encoding='utf-8')
    if args.geotiff:shutil.copyfile(args.geotiff,Path(directory,'synthetic-imagery.tif'))
    authorizer=DummyAuthorizer();authorizer.add_user('fixture','local-fixture-only',directory,perm='elr')
    class Handler(FTPHandler):pass
    Handler.authorizer=authorizer;Handler.passive_ports=range(49000,49100)
    server=FTPServer(('127.0.0.1',0),Handler);port=server.socket.getsockname()[1]
    endpoint={'host':'127.0.0.1','port':port,'data_port_min':49000,'data_port_max':49099}
    grant={'name':'隔离测试资料','endpoint':endpoint,'root':'/','username_ref':'GA_FTP_USERNAME_FIXTURE','password_ref':'GA_FTP_PASSWORD_FIXTURE','server_chroot_confirmed':True,'plaintext_risk_confirmed':True}
    env={'GA_FTP_ALLOW_LOOPBACK_FIXTURE':'1','GA_FTP_USERNAME_FIXTURE':'fixture','GA_FTP_PASSWORD_FIXTURE':'local-fixture-only','GA_FTP_ALLOWED_ENDPOINTS':json.dumps([endpoint]),'GA_FTP_ACCESS_GRANTS':json.dumps({args.workspace_id:[grant]},ensure_ascii=False)}
    fd=os.open(args.environment_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(env,f,ensure_ascii=False)
    print('FTP fixture ready on 127.0.0.1:%s; fixture-only environment written to %s'%(port,args.environment_file),flush=True)
    try:server.serve_forever()
    finally:server.close_all()
