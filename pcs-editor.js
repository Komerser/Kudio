/* PCS is parsed only by the backend. Source drafts and async results belong to
   one project and one revision; polling never replaces a local editing draft. */
const scriptDrafts=new Map();
let scriptOwner=null,scriptTimer=null,scriptView='raw';
const scriptNewKey='__new__';
function scriptKey(){return project?.id||scriptNewKey}
function projectSource(p){return typeof p.source_text==='string'?p.source_text:(p.segments||[]).map(s=>s.text).join('\n\n')}
function makeScriptDraft(p){
 const text=p?projectSource(p):'',format=p?.source_format==='pcs'?'pcs':'txt',limit=Number(p?.limit||160);
 return {text,format,limit,savedText:text,savedFormat:format,savedLimit:limit,revision:0,request:0,dirty:false,parse:p?.source_format!=='legacy'&&p?.ast?{ast:p.ast,diagnostics:p.diagnostics||[],valid:!(p.diagnostics||[]).some(d=>d.level==='error'),stats:{},source_format:p.source_format}:null,compiled:null,filename:'',parseBusy:false,compileBusy:false,applyBusy:false};
}
function scriptDraft(){const key=scriptKey();if(!scriptDrafts.has(key))scriptDrafts.set(key,makeScriptDraft(project));return scriptDrafts.get(key)}
function scriptIsDirty(){return !!project&&!!scriptDrafts.get(project.id)?.dirty}
function scriptHasErrors(){return (scriptDraft().parse?.diagnostics||[]).some(d=>d.level==='error')}
function scriptParsed(){const s=scriptDraft();return !!s.parse&&!s.parseBusy&&s.parse.valid!==false&&!scriptHasErrors()}
function syncScriptOwner(){
 const key=scriptKey(),s=scriptDraft();
 if(scriptOwner!==key){clearTimeout(scriptTimer);scriptTimer=null;scriptOwner=key;$('source').value=s.text;$('sourceFormat').value=s.format;$('limit').value=s.limit;scriptView='raw';}
 else if(project&&!s.dirty&&s.savedText!==projectSource(project)){
  const fresh=makeScriptDraft(project);scriptDrafts.set(key,fresh);if($('source').value!==fresh.text)$('source').value=fresh.text;
 }
 $('importCard').classList.remove('hidden');$('newProjectName').classList.toggle('hidden',!!project);$('create').classList.toggle('hidden',!!project);$('applyScript').classList.toggle('hidden',!project);
}
function scriptSourceChanged(){
 const s=scriptDraft();s.text=$('source').value;s.format=$('sourceFormat').value||'txt';s.limit=Number($('limit').value)||160;s.revision++;s.request++;s.parse=null;s.compiled=null;s.parseBusy=false;s.compileBusy=false;
 s.dirty=!!project&&(s.text!==s.savedText||s.format!==s.savedFormat||s.limit!==s.savedLimit);renderScriptWorkspace();if(project)render();
 clearTimeout(scriptTimer);const key=scriptKey(),revision=s.revision;
 scriptTimer=setTimeout(()=>{if(key===scriptKey()&&revision===scriptDraft().revision)safe(()=>parseScript())},420);
}
/** Translate Python Unicode codepoint offsets to textarea UTF-16 offsets. */
function sourceSelectionOffset(text,offset){return Array.from(text).slice(0,Math.max(0,Number(offset)||0)).join('').length}
function selectScriptSpan(node){
 const source=$('source'),s=scriptDraft();scriptView='raw';renderScriptView();source.focus();source.setSelectionRange(sourceSelectionOffset(s.text,node.source_start),sourceSelectionOffset(s.text,node.source_end));source.scrollIntoView({behavior:'smooth',block:'center'});
}
function controlLabel(node){
 const command=node.command||({page:'p',pause:'pause',rate:'rate',section:'section'})[node.type],value=node.value??node.page??node.duration_ms??node.name;
 if(command==='p')return `PPT · ${value}`;
 if(command==='pause')return `PAUSE · ${value} ms`;
 if(command==='rate')return `SPEED · ${Number(value).toFixed(2)}×`;
 if(command==='section')return `SECTION · ${value}`;
 return `CONTROL · ${command||node.type}`;
}
function scriptChip(node,clickable=true){const chip=document.createElement(clickable?'button':'span');chip.className='pcs-chip pcs-'+(node.command||node.type);chip.textContent=controlLabel(node);if(clickable){chip.type='button';chip.onclick=()=>selectScriptSpan(node);chip.title='点击定位原始脚本'}return chip}
function setScriptText(id,text){if($(id).textContent!==text)$(id).textContent=text}
function renderScriptView(){
 $('source').classList.toggle('hidden',scriptView!=='raw');$('scriptToolbar').classList.toggle('hidden',scriptView!=='raw');$('scriptVisual').classList.toggle('hidden',scriptView!=='visual');
 $('rawMode').classList.toggle('tab-active',scriptView==='raw');$('visualMode').classList.toggle('tab-active',scriptView==='visual');$('rawMode').setAttribute('aria-selected',String(scriptView==='raw'));$('visualMode').setAttribute('aria-selected',String(scriptView==='visual'));
}
function renderScriptParse(s){
 const parse=s.parse,ast=parse?.ast||[],key=JSON.stringify([scriptKey(),s.revision,s.parseBusy,parse]);
 if($('scriptVisual').dataset.renderKey===key)return;$('scriptVisual').dataset.renderKey=key;
 const visual=$('scriptVisual'),nodes=$('astNodes'),diagnostics=$('parserDiagnostics');visual.replaceChildren();nodes.replaceChildren();diagnostics.replaceChildren();
 if(!parse){setScriptText('parserSummary',s.parseBusy?'正在解析…':'脚本尚未解析 · AST 和推理计划待更新');setScriptText('astJson','');const hint=document.createElement('p');hint.className='muted';hint.textContent='解析后显示正文与控制 Chip。';visual.append(hint);return}
 const errors=(parse.diagnostics||[]).filter(d=>d.level==='error').length,warnings=(parse.diagnostics||[]).filter(d=>d.level==='warning').length,controls=ast.filter(n=>n.type==='control');
 setScriptText('parserSummary',`${errors?'发现 '+errors+' 个错误':'✓ 脚本语法有效'} · ${controls.filter(n=>n.command==='p').length} Page · ${controls.filter(n=>n.command==='pause').length} Pause · ${controls.filter(n=>n.command==='rate').length} Rate · ${controls.filter(n=>n.command==='section').length} Section · ${warnings} Warning`);
 for(const d of parse.diagnostics||[]){const diagnostic=document.createElement('button');diagnostic.className='pcs-diagnostic '+d.level;const before=Array.from(s.text).slice(0,d.source_start||0).join(''),line=before.split('\n').length;diagnostic.textContent=`${(d.level||'info').toUpperCase()} · 第 ${line} 行 · ${d.message} (${d.code})`;diagnostic.onclick=()=>selectScriptSpan(d);diagnostics.append(diagnostic)}
 for(const [index,n] of ast.entries()){
  if(n.type==='control')visual.append(scriptChip(n));else{const text=document.createElement('span');text.className='pcs-text';text.textContent=n.text||'';visual.append(text)}
  const row=document.createElement('button');row.className='ast-node';row.onclick=()=>selectScriptSpan(n);const number=document.createElement('span');number.className='ast-number';number.textContent=`#${index+1}`;const label=document.createElement('strong');label.textContent=n.type==='control'?controlLabel(n):'TEXT';const content=document.createElement('span');content.textContent=n.type==='text'?n.text:`源码 ${n.source_start}–${n.source_end}`;row.append(number,label,content);nodes.append(row);
 }
 setScriptText('astJson',JSON.stringify(ast,null,2));
}
function planItems(plan){return Array.isArray(plan)?plan:(plan?.items||[])}
function segmentMetadata(s){
 const row=document.createElement('div');row.className='pcs-metadata';
 for(const [type,value] of [['page',s.page],['section',s.section],['rate',s.rate??s.overrides?.speed??project?.voice?.speed??1]]){if(value===null||value===undefined)continue;const badge=document.createElement('span');badge.className='pcs-chip pcs-'+type;badge.textContent=type==='page'?`PPT ${value}`:type==='section'?`SECTION ${value}`:`${Number(value).toFixed(2)}×`;row.append(badge)}
 return row;
}
function renderScriptPlan(s){
 const preview=s.compiled,plan=preview?.execution_plan||(!s.dirty?project?.execution_plan:null),segments=preview?.segments||(!s.dirty?project?.segments:[])||[],key=JSON.stringify([scriptKey(),s.revision,!!preview,s.dirty,plan,segments.map(n=>[n.id,n.text,n.page,n.section,n.rate,n.status])]);
 if($('executionPlan').dataset.renderKey===key)return;$('executionPlan').dataset.renderKey=key;const root=$('executionPlan');root.replaceChildren();
 setScriptText('planSummary',preview?'预览计划 · 应用后才更改作品和音频':s.dirty?'推理计划待更新 · 请解析、预览并应用脚本':plan?'已编译 · 按源码顺序执行':'解析成功后可预览最终推理计划');
 let number=0;for(const item of planItems(plan)){
  if(item.kind==='event'){const event=document.createElement('div');event.className='pcs-plan-event';event.append(scriptChip(item,false));root.append(event);continue}
  if(item.kind!=='speech')continue;const speech=segments.find(segment=>segment.id===(item.segment_id||item.id))||item;const box=document.createElement('article');box.className='pcs-plan-speech';const title=document.createElement('strong');title.textContent=`Speech #${++number}`;const text=document.createElement('p');text.textContent=speech.text||item.text||'';box.append(title,segmentMetadata(speech),text);root.append(box);
 }
}
function renderScriptWorkspace(){
 if(loading||terminating||exited)return;syncScriptOwner();const s=scriptDraft(),busy=active===project?.id||s.applyBusy;
 renderScriptView();renderScriptParse(s);renderScriptPlan(s);
 const state=s.dirty?'SOURCE MODIFIED · 解析 / 计划 / 时间轴 / 导出待更新':scriptHasErrors()?'PARSER ERROR · 请修正源码':s.parseBusy?'PARSING':project?.timeline?.timing_status==='exact'?'EXACT TIMELINE · 可导出':project?.execution_plan?.length?'COMPILED':scriptParsed()?'PARSED':'SOURCE';setScriptText('scriptState',state);
 setScriptText('sourceNotice',project?.source_format==='legacy'?'旧项目音频安全保留。当前源码由旧片段重建；“应用并编译脚本”会显式转为脚本项目。':s.dirty?'修改只保存在本次页面的作品草稿中；应用后才重新编译。相同片段尽可能复用音频。':'原始脚本是唯一正文来源。控制标签用于 Page、Pause、Rate、Section，不参与朗读。');
 $('parseScript').disabled=s.parseBusy||s.applyBusy;$('previewScript').disabled=!scriptParsed()||s.compileBusy||busy;$('applyScript').disabled=!project||!scriptParsed()||s.compileBusy||busy;
 $('source').disabled=busy;$('file').disabled=busy;$('sourceFormat').disabled=busy;$('limit').disabled=busy;$('insertControl').disabled=busy;
 if(project){const blocked=s.dirty||scriptHasErrors();for(const id of ['start','retry','export','exportAll','exportSrt','exportKson','exportKsonPreview'])if(blocked)$(id).disabled=true;renderScriptTimeline();renderKsonPreview()}
}
async function parseScript(){
 const key=scriptKey(),s=scriptDraft(),revision=s.revision,request=++s.request,text=s.text,format=s.format,view=navigation;s.parseBusy=true;renderScriptWorkspace();
 try{const result=await api('pcs/parse',{text,source_format:format,limit:s.limit});if(request!==s.request||revision!==s.revision)return null;s.parse=result;s.parseBusy=false;if(key===scriptKey()&&view===navigation){$('astPanel').open=true;renderScriptWorkspace()}return result}
 catch(e){if(request===s.request){s.parseBusy=false;if(key===scriptKey()&&view===navigation){renderScriptWorkspace();throw e}}return null}
}
async function previewScript(){
 const s=scriptDraft();if(!scriptParsed())throw Error('请先成功解析脚本');const key=scriptKey(),revision=s.revision,view=navigation;s.compileBusy=true;renderScriptWorkspace();
 try{const result=await api('pcs/compile',{text:s.text,source_format:s.format,limit:s.limit,voice:project?.voice});if(revision!==s.revision)return;s.compiled=result;if(result.ast)s.parse=result;if(key===scriptKey()&&view===navigation)$('planPanel').open=true}
 finally{s.compileBusy=false;if(key===scriptKey()&&view===navigation)renderScriptWorkspace()}
}
async function applyScript(){
 if(!project||!scriptParsed())throw Error('请先修正错误并解析脚本');if(active===project.id)throw Error('请先暂停生成队列');const owner=project.id,s=scriptDraft(),revision=s.revision,view=navigation;s.applyBusy=true;renderScriptWorkspace();
 try{const result=await api('project/source',{id:owner,text:s.text,source_format:s.format,limit:s.limit});const updated=result.project||result;if(!updated.id)throw Error('作品保存结果无效');
  if(revision===s.revision)scriptDrafts.set(owner,makeScriptDraft(updated));if(owner!==project?.id||view!==navigation)return;stopPlayback();project=updated;active=result.active??null;signature='';render();await list();notify('脚本已应用并编译；未变化片段保留已生成音频');
 }finally{s.applyBusy=false;if(owner===project?.id&&view===navigation)renderScriptWorkspace()}
}
function renderScriptTimeline(){
 const data=project.timeline;if(!data)return;setScriptText('timelineSummary',`${scriptIsDirty()?'STALE · ':''}${data.timing_status==='exact'?'Exact Timeline · 实测时间轴':'Estimated Timeline · 估算时间轴'} ${timecode((data.duration_ms||0)/1000)} · 含默认间隔及显式停顿；控制事件按源码顺序执行。`);const key=JSON.stringify([project.id,data,scriptIsDirty()]);if($('timelineEvents').dataset.renderKey===key)return;$('timelineEvents').dataset.renderKey=key;
 const strip=$('timelineStrip'),events=$('timelineEvents');strip.replaceChildren();events.replaceChildren();$('groupLegend').classList.add('hidden');const total=Math.max(1,data.duration_ms||0);
 for(const entry of data.segments||[]){const start=entry.start_ms||0,end=entry.end_ms||start;const b=button('',()=>playSegment(entry.id),!project.segments.find(s=>s.id===entry.id&&s.status==='done'));b.style.left=`${start/total*100}%`;b.style.width=`${Math.max(.2,(end-start)/total*100)}%`;b.className='pcs-speech-block '+(entry.estimated?'pending':'done');b.title=`${timecode(start/1000)} → ${timecode(end/1000)} · ${entry.text||''}`;b.setAttribute('aria-label',b.title);strip.append(b)}
 for(const [index,event] of (data.events||[]).entries()){const start=event.time_ms??event.start_ms??0;const b=document.createElement('button');b.className='pcs-timeline-event pcs-'+event.type;b.disabled=scriptIsDirty();b.style.left=`${Math.min(98,start/total*100)}%`;b.style.top=`${(index%4)*29}px`;b.textContent=event.type==='page'?`P${event.page}`:event.type==='pause'?`Pause ${event.duration_ms}ms`:event.type==='rate'?`${Number(event.value).toFixed(2)}×`:event.name||'Section';b.title=`${timecode(start/1000)} · ${controlLabel(event)}`;b.onclick=()=>selectScriptSpan(event);events.append(b)}
 setScriptText('timelineSummary',`${scriptIsDirty()?'STALE · ':''}${data.timing_status==='exact'?'Exact Timeline · 实测时间轴':'Estimated Timeline · 估算时间轴'} ${timecode((data.duration_ms||0)/1000)} · 含默认间隔及显式停顿；控制事件按源码顺序执行。`);
}
function renderKsonPreview(){
 const kson=project.kson;$('ksonPanel').classList.toggle('hidden',!kson);const dirty=scriptIsDirty(),exact=project.timeline?.timing_status==='exact';
 setScriptText('ksonStatus',dirty?'STALE · 应用脚本后更新':exact?'Exact · 实测 WAV 帧时间':'Estimated · 推理前结构预览');setScriptText('ksonJson',kson?JSON.stringify(kson,null,2):'');$('copyKson').disabled=dirty||!kson;for(const id of ['exportKson','exportKsonPreview'])$(id).disabled=dirty||!exact||active===project.id||scriptHasErrors();
}
function continuousGapMs(){
 if(!project||!currentPlay)return 0;const current=timeEntries.find(s=>s.id===currentPlay.id),next=timeEntries.find(s=>s.id===playQueue[playQueue.indexOf(currentPlay.id)+1]);
 return current&&next?Math.max(0,Math.round((next.timeline.start-current.timeline.end)*1000)):Math.max(0,Number(project.voice.gap)||0)*1000;
}
// Capture drafts before navigation changes the owner. A stale parser response can
// update its own draft but cannot paint over the latest selected project.
const loadBeforeScript=load;load=async function(id){if(!loading&&scriptOwner===scriptKey()){const s=scriptDraft();s.text=$('source').value;s.format=$('sourceFormat').value||s.format;s.limit=Number($('limit').value)||s.limit}await loadBeforeScript(id);if(!loading&&(!id||project?.id===id))renderScriptWorkspace()};
const showNewBeforeScript=showNew;showNew=function(){showNewBeforeScript();renderScriptWorkspace()};
const renderBeforeScript=render;render=function(stopping=false){renderBeforeScript(stopping);renderScriptWorkspace()};
$('source').oninput=scriptSourceChanged;$('sourceFormat').onchange=scriptSourceChanged;$('limit').oninput=scriptSourceChanged;
$('rawMode').onclick=()=>{scriptView='raw';renderScriptView()};$('visualMode').onclick=()=>{scriptView='visual';renderScriptView();if(!scriptDraft().parse)safe(()=>parseScript())};
for(const [id,command,value] of [['insertPage','p','1'],['insertPause','pause','800'],['insertRate','rate','0.90'],['insertSection','section','intro']])$(id).onclick=()=>{$('controlCommand').value=command;$('controlValue').value=value;$('controlValue').focus()};
$('insertControl').onclick=()=>{const source=$('source'),tag=`#[${$('controlCommand').value}:${$('controlValue').value.trim()}]#`,start=source.selectionStart??source.value.length,end=source.selectionEnd??start;source.value=source.value.slice(0,start)+tag+source.value.slice(end);source.focus();source.setSelectionRange(start+tag.length,start+tag.length);scriptSourceChanged()};
$('parseScript').onclick=()=>safe(()=>parseScript());$('previewScript').onclick=()=>safe(()=>previewScript());$('applyScript').onclick=()=>safe(()=>applyScript());
$('file').onchange=()=>safe(async()=>{const f=$('file').files[0];if(!f)return;if(f.size>15000000)throw Error('文件过大，请按卷导入');const key=scriptKey(),view=navigation,bytes=await f.arrayBuffer();let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes)}catch{text=new TextDecoder('gb18030').decode(bytes)}if(view!==navigation||key!==scriptKey())return;$('source').value=text;$('sourceFormat').value=/\.pcs$/i.test(f.name)?'pcs':'txt';scriptDraft().filename=f.name;if(!project)$('title').value=f.name.replace(/\.(txt|pcs)$/i,'');scriptSourceChanged();clearTimeout(scriptTimer);await parseScript()});
$('create').onclick=()=>safe(async()=>{const s=scriptDraft(),key=scriptKey(),view=navigation;const result=await api('create',{title:$('title').value,text:s.text,limit:s.limit,source_format:s.format,filename:s.filename});await list();if(key!==scriptKey()||view!==navigation)return;scriptDrafts.delete(scriptNewKey);await load(result.id||result.project?.id);notify('作品已保存。请核对脚本、音色后开始生成')});
for(const id of ['start','retry','export','exportAll','exportSrt']){const handler=$(id).onclick;$(id).onclick=event=>{if(scriptIsDirty()||scriptHasErrors()){notify('脚本有未应用修改或解析错误，请先解析并应用');return}return handler?.(event)}}
for(const id of ['exportKson','exportKsonPreview'])$(id).onclick=()=>safe(async()=>{if(scriptIsDirty())throw Error('请先应用脚本');if(project?.timeline?.timing_status!=='exact')throw Error('请先生成全部语音，正式 KSON 使用实测时间轴');await action('export-kson');notify('KSON 已导出到作品的 exports 文件夹')});
$('copyKson').onclick=()=>safe(async()=>{if(scriptIsDirty())throw Error('请先应用脚本');await navigator.clipboard.writeText($('ksonJson').textContent);notify('KSON JSON 已复制')});
$('jumpGroups').onclick=()=>{$('groupPanel').open=true;$('groupPanel').scrollIntoView({behavior:'smooth'})};
for(const id of ['create','parseScript','previewScript','applyScript','exportKson','exportKsonPreview','copyKson']){const b=$(id),handler=b.onclick;b.onclick=event=>runUI(b,()=>handler(event))}
renderScriptWorkspace();
