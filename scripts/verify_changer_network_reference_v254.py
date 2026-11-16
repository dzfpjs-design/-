#!/usr/bin/env python3
"""Independent, research-only Changer network reference comparison.

Uses pinned public transcriptions, selected upstream control flow and explicit
minimal compatibility. Never imports the product worker/factory, downloads,
executes a config, reads labels, or modifies inputs/weights/product recipes.
Only Root executes this script. Exit0 means tested reference-network allclose,
NOT official runtime equivalence, domain accuracy, licence or release approval.
"""
import argparse
import ast
import copy
import functools
import hashlib
import inspect
import io
import json
import os
from pathlib import Path,PurePosixPath
import platform
import stat
import sys
import tempfile
from datetime import datetime,timezone
from types import SimpleNamespace
from typing import Dict,Optional,Tuple,Union
import warnings
from abc import ABCMeta,abstractmethod

MANIFEST_SHA='8823d083086e9711c576e47808e379211a5d9aa816b6d71d8e0f7c326a986bcf'
LOCAL_SHA='1cbcc80a70015112f42dc4da66b4995dda5e788f015ce7b06ad79047a37a846b'
WEIGHT_SHA='507932767138639cc64079bcfd5df2861aeeef9c26e924b799e2d4de92bce2a6'
WEIGHT_BYTES=45624736
CONFIG_SHA='0f5ed15817b075fb005c4542a489e6885899d6d36874a8c1f7d1aeff60845081'
SEED=2541031
ATOL,RTOL=1e-6,1e-5
MAX_REPORT=16*1024*1024
MAX_PIXELS=1_500_000
LOCAL_NAMES=('ResidualBlock','exchange','InteractionResNet18','ConvBN','DualAlignment','PointwiseResidual','ChangeHead','ChangerExR18')
SELECTED=(
 ('training-era-dependencies/mmcv-v1.6.2/norm.py',('infer_abbr','build_norm_layer')),
 ('training-era-dependencies/mmcv-v1.6.2/conv_module.py',('ConvModule',)),
 ('training-era-dependencies/mmseg-v0.28.0/res_layer.py',('ResLayer',)),
 ('training-era-dependencies/mmseg-v0.28.0/resnet.py',('BasicBlock','Bottleneck','ResNet')),
 ('training-era-dependencies/mmseg-v0.28.0/decode_head.py',('BaseDecodeHead',)),
 ('training-era-da8b536/interaction_layer.py',('ChannelExchange','SpatialExchange','TwoIdentity')),
 ('training-era-da8b536/interaction_resnet.py',('IA_ResNet','IA_ResNetV1c')),
 ('training-era-da8b536/changer.py',('FDAF','MixFFN','Changer')),
)
DI_SOURCE='training-era-da8b536/dual_input_encoder_decoder.py'
PHOTO_SOURCE='training-era-dependencies/mmcv-v1.6.2/photometric.py'
DI_METHODS=('extract_feat','encode_decode','_decode_head_forward_test')


def need(value,message):
    if not value:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def read_regular(path,limit,expected_sha=None,expected_size=None):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'rb') as stream:
        before=os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size<=limit,'not a bounded regular input')
        raw=stream.read(limit+1);after=os.fstat(stream.fileno())
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        need(tuple(getattr(before,k) for k in fields)==tuple(getattr(after,k) for k in fields)
            and len(raw)==before.st_size,'input changed during read')
    need(expected_sha is None or sha(raw)==expected_sha,'pinned input SHA differs')
    need(expected_size is None or len(raw)==expected_size,'pinned input size differs')
    return raw


def write_new(path,value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    need(len(raw)<=MAX_REPORT,'complete report over limit; no truncation')
    parent=path.parent.resolve(strict=True);need(parent.is_dir(),'output parent must exist')
    descriptor,temporary=tempfile.mkstemp(prefix='.changer-network-',dir=parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path,follow_symlinks=False)
        descriptor=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
        need(read_regular(path,MAX_REPORT)==raw,'report exact readback failed')
    finally:Path(temporary).unlink(missing_ok=True)
    return {'bytes':len(raw),'sha256':sha(raw)}


def read_under(root,relative,expected):
    rel=PurePosixPath(relative)
    need(not rel.is_absolute() and '..' not in rel.parts,'invalid public source path')
    target=root.joinpath(*rel.parts)
    for component in (target,*target.parents):
        if component==root:break
        need(not component.is_symlink(),'public source symlink is not allowed')
    need(target.resolve(strict=True).is_relative_to(root),'public source escaped fixed root')
    return read_regular(target,2*1024*1024,expected)


def selected_ast(raw,names,*,class_name=None):
    tree=ast.parse(raw.decode('utf8'))
    nodes=tree.body
    if class_name:
        classes=[n for n in nodes if isinstance(n,ast.ClassDef) and n.name==class_name]
        need(len(classes)==1,'upstream container class missing');nodes=classes[0].body
    chosen=[copy.deepcopy(n) for n in nodes if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in names]
    need(len(chosen)==len(names) and {n.name for n in chosen}==set(names),'selected upstream definitions missing/duplicated')
    removed={}
    for n in chosen:
        removed[n.name]=[ast.unparse(d) for d in n.decorator_list]
        # Top-level registry decorators only. Method/static/abstract decorators
        # inside selected classes stay exactly as published.
        n.decorator_list=[]
    module=ast.fix_missing_locations(ast.Module(body=chosen,type_ignores=[]))
    return module,{'definitions':list(names),'container':class_name,'top_decorators_removed':removed,
        'ast_sha256':sha(ast.dump(module,include_attributes=False).encode())}


def literal(node,known):
    if isinstance(node,ast.Constant):return node.value
    if isinstance(node,(ast.List,ast.Tuple)):
        values=[literal(n,known) for n in node.elts];return tuple(values) if isinstance(node,ast.Tuple) else values
    if isinstance(node,ast.Dict):return {literal(k,known):literal(v,known) for k,v in zip(node.keys,node.values)}
    if isinstance(node,ast.Name) and node.id in known:return copy.deepcopy(known[node.id])
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='dict' and not node.args:
        need(all(k.arg is not None for k in node.keywords),'config dictionary expansion unsupported')
        return {k.arg:literal(k.value,known) for k in node.keywords}
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):return -literal(node.operand,known)
    raise ValueError('configuration is not the explicit literal subset; it is never executed')


def config_model(public):
    need(type(public.get('config')) is str,'public checkpoint config missing')
    known={}
    for node in ast.parse(public['config']).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            name=node.targets[0].id
            if name in {'norm_cfg','model'}:known[name]=literal(node.value,known)
            if name=='model':break
    model=known.get('model');need(type(model) is dict and set(model)=={'type','pretrained','backbone','decode_head','train_cfg','test_cfg'},'model config differs')
    need(model['type']=='DIEncoderDecoder' and model['pretrained'] is None and model['train_cfg']=={} and model['test_cfg']=={'mode':'whole'},'only actual no-pretrained whole config admitted')
    backbone=model['backbone'];head=model['decode_head']
    need(backbone['type']=='IA_ResNetV1c' and backbone['depth']==18 and backbone['num_stages']==4
        and tuple(backbone['out_indices'])==(0,1,2,3) and tuple(backbone['strides'])==(1,2,2,2)
        and tuple(backbone['dilations'])==(1,1,1,1) and backbone['norm_cfg']=={'type':'SyncBN','requires_grad':True},'actual backbone config not supported')
    need(head['type']=='Changer' and head['in_channels']==[64,128,256,512] and head['channels']==128
        and head['num_classes']==2 and head['align_corners'] is False and head['norm_cfg']=={'type':'SyncBN','requires_grad':True},'actual Changer head config not supported')
    return model


def bundle(args):
    root=Path(args.sources).resolve(strict=True)
    manifest_raw=read_regular(root/'SOURCES.json',1024*1024,MANIFEST_SHA);manifest=json.loads(manifest_raw)
    need(manifest['schema']=='changer-official-web-transcription-v251' and len(manifest['files'])==47
        and manifest['open_cd_training_era_commit']=='da8b5362f381e890a4ec4e8f74d02c31bf958184'
        and manifest['source_bytes_downloaded'] is False and manifest['transcribed'] is True,'reviewed source manifest differs')
    entries={x['path']:x for x in manifest['files']};modules=[];proof=[];raws={}
    for relative,names in (*SELECTED,(DI_SOURCE,()),(PHOTO_SOURCE,())):
        entry=entries[relative]
        need(entry['complete_web_line_coverage'] is True and entry['transcribed'] is True,'source not complete transcription')
        raw=read_under(root,relative,entry['transcribed_file_sha256']);raws[relative]=raw
        extracted=None
        if names:
            module,extracted=selected_ast(raw,names);modules.append((relative,module))
        proof.append({'path':relative,'transcribed_file_sha256':sha(raw),'bytes':len(raw),
            'source_url':entry.get('canonical_source_url'),'transcribed':True,'original_http_byte_sha256':None,'selected':extracted})
    di,di_meta=selected_ast(raws[DI_SOURCE],DI_METHODS,class_name='DIEncoderDecoder')
    photo,photo_meta=selected_ast(raws[PHOTO_SOURCE],('imnormalize','imnormalize_'))
    config_raw=read_under(root,'checkpoint-public-training-config-v251.json',CONFIG_SHA)
    public=json.loads(config_raw);model=config_model(public)
    local_raw=read_regular(Path(args.local_model),1024*1024,LOCAL_SHA)
    local_ast,local_meta=selected_ast(local_raw,LOCAL_NAMES)
    return root,modules,di,photo,local_ast,model,{'manifest_sha256':sha(manifest_raw),'source_files':proof,
        'config_sha256':sha(config_raw),'config_not_executed':True,'checkpoint_version_metadata':public['version_metadata'],
        'checkpoint_class_metadata':public['class_metadata'],'di_methods':di_meta,'photometric_ast':photo_meta,
        'local_model_sha256':sha(local_raw),'local_ast':local_meta,'actual_training_mmseg_version':'unknown; >=0.28 requirement only',
        'reference_mmseg_source':'v0.28.0 lower-bound source, not proved installed training version',
        'original_http_bytes_verified':False,'official_factory_executed':False}


def compat_namespace(torch):
    nn=torch.nn;F=torch.nn.functional;used=[]
    def unsupported(*_a,**_kw):raise RuntimeError('unsupported factory/training/plugin path was invoked')
    class BaseModule(nn.Module):
        def __init__(self,init_cfg=None):
            super().__init__();self.init_cfg=copy.deepcopy(init_cfg)
            def checked(v):
                if v is None:return
                if isinstance(v,list):
                    for item in v:checked(item)
                else:need(type(v) is dict and v.get('type') in {'Normal','Kaiming','Constant'},'initialization config not supported')
            checked(init_cfg)
            used.append({'operation':'BaseModule_init','init_cfg':copy.deepcopy(init_cfg),'initialization_executed':False})
        def init_weights(self):raise RuntimeError('BaseModule factory initialization not reproduced; strict weights are required')
    def build_conv_layer(cfg,*args,**kwargs):
        need(cfg is None or cfg=={'type':'Conv2d'},'only actual Conv2d supported')
        return nn.Conv2d(*args,**kwargs)
    def build_activation_layer(cfg):
        need(type(cfg) is dict and cfg.get('type') in {'ReLU','GELU'},'only actual ReLU/GELU supported')
        value=copy.deepcopy(cfg);kind=value.pop('type');return {'ReLU':nn.ReLU,'GELU':nn.GELU}[kind](**value)
    def build_dropout(cfg):
        need(cfg=={'type':'DropPath','drop_prob':0.},'only actual zero DropPath supported')
        used.append({'operation':'DropPath','drop_prob':0.0,'implementation':'nn.Identity'});return nn.Identity()
    def kaiming_init(module,a=0,mode='fan_out',nonlinearity='relu',bias=0,distribution='normal'):
        need(distribution=='normal','initializer distribution unsupported')
        nn.init.kaiming_normal_(module.weight,a=a,mode=mode,nonlinearity=nonlinearity)
        if getattr(module,'bias',None) is not None:nn.init.constant_(module.bias,bias)
    def constant_init(module,val,bias=0):
        if getattr(module,'weight',None) is not None:nn.init.constant_(module.weight,val)
        if getattr(module,'bias',None) is not None:nn.init.constant_(module.bias,bias)
    def resize(input,size=None,scale_factor=None,mode='nearest',align_corners=None,warning=True):
        need(input.device.type=='cpu' and input.dtype==torch.float32,'resize requires CPUfloat32')
        need(mode=='bilinear' and align_corners is False and scale_factor is None and size is not None,'only actual bilinear explicit-size path supported')
        return F.interpolate(input,size=size,mode=mode,align_corners=align_corners)
    class DisabledLoss(nn.Module):
        def __init__(self,cfg):super().__init__();self.cfg=copy.deepcopy(cfg)
        @property
        def loss_name(self):return 'loss_ce'
        def forward(self,*_a,**_kw):raise RuntimeError('research reference cannot train or compute loss')
    def build_loss(cfg):
        need(cfg=={'type':'CrossEntropyLoss','use_sigmoid':False,'loss_weight':1.0},'only actual loss declaration admitted')
        used.append({'operation':'loss_factory','cfg':copy.deepcopy(cfg),'inference_only_stub':True});return DisabledLoss(cfg)
    class DisabledSampler:
        def sample(self,*_a,**_kw):raise RuntimeError('research reference cannot sample training labels')
    def build_pixel_sampler(cfg,context):
        need(cfg=={'type':'OHEMPixelSampler','thresh':0.7,'min_kept':100000},'only actual sampler declaration admitted')
        used.append({'operation':'sampler_factory','cfg':copy.deepcopy(cfg),'inference_only_stub':True});return DisabledSampler()
    def auto_fp16(*args,**kwargs):
        need(not args and not kwargs,'unexpected fp16 decorator arguments')
        return lambda fn:fn
    def force_fp32(*args,**kwargs):
        need(not args and kwargs=={'apply_to':('seg_logit',)},'unexpected fp32 decorator arguments')
        def decorate(fn):
            @functools.wraps(fn)
            def disabled(*_a,**_kw):raise RuntimeError('training loss/forced cast path is outside reference scope')
            return disabled
        return decorate
    class FeatureFusionNeck:
        @staticmethod
        def fusion(a,b,policy):
            need(policy=='concat','only actual channel concatenation primitive supported')
            return torch.cat((a,b),dim=1)
    ns={'__name__':'changer_public_reference_v254','__builtins__':__builtins__,'torch':torch,'nn':nn,'F':F,
        'BaseModule':BaseModule,'Sequential':nn.Sequential,'Conv2d':nn.Conv2d,'build_conv_layer':build_conv_layer,
        'build_activation_layer':build_activation_layer,'build_dropout':build_dropout,'kaiming_init':kaiming_init,
        'constant_init':constant_init,'resize':resize,'build_loss':build_loss,'build_pixel_sampler':build_pixel_sampler,
        'build_padding_layer':unsupported,'build_plugin_layer':unsupported,'accuracy':unsupported,'auto_fp16':auto_fp16,
        'force_fp32':force_fp32,'FeatureFusionNeck':FeatureFusionNeck,'cp':SimpleNamespace(checkpoint=unsupported),
        '_BatchNorm':nn.modules.batchnorm._BatchNorm,'_InstanceNorm':nn.modules.instancenorm._InstanceNorm,
        'NORM_LAYERS':{'BN':nn.BatchNorm2d,'SyncBN':nn.SyncBatchNorm},'inspect':inspect,'warnings':warnings,
        'Dict':Dict,'Optional':Optional,'Tuple':Tuple,'Union':Union,'ABCMeta':ABCMeta,'abstractmethod':abstractmethod}
    def build_interaction_layer(cfg):
        need(type(cfg) is dict and cfg.get('type') in {'TwoIdentity','SpatialExchange','ChannelExchange'},'unexpected interaction')
        params=copy.deepcopy(cfg);kind=params.pop('type');return ns[kind](**params)
    ns['build_interaction_layer']=build_interaction_layer
    return ns,used


def tensor_info(torch,tensor):
    need(isinstance(tensor,torch.Tensor) and tensor.device.type=='cpu','observed value is not a CPU tensor')
    value=tensor.detach().contiguous();finite=bool(torch.isfinite(value).all())
    result={'shape':list(value.shape),'dtype':str(value.dtype),'elements':value.numel(),
        'sha256':sha(value.numpy().tobytes(order='C')),'finite':finite}
    if value.numel() and finite:result.update(min=float(value.min()),max=float(value.max()),mean=float(value.double().mean()))
    return result


def compare(torch,name,a,b):
    info={'name':name,'reference':tensor_info(torch,a),'local':tensor_info(torch,b)}
    equal_shape=a.shape==b.shape and a.dtype==b.dtype
    finite=info['reference']['finite'] and info['local']['finite']
    if not equal_shape or not finite:return {**info,'bitwise_equal':False,'within_tolerance':False,'max_abs':None,'mean_abs':None,'different_elements':None}
    delta=(a.detach()-b.detach()).abs()
    return {**info,'bitwise_equal':bool(torch.equal(a,b)),
        'within_tolerance':bool(torch.allclose(a,b,atol=ATOL,rtol=RTOL,equal_nan=False)),
        'max_abs':float(delta.max()) if delta.numel() else 0.,'mean_abs':float(delta.double().mean()) if delta.numel() else 0.,
        'different_elements':int((a!=b).sum())}


def state_info(torch,model):return {k:tensor_info(torch,v) for k,v in model.state_dict().items()}


def strict_load(torch,module,state):
    need(len(state)==173 and set(module.state_dict())==set(state),'complete independent 173-key state differs')
    for key,value in state.items():
        target=module.state_dict()[key]
        need(target.shape==value.shape and target.dtype==value.dtype,'reference state shape/dtype differs: '+key)
        need(bool(torch.isfinite(value).all()),'nonfinite source weight')
    result=module.load_state_dict({k:v.detach().clone() for k,v in state.items()},strict=True)
    need(not result.missing_keys and not result.unexpected_keys,'strict load incomplete')
    need(all(torch.equal(v,state[k]) for k,v in module.state_dict().items()),'loaded tensor differs from original safetensors')
    return {'keys':len(state),'strict':True,'from_original_safetensors':True,'missing':list(result.missing_keys),'unexpected':list(result.unexpected_keys)}


def module_attributes(torch,module):
    attrs={'backbone_configuration':{key:copy.deepcopy(getattr(module.backbone,key,None)) for key in
        ('depth','deep_stem','avg_down','strides','dilations','out_indices','style','contract_dilation','norm_eval','norm_cfg')},
        'head_configuration':{key:copy.deepcopy(getattr(module.decode_head,key,None)) for key in
        ('in_channels','channels','num_classes','out_channels','align_corners','dropout_ratio','norm_cfg')}}
    for name,child in module.named_modules():
        if isinstance(child,(torch.nn.modules.batchnorm._BatchNorm,torch.nn.modules.instancenorm._InstanceNorm)):
            attrs[name]={'class':type(child).__name__,'eps':child.eps,'momentum':child.momentum,'affine':child.affine,
                'track_running_stats':child.track_running_stats,'training':child.training}
        elif isinstance(child,(torch.nn.Conv2d,torch.nn.MaxPool2d,torch.nn.AvgPool2d)):
            attrs[name]={'class':type(child).__name__,**{k:getattr(child,k) for k in
                ('kernel_size','stride','padding','dilation','groups','ceil_mode','count_include_pad') if hasattr(child,k)}}
    return attrs


class TapRecorder:
    def __init__(self,torch,model,reference,sink,head_only=False):
        self.torch=torch;self.sink=sink;self.handles=[];self.counts={}
        def emit(name,value):self.sink(name,value.detach().clone())
        def temporal(name):
            def callback(_m,_args,out):
                i=self.counts.get(name,0);self.counts[name]=i+1;need(i<2,'unexpected temporal tap count')
                emit(name+('/a' if i==0 else '/b'),out)
            return callback
        def pair(name):
            def callback(_m,_args,out):
                need(type(out) in {tuple,list} and len(out)==2,'expected paired feature output')
                emit(name+'/a',out[0]);emit(name+'/b',out[1])
            return callback
        def hook(module,fn):self.handles.append(module.register_forward_hook(fn))
        if not head_only:
            hook(model.backbone.stem,temporal('backbone/stem'))
            def pooled(_m,args):
                name='backbone/pool';i=self.counts.get(name,0);self.counts[name]=i+1;need(i<2,'unexpected pool calls')
                emit(name+('/a' if i==0 else '/b'),args[0])
            self.handles.append(model.backbone.layer1.register_forward_pre_hook(pooled))
            for i in range(1,5):
                hook(getattr(model.backbone,'layer'+str(i)),temporal('backbone/stage'+str(i)+'/before_exchange'))
                if reference:hook(model.backbone.ccs[i-1],pair('backbone/stage'+str(i)+'/after_exchange'))
            if not reference:
                def post_stages(_m,_args,out):
                    need(len(out)==4,'local backbone feature count differs')
                    for i,value in enumerate(out,1):
                        a,b=value.chunk(2,dim=1);emit('backbone/stage'+str(i)+'/after_exchange/a',a);emit('backbone/stage'+str(i)+'/after_exchange/b',b)
                hook(model.backbone,post_stages)
        head=model.decode_head
        for i,conv in enumerate(head.convs,1):hook(conv,temporal('head/projection'+str(i)+'/native'))
        def fusion_input(_m,args):
            name='head/projected_concat';i=self.counts.get(name,0);self.counts[name]=i+1;need(i<2,'unexpected fusion calls')
            side='/a' if i==0 else '/b';emit(name+side,args[0])
            chunks=args[0].chunk(4,dim=1)
            for number,value in enumerate(chunks,1):emit('head/projection'+str(number)+'/resized'+side,value)
        self.handles.append(head.fusion_conv.register_forward_pre_hook(fusion_input))
        hook(head.fusion_conv,temporal('head/fusion'))
        hook(head.neck_layer.flow_make,lambda _m,_a,out:emit('head/flow',out))
        def aligned(_m,_args,out):
            emit('head/fdaf',out);a,b=out.chunk(2,dim=1);emit('head/aligned/a',a);emit('head/aligned/b',b)
        hook(head.neck_layer,aligned)
        hook(head.discriminator.layers,lambda _m,_a,out:emit('head/mixffn_branch',out))
        hook(head.discriminator,lambda _m,_a,out:emit('head/mixffn_residual',out))
        hook(head.conv_seg,lambda _m,_a,out:emit('head/raw_logits',out))
    def close(self):
        for handle in self.handles:handle.remove()


def semantic_order(name):
    before=['backbone/stem','backbone/pool']
    for stage in range(1,5):before.extend(['backbone/stage'+str(stage)+'/before_exchange','backbone/stage'+str(stage)+'/after_exchange'])
    for stage in range(1,5):before.extend(['head/projection'+str(stage)+'/native','head/projection'+str(stage)+'/resized'])
    before+=['head/projected_concat','head/fusion','head/flow','head/aligned','head/fdaf','head/mixffn_branch','head/mixffn_residual','head/raw_logits','network/final_logits','network/logit_margin','network/probabilities']
    index=next((i for i,prefix in enumerate(before) if name==prefix or name.startswith(prefix+'/')),999)
    return index,name


def compare_pass(torch,reference,local,run_reference,run_local,*,head_only=False):
    stored={};results=[]
    def save(name,value):need(name not in stored,'duplicate reference tap '+name);stored[name]=value
    ref_rec=TapRecorder(torch,reference,True,save,head_only)
    try:ref_output=run_reference()
    finally:ref_rec.close()
    def check(name,value):
        need(name in stored,'local tap has no independent reference: '+name)
        results.append(compare(torch,name,stored.pop(name),value))
    local_rec=TapRecorder(torch,local,False,check,head_only)
    try:local_output=run_local()
    finally:local_rec.close()
    need(not stored,'local execution did not produce all reference taps: '+','.join(stored))
    results.sort(key=lambda row:semantic_order(row['name']))
    return ref_output,local_output,results


def rgb_tensor(torch,np,rgb):
    need(rgb.dtype==np.uint8 and rgb.ndim==3 and rgb.shape[2]==3,'explicit RGB8 input required')
    value=torch.from_numpy(rgb.copy()).permute(2,0,1).unsqueeze(0).float()
    mean=value.new_tensor([123.675,116.28,103.53]).reshape(1,3,1,1)
    std=value.new_tensor([58.395,57.12,57.375]).reshape(1,3,1,1)
    return (value-mean)/std # Deliberately no resize/pad for identical-network-input isolation.


def input_cases(args,torch,np):
    generator=torch.Generator(device='cpu').manual_seed(SEED);cases=[];files=[]
    for label,h,w in [('synthetic_square',64,64),('synthetic_odd',65,97),('synthetic_nonsquare',96,160),('synthetic_512',512,512)]:
        a=torch.randn((1,3,h,w),generator=generator,dtype=torch.float32)
        b=torch.randn((1,3,h,w),generator=generator,dtype=torch.float32)
        cases.append({'label':label,'a':a,'b':b,'rgb':None,'provenance':{'kind':'fixed_seed_normalized_float32','seed':SEED}})
    if args.pairs:
        descriptor=read_regular(Path(args.pairs),256*1024);declared=json.loads(descriptor)
        need(type(declared) is list and 1<=len(declared)<=4,'pairs JSON requires one to four explicit RGB pairs')
        from PIL import Image
        for item in declared:
            need(type(item) is dict and set(item)=={'label','before','after'} and type(item['label']) is str and 1<=len(item['label'])<=100,'pairs accept only label/before/after; no GT')
            values=[];proof=[]
            for side in ('before','after'):
                need(type(item[side]) is str,'explicit image path required')
                path=Path(item[side]);raw=read_regular(path,64*1024*1024)
                need(raw[:8]==b'\x89PNG\r\n\x1a\n' and raw[12:16]==b'IHDR' and raw[24:26]==bytes([8,2]),'only unchanged RGB8 PNG inputs supported')
                declared_w=int.from_bytes(raw[16:20],'big');declared_h=int.from_bytes(raw[20:24],'big')
                need(32<=declared_h<=2048 and 32<=declared_w<=2048 and declared_h*declared_w<=MAX_PIXELS,'real pair exceeds whole-network research memory bounds')
                with Image.open(io.BytesIO(raw)) as image:
                    need(image.format=='PNG' and image.mode=='RGB' and image.size==(declared_w,declared_h),'explicit unchanged RGB PNG required')
                    rgb=np.asarray(image).copy()
                h,w=rgb.shape[:2]
                values.append(rgb);proof.append({'side':side,'name':path.name,'sha256':sha(raw),'bytes':len(raw),'width':w,'height':h})
                files.append((path,sha(raw),len(raw)))
            need(values[0].shape==values[1].shape,'pair pixel grids differ')
            cases.append({'label':item['label'],'a':rgb_tensor(torch,np,values[0]),'b':rgb_tensor(torch,np,values[1]),
                'rgb':values,'provenance':{'kind':'explicit_public_RGB8','pairs_descriptor_sha256':sha(descriptor),'images':proof,'ground_truth_supplied':False}})
        files.append((Path(args.pairs),sha(descriptor),len(descriptor)))
    need(len({c['label'] for c in cases})==len(cases),'input labels must be unique')
    return cases,files


def preprocess_comparison(torch,np,photo,cases):
    rows=[]
    try:import cv2
    except ImportError:return {'available':False,'reason':'OpenCV unavailable; no preprocessing equivalence claimed','network_inputs_still_identical':True}
    cv2.setNumThreads(1)
    ns={'np':np,'cv2':cv2,'__name__':'public_mmcv_photometric_v254'}
    exec(compile(photo,'<selected-public-mmcv-photometric>','exec'),ns)
    for case in cases:
        if case['rgb'] is None:continue
        for side,rgb,tensor in zip(('before','after'),case['rgb'],(case['a'],case['b'])):
            bgr=rgb[:,:,::-1].copy()
            value=ns['imnormalize'](bgr,np.array([123.675,116.28,103.53],dtype=np.float32),np.array([58.395,57.12,57.375],dtype=np.float32),to_rgb=True)
            reference=torch.from_numpy(value.copy()).permute(2,0,1).unsqueeze(0)
            rows.append(compare(torch,case['label']+'/'+side,reference,tensor))
    return {'available':True,'cv2_version':cv2.__version__,'comparisons':rows,
        'scope':'only RGB-array -> BGR-array -> selected mmcv1.6.2 imnormalize(to_rgb=True); no image decoder/1024 resize/tiling/pad comparison',
        'network_inputs_still_identical':True}


def verify(args,stage):
    root,modules,di,photo,local_ast,model_cfg,source_proof=bundle(args)
    out=Path(args.out);plan_path=Path(str(out)+'.plan.json')
    need(not out.exists() and not plan_path.exists(),'output and plan must be new, never replace old artifacts')
    need(not out.parent.resolve(strict=True).is_relative_to(root),'cannot write into frozen source tree')
    weights=read_regular(Path(args.weights),64*1024*1024,WEIGHT_SHA,WEIGHT_BYTES)
    import numpy as np
    import torch
    from safetensors.torch import load as load_safetensors
    torch.set_num_threads(1);torch.set_num_interop_threads(1);torch.manual_seed(SEED);torch.use_deterministic_algorithms(True)
    need(torch.get_num_threads()==torch.get_num_interop_threads()==1,'single-thread CPU setup failed')
    state=load_safetensors(weights);need(len(state)==173 and all(t.device.type=='cpu' for t in state.values()),'fixed safetensors state differs')
    cases,input_files=input_cases(args,torch,np)
    ns,compat_used=compat_namespace(torch)
    for relative,module in modules:exec(compile(module,'<public-transcribed-'+relative+'>','exec'),ns)
    exec(compile(di,'<selected-public-DI-methods>','exec'),ns)
    local_ns={'__name__':'pinned_local_changer_v254','torch':torch,'nn':torch.nn,'F':torch.nn.functional}
    exec(compile(local_ast,'<pinned-local-model-selected-ast>','exec'),local_ns)
    class ReferenceNetwork(torch.nn.Module):
        def __init__(self):
            super().__init__();bc=copy.deepcopy(model_cfg['backbone']);bc.pop('type');hc=copy.deepcopy(model_cfg['decode_head']);hc.pop('type')
            self.backbone=ns['IA_ResNetV1c'](**bc);self.decode_head=ns['Changer'](**hc)
            self.backbone_inchannels=3;self.with_neck=False;self.test_cfg=copy.deepcopy(model_cfg['test_cfg']);self.align_corners=self.decode_head.align_corners
        def forward(self,a,b):return self.encode_decode(torch.cat((a,b),dim=1),None)
    for name in DI_METHODS:setattr(ReferenceNetwork,name,ns[name])
    reference=ReferenceNetwork().cpu().eval();local=local_ns['ChangerExR18']().cpu().eval()
    loaded={'reference':strict_load(torch,reference,state),'local':strict_load(torch,local,state)}
    initial_states={'reference':state_info(torch,reference),'local':state_info(torch,local)}
    attributes={'reference':module_attributes(torch,reference),'local':module_attributes(torch,local)}
    plan={'schema':'changer-network-reference-plan-v254','created_at':datetime.now(timezone.utc).isoformat(),
        'source':source_proof,'weights':{'sha256':WEIGHT_SHA,'bytes':WEIGHT_BYTES,'state_keys':173},
        'model_literal':model_cfg,'seed':SEED,'tolerance':{'atol':ATOL,'rtol':RTOL},'cpu_threads':1,
        'inputs':[{'label':c['label'],'a':tensor_info(torch,c['a']),'b':tensor_info(torch,c['b']),'provenance':c['provenance']} for c in cases],
        'loaded':loaded,'attributes':attributes,'compatibility':compat_used,
        'scope':{'official_factory_executed':False,'training_mmseg_exact_version_known':False,
            'reference_sources_selected_mmseg':'0.28.0 lower bound','network_only_identical_input_tensors':True,
            'training_loss_sampler_usable':False,'upstream_preprocessing_equivalence_claimed':False,'ground_truth_inputs':False,
            'whole_vs_tiled_recipe_equivalence_claimed':False,'domain_quality_tested':False,'release_authorized':False}}
    plan_info=write_new(plan_path,plan);stage['plan']=plan_info;stage['plan_path']=str(plan_path)
    preprocessing=preprocess_comparison(torch,np,photo,cases)
    reports=[]
    with torch.inference_mode():
        for case in cases:
            a,b=case['a'],case['b'];before_a=tensor_info(torch,a);before_b=tensor_info(torch,b)
            fref=[];flocal=[]
            def retain_ref(_m,_args,out):fref.extend([t.detach().clone() for t in out])
            def retain_local(_m,_args,out):flocal.extend([t.detach().clone() for t in out])
            hr=reference.backbone.register_forward_hook(retain_ref);hl=local.backbone.register_forward_hook(retain_local)
            try:
                r,l,comparisons=compare_pass(torch,reference,local,lambda:reference(a,b),lambda:local(a,b))
            finally:hr.remove();hl.remove()
            comparisons += [compare(torch,'network/final_logits',r,l),compare(torch,'network/logit_margin',r[:,1]-r[:,0],l[:,1]-l[:,0]),
                compare(torch,'network/probabilities',r.softmax(dim=1),l.softmax(dim=1))]
            isolated=[]
            for origin,features in [('reference_backbone',fref),('local_backbone',flocal)]:
                before_features=[tensor_info(torch,x) for x in features]
                rr,ll,rows=compare_pass(torch,reference,local,lambda:reference.decode_head(features),lambda:local.decode_head(features),head_only=True)
                rows.append(compare(torch,'network/final_logits',torch.nn.functional.interpolate(rr,size=a.shape[-2:],mode='bilinear',align_corners=False),
                    torch.nn.functional.interpolate(ll,size=a.shape[-2:],mode='bilinear',align_corners=False)))
                need(before_features==[tensor_info(torch,x) for x in features],'head mutated shared input features')
                isolated.append({'feature_source':origin,'comparisons':rows,'first_not_allclose':next((x['name'] for x in rows if not x['within_tolerance']),None)})
            comparisons.sort(key=lambda row:semantic_order(row['name']))
            rp=r.softmax(dim=1)[:,1];lp=l.softmax(dim=1)[:,1]
            decisions={'reference_probability_gte_half_pixels':int((rp>=0.5).sum()),'local_probability_gte_half_pixels':int((lp>=0.5).sum()),
                'argmax_disagreement_pixels':int((r.argmax(1)!=l.argmax(1)).sum()),
                'reference_exact_logit_ties':int((r[:,0]==r[:,1]).sum()),'local_exact_logit_ties':int((l[:,0]==l[:,1]).sum()),
                'reference_argmax_vs_gte_half_disagreement':int(((r.argmax(1)==1)!=(rp>=0.5)).sum()),
                'local_argmax_vs_gte_half_disagreement':int(((l.argmax(1)==1)!=(lp>=0.5)).sum()),'threshold_optimized':False}
            need(before_a==tensor_info(torch,a) and before_b==tensor_info(torch,b),'network mutated input tensor')
            need(initial_states['reference']==state_info(torch,reference) and initial_states['local']==state_info(torch,local),'network state changed during inference')
            reports.append({'label':case['label'],'comparisons':comparisons,'head_isolation':isolated,'decisions':decisions,
                'first_not_allclose':next((x['name'] for x in comparisons if not x['within_tolerance']),None),'inputs_unchanged':True,'states_unchanged':True})
            del r,l,rr,ll,rp,lp,fref,flocal
    for path,expected,size in input_files:read_regular(path,64*1024*1024,expected,size)
    read_regular(Path(args.weights),64*1024*1024,WEIGHT_SHA,WEIGHT_BYTES);read_regular(Path(args.local_model),1024*1024,LOCAL_SHA)
    bundle(args) # Recheck the complete selected source/config pins after forward.
    allclose=all(row['within_tolerance'] for report in reports for row in report['comparisons']) and all(
        row['within_tolerance'] for report in reports for isolated in report['head_isolation'] for row in isolated['comparisons'])
    return {'schema':'changer-network-reference-result-v254','created_at':datetime.now(timezone.utc).isoformat(),
        'status':'tested_reference_allclose' if allclose else 'numerical_divergence','allclose':allclose,'plan':plan_info,
        'environment':{'python':platform.python_version(),'torch':torch.__version__,'numpy':np.__version__,'cpu_threads':torch.get_num_threads(),
            'interop_threads':torch.get_num_interop_threads(),'cuda_used':False},'source':source_proof,
        'preprocessing_comparison':preprocessing,'cases':reports,'inputs_files_weights_sources_unchanged':True,
        'limits':plan['scope'],'conclusion':'Only selected public-transcription control flow plus declared compatibility and these inputs; not full official factory/training runtime/domain quality or production approval.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',required=True);parser.add_argument('--weights',required=True)
    parser.add_argument('--local-model',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--pairs',help='New explicit JSON list of {label,before,after} RGB8 PNG paths; no GT fields')
    args=parser.parse_args();stage={};output_allowed=False
    try:
        output=Path(args.out);source_root=Path(args.sources).resolve(strict=True)
        need(output.parent.resolve(strict=True).is_dir() and not output.parent.resolve(strict=True).is_relative_to(source_root),'output must be outside frozen sources')
        need(not output.exists() and not Path(str(output)+'.plan.json').exists(),'output/plan already exists; preserve old artifacts')
        output_allowed=True
        result=verify(args,stage);saved=write_new(Path(args.out),result)
        print(json.dumps({'status':result['status'],'cases':len(result['cases']),'report':saved,'output':args.out},sort_keys=True))
        return 0 if result['allclose'] else 1
    except Exception as error:
        if not output_allowed:
            print(json.dumps({'status':'failed_before_safe_output','error_type':type(error).__name__,'error':str(error)[:500]}),file=sys.stderr);return 2
        failure={'schema':'changer-network-reference-failure-v254','status':'failed','error_type':type(error).__name__,
            'error':str(error)[:2000],'stage':stage,'official_factory_executed':False,'domain_quality_tested':False,'release_authorized':False}
        try:saved=write_new(Path(args.out),failure)
        except Exception as save_error:
            print(json.dumps({'status':'failed','error_type':type(error).__name__,'save_error_type':type(save_error).__name__}),file=sys.stderr);return 2
        print(json.dumps({'status':'failed','error_type':type(error).__name__,'report':saved,'output':args.out}),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
