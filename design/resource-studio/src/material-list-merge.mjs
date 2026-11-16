// The server owns original files. Add newly discovered originals to the local
// configuration view without replacing human annotations or writing a revision.
export function appendServerMaterials(draft, rows){
 if(!draft||!Array.isArray(rows))return draft;
 const existing=Array.isArray(draft.materials)?draft.materials:[];
 const ids=new Set(existing.map(row=>row.id));
 const added=rows.filter(row=>row&&row.id&&!ids.has(row.id)&&ids.add(row.id));
 return added.length?{...draft,materials:[...existing,...added]}:draft;
}

export function materialReadScope(account,epoch){return JSON.stringify([account?.workspace_id,account?.user_id,account?.username,account?.role,epoch]);}
