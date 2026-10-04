// UI language regressions using the real runtime, with no live engine or user data.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const html=fs.readFileSync(__dirname+'/index.html','utf8');
const rebuild=fs.readFileSync(__dirname+'/rebuild.js','utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const voice=name=>({name,gpt:name+'.ckpt',sovits:name+'.pth',reference:name+'.wav',prompt:'参考台词',prompt_lang:'zh',text_lang:'zh',speed:1,seed:42,gap:.3});
const roles=[{id:'r1',name:'长者',voice:voice('长者'),profile:{description:'沉稳',tags:['低沉'],color:'#287f78'}},{id:'r2',name:'少女',voice:voice('少女'),profile:{description:'明亮',tags:['轻快'],color:'#547cc2'}}];
function fixture(id,format='pcs'){
 const text=format==='pcs'?'#[voice:male_elder]#第一句。#[voice:female_child]#第二句。':'普通正文。';
 const ast=format==='pcs'?[{type:'control',command:'voice',value:'male_elder',source_start:0,source_end:20},{type:'text',text:'第一句。',source_start:20,source_end:24},{type:'control',command:'voice',value:'female_child',source_start:24,source_end:46},{type:'text',text:'第二句。',source_start:46,source_end:50}]:[{type:'text',text,source_start:0,source_end:text.length}];
 const segments=(format==='pcs'?['male_elder','female_child']:['']).map((label,i)=>({id:id+(i+1),text:format==='pcs'?(i?'第二句。':'第一句。'):text,voice_label:label||null,chapter:'正文',role:'旁白',status:'pending',duration:0,page:null,section:null,rate:null,source_format:format,overrides:{},resolved_role_id:label==='female_child'?'r2':'r1',resolved_role_name:label==='female_child'?'少女':'长者'}));
 return {project:{id,title:id,voice:voice('作品原声音'),source_text:text,source_format:format,limit:160,ast,diagnostics:[],segments,execution_plan:segments.map(s=>({kind:'speech',segment_id:s.id})),timeline:{timing_status:'estimated',duration_ms:2000,segments:segments.map((s,i)=>({...s,start_ms:i*1000,end_ms:(i+1)*1000,estimated:true})),events:[]},exports:[],default_role_id:'r1',voice_bindings:format==='pcs'?{male_elder:'r1',female_child:'r2'}:{},role_snapshots:Object.fromEntries(roles.map(r=>[r.id,clone(r)])),voice_labels:format==='pcs'?['male_elder','female_child']:[],voice_binding_errors:[]},active:null};
}


const catalogScope={window:{}};
vm.runInNewContext(fs.readFileSync(__dirname+'/i18n-catalog.js','utf8'),catalogScope);
const catalog=catalogScope.window.KUDIO_MESSAGES;
function harness(){
 const elements=new Map(),requests=[],storage=new Map(),effects={fetch:0,created:0,revoked:0};let activeElement=null,handler;
 class Text{
  constructor(text){this.nodeType=3;this.nodeValue=String(text)}
  get textContent(){return this.nodeValue}set textContent(value){this.nodeValue=String(value)}
  get parentElement(){return this.parentNode?.nodeType===1?this.parentNode:null}
  get isConnected(){return !!this.parentNode?.isConnected}
 }
 class Element{
  constructor(tag='div'){this.nodeType=1;this.tagName=tag.toUpperCase();this._nodes=[];this.dataset={};this.attributes={};this._value='';this.disabled=false;this.open=false;this.classes=new Set();this.style={setProperty(k,v){this[k]=v}};this.classList={add:(...cs)=>cs.forEach(c=>this.classes.add(c)),remove:(...cs)=>cs.forEach(c=>this.classes.delete(c)),contains:c=>this.classes.has(c),toggle:(c,on)=>{const add=on===undefined?!this.classes.has(c):on;add?this.classes.add(c):this.classes.delete(c);return add}};this.selectionStart=0;this.selectionEnd=0;this.events=new Map()}
  get parentElement(){return this.parentNode?.nodeType===1?this.parentNode:null}get isConnected(){return this===root||!!this.parentNode?.isConnected}
  get children(){return this._nodes.filter(n=>n.nodeType===1)}get childNodes(){return this._nodes}
  get className(){return [...this.classes].join(' ')}set className(value){this.classes=new Set(String(value).split(/\s+/).filter(Boolean))}
  get textContent(){return this._nodes.map(e=>e.textContent).join('')}set textContent(value){this.replaceChildren(new Text(value??''))}
  get value(){return this._value}set value(value){this._value=String(value??'')}
  get options(){return this.children}getAttribute(k){return this.attributes[k]??null}hasAttribute(k){return this.getAttribute(k)!==null}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='id')this.id=String(v);else if(k==='class')this.className=v;else if(k==='value')this.value=v;else if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v);else this[k]=v}
  removeAttribute(k){delete this.attributes[k];delete this[k]}
  append(...items){for(let item of items){if(typeof item==='string')item=new Text(item);item.parentNode?.removeChild(item);item.parentNode=this;this._nodes.push(item)}}
  prepend(...items){for(const item of items){item.parentNode?.removeChild(item);item.parentNode=this}this._nodes.unshift(...items)}
  removeChild(item){this._nodes=this._nodes.filter(e=>e!==item);item.parentNode=null}
  replaceChildren(...items){for(const item of this._nodes)item.parentNode=null;this._nodes=[];this.append(...items)}
  add(item){this.append(item)}after(){}remove(){this.parentNode?.removeChild(this)}
  contains(item){return descendants(this).includes(item)}
  querySelectorAll(selector){return descendants(this).slice(1).filter(e=>e.nodeType===1&&matches(e,selector))}querySelector(selector){return this.querySelectorAll(selector)[0]||null}
  closest(selector){let e=this;while(e){if(e.nodeType===1&&matches(e,selector))return e;e=e.parentNode}return null}
  addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,[]);this.events.get(type).push(fn)}
  async dispatch(type,extra={}){const event={target:this,preventDefault(){},...extra};for(const fn of this.events.get(type)||[])await fn(event);return this['on'+type]?.(event)}
  click(){if(!this.disabled)return this.dispatch('click')}focus(){activeElement=this}setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b}scrollIntoView(){}getBoundingClientRect(){return {left:0,right:100,top:0,bottom:100}}
  pause(){this.pauseCount=(this.pauseCount||0)+1}load(){}play(){return Promise.resolve()}showModal(){this.open=true}close(){this.open=false}
 }
 function descendants(e){return [e,...(e.childNodes||[]).flatMap(descendants)]}
 function matches(e,selector){return selector.split(',').some(raw=>{const s=raw.trim();if(s==='*')return true;if(s.includes(' ')){const parts=s.split(/\s+/);return matches(e,parts.pop())&&e.parentNode?.closest(parts.join(' '))}if(s[0]==='#')return e.id===s.slice(1);if(s[0]==='.')return e.classes.has(s.slice(1));const attr=s.match(/^([a-z]*)\[([^=\]]+)(?:=['"]?([^'"\]]+)['"]?)?\]$/i);if(attr)return (!attr[1]||e.tagName.toLowerCase()===attr[1])&&e.getAttribute(attr[2])!==null&&(attr[3]===undefined||e.getAttribute(attr[2])===attr[3]);return e.tagName.toLowerCase()===s})}
 const root=new Element('html'),body=new Element('body');root.append(body);
 const stack=[body],voidTags=new Set(['input','img','br','hr','meta','link','source']);
 for(const token of html.replace(/<script\b[\s\S]*?<\/script>/gi,'').match(/<\/?[a-z][^>]*>|[^<]+/gi)||[]){
  if(!token.startsWith('<')){stack.at(-1).append(new Text(token));continue}
  if(token.startsWith('</')){const closing=token.match(/^<\/([a-z0-9-]+)/i)[1].toUpperCase();const index=stack.findLastIndex(e=>e.tagName===closing);if(index>0)stack.length=index;continue}
  const tag=token.match(/^<([a-z0-9-]+)/i)[1].toLowerCase();if(['html','head','body'].includes(tag))continue;
  const e=new Element(tag);for(const a of token.matchAll(/([\w-]+)\s*=\s*"([^"]*)"/g))e.setAttribute(a[1],a[2]);if(/\bchecked\b/.test(token))e.checked=true;if(/\bopen\b/.test(token))e.open=true;
  stack.at(-1).append(e);if(e.id)elements.set(e.id,e);if(!voidTags.has(tag)&&!token.endsWith('/>'))stack.push(e);
 }
 const element=id=>{if(!elements.has(id)){const e=new Element();e.id=id;elements.set(id,e);body.append(e)}return elements.get(id)};
 const document={body,documentElement:root,getElementById:element,createElement:tag=>new Element(tag),querySelectorAll:s=>root.querySelectorAll(s),querySelector:s=>root.querySelector(s),get activeElement(){return activeElement},createTreeWalker:e=>{const nodes=descendants(e===document?root:e).filter(n=>n.nodeType===3);let index=0;return {nextNode:()=>nodes[index++]||null}},dispatchEvent(){}};
 const context=vm.createContext({document,NodeFilter:{SHOW_TEXT:4},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail}},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null,removeItem:k=>storage.delete(k)},sessionStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)},fetch:async()=>{effects.fetch++;return {ok:true,blob:async()=>({})}},URL:{createObjectURL:()=>{effects.created++;return 'blob:'+effects.created},revokeObjectURL(){effects.revoked++}},Option:class extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value}},setTimeout:()=>1,clearTimeout(){},setInterval(){},console,Map,encodeURIComponent,TextDecoder,navigator:{clipboard:{writeText:async()=>{}}},confirm:()=>true,prompt:()=>null});
 context.window=context;context.KUDIO_MESSAGES=catalog;const run=code=>vm.runInContext(code,context);context.request=async(path,data)=>{requests.push({path,data:clone(data??null)});return handler(path,data)};
 const projects=new Map([['A',fixture('A')],['B',fixture('B','txt')]]);let savedRoles=clone(roles);
 handler=async(path,data)=>{if(path.startsWith('project?id='))return clone(projects.get(path.split('=')[1]));if(path==='state')return {projects:[...projects.values()].map(({project:p})=>({id:p.id,title:p.title,count:p.segments.length,done:0})),trash:[],active:null};if(path==='presets')return {presets:clone(savedRoles)};throw Error('Unexpected API '+path)};
 run(fs.readFileSync(__dirname+'/i18n.js','utf8'));run(html.match(/<script>([\s\S]*?)<\/script>/)[1]);run('api=request');
 for(const name of ['studio.js','library.js','assets.js','segmentation.js','pcs-editor.js']){let source=fs.readFileSync(__dirname+'/'+name,'utf8');if(name==='studio.js')source=source.split('// Boot')[0].replace('},4000);monitor();','},4000);');if(name==='segmentation.js')source=source.split('// Boot')[0];if(name==='assets.js')source=source.replace(/refreshAssets\(\)\.catch\([\s\S]*$/,'');run(source)}
 run(rebuild);
 return {run,element,document,requests,projects,context,effects,setHandler(fn){handler=fn},async boot(){await run('loadPresets()');await run("load('A')")},language(next){context.KudioI18n.setLanguage(next)},t(key,params){return context.KudioI18n.t(key,params)},async change(id,value){element(id).value=value;await element(id).dispatch('input')}};
}
const tests=[];const test=(name,fn)=>tests.push({name,fn});
test('every studio UI key has English and Japanese with matching placeholders',()=>{
 for(const name of ['studio.js','pcs-editor.js','segmentation.js']){
  const source=fs.readFileSync(__dirname+'/'+name,'utf8');
  for(const match of source.matchAll(/uiText\('([^']+)'\)/g))assert(catalog[match[1]],name+' missing translation: '+match[1]);
 }
 for(const [key,translations] of Object.entries(JSON.parse(fs.readFileSync(__dirname+'/i18n-studio.json','utf8')))){
  const placeholders=value=>[...value.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort();
  for(const language of ['en','ja']){assert.equal(typeof translations[language],'string',key+' missing '+language);assert.deepEqual(placeholders(translations[language]),placeholders(key),key+' placeholder mismatch: '+language)}
 }
});
test('language switches redraw cached labels and preserve all unsaved drafts',async()=>{
 const h=harness();await h.boot();h.run("selectRole('r1')");await h.change('name','试听');await h.change('roleDescription','保存分段');
 const bindings=h.element('voiceBindings').querySelectorAll('select');bindings[0].value='r2';await bindings[0].dispatch('change');
 h.element('source').value='#[voice:male_elder]#未保存正文。';h.run('scriptSourceChanged()');
 h.run("groupEdit={id:'g',owner:'A'}");h.element('groupName').value='播放列表结束';h.element('rangeStart').value='1';h.element('rangeEnd').value='2';
 const snapshot=h.run('JSON.stringify([project,scriptDraft(),roleEditorDraft,castingDraft(),groupEdit])'),before=h.requests.length;
 for(const language of ['en','ja','zh']){
  h.language(language);
  assert.equal(h.run('JSON.stringify([project,scriptDraft(),roleEditorDraft,castingDraft(),groupEdit])'),snapshot);
  assert.equal(h.element('name').value,'试听');assert.equal(h.element('roleDescription').value,'保存分段');assert.equal(h.element('groupName').value,'播放列表结束');
  assert.equal(h.element('source').value,'#[voice:male_elder]#未保存正文。');assert.equal(h.element('groupEditStatus').textContent,h.t('正在编辑已确认分段'));
  assert.equal(h.element('segments').querySelector('strong').textContent,h.t('片段 #{number}',{number:1}));
  assert.equal(h.run("castingDraft().voice_bindings.male_elder"),'r2');assert.equal(h.requests.length,before);
  assert(h.element('parserSummary').textContent.includes(h.t('PCS 尚未解析 · AST 和推理计划待更新')));
 }
});
test('open segment editor translates labels without replacing unsaved fields',async()=>{
 const h=harness();await h.boot();h.run("openEditor('A2')");
 for(const [id,value] of Object.entries({editPrompt:'尚未选择片段',editReference:'D:\\自定义\\试听.wav',editSpeed:'1.73',editSeed:'135',editPreset:'r1'}))h.element(id).value=value;
 const snapshot=['editText','editPrompt','editReference','editSpeed','editSeed','editPreset','editLanguage','editPromptLang'].map(id=>h.element(id).value);
 for(const language of ['en','ja']){h.language(language);assert(h.element('segmentEditor').open);assert.deepEqual(['editText','editPrompt','editReference','editSpeed','editSeed','editPreset','editLanguage','editPromptLang'].map(id=>h.element(id).value),snapshot);assert(h.element('editHeading').textContent.includes(h.t('片段 #{number} · 独立参数',{number:2})));assert.equal(h.element('editPreset').options[0].textContent,h.t('不更改参考音频'))}
});
test('active Blob playback keeps source, time, token, queue and ownership',async()=>{
 const h=harness();await h.boot();h.run("project.segments[0].status='done';project.segments[0].duration=2;render()");await h.run("playSegment('A1')");
 h.run('player.currentTime=.5;updatePlayerPosition()');const player=h.element('continuousAudio'),snapshot=h.run('JSON.stringify([currentPlay,playToken,playQueue])'),src=player.src,pauses=player.pauseCount,effects=clone(h.effects);
 for(const language of ['en','ja','zh']){h.language(language);assert.equal(h.run('JSON.stringify([currentPlay,playToken,playQueue])'),snapshot);assert.equal(player.src,src);assert.equal(player.currentTime,.5);assert.equal(player.pauseCount,pauses);assert.deepEqual(h.effects,effects);assert.equal(h.element('nowPlaying').textContent,'#1 · 第一句。');assert.equal(h.element('playPosition').textContent,h.t('整书位置 {estimate}{time} · 本段 {segmentTime}',{estimate:'≈ ',time:'00:00.500',segmentTime:'00:00.500'}))}
});
test('waiting playback changes its notice without restarting audio',async()=>{
 const h=harness();await h.boot();h.element('skipPending').checked=false;await h.run("playSegment('A1')");const token=h.run('playToken'),pauses=h.element('continuousAudio').pauseCount;
 for(const language of ['en','ja']){h.language(language);assert.equal(h.element('playPosition').textContent,h.t('等待此片段生成，完成后自动继续'));assert.equal(h.run('playToken'),token);assert.equal(h.run('waiting'),true);assert.equal(h.element('continuousAudio').pauseCount,pauses)}
});
test('busy buttons stay disabled and restore the current language after async completion',async()=>{
 const h=harness();await h.boot();h.language('en');let finish,calls=0;h.context.hold=()=>{calls++;return new Promise(resolve=>finish=resolve)};
 h.run("testButton=button(uiText('编辑分段'),hold)");const pending=h.run('testButton.onclick()');assert.equal(calls,1);assert(h.context.testButton.disabled);
 h.language('ja');assert.equal(h.context.testButton.textContent,h.t('处理中…'));await h.run('testButton.onclick()');assert.equal(calls,1);
 finish();await pending;assert.equal(h.context.testButton.textContent,h.t('编辑分段'));assert.equal(h.context.testButton.disabled,false);assert.equal(h.run('uiOperations'),0);
});
test('busy static buttons retain their icon and translate detached text on restoration',async()=>{
 const h=harness();await h.boot();const b=h.element('start'),original=b.childNodes.slice();let finish;h.context.hold=()=>new Promise(resolve=>finish=resolve);
 const pending=h.run("runUI($('start'),hold)");h.language('ja');assert.equal(b.textContent,h.t('处理中…'));finish();await pending;
 assert.deepEqual(b.childNodes,original);assert(b.textContent.includes(h.t('开始 / 继续生成')),'Restored static caption stayed in its old language: '+b.textContent);
});
test('async failure notices use the new language and restore buttons',async()=>{
 const h=harness();await h.boot();h.language('en');let fail;h.context.hold=()=>new Promise((resolve,reject)=>fail=reject);
 h.run("testButton=button(uiText('编辑分段'),hold)");const pending=h.run('testButton.onclick()'),error=h.t('请先选择作品');h.language('ja');fail(Error(error));await pending;
 assert.equal(h.element('toast').textContent,h.t('操作失败：{error}',{error:h.t('请先选择作品')}));assert.equal(h.context.testButton.textContent,h.t('编辑分段'));assert.equal(h.context.testButton.disabled,false);
});
test('user group names remain exact even when they match a UI message',async()=>{
 const h=harness();await h.boot();h.run("project.source_format='legacy';delete project.timeline;project.groups=[{id:'g',name:'试听',start:'A1',end:'A2',color:'#547cc2'}];project.suggestions=[{id:'sg',name:'播放列表结束',start:'A1',end:'A2'}];render()");h.language('en');
 const legend=h.element('groupLegend').querySelectorAll('button');assert.equal(legend[0].textContent,'试听');await legend[0].onclick();assert.equal(legend[0].textContent,'试听');
 let finish;h.context.hold=()=>new Promise(resolve=>finish=resolve);const suggestion=legend[1];h.context.suggestion=suggestion;const pending=h.run('runUI(suggestion,hold)');h.language('ja');finish();await pending;
 assert.equal(suggestion.textContent,h.t('建议 · {name}',{name:'播放列表结束'}));assert.equal(h.run('project.groups[0].name'),'试听');assert.equal(h.run('project.suggestions[0].name'),'播放列表结束');
});
test('switching language during the first asset scan preserves progress, roots and empty cache',async()=>{
 const h=harness();await h.boot();let finish;
 h.element('engineRoot').value='D:\\声音引擎';h.element('modelRoot').value='D:\\未保存模型';h.element('referenceRoot').value='D:\\参考音频';
 h.setHandler(path=>{assert.equal(path,'assets');return new Promise(resolve=>finish=resolve)});
 const pending=h.run('refreshAssets()'),snapshot=h.run('JSON.stringify(assetCatalog)'),token=h.run('assetScanToken'),before=h.requests.length;
 for(const language of ['en','ja','zh']){h.language(language);assert.equal(h.element('assetStatus').textContent,h.t('正在扫描本机素材…'));assert.equal(h.run('assetCatalogData'),null);assert.equal(h.run('JSON.stringify(assetCatalog)'),snapshot);assert.equal(h.run('assetScanToken'),token);assert.equal(h.requests.length,before);assert.equal(h.element('engineRoot').value,'D:\\声音引擎');assert.equal(h.element('modelRoot').value,'D:\\未保存模型');assert.equal(h.element('referenceRoot').value,'D:\\参考音频')}
 finish({roots:{engine_root:'D:\\声音引擎',model_root:'D:\\未保存模型',reference_root:'D:\\参考音频'},candidates:{gpt:[],sovits:[],reference:[]}});await pending;
 assert.notEqual(h.run('assetCatalogData'),null);assert(h.element('assetStatus').textContent.includes(h.t('找到 {gpt} 个 GPT 模型、{sovits} 个 SoVITS 模型、{reference} 个参考音频',{gpt:0,sovits:0,reference:0})));
});
test('a failed first asset scan translates its notice and keeps raw failure details',async()=>{
 const h=harness();await h.boot();let fail;
 h.setHandler(path=>{assert.equal(path,'assets');return new Promise((resolve,reject)=>fail=reject)});
 const pending=h.run('refreshAssets()');h.language('en');const error='File unavailable: D:\\声音\\试听.wav';fail(Error(error));await assert.rejects(pending,{message:error});
 for(const language of ['ja','zh','en']){h.language(language);assert.equal(h.run('assetCatalogData'),null);assert.equal(h.element('assetStatus').textContent,h.t('素材扫描未完成：{error}。请检查目录后重试。',{error}))}
});
test('log placeholders follow the interface language while real logs stay byte-for-byte intact',async()=>{
 const h=harness();await h.boot();
 h.language('en');assert.equal(h.element('engineLog').textContent,h.t('等待引擎状态…'));assert.equal(h.element('trainingLog').textContent,h.t('等待训练面板状态…'));
 h.setHandler(path=>{assert.equal(path,'monitor');return {paths:{},stage:'工作台未连接',training_online:false,training_running:false,engine_log:'',training_log:''}});await h.run('monitor()');
 const requests=h.requests.length;
 for(const language of ['ja','zh','en']){h.language(language);assert.equal(h.element('engineLog').textContent,h.t('暂无日志'));assert.equal(h.element('trainingLog').textContent,h.t('暂无日志'));assert.equal(h.requests.length,requests)}
 const engine='暂无日志',training='等待训练面板状态…\nD:\\音频\\已完成.wav\n{"message":"已完成"}';
 h.setHandler(path=>{assert.equal(path,'monitor');return {paths:{},stage:'工作台未连接',training_online:false,training_running:false,engine_log:engine,training_log:training}});await h.run('monitor()');
 assert.equal(h.element('engineLog').dataset.uiLogPlaceholder,undefined);assert.equal(h.element('trainingLog').dataset.uiLogPlaceholder,undefined);
 for(const language of ['zh','ja','en']){h.language(language);assert.equal(h.element('engineLog').textContent,engine);assert.equal(h.element('trainingLog').textContent,training)}
});
(async()=>{let failed=0;for(const {name,fn} of tests){try{await fn();console.log('PASS: '+name)}catch(e){failed++;console.error('FAIL: '+name+'\n'+e.stack)}}console.log((tests.length-failed)+'/'+tests.length+' UI language regressions passed');if(failed)process.exitCode=1})().catch(e=>{console.error(e);process.exitCode=1});
