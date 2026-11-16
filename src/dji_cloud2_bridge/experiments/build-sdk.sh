#!/bin/sh
# Offline, isolated experimental build. No credentials or production endpoints.
set -eu
if [ "$#" -ne 3 ]; then echo 'usage: build-sdk.sh /absolute/sdk.tar.gz /absolute/lws.tar.gz /absolute/output-directory' >&2; exit 2; fi
sdk_archive=$1
lws_archive=$2
output=$3
for path in "$sdk_archive" "$lws_archive" "$output"; do
  case "$path" in /*) ;; *) echo 'Use absolute paths.' >&2; exit 2;; esac
done
[ -f "$sdk_archive" ] && [ -f "$lws_archive" ] || { echo 'Missing source archives.' >&2; exit 1; }
[ -d "$output" ] || { echo 'Create the external output directory first.' >&2; exit 1; }
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(git -C "$script_dir" rev-parse --show-toplevel)
python3 - "$sdk_archive" "$lws_archive" "$output" "$repo_dir" <<'PY'
import hashlib, os, sys
pairs=[(sys.argv[1], 'eabefe04f6d2e8a6bf3e57d2752399b5592c024539a571f44ca5cbf7274e2fd4'),
       (sys.argv[2], '52904d4be01a659cef886bf42fa740743f50b45b896eed55d39e9137b25b7ea6')]
for name,expected in pairs:
    h=hashlib.sha256()
    with open(name,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    if h.hexdigest()!=expected: raise SystemExit('Source archive checksum mismatch; no build performed.')
out=os.path.realpath(sys.argv[3]); repo=os.path.realpath(sys.argv[4])
if os.path.commonpath([out,repo])==repo: raise SystemExit('Output must be outside the project repository.')
for filename in ('sdk-patched.jar','sdk-vulnerable-control.jar','patched-native-sha256.txt','jar-sha256.txt','build-packages.txt'):
    if os.path.lexists(os.path.join(out,filename)):raise SystemExit('Output already exists; choose an empty directory.')
PY
work=$(mktemp -d "${TMPDIR:-/tmp}/cloud2-build.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
mkdir -p "$work/extracted-sdk" "$work/extracted-lws" "$work/output"
python3 - "$sdk_archive" "$lws_archive" "$work" <<'PY'
import os,sys,tarfile
root=os.path.abspath(sys.argv[3])
for archive,directory,select_sdk in [(sys.argv[1],'extracted-sdk',True),(sys.argv[2],'extracted-lws',False)]:
    target=os.path.join(root,directory)
    with tarfile.open(archive,'r:gz') as tar:
        members=[m for m in tar.getmembers() if not select_sdk or '/sdk/' in m.name]
        for m in members:
            p=os.path.abspath(os.path.join(target,m.name))
            if not p.startswith(target+'/') or m.isdev() or m.isfifo():raise SystemExit('Unsafe archive member')
            if m.issym() or m.islnk():
                p=os.path.abspath(os.path.join(target,os.path.dirname(m.name),m.linkname))
                if not p.startswith(target+'/'):raise SystemExit('Unsafe archive link')
        tar.extractall(target,members=members)
PY
set -- "$work"/extracted-sdk/*/sdk
[ "$#" = 1 ] && [ -d "$1" ] || { echo 'SDK archive layout mismatch.' >&2; exit 1; }
mv "$1" "$work/sdk"
set -- "$work"/extracted-lws/*
[ "$#" = 1 ] && [ -d "$1" ] || { echo 'libwebsockets archive layout mismatch.' >&2; exit 1; }
mv "$1" "$work/lws"
image=${CLOUD2_BUILD_IMAGE:-cloud2-experiment-build:local}
jobs=${CLOUD2_BUILD_JOBS:-6}
case "$jobs" in 1|2|3|4|5|6) ;; *) echo 'CLOUD2_BUILD_JOBS must be 1 through 6.' >&2; exit 2;; esac
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 \
  -e CLOUD2_BUILD_JOBS="$jobs" -v "$work:/work" -v "$work/output:/output" \
  -v "$script_dir:/patches:ro" --entrypoint sh "$image" /patches/build-in-container.sh
# Avoid overwriting a file created while the build was running.
python3 - "$work/output" "$output" <<'PY'
import os,sys
for name in ('sdk-patched.jar','sdk-vulnerable-control.jar','patched-native-sha256.txt','jar-sha256.txt','build-packages.txt'):
    with open(os.path.join(sys.argv[1],name),'rb') as src:
        fd=os.open(os.path.join(sys.argv[2],name),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as dst:
            for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
print('Experimental artifacts saved outside repository; never use the control jar with real credentials.')
PY
