"""Restricted conversion of ONE checksum-pinned official checkpoint, never inference.

NumPy scalar/dtype constructors describe numeric training metadata. The scoped
allowlist is applied only after the fixed artifact and its GLOBAL set are checked.
No arbitrary pickle loading, model factory, dynamic importer or cloud operation.
"""
import hashlib
import json
import os
import pickletools
import re
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'.runtime'/'changer-candidate'
SHA='da3f569306dadd1fac5b64abc2a3d484571cd0ecf6410e1f152275cfe49a3618'
SIZE=136880383
NAME='ChangerEx_r18-512x512_40k_levircd.pth'
GLOBALS={'_codecs encode','collections OrderedDict','numpy dtype',
         'numpy.core.multiarray scalar','torch FloatStorage','torch LongStorage',
         'torch._utils _rebuild_tensor_v2'}

def fingerprint(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def validate_original(path):
    if not path.is_file() or path.stat().st_size!=SIZE or fingerprint(path)!=SHA:
        raise ValueError('only the fixed official checkpoint may use this conversion profile')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if len(entries)>2000:raise ValueError('archive metadata exceeds bounds')
        meta=next(x for x in entries if x.filename.endswith('/data.pkl'))
        if meta.file_size>4*1024*1024:raise ValueError('pickle metadata exceeds bounds')
        detected=set();count=0
        for op,arg,pos in pickletools.genops(archive.read(meta)):
            count+=1
            if count>200000:raise ValueError('pickle opcode limit exceeded')
            if op.name=='GLOBAL':detected.add(arg)
            elif op.name in {'INST','OBJ','EXT1','EXT2','EXT4','STACK_GLOBAL','PERSID'}:
                raise ValueError('unsupported dynamic pickle opcode')
        if detected!=GLOBALS:raise ValueError('fixed checkpoint GLOBAL set changed')

def convert():
    source=ROOT/NAME;validate_original(source)
    # Trusted installed local runtime; no remote model code is imported.
    import numpy as np
    import torch
    from safetensors.torch import save_file,load_file
    if torch.__version__.split('+')[0]!='2.8.0':raise ValueError('torch conversion version changed')
    torch.set_num_threads(2)
    prior=torch.serialization.get_safe_globals()
    # NumPy dtype creates numeric descriptors; only float64 BUILD is allowed.
    # Other dtype subclasses and all other checkpoint globals remain rejected.
    allowed=[(np._core.multiarray.scalar,'numpy.core.multiarray.scalar'),np.dtype,np.dtypes.Float64DType]
    with torch.serialization.safe_globals(allowed):
        checkpoint=torch.load(source,weights_only=True,map_location='cpu')
    if torch.serialization.get_safe_globals()!=prior:raise ValueError('scoped safety profile leaked')
    if type(checkpoint) is not dict or not isinstance(checkpoint.get('state_dict'),dict):
        raise ValueError('checkpoint does not contain the expected state dictionary')
    state=checkpoint['state_dict'];values={};bytes_=0
    if not 1<=len(state)<=2000:raise ValueError('tensor count exceeds bounds')
    for key,value in state.items():
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*',key):raise ValueError('unexpected tensor key')
        if type(value) is not torch.Tensor or value.device.type!='cpu' or value.layout!=torch.strided or value.ndim>4:
            raise ValueError('only bounded dense CPU tensors are exported')
        if value.dtype not in {torch.float32,torch.int64}:raise ValueError('unexpected parameter dtype')
        bytes_+=value.numel()*value.element_size()
        if bytes_>SIZE or not bool(torch.isfinite(value).all()):raise ValueError('nonfinite or oversized tensor data')
        values[key]=value.detach().contiguous().clone()
    target=ROOT/'changer-r18.safetensors';temp=ROOT/'changer-r18.safetensors.part'
    save_file(values,temp,metadata={'source_sha256':SHA,'conversion':'fixed-official-changer-numeric-metadata-v1'})
    temp.chmod(0o600);restored=load_file(temp,device='cpu')
    if restored.keys()!=values.keys() or any(not torch.equal(values[k],restored[k]) for k in values):
        raise ValueError('safetensors readback differs from original state dictionary')
    os.replace(temp,target)
    proof={'original_sha256':SHA,'original_bytes':SIZE,'conversion_profile':'fixed-official-changer-numeric-metadata-v1',
           'torch_version':torch.__version__,'numpy_version':np.__version__,'weights_only':True,
           'scoped_numeric_globals_only':True,'global_allowlist_restored':True,'unsafe_fallback_used':False,
           'tensor_count':len(values),'tensor_bytes':bytes_,'all_tensors_exact_readback':True,
           'safetensors_file':target.name,'safetensors_sha256':fingerprint(target),'safetensors_bytes':target.stat().st_size,
           'model_inference_executed':False,'model_enabled':False}
    p=ROOT/'conversion.json';p.write_text(json.dumps(proof,indent=2)+'\n');p.chmod(0o600)
    return proof

if __name__=='__main__':
    try:
        proof=convert();print(json.dumps(proof))
    except Exception as error:
        proof={'conversion_failed':True,'error_type':type(error).__name__,'unsafe_fallback_used':False,'model_enabled':False}
        p=ROOT/'conversion-failed.json';p.write_text(json.dumps(proof,indent=2)+'\n');p.chmod(0o600)
        print(json.dumps(proof));raise SystemExit(1)
