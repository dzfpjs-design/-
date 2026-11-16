#!/usr/bin/env python3
"""Isolated synthetic integration checks; no DJI platform or real device."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
IMAGE = os.environ.get('CLOUD2_PROBE_IMAGE', 'cloud2-experiment-build:local')
STATUSES = (301, 302, 303, 307, 308)
MODES = ('crossport', 'crossip', 'samehost', 'samepath')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def docker(testdir, jar, shell, *, timeout=120):
    args = ['docker', 'run', '--rm', '--network', 'none', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges', '--pids-limit', '128', '-v', f'{testdir}:/test',
            '-v', f'{jar}:/sdk/flight-hub-sdk-patched.jar:ro', '--entrypoint', '/usr/bin/env', IMAGE,
            '-i', 'PATH=/opt/java/openjdk/bin:/usr/bin:/bin', 'HOME=/tmp', 'LANG=C.UTF-8', 'sh', '-c', shell]
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Synthetic test container failed; inspect locally without real credentials. ' + result.stderr[-500:])
    return result.stdout.strip()


def result_line(line):
    return dict(part.split('=', 1) for part in line.split() if '=' in part)


def check_integrity(artifacts):
    manifest = {}
    for line in (artifacts / 'jar-sha256.txt').read_text().splitlines():
        sha, name = line.split(None, 1); manifest[Path(name.strip()).name] = sha
    for name in ('sdk-patched.jar', 'sdk-vulnerable-control.jar'):
        if digest(artifacts / name) != manifest.get(name): raise RuntimeError('JAR build fingerprint mismatch')
    native_manifest = {}
    for line in (artifacts / 'patched-native-sha256.txt').read_text().splitlines():
        sha, name = line.split(None, 1); native_manifest[Path(name.strip()).name] = sha
    with zipfile.ZipFile(artifacts / 'sdk-patched.jar') as jar:
        for name in ('libflighthubsdk_jni.so', 'libwebsockets.so.12'):
            binary = jar.read('lib/linux-aarch64/' + name)
            if hashlib.sha256(binary).hexdigest() != native_manifest.get(name):
                raise RuntimeError('Bundled native build fingerprint mismatch')
    # The bundled libcurl is statically linked into JNI; the JNI digest binds it.


def setup(testdir, jar, source, mode, debug=False):
    s = source.read_text()
    find = 'cfg.setVerifySsl(true);'
    if s.count(find) != 1: raise RuntimeError('Bridge source differs from audited template')
    s = s.replace(find, 'cfg.setSslCaPath("/test/cert.pem"); ' + find)
    if debug:
        old='Logger.initialize(null, null, Logger.LogLevel.ERROR, false, false, 1, 1);'
        new='Logger.initialize("/test", "probe-java", Logger.LogLevel.DEBUG, true, false, 1048576, 2);'
        if s.count(old) != 1: raise RuntimeError('Bridge logger differs from audited template')
        s=s.replace(old,new)
        old='cfg.setLogLevel("off"); cfg.setLogFilePath("/dev/null");'
        if s.count(old) != 1: raise RuntimeError('Native logger differs from audited template')
        s=s.replace(old,'cfg.setLogLevel("debug"); cfg.setLogFilePath("/test/native.log");')
    (testdir / 'DjiCloud2Bridge.java').write_text(s)
    for file in ('RedirectVariants.java', 'HandshakeProbe.java'):
        (testdir / file).write_bytes((HERE / file).read_bytes())
    cn = '127.0.0.2' if mode == 'crossip' else 'localhost' if mode == 'samehost' else '127.0.0.1'
    san = 'DNS:localhost,IP:127.0.0.1,IP:127.0.0.2' if mode == 'samehost' else 'IP:127.0.0.1,IP:127.0.0.2'
    shell = f'''set -eu
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -keyout /test/server.key -out /test/cert.pem -subj /CN={cn} -addext 'subjectAltName={san}' >/dev/null 2>&1
openssl pkcs12 -export -out /test/server.p12 -inkey /test/server.key -in /test/cert.pem -passout pass:synthetic >/dev/null 2>&1
mkdir -p /test/out
javac --release 17 -cp /sdk/flight-hub-sdk-patched.jar -d /test/out /test/DjiCloud2Bridge.java
javac --release 17 -d /test /test/RedirectVariants.java /test/HandshakeProbe.java
'''
    docker(testdir, jar, shell)


def main():
    if len(sys.argv) != 3: raise RuntimeError('usage: run_probes.py /absolute/build-output-dir /absolute/DjiCloud2Bridge.java')
    artifacts, source = (Path(p).resolve(strict=True) for p in sys.argv[1:])
    check_integrity(artifacts)
    patched, control = (artifacts / 'sdk-patched.jar', artifacts / 'sdk-vulnerable-control.jar')
    for mode in MODES:
        with tempfile.TemporaryDirectory(prefix='cloud2-probe-') as tmp:
            td=Path(tmp); setup(td, patched, source, mode)
            for label, jar in (('control', control), ('patched', patched)):
                command=f'for s in 301 302 303 307 308; do java -cp /test RedirectVariants "$s" {mode}; done'
                lines=docker(td,jar,command).splitlines()
                if len(lines)!=len(STATUSES): raise RuntimeError('Missing redirect test result')
                for status,line in zip(STATUSES,lines):
                    row=result_line(line)
                    if (row.get('mode')!=mode or row.get('status')!=str(status) or row.get('timeout')!='false'
                        or int(row.get('source_requests','0'))<1 or int(row.get('source_ws','0'))<1):
                        raise RuntimeError(f'{label} {mode} {status}: test did not reach both sources')
                    if label=='patched':
                        if row.get('destination_requests')!='0' or row.get('incorrectly_reported_success')!='false':
                            raise RuntimeError(f'Patched {mode} {status}: redirected or reported false success')
                    elif int(row.get('destination_received_auth','0'))<1 or int(row.get('destination_ws_contains_auth','0'))<1:
                        raise RuntimeError(f'Control {mode} {status}: expected HTTP and WSS leaks not detected')
                print(f'{mode}: {label} 301/302/303/307/308 verified',flush=True)
    for debug in (False,True):
        with tempfile.TemporaryDirectory(prefix='cloud2-handshake-') as tmp:
            td=Path(tmp); setup(td, patched, source, 'crossport', debug)
            row=result_line(docker(td, patched, 'java -cp /test HandshakeProbe').splitlines()[-1])
            for key in ('http_seen','http_result','wss_request','wss_101_sent'):
                if row.get(key)!='true': raise RuntimeError(f'Handshake: {key} not proven')
            if debug and row.get('native_ws_established')!='true': raise RuntimeError('Native WSS establishment not observed')
            for key in ('secret_plain_logged','secret_encoded_logged','secret_in_stdout','timeout'):
                if row.get(key)!='false': raise RuntimeError(f'Handshake: {key} failed')
            print('Handshake and synthetic credential log check:', 'DEBUG' if debug else 'default', 'passed',flush=True)
    print('Synthetic regression passed; official simulator and production remain unverified.')


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        print('Synthetic regression failed:', str(e), file=sys.stderr); sys.exit(1)
