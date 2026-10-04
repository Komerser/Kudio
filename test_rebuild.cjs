// Behavioral regressions for the rebuilt workspace. No live engine or user data.
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

function harness(){
 const elements=new Map(),rootNodes=[],requests=[],storage=new Map();let activeElement=null,handler;
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this._text='';this._value='';this.disabled=false;this.open=false;this.classes=new Set();this.style={setProperty(k,v){this[k]=v}};this.classList={add:(...cs)=>cs.forEach(c=>this.classes.add(c)),remove:(...cs)=>cs.forEach(c=>this.classes.delete(c)),contains:c=>this.classes.has(c),toggle:(c,on)=>{const add=on===undefined?!this.classes.has(c):on;add?this.classes.add(c):this.classes.delete(c);return add}};this.selectionStart=0;this.selectionEnd=0;this.events=new Map()}
  get className(){return [...this.classes].join(' ')}set className(value){this.classes=new Set(String(value).split(/\s+/).filter(Boolean))}
  get textContent(){return this._text+this.children.map(e=>e.textContent).join('')}set textContent(value){this._text=String(value??'');this.children=[]}
  get value(){return this._value}set value(value){this._value=String(value??'')}
  get options(){return this.children}getAttribute(k){return this.attributes[k]??null}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='id')this.id=String(v);else if(k==='class')this.className=v;else if(k==='value')this.value=v;else if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v);else this[k]=v}
  removeAttribute(k){delete this.attributes[k];delete this[k]}
  append(...items){for(const item of items){item.parentNode=this;this.children.push(item)}}prepend(...items){for(const item of items)item.parentNode=this;this.children.unshift(...items)}
  replaceChildren(...items){this._text='';this.children=[];this.append(...items)}add(item){this.append(item)}after(){}remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(e=>e!==this)}
  querySelectorAll(selector){return descendants(this).slice(1).filter(e=>matches(e,selector))}querySelector(selector){return this.querySelectorAll(selector)[0]||null}
  closest(selector){let e=this;while(e){if(matches(e,selector))return e;e=e.parentNode}return null}
  addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,[]);this.events.get(type).push(fn)}
  async dispatch(type,extra={}){const event={target:this,preventDefault(){},...extra};for(const fn of this.events.get(type)||[])await fn(event);return this['on'+type]?.(event)}
  click(){if(!this.disabled)return this.dispatch('click')}focus(){activeElement=this}setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b}scrollIntoView(){}getBoundingClientRect(){return {left:0,right:100,top:0,bottom:100}}
  pause(){this.pauseCount=(this.pauseCount||0)+1}load(){}play(){return Promise.resolve()}showModal(){this.open=true}close(){this.open=false}
 }
 function descendants(e){return [e,...e.children.flatMap(descendants)]}
 function matches(e,selector){return selector.split(',').some(raw=>{const s=raw.trim();if(s.includes(' ')){const parts=s.split(/\s+/);return matches(e,parts.pop())&&e.parentNode?.closest(parts.join(' '))}if(s[0]==='#')return e.id===s.slice(1);if(s[0]==='.')return e.classes.has(s.slice(1));const attr=s.match(/^([a-z]*)\[([^=\]]+)(?:=['"]?([^'"\]]+)['"]?)?\]$/i);if(attr)return (!attr[1]||e.tagName.toLowerCase()===attr[1])&&e.getAttribute(attr[2])!==null&&(attr[3]===undefined||e.getAttribute(attr[2])===attr[3]);return e.tagName.toLowerCase()===s})}
 const body=new Element('body');rootNodes.push(body);
 // Register real IDs and navigation attributes; generated cards use the same tree.
 const stack=[body],voidTags=new Set(['input','img','br','hr','meta','link','source']);
 for(const token of html.replace(/<script\b[\s\S]*?<\/script>/gi,'').match(/<\/?[a-z][^>]*>/gi)||[]){
  if(token.startsWith('</')){const closing=token.match(/^<\/([a-z0-9-]+)/i)[1].toUpperCase();const index=stack.findLastIndex(e=>e.tagName===closing);if(index>0)stack.length=index;continue}
  const tag=token.match(/^<([a-z0-9-]+)/i)[1].toLowerCase();if(['html','head','body','!doctype'].includes(tag))continue;
  const e=new Element(tag);for(const a of token.matchAll(/([\w-]+)\s*=\s*"([^"]*)"/g))e.setAttribute(a[1],a[2]);if(/\bchecked\b/.test(token))e.checked=true;if(/\bopen\b/.test(token))e.open=true;
  stack.at(-1).append(e);if(e.id)elements.set(e.id,e);if(!voidTags.has(tag)&&!token.endsWith('/>'))stack.push(e);
 }
 const element=id=>{if(!elements.has(id)){const e=new Element();e.id=id;elements.set(id,e);body.append(e)}return elements.get(id)};
 const document={body,getElementById:element,createElement:tag=>new Element(tag),querySelectorAll:s=>descendants(body).filter(e=>matches(e,s)),querySelector:s=>document.querySelectorAll(s)[0]||null,get activeElement(){return activeElement}};
 const context=vm.createContext({document,localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)},sessionStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)},fetch:async()=>({ok:true,blob:async()=>({})}),URL:{createObjectURL:()=>`blob:${Math.random()}`,revokeObjectURL(){}},Option:class extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value}},setTimeout:()=>1,clearTimeout(){},setInterval(){},console,Map,encodeURIComponent,TextDecoder,navigator:{clipboard:{writeText:async()=>{}}},confirm:()=>true,prompt:()=>null});
 context.window=context;const run=code=>vm.runInContext(code,context);context.request=async(path,data)=>{requests.push({path,data:clone(data??null)});return handler(path,data)};
 const projects=new Map([['A',fixture('A')],['B',fixture('B','txt')]]);let savedRoles=clone(roles);
 handler=async(path,data)=>{if(path.startsWith('project?id='))return clone(projects.get(path.split('=')[1]));if(path==='state')return {projects:[...projects.values()].map(({project:p})=>({id:p.id,title:p.title,count:p.segments.length,done:0})),trash:[],active:null};if(path==='presets')return {presets:clone(savedRoles)};if(path==='preset'){const record={id:data.preset_id||'r3',name:data.voice.name,voice:clone(data.voice),profile:clone(data.profile)};savedRoles=savedRoles.filter(r=>r.id!==record.id).concat(record);return record}if(path==='project/voices'){const p=clone(projects.get(data.id));p.project.default_role_id=data.default_role_id;p.project.voice_bindings=clone(data.voice_bindings);projects.set(data.id,clone(p));return p}throw Error('Unexpected API '+path)};
 run(html.match(/<script>([\s\S]*?)<\/script>/)[1]);run('api=request');
 for(const name of ['studio.js','library.js','assets.js','segmentation.js','pcs-editor.js']){let source=fs.readFileSync(__dirname+'/'+name,'utf8');if(name==='studio.js')source=source.split('// Boot')[0].replace('},4000);monitor();','},4000);');if(name==='segmentation.js')source=source.split('// Boot')[0];if(name==='assets.js')source=source.replace(/refreshAssets\(\)\.catch\([\s\S]*$/,'');run(source)}
 run(rebuild);
 return {run,element,document,requests,projects,context,setHandler(fn){handler=fn},async boot(){await run('loadPresets()');await run("load('A')")},async select(id){await run(`load('${id}')`)},binding(label){return element('voiceBindings').children.find(e=>e.children[0]?.children[0]?.textContent===label)?.querySelector('select')},async change(id,value){element(id).value=value;await element(id).dispatch('input')},async cast(label,id){const e=this.binding(label);assert(e,'Missing casting select for '+label);e.value=id;await e.dispatch('change')}};
}

const tests=[];const test=(name,fn)=>tests.push({name,fn});
test('real HTML contains rebuilt controls and page destinations',()=>{
 assert(/<script\b[^>]*src="\/rebuild\.js"/.test(html),'HTML does not load rebuild.js');
 const realIds=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
 assert.equal(realIds.size,[...html.matchAll(/\bid="([^"]+)"/g)].length,'Duplicate HTML IDs');
 const needed=new Set([...rebuild.matchAll(/\$\('([^']+)'\)/g)].map(m=>m[1]));
 for(const id of ['roleDescription','roleTags','roleColor','roleAvatar','rolePortrait'])needed.add(id);
 for(const id of needed)assert(realIds.has(id),'Rebuild control absent from HTML: '+id);
 const pageValues=new Set([...html.matchAll(/data-studio-page="([^"]+)"/g)].map(m=>m[1]));for(const page of ['engine','roles','text','inference'])assert(pageValues.has(page),'Page absent: '+page);
});
test('role editing works without a book and never adopts another book voice',async()=>{
 const h=harness();await h.run('loadPresets()');h.run("selectRole('r1')");await h.change('name','长者未保存稿');await h.change('roleDescription','未保存介绍');
 await h.select('A');assert.equal(h.element('name').value,'长者未保存稿');await h.select('B');assert.equal(h.element('name').value,'长者未保存稿');assert.equal(h.element('roleDescription').value,'未保存介绍');
 h.run("selectRole('r2')");assert.equal(h.element('name').value,'少女');h.run("selectRole('r1')");assert.equal(h.element('name').value,'长者未保存稿');
 await h.select('');assert.equal(h.run('project'),null);assert.equal(h.element('name').value,'长者未保存稿');await h.element('savePreset').onclick();assert(h.requests.some(r=>r.path==='preset'));assert(!h.requests.some(r=>r.path==='voice'||r.path==='project/voices'));
});
test('new role can be saved and assigned as book default',async()=>{
 const h=harness();await h.boot();h.run("selectRole('')");for(const [id,value] of Object.entries(voice('新角色')))await h.change(id,value);
 assert.equal(h.element('applyRoleToBook').disabled,false);await h.element('voiceForm').onsubmit({preventDefault(){}});
 const request=h.requests.find(r=>r.path==='project/voices');assert(request);assert.equal(request.data.default_role_id,'r3');assert.equal(h.run('project.default_role_id'),'r3');assert.equal(h.run('studioPage'),'text');
});
test('assigning a default role preserves pending token casting choices',async()=>{
 const h=harness();await h.boot();await h.cast('male_elder','r2');h.run("selectRole('r2')");await h.element('voiceForm').onsubmit({preventDefault(){}});
 // The token edit may be applied together or stay as a draft, but must survive.
 assert.equal(h.binding('male_elder').value,'r2');
 const saved=h.run('project.voice_bindings.male_elder');assert(saved==='r2'||h.run('castingIsDirty()'));
});
test('default-role response preserves newer casting and default edits',async()=>{
 const h=harness();await h.boot();h.run("selectRole('r2')");let finish;
 h.setHandler((path,data)=>{if(path==='preset')return {id:'r2',name:data.voice.name,voice:clone(data.voice),profile:clone(data.profile)};if(path==='presets')return {presets:clone(roles)};if(path==='project/voices')return new Promise(resolve=>finish=()=>{const p=fixture('A');p.project.default_role_id=data.default_role_id;p.project.voice_bindings=clone(data.voice_bindings);resolve(p)});throw Error(path)});
 const assigning=h.element('voiceForm').onsubmit({preventDefault(){}});for(let i=0;i<20&&!finish;i++)await Promise.resolve();assert(finish,'Default assignment did not reach the server');
 await h.cast('female_child','r1');h.element('defaultRole').value='';await h.element('defaultRole').dispatch('change');finish();await assigning;
 assert.equal(h.binding('female_child').value,'r1');assert.equal(h.element('defaultRole').value,'');assert.equal(h.run('castingIsDirty()'),true);
});
test('per-book casting drafts survive navigation and block inference',async()=>{
 const h=harness();await h.boot();await h.cast('male_elder','r2');assert.equal(h.run('castingIsDirty()'),true);const before=h.requests.length;await h.element('start').onclick();assert.equal(h.requests.length,before);assert.match(h.element('toast').textContent,/保存角色安排/);
 await h.select('B');assert.equal(h.element('defaultRole').value,'r1');assert.equal(h.run('castingIsDirty()'),false);await h.select('A');assert.equal(h.binding('male_elder').value,'r2');assert.equal(h.run('castingIsDirty()'),true);
});
test('casting save cannot replace newer unsaved binding edits',async()=>{
 const h=harness();await h.boot();await h.cast('male_elder','r2');let finish;h.setHandler((path,data)=>{assert.equal(path,'project/voices');return new Promise(resolve=>finish=()=>{const p=fixture('A');p.project.voice_bindings=clone(data.voice_bindings);resolve(p)})});
 const save=h.element('saveBindings').onclick();assert(finish);await h.cast('female_child','r1');finish();await save;
 assert.equal(h.binding('female_child').value,'r1','A late save discarded newer casting edits');assert.equal(h.run('castingIsDirty()'),true);
});
test('a pending casting save rejects duplicate submissions and recovers after failure',async()=>{
 const h=harness();await h.boot();await h.cast('male_elder','r2');let reject;
 h.setHandler(path=>{assert.equal(path,'project/voices');return new Promise((resolve,fail)=>reject=fail)});
 const save=h.element('saveBindings').onclick();assert.equal(h.element('saveBindings').disabled,true);await h.element('saveBindings').onclick();assert.equal(h.requests.filter(r=>r.path==='project/voices').length,1);
 reject(Error('保存失败'));await save;assert.equal(h.run('castingIsDirty()'),true);assert.equal(h.element('saveBindings').disabled,false);assert.match(h.element('toast').textContent,/保存失败/);
});
test('clean casting cache follows current server state on refresh',async()=>{
 const h=harness();await h.boot();const p=fixture('A');p.project.default_role_id='r2';p.project.voice_bindings.male_elder='r2';h.projects.set('A',p);await h.run('refresh()');
 assert.equal(h.element('defaultRole').value,'r2');assert.equal(h.binding('male_elder').value,'r2');assert.equal(h.run('castingIsDirty()'),false);
});
test('save response for a departed book does not update current book',async()=>{
 const h=harness();await h.boot();await h.cast('male_elder','r2');let finish;const normalProjects=h.projects;
 h.setHandler((path,data)=>{if(path==='project/voices')return new Promise(resolve=>finish=()=>{const p=fixture('A');p.project.voice_bindings=clone(data.voice_bindings);resolve(p)});if(path.startsWith('project?id='))return clone(normalProjects.get(path.split('=')[1]));throw Error(path)});
 const save=h.element('saveBindings').onclick();await h.select('B');finish();await save;assert.equal(h.run('project.id'),'B');assert.equal(h.element('defaultRole').value,'r1');
});
test('completed casting save acknowledges its untouched draft after navigation',async()=>{
 const h=harness();await h.boot();await h.cast('male_elder','r2');let finish;
 h.setHandler((path,data)=>{if(path==='project/voices')return new Promise(resolve=>finish=()=>{const p=fixture('A');p.project.voice_bindings=clone(data.voice_bindings);h.projects.set('A',clone(p));resolve(p)});if(path.startsWith('project?id='))return clone(h.projects.get(path.split('=')[1]));throw Error(path)});
 const save=h.element('saveBindings').onclick();await h.select('B');finish();await save;await h.select('A');
 assert.equal(h.binding('male_elder').value,'r2');assert.equal(h.run('castingIsDirty()'),false,'Already saved casting remains marked as an unsaved draft');
});
test('role save response preserves a different selected role and its draft',async()=>{
 const h=harness();await h.boot();h.run("selectRole('r1')");await h.change('name','长者已改');let finish;
 h.setHandler((path,data)=>{if(path==='preset')return new Promise(resolve=>finish=()=>resolve({id:'r1',name:data.voice.name,voice:clone(data.voice),profile:clone(data.profile)}));if(path==='presets')return {presets:clone(roles)};throw Error(path)});
 const save=h.run('persistPreset(true)');h.run("selectRole('r2')");await h.change('name','少女新稿');finish();await save;
 assert.equal(h.run('selectedRoleId'),'r2');assert.equal(h.element('name').value,'少女新稿');
});
test('old file picker result cannot cross role A to B to A navigation',async()=>{
 const h=harness();await h.boot();h.run("selectRole('r1')");let finish;h.setHandler(path=>{assert.equal(path,'pick-path');return new Promise(resolve=>finish=resolve)});
 const pick=h.run("chooseLocalFile('reference','reference')");h.run("selectRole('r2');selectRole('r1')");await h.change('reference','newer-choice.wav');finish({path:'stale-picker.wav',cancelled:false});await pick;
 assert.equal(h.element('reference').value,'newer-choice.wav');
});
test('deleting one role preserves a different currently selected role',async()=>{
 const h=harness();await h.boot();h.run("selectRole('r1')");let finish;
 h.setHandler((path,data)=>{if(path==='delete-preset')return new Promise(resolve=>finish=()=>resolve({deleted:true}));if(path==='presets')return {presets:clone(roles.filter(r=>r.id==='r2'))};throw Error(path)});
 const deleting=h.element('deleteRole').onclick();h.run("selectRole('r2')");await h.change('roleDescription','少女的新介绍');finish();await deleting;
 assert.equal(h.run('selectedRoleId'),'r2');assert.equal(h.element('roleDescription').value,'少女的新介绍');
});
test('workspace navigation marks pages and keeps source and role drafts',async()=>{
 const h=harness();await h.boot();h.run("selectRole('r1')");await h.change('name','保持角色稿');h.element('source').value='保持文本稿';h.run('scriptSourceChanged()');
 for(const page of ['roles','engine','inference','text']){h.run(`navigateStudio('${page}')`);assert.equal(h.document.body.dataset.page,page);for(const e of h.document.querySelectorAll('[data-studio-page]'))assert.equal(e.classes.has('hidden'),e.dataset.studioPage!==page);assert.equal(h.element('source').value,'保持文本稿');assert.equal(h.element('name').value,'保持角色稿')}
 h.run("navigateStudio('invalid')");assert.equal(h.run('studioPage'),'text');
});
test('process details open in a dialog and diagnostics return to source editing',async()=>{
 const h=harness();await h.boot();assert.equal(h.element('parsePanel').closest('dialog')?.id,'processDialog');assert.equal(h.element('planPanel').closest('dialog')?.id,'processDialog');
 const parse=h.document.querySelector('[data-process="parse"]');assert(parse);await parse.click();assert.equal(h.element('processDialog').open,true);assert.equal(h.element('parsePanel').classes.has('hidden'),false);assert.equal(h.element('planPanel').classes.has('hidden'),true);
 h.run("navigateStudio('inference');selectScriptSpan({source_start:2,source_end:5})");assert.equal(h.element('processDialog').open,false);assert.equal(h.run('studioPage'),'text');assert.equal(h.element('source').selectionStart,2);assert.equal(h.element('source').selectionEnd,5);
});
test('single segment editor uses role snapshot and persistent PCS rate',async()=>{
 const h=harness();await h.boot();h.run("project.role_snapshots.r2.voice.speed=1.6;project.role_snapshots.r2.voice.reference='少女角色.wav';project.segments[1].rate=.8;openEditor('A2')");
 assert.equal(h.element('editReference').value,'少女角色.wav');assert.equal(Number(h.element('editSpeed').value),.8);
});

(async()=>{let failed=0;for(const {name,fn} of tests){try{await fn();console.log('PASS: '+name)}catch(e){failed++;console.error('FAIL: '+name+'\n'+e.stack)}}console.log(`${tests.length-failed}/${tests.length} rebuild regressions passed`);if(failed)process.exitCode=1})().catch(e=>{console.error(e);process.exitCode=1});
