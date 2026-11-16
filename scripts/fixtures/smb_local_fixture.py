"""Signed unencrypted SMB2 fixture, for loopback-only disposable backend tests."""
import argparse,json,os,socket,tempfile,threading,shutil
from pathlib import Path
from impacket import smbserver
from impacket.ntlm import compute_lmhash,compute_nthash
parser=argparse.ArgumentParser();parser.add_argument('--workspace-id',required=True);parser.add_argument('--environment-file',required=True);parser.add_argument('--geotiff',help='optional synthetic TIFF to add to fixture share');args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='ga-smb-fixture-') as directory:
    Path(directory,'测试资料.txt').write_text('巡护人员核查异常并形成记录。\n',encoding='utf-8')
    if args.geotiff:shutil.copyfile(args.geotiff,Path(directory,'synthetic-imagery.tif'))
    original=smbserver.findFirst2
    def with_inode(*args,**kwargs):
        rows,count,error=original(*args,**kwargs)
        for item in rows or []:
            if type(item).__name__=='SMBFindFileIdBothDirectoryInfo':
                name=item['FileName'].decode('utf-16-le');item['FileID']=os.stat(Path(directory)/name).st_ino
        return rows,count,error
    smbserver.findFirst2=with_inode
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=smbserver.SimpleSMBServer(listenAddress='127.0.0.1',listenPort=port);server.setSMB2Support(True);server.addShare('DATA',directory);server.addCredential('fixture',0,compute_lmhash('local-fixture-only'),compute_nthash('local-fixture-only'));server.setLogFile('/dev/null')
    ep={'host':'127.0.0.1','port':port,'share':'DATA'};grant={'name':'隔离测试共享','endpoint':ep,'username_ref':'GA_SMB_USERNAME_FIXTURE','password_ref':'GA_SMB_PASSWORD_FIXTURE','share_root_confirmed':True}
    env={'GA_SMB_ALLOW_LOOPBACK_FIXTURE':'1','GA_SMB_ALLOW_UNENCRYPTED_FIXTURE':'1','GA_SMB_USERNAME_FIXTURE':'fixture','GA_SMB_PASSWORD_FIXTURE':'local-fixture-only','GA_SMB_ALLOWED_ENDPOINTS':json.dumps([ep]),'GA_SMB_ACCESS_GRANTS':json.dumps({args.workspace_id:[grant]},ensure_ascii=False)}
    fd=os.open(args.environment_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(env,f,ensure_ascii=False)
    print('SMB2 signed fixture ready on 127.0.0.1:%s; environment written to %s'%(port,args.environment_file),flush=True)
    try:server.start()
    finally:server.stop()
