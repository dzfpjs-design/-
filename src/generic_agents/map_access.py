"""Workspace map access policy; no implicit access for unconfigured members."""
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select, text
from .contracts import Contract
from .models import Job, Membership, SetupDraft, User, InstanceRelease
from .security import require
from .services import audit
from .setup_api import P, DB
from .db import session

router=APIRouter(prefix='/api/map-access')
KIND='map_business_access_policy'
KEY='map-business-access-policy-v1'

class Policy(Contract):
    version:int=Field(ge=0)
    businesses:dict[str,dict]

def _row(db,p):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal',Job.idempotency_key==KEY))

def _names(db,p):
    names=set()
    draft=db.scalar(select(SetupDraft).where(SetupDraft.workspace_id==p.workspace_id))
    if draft and isinstance(draft.data,dict):
        for b in draft.data.get('businesses',[]):
            n=b if isinstance(b,str) else b.get('name') if isinstance(b,dict) else None
            if isinstance(n,str) and n.strip():names.add(n)
    for r in db.scalars(select(InstanceRelease).where(InstanceRelease.workspace_id==p.workspace_id)):
        for b in r.data.get('snapshot',{}).get('businesses',[]):
            if isinstance(b,dict) and isinstance(b.get('name'),str):names.add(b['name'])
    return sorted(names)

def can_read(db,p,names):
    if p.role=='admin':return True
    row=_row(db,p)
    # Existing workspaces retain their prior access until an administrator
    # explicitly saves a policy; the management UI labels this as legacy.
    if not row:return True
    if not isinstance(row.data,dict):return False
    policies=row.data.get('businesses',{})
    if not isinstance(policies,dict):return False
    for name in names:
        if not isinstance(name,str):continue
        entry=policies.get(name,{})
        if isinstance(entry,dict) and isinstance(entry.get('users'),list) and isinstance(entry.get('roles'),list) and (p.user_id in entry['users'] or p.role in entry['roles']):
            return True
    return False

def require_config(db,p):
    if _row(db,p):require(p,'admin')

def filter_capabilities(db,p,rows):
    # A workspace registry is not bound to a specific enabled business.
    # Non-admins use instance-bound source directories once policy is enabled.
    if p.role=='admin' or not _row(db,p):return rows
    return [row for row in rows if row.kind!='source']

def require_evidence(db,p,identity):
    # Generic evidence lacks a reliable complete lineage/classification. Once
    # policy is enabled, direct raw download cannot safely infer its business.
    # Business-specific routes perform their own authorization instead.
    require_config(db,p)

def config_guard(p:P,db:DB):
    require_config(db,p)

def route_guard(request:Request,db=Depends(session)):
    """Global dependency for raw-map paths; instance read is separately gated."""
    path=request.url.path
    protected=path=='/api/videos' or path.startswith(('/api/geo/','/api/wms/','/api/map-services/','/api/imagery-daily/','/api/media/','/api/videos/'))
    protected=protected or (request.method in {'GET','HEAD'} and (path=='/api/evidence' or path.startswith('/api/evidence/')))
    protected=protected or (path.startswith(('/api/workspace/','/api/instances/')) and any(part in path.lower() for part in ('/pixel-change/','/pixel-questions/','/graph-event-native-learned-geo-producers/','original','preview','export','image','evidence','download','mask')))
    # Generic file endpoints can carry images regardless of their module of
    # origin. Fail closed for direct reads once enabled. Instance observations
    # use an internally checked path with per-business access enforcement.
    import re
    # Only these two exact public-observation routes have an instance-bound
    # configured_cap business check. Other paths keep the fail-closed guard.
    instance_public=bool(re.fullmatch(r'/api/public-observations/[0-9a-fA-F-]{36}/(?:snapshot|tiles/\d{4}-\d{2}-\d{2}/\d+/\d+/\d+)',path))
    protected=protected or (request.method in {'GET','HEAD'} and path.startswith('/api/') and not path.startswith('/api/instance-observations/') and not instance_public and any(token in path.lower() for token in ('content','original','preview','export','image','evidence','download','mask','artifact','file','blob','binary','raster','tile','thumbnail','snapshot','attachment','stream','/rgb','/audio/','/response')))
    # These setup actions return or manipulate full frozen report sources.
    # The raw-evidence GET policy must apply to their POST/PUT paths too;
    # otherwise a downgraded operator can read the same source via preview.
    report_evidence_roots = ('/api/setup/report-evidence-readings', '/api/setup/report-evidence-delegated')
    protected = protected or (request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and
        any(path == root or path.startswith(root + '/') for root in report_evidence_roots))
    # Older report APIs also return frozen source text in previews, readings,
    # draft replies and receipt replays. Protect those exact response shapes,
    # without changing unrelated status, listing or configuration routes.
    uuid_part = r'[0-9a-fA-F-]{32,36}'
    full_root = '/api/setup/report-full-readings'
    draft_root = '/api/setup/report-drafts'
    full_item = re.escape(full_root) + '/' + uuid_part
    draft_item = re.escape(draft_root) + '/' + uuid_part
    raw_get = (
        re.fullmatch(full_item, path) or
        re.fullmatch(re.escape(full_root) + r'/by-request/[^/]+', path) or
        re.fullmatch(full_item + r'/stages/[^/]+/(?:raw|failed-candidate|provisional)', path) or
        re.fullmatch(full_item + r'/(?:provisional|merge|review|recovery)-receipts/[^/]+', path) or
        re.fullmatch(draft_item, path) or
        re.fullmatch(re.escape(draft_root) + r'/(?:by-request|readings)/[^/]+', path) or
        re.fullmatch(draft_item + r'/review-receipts/[^/]+', path))
    raw_post = (
        path in {full_root, full_root + '/preview', draft_root, draft_root + '/preview'} or
        re.fullmatch(full_item + r'/(?:merge|merge-preview|recover-saved)', path) or
        re.fullmatch(full_item + r'/stages/[^/]+/provisional', path))
    raw_put = re.fullmatch(full_item + r'/review', path) or re.fullmatch(draft_item + r'/review', path)
    protected = protected or bool((request.method in {'GET', 'HEAD'} and raw_get) or
        (request.method == 'POST' and raw_post) or (request.method == 'PUT' and raw_put))
    if protected:
        if (request.method in {'GET', 'HEAD'} and
                (path == '/api/setup/report-evidence-readings' or
                 path.startswith('/api/setup/report-evidence-readings/') or
                 path == full_root or path.startswith(full_root + '/'))):
            from .db import begin_private_read_snapshot
            begin_private_read_snapshot(db)
        from .security import authenticated
        require_config(db,authenticated(request,db))

@router.get('')
def read_policy(p:P,db:DB):
    require(p,'admin');row=_row(db,p)
    members=db.execute(select(User,Membership).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==p.workspace_id,User.active.is_(True))).all()
    return {'version':row.version if row else 0,'enabled':bool(row),'businesses':row.data.get('businesses',{}) if row else {},'available_businesses':_names(db,p),'members':[{'id':u.id,'username':u.username,'role':m.role} for u,m in members]}

@router.put('')
def save_policy(body:Policy,p:P,db:DB):
    require(p,'admin')
    key=int.from_bytes(hashlib.sha256((p.workspace_id+':'+KEY).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
    row=_row(db,p);version=row.version if row else 0
    if body.version!=version:raise HTTPException(409,'授权设置已更新，请重新读取；本次输入未保存')
    names=set(_names(db,p));members=set(db.scalars(select(Membership.user_id).where(Membership.workspace_id==p.workspace_id)).all())
    if len(body.businesses)>200:raise HTTPException(422,'业务授权过多')
    checked={}
    for name,value in body.businesses.items():
        if name not in names or not isinstance(value,dict) or set(value)!={'roles','users'}:raise HTTPException(422,'业务或授权内容无效')
        roles=value['roles'];users=value['users']
        if not isinstance(roles,list) or not isinstance(users,list) or any(not isinstance(x,str) for x in roles+users) or len(roles)>2 or len(users)>1000 or len(set(roles))!=len(roles) or len(set(users))!=len(users) or not set(roles).issubset({'operator','viewer'}) or not set(users).issubset(members):raise HTTPException(422,'授权角色或成员无效')
        checked[name]={'roles':sorted(roles),'users':sorted(users)}
    if row is None:
        row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=KEY,data={'businesses':checked});db.add(row);db.flush()
    else:row.data={'businesses':checked};row.version+=1
    audit(db,p,'map_access.policy_saved',row.id,{'business_count':len(checked)});db.commit()
    return {'version':row.version,'businesses':checked}
