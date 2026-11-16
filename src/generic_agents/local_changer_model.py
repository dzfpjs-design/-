"""Standalone PyTorch inference graph for the pinned ChangerEx R18 checkpoint.

Architecture references: Apache-2.0 Open-CD changer_r18 / interaction_resnet /
interaction_layer / changer decode head, reviewed against main commit
790a1972b538baaaffd4c3022a4d9afaa3f861e5. This implementation does not import
OpenMMLab, run upstream factories, load pickle, or assign business semantics.
"""
import torch
from torch import nn
from torch.nn import functional as F


class ResidualBlock(nn.Module):
    def __init__(self, incoming, outgoing, stride):
        super().__init__()
        self.conv1=nn.Conv2d(incoming,outgoing,3,stride=stride,padding=1,bias=False)
        self.bn1=nn.BatchNorm2d(outgoing)
        self.conv2=nn.Conv2d(outgoing,outgoing,3,padding=1,bias=False)
        self.bn2=nn.BatchNorm2d(outgoing)
        self.downsample=(nn.Sequential(nn.Conv2d(incoming,outgoing,1,stride=stride,bias=False),nn.BatchNorm2d(outgoing))
                         if incoming!=outgoing or stride!=1 else None)

    def forward(self, value):
        shortcut=value if self.downsample is None else self.downsample(value)
        value=F.relu(self.bn1(self.conv1(value)))
        return F.relu(self.bn2(self.conv2(value))+shortcut)


def exchange(a,b,axis):
    """Swap alternate spatial columns or channels; never alter inputs in place."""
    shape=[1]*a.ndim;shape[axis]=a.shape[axis]
    selected=(torch.arange(a.shape[axis],device=a.device)%2==0).reshape(shape)
    return torch.where(selected,b,a),torch.where(selected,a,b)


class InteractionResNet18(nn.Module):
    def __init__(self):
        super().__init__()
        stem=[]
        for incoming,outgoing,stride in [(3,32,2),(32,32,1),(32,64,1)]:
            stem.extend([nn.Conv2d(incoming,outgoing,3,stride=stride,padding=1,bias=False),nn.BatchNorm2d(outgoing),nn.ReLU()])
        self.stem=nn.Sequential(*stem)
        incoming=64
        for index,outgoing in enumerate([64,128,256,512],1):
            setattr(self,'layer'+str(index),nn.Sequential(ResidualBlock(incoming,outgoing,1 if index==1 else 2),ResidualBlock(outgoing,outgoing,1)))
            incoming=outgoing

    def forward(self,a,b):
        a=F.max_pool2d(self.stem(a),3,stride=2,padding=1)
        b=F.max_pool2d(self.stem(b),3,stride=2,padding=1)
        outputs=[]
        for index in range(1,5):
            layer=getattr(self,'layer'+str(index));a=layer(a);b=layer(b)
            if index==2:a,b=exchange(a,b,3)
            elif index>=3:a,b=exchange(a,b,1)
            outputs.append(torch.cat((a,b),dim=1))
        return outputs


class ConvBN(nn.Module):
    def __init__(self,incoming,outgoing):
        super().__init__();self.conv=nn.Conv2d(incoming,outgoing,1,bias=False);self.bn=nn.BatchNorm2d(outgoing)

    def forward(self,value):return F.relu(self.bn(self.conv(value)))


class DualAlignment(nn.Module):
    def __init__(self):
        super().__init__()
        self.flow_make=nn.Sequential(nn.Conv2d(128,128,5,padding=2,groups=128,bias=True),
                                    nn.InstanceNorm2d(128),nn.GELU(),nn.Conv2d(128,4,1,bias=False))

    @staticmethod
    def warp(value,displacement):
        batch,_,height,width=value.shape
        rows=torch.linspace(-1,1,height,device=value.device,dtype=value.dtype)
        cols=torch.linspace(-1,1,width,device=value.device,dtype=value.dtype)
        yy,xx=torch.meshgrid(rows,cols,indexing='ij')
        coordinates=torch.stack((xx,yy),dim=-1).unsqueeze(0).expand(batch,-1,-1,-1)
        scale=value.new_tensor([width,height]).reshape(1,1,1,2)
        shifted=coordinates+displacement.permute(0,2,3,1)/scale
        return F.grid_sample(value,shifted,mode='bilinear',padding_mode='zeros',align_corners=True)

    def forward(self,a,b):
        displacement=self.flow_make(torch.cat((a,b),dim=1))
        da,db=displacement.chunk(2,dim=1)
        return torch.cat((self.warp(a,da)-b,self.warp(b,db)-a),dim=1)


class PointwiseResidual(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers=nn.Sequential(nn.Conv2d(128,128,1),nn.Conv2d(128,128,3,padding=1,groups=128),
                                 nn.GELU(),nn.Dropout(0),nn.Conv2d(128,128,1),nn.Dropout(0))

    def forward(self,value):return value+self.layers(value)


class ChangeHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.convs=nn.ModuleList([ConvBN(width,128) for width in [64,128,256,512]])
        self.fusion_conv=ConvBN(512,64)
        self.neck_layer=DualAlignment();self.discriminator=PointwiseResidual()
        self.dropout=nn.Dropout2d(.1);self.conv_seg=nn.Conv2d(128,2,1)

    def projected(self,values):
        size=values[0].shape[-2:]
        projected=[F.interpolate(layer(value),size=size,mode='bilinear',align_corners=False)
                   for layer,value in zip(self.convs,values)]
        return self.fusion_conv(torch.cat(projected,dim=1))

    def forward(self,values):
        pairs=[v.chunk(2,dim=1) for v in values]
        a=self.projected([x[0] for x in pairs]);b=self.projected([x[1] for x in pairs])
        return self.conv_seg(self.dropout(self.discriminator(self.neck_layer(a,b))))


class ChangerExR18(nn.Module):
    def __init__(self):
        super().__init__();self.backbone=InteractionResNet18();self.decode_head=ChangeHead()

    def forward(self,before,after):
        logits=self.decode_head(self.backbone(before,after))
        return F.interpolate(logits,size=before.shape[-2:],mode='bilinear',align_corners=False)


def normalized_rgb(image):
    """RGB bytes, fixed ImageNet normalization then zero-pad to a 32px divisor."""
    import numpy as np
    value=np.asarray(image)
    if value.dtype!=np.uint8 or value.ndim!=3 or value.shape[2]!=3:
        raise ValueError('local Changer requires explicitly rendered uint8 RGB')
    height,width=value.shape[:2]
    if not 32<=height<=512 or not 32<=width<=512:raise ValueError('tile side must be 32 to 512 pixels')
    tensor=torch.from_numpy(value.copy()).permute(2,0,1).unsqueeze(0).float()
    mean=tensor.new_tensor([123.675,116.28,103.53]).reshape(1,3,1,1)
    scale=tensor.new_tensor([58.395,57.12,57.375]).reshape(1,3,1,1)
    normalized=(tensor-mean)/scale
    return F.pad(normalized,(0,(-width)%32,0,(-height)%32)),(height,width)


def probabilities(model,before,after):
    if any(part.training for part in model.modules()):
        raise ValueError('local change inference requires an evaluation-mode model')
    a,shape=normalized_rgb(before);b,other=normalized_rgb(after)
    if shape!=other:raise ValueError('aligned tiles must have the same pixel dimensions')
    with torch.inference_mode():logits=model(a,b)
    if (not isinstance(logits,torch.Tensor) or logits.device.type!='cpu'
            or logits.dtype!=torch.float32 or tuple(logits.shape)!=(1,2,*a.shape[-2:])):
        raise ValueError('local change logits must be two aligned CPU float32 classes')
    if not bool(torch.isfinite(logits).all()):raise ValueError('local change logits are not finite')
    return logits.softmax(dim=1)[0,1,:shape[0],:shape[1]].cpu().numpy()
