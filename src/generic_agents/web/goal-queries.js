// The business workspace keeps delegated evidence beside decisions and recovery controls.
function augmentGoalQueries(){if(current!=='tasks')return;
 for(const task of overview.tasks){if(!task.data.goal_spec?.data_query&&!task.data.data_query_history?.length)continue;
  const card=document.querySelector('[data-task="'+task.id+'"]');if(!card)continue;
  const panel=document.createElement('section');panel.className='evidence';panel.dataset.goalQuery=task.id;
  const run=queries.find(q=>q.id===task.data.data_query_run_id);
  panel.innerHTML='<h3>数据智能体委托查询</h3><p>查询方案与原目标、范围和日期绑定；等待登记计算及专业核查，不把原始字段齐全当作推导已完成。</p>';
  if(run){const entry=document.createElement('section');entry.className='evidence';
   if(dataQueryReferenceLike(run))entry.innerHTML=dataQueryReferenceCard(run);
   else{entry.innerHTML=badge(run.status)+(run.data.error?'<p>'+esc(run.data.error)+'</p>':'')+'<p>'+esc(run.question)+'</p>';appendQueryTools(run,entry);}panel.append(entry);}
  else panel.insertAdjacentHTML('beforeend','<p>规划时会先保存委托请求；当前尚无本目标的委托结果。</p>');
  if(task.status==='completed'&&run?.status==='rejected')panel.insertAdjacentHTML('beforeend','<p class="notice error">此前验收历史保留；查询核查已撤回，此结果及对应经验不能继续复用。</p>');
  if(task.data.data_query_history?.length){const history=document.createElement('details');history.innerHTML='<summary>查看先前目标的委托记录</summary>';
   for(const link of task.data.data_query_history){const old=queries.find(q=>q.id===link.query_id);if(!old)continue;const entry=document.createElement('section');entry.className='evidence';
    entry.innerHTML='<p>目标第'+esc(link.goal_version)+'版 · '+esc(old.question)+'</p>';
    if(dataQueryReferenceLike(old))entry.insertAdjacentHTML('beforeend',dataQueryReferenceCard(old));
    else{entry.insertAdjacentHTML('beforeend',badge(old.status));appendQueryTools(old,entry);}history.append(entry);}panel.append(history);}
  card.append(panel);
 }
}
