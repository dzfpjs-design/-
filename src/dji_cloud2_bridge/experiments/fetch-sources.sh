#!/bin/sh
# Download fixed public upstream source archives without overwriting user files.
set -eu
[ "$#" -eq 1 ] || { echo 'usage: fetch-sources.sh /absolute/empty-source-directory' >&2; exit 2; }
out=$1
case "$out" in /*) ;; *) echo 'Use an absolute path.' >&2; exit 2;; esac
[ -d "$out" ] || { echo 'Create the directory first.' >&2; exit 1; }
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(git -C "$script_dir" rev-parse --show-toplevel)
python3 - "$out" "$repo_dir" <<'PY'
import os,sys
out,repo=(os.path.realpath(p) for p in sys.argv[1:])
if os.path.commonpath([out,repo])==repo: raise SystemExit('Source archives must be stored outside the project repository.')
for name in ('dji-sdk.tar.gz','libwebsockets.tar.gz'):
    if os.path.lexists(os.path.join(out,name)):raise SystemExit('Source file already exists; refusing overwrite.')
PY
work=$(mktemp -d "$out/.cloud2-download.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
curl -fL --retry 2 'https://api.github.com/repos/dji-sdk/Cloud-API-2/tarball/fd679f89301295e3c2085a90dac1d5750cfa563d' -o "$work/dji-sdk.tar.gz"
curl -fL --retry 2 'https://api.github.com/repos/warmcat/libwebsockets/tarball/8964ce9db75a98e463dfafd2e89f2bc8a95ec6ed' -o "$work/libwebsockets.tar.gz"
python3 - "$work" "$out" <<'PY'
import hashlib,os,sys
items=[('dji-sdk.tar.gz','eabefe04f6d2e8a6bf3e57d2752399b5592c024539a571f44ca5cbf7274e2fd4'),
       ('libwebsockets.tar.gz','52904d4be01a659cef886bf42fa740743f50b45b896eed55d39e9137b25b7ea6')]
for name,expected in items:
    h=hashlib.sha256()
    with open(os.path.join(sys.argv[1],name),'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    if h.hexdigest()!=expected:raise SystemExit('Upstream checksum mismatch; source files were not saved.')
created=[]
try:
    for name,_ in items:
        dst=os.path.join(sys.argv[2],name)
        os.link(os.path.join(sys.argv[1],name),dst)
        created.append(dst)
except Exception:
    for dst in created:os.unlink(dst)
    raise
print('Verified source archives saved outside the project repository.')
PY
