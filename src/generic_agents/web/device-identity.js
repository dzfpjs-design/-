let deviceOverview={devices:[]},deviceOffset=0;
const deviceSchemeNames={manufacturer_serial:'制造商序列号',inventory_tag:'资产台账号',supplier_device_id:'供应商设备标识'};
function deviceStatus(s){return ({review:'待核查身份',enabled:'身份已确认',disabled:'停止后续调度'})[s]||s;}
function deviceIdentityText(d){return deviceSchemeNames[d.identity_scheme]+' · '+d.identity_issuer+' · '+d.identity_reference;}
function deviceOccupancyText(hold){
  if(!hold)return '当前无登记占用';
  const plan=sensingOverview.plans.find(p=>p.id===hold.plan_id);
  const state=hold.action_terminal?'原设备动作已结束，等待合格观测归集':sensingStatus(hold.status);
  return '占用中：'+state+' · '+(entities.find(e=>e.id===hold.entity_id)?.data.name||'登记对象')+' · '+(plan?.data.reason||hold.reason||'等待原请求的合格观测或永久终止回执');
}
function openDeviceCreate(){const d=sensingDialog('登记统一设备身份'),form=d.querySelector('form');
  form.innerHTML='<p>同一设备的不同来源接口绑定到同一条设备记录。依据明确序列号或台账号核查，不按名称自动合并。登记和人工确认不代表已完成硬件探测。</p><label>设备名称<input name="title" required maxlength="200"></label><label>标识类型<select name="scheme">'+Object.entries(deviceSchemeNames).map(([v,t])=>`<option value="${v}">${esc(t)}</option>`).join('')+'</select></label><label>标识签发方<input name="issuer" required maxlength="100" placeholder="例如：设备制造商或资产台账责任单位"></label><label>明确设备标识<input name="reference" required maxlength="200"></label><label>身份核查依据<textarea name="reason" required maxlength="2000"></textarea></label><div class="actions"><button>保存待核查设备</button><button type="button" data-close class="secondary">取消</button></div>';
  form.querySelector('[data-close]').onclick=()=>d.close();form.onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{let f=form.elements;await api('/sensing/devices','POST',{title:f.title.value,identity_scheme:f.scheme.value,identity_issuer:f.issuer.value,identity_reference:f.reference.value,reason:f.reason.value});d.close();await load();notice('明确设备标识与核查依据已保存，管理员确认后可绑定来源接口。');});};d.showModal();}
async function openDeviceBinding(asset){const current=await api('/sensing/assets/'+asset.id+'/device'),d=sensingDialog('核查来源接口的设备归属'),form=d.querySelector('form');
  form.innerHTML=`<h3>${esc(asset.title)}</h3><p>来源：${esc(caps.find(c=>c.id===asset.source_id)?.data.description||'登记来源')}</p><p>当前设备：${esc(current.device?current.device.title+' · '+deviceIdentityText(current.device):'未绑定')}</p><p>${esc(current.binding?.data.reason||'核查真实台账与供应商设备编号后建立归属')}</p><p>同一设备的不同接口共享占用。尚未结束或未知动作不能改绑；身份变更不会扩大已保存的监测设备范围。</p>`;
  if(me.role==='admin'){
    let options=deviceOverview.devices.filter(d=>d.status==='enabled');if(current.device?.status==='enabled'&&!options.some(d=>d.id===current.device.id))options.push(current.device);
    form.insertAdjacentHTML('beforeend',`<label>核查归属设备<select name="device">${selectOptions(options,x=>x.title+' · '+deviceIdentityText(x))}</select></label><p>设备列表每页50项；列表外的设备可先通过设备分页查看。未确认设备不能绑定。</p><label><input type="checkbox" name="reconfirm">重新核查原绑定（仅保留同一设备）</label><label>接口归属核查依据<textarea name="reason" required maxlength="2000"></textarea></label><div class="actions"><button>保存设备归属</button><button type="button" data-close class="secondary">取消</button></div>`);
    if(current.device)form.elements.device.value=current.device.id;
    form.onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{let choice=options.find(x=>x.id===form.elements.device.value);if(!choice)throw Error('先登记并确认统一设备，再选择该接口的归属');
      await api('/sensing/assets/'+asset.id+'/device','POST',{expected_version:asset.version,device_id:choice.id,device_expected_version:choice.version,reconfirm:form.elements.reconfirm.checked,reason:form.elements.reason.value});d.close();await load();notice('设备归属与核查历史已保存；已有监测授权不会自动改成其他设备。');});};
  }else form.insertAdjacentHTML('beforeend','<button type="button" data-close>关闭</button>');
  form.querySelector('[data-close]').onclick=()=>d.close();d.showModal();}
function augmentDeviceIdentity(section){const panel=document.createElement('section');panel.innerHTML='<h3>统一设备身份与占用</h3><p>先核查明确设备身份，再将不同来源接口绑定到同一设备。未核查身份禁止新控制；旧策略需重新核查统一设备范围。停用不会取消已经派发的外部动作。</p>';
  if(me.role!=='viewer'){let b=document.createElement('button');b.textContent='登记统一设备';b.onclick=openDeviceCreate;panel.append(b);}
  if(!deviceOverview.devices.length)panel.insertAdjacentHTML('beforeend',empty('尚无统一设备记录；来源接口仍可登记，控制前需核查并绑定设备。'));
  for(const device of deviceOverview.devices){let card=document.createElement('div');card.className='card';card.dataset.sensorDevice=device.id;
    card.innerHTML=`<h3>${esc(device.title)}</h3><p>${esc(deviceStatus(device.status))} · ${esc(deviceIdentityText(device))}</p><p>身份依据：${esc(device.data.identity_basis)}</p><p>${esc(deviceOccupancyText(device.occupancy))}</p><p>已绑定接口：${esc(device.bindings.map(link=>sensingOverview.assets.find(a=>a.id===link.asset_id)?.title||'其他页登记接口').join('、')||'尚未绑定')}${device.bindings_truncated?'（仅显示前100条；未据此声称完整）':''}</p>`;
    if(me.role==='admin'){
      let action=({review:'approve',enabled:'pause',disabled:'resume'})[device.status],b=document.createElement('button');b.textContent=({review:'确认明确设备身份',enabled:'停止此设备后续调度',disabled:'核查后恢复设备'})[device.status];
      b.onclick=()=>simpleReview(action==='pause'?'停止新派发，已派发或未知动作仍需核对原回执':'核查明确设备标识与依据；人工确认不替代硬件探测',reason=>api('/sensing/devices/'+device.id+'/'+action,'POST',{expected_version:device.version,reason}));card.append(b);
    }
    let h=document.createElement('button');h.textContent='查看设备身份历史';h.className='secondary';h.onclick=e=>action(e.target,async()=>{let rows=await api('/sensing/devices/'+device.id+'/history'),d=sensingDialog('设备身份核查历史');d.querySelector('form').innerHTML=rows.map(r=>`<div class="evidence"><p>${date(r.created_at)} · 版本${r.data.after.version} · ${esc(deviceStatus(r.data.after.status))}</p><p>${esc(r.data.reason)}</p><p>${esc(deviceIdentityText(r.data.after))}</p></div>`).join('')+'<button type="button">关闭历史</button>';d.querySelector('button').onclick=()=>d.close();d.showModal();});card.append(h);panel.append(card);
  }
  panel.insertAdjacentHTML('beforeend',`<p>本页设备${deviceOverview.devices.length}项；每页最多50项。</p>`);
  if(deviceOffset>0){let b=document.createElement('button');b.textContent='上一页统一设备';b.onclick=e=>action(e.target,async()=>{deviceOffset=Math.max(0,deviceOffset-50);await load();});panel.append(b);}
  if(deviceOverview.devices.length===50){let b=document.createElement('button');b.textContent='下一页统一设备';b.onclick=e=>action(e.target,async()=>{deviceOffset+=50;await load();});panel.append(b);}
  section.append(panel);
}
