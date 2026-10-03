// Script workspace regression tests. Backend fixtures are authoritative parser
// and timeline results; this suite exercises rendering and asynchronous UI state.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map(),requests=[];
let activeElement=null,clipboard='';
class Element{
 constructor(){this.value='';this.children=[];this.textContent='';this.style={};this.dataset={};this.classes=new Set();this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)};this.selectionStart=0;this.selectionEnd=0;this.valueWrites=0}
 setAttribute(k,v){this[k]=v}removeAttribute(k){delete this[k]}replaceChildren(...children){this.children=children}append(...children){this.children.push(...children)}querySelectorAll(){return []}querySelector(){return null}scrollIntoView(){}close(){}showModal(){}after(){}add(item){this.children.push(item)}
 focus(){activeElement=this}setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end}pause(){this.pauseCount=(this.pauseCount||0)+1}load(){}play(){return Promise.resolve()}
}
const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const context=vm.createContext({document:{getElementById:element,createElement:()=>new Element(),querySelector:()=>null,querySelectorAll:()=>[],get activeElement(){return activeElement}},localStorage:{setItem(){},getItem(){},removeItem(){}},fetch:async()=>({ok:true,blob:async()=>({})}),URL:{createObjectURL:()=>`blob:${Math.random()}`,revokeObjectURL(){}},Option:class{constructor(text,value){this.text=text;this.value=value}},setTimeout:()=>1,clearTimeout(){},setInterval(){},console,Map,encodeURIComponent,TextDecoder,navigator:{clipboard:{writeText:async text=>{clipboard=text}}}});
const html=fs.readFileSync(__dirname+'/index.html','utf8');
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
vm.runInContext(fs.readFileSync(__dirname+'/studio.js','utf8').split('// Boot')[0].replace('},4000);monitor();','},4000);'),context);
vm.runInContext(fs.readFileSync(__dirname+'/library.js','utf8'),context);
vm.runInContext(fs.readFileSync(__dirname+'/segmentation.js','utf8').split('// Boot')[0],context);
vm.runInContext(fs.readFileSync(__dirname+'/pcs-editor.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
const textOf=e=>[e.textContent,...e.children.map(textOf)].join(' ');
const descendants=e=>[e,...e.children.flatMap(descendants)];
const fixture=(id,page=1)=>{
 const source=`#[p:${page}]#[section:intro]#[rate:0.9]#正文${id}。#[pause:800]#下一句。`,first=source.indexOf('正文'),pause=source.indexOf('#[pause');
 const ast=[{type:'control',command:'p',value:page,source_start:0,source_end:7},{type:'control',command:'section',value:'intro',source_start:7,source_end:24},{type:'control',command:'rate',value:0.9,source_start:24,source_end:first},{type:'text',text:`正文${id}。`,source_start:first,source_end:pause},{type:'control',command:'pause',value:800,source_start:pause,source_end:pause+14},{type:'text',text:'下一句。',source_start:pause+14,source_end:source.length}];
 const segments=[{id:id+'1',text:`正文${id}。`,page,section:'intro',rate:0.9,chapter:'正文',status:'done',duration:2,audio_version:1},{id:id+'2',text:'下一句。',page,section:'intro',rate:0.9,chapter:'正文',status:'pending',duration:0}];
 const execution_plan=[{kind:'event',type:'page',page,source_start:0,source_end:7},{kind:'event',type:'section',name:'intro',source_start:7,source_end:24},{kind:'event',type:'rate',value:0.9,source_start:24,source_end:first},{kind:'speech',segment_id:id+'1',...segments[0]},{kind:'event',type:'pause',duration_ms:800,source_start:pause,source_end:pause+14},{kind:'speech',segment_id:id+'2',...segments[1]}];
 const timeline={timing_status:'estimated',duration_ms:3800,segments:[{...segments[0],start_ms:0,end_ms:2000,estimated:false},{...segments[1],start_ms:2800,end_ms:3800,estimated:true}],events:[{type:'page',page,time_ms:0},{type:'section',name:'intro',time_ms:0},{type:'rate',value:0.9,time_ms:0},{type:'pause',start_ms:2000,end_ms:2800,duration_ms:800}]};
 return {project:{id,title:id,source_text:source,source_format:'pcs',limit:160,voice:{name:id,speed:1,gap:0.3},ast,diagnostics:[],segments,execution_plan,timeline,kson:{format:'kson',version:'0.1',timebase:'ms',timing_status:'estimated',duration_ms:3800,segments:timeline.segments,events:timeline.events},exports:[]},active:null};
};
const projects=new Map([['A',fixture('A')],['B',fixture('B',2)]]);
const parseFixture=owner=>({ast:projects.get(owner).project.ast,diagnostics:[],valid:true,stats:{},source_format:'pcs'});
let nextParse=null,nextCompile=null,nextApply=null;
context.request=async(path,data)=>{
 requests.push({path,data});
 if(path.startsWith('project?id='))return structuredClone(projects.get(path.split('=')[1]));
 if(path==='state')return {projects:[...projects.values()].map(({project:p})=>({id:p.id,title:p.title,count:p.segments.length,done:1})),trash:[],active:null};
 if(path==='pcs/parse'){const response=nextParse||parseFixture(run('project.id'));nextParse=null;return await response}
 if(path==='pcs/compile'){const response=nextCompile||{...parseFixture('A'),segments:projects.get('A').project.segments,execution_plan:projects.get('A').project.execution_plan};nextCompile=null;return await response}
 if(path==='project/source'){const response=nextApply||structuredClone(projects.get(data.id));nextApply=null;return await response}
 if(path==='export-kson')return {exports:[{file:'A.kson'}]};
 throw Error('Unexpected API '+path);
};
run('api=request');
(async()=>{
 assert.match(html,/accept="\.txt,\.pcs"/);
 await run("load('A')");
 assert.equal(element('source').value,projects.get('A').project.source_text);
 assert.match(textOf(element('scriptVisual')),/PPT · 1/);assert.match(textOf(element('scriptVisual')),/PAUSE · 800 ms/);assert.match(textOf(element('scriptVisual')),/SPEED · 0.90×/);assert.match(textOf(element('scriptVisual')),/SECTION · intro/);
 assert.equal(element('astNodes').children.length,6);assert.equal(JSON.parse(element('astJson').textContent).length,6);
 assert.equal(Number(element('pending').textContent),1);
 assert.match(textOf(element('segments')),/PPT 1/);assert.match(textOf(element('segments')),/SECTION intro/);assert.match(textOf(element('segments')),/0.90×/);
 assert.equal(element('executionPlan').children.length,6);assert.equal(element('timelineEvents').children.length,4);assert.equal(run('timeEntries[1].timeline.start'),2.8);assert.equal(run('continuousGapMs()'),0);
 const kson=JSON.parse(element('ksonJson').textContent);assert.equal(kson.format,'kson');assert.equal(kson.events.length,4);assert.equal(element('exportKson').disabled,true);
 run("openEditor('A1')");assert.equal(element('editText').readOnly,true);
 assert.equal(element('restoreDeleted').classes.has('hidden'),true);
 assert.equal(textOf(element('segments')).includes('删除'),false);
 console.log('PASS: backend control chips, AST, ordered plan, segment metadata, estimated timeline and KSON');

 // A diagnostic must select Python codepoints correctly across an astral emoji.
 element('source').value='😀#[p:abc]#';run('scriptSourceChanged()');
 assert.equal(element('start').disabled,true);assert.equal(element('exportAll').disabled,true);assert.match(element('scriptState').textContent,/SOURCE MODIFIED/);
 nextParse={ast:[],valid:false,diagnostics:[{level:'error',code:'PCS_INVALID_PAGE',message:'页码必须为正整数',source_start:1,source_end:10}]};
 await run('parseScript()');assert.match(textOf(element('parserDiagnostics')),/PCS_INVALID_PAGE/);assert.equal(element('previewScript').disabled,true);assert.equal(element('applyScript').disabled,true);
 element('parserDiagnostics').children[0].onclick();assert.equal(element('source').selectionStart,2);assert.equal(element('source').selectionEnd,11);assert.equal(activeElement,element('source'));
 const oldCount=requests.length;await element('start').onclick();assert.equal(requests.length,oldCount);

 // Inserting at the textarea cursor preserves the raw source as the authority.
 element('source').value='AB';element('source').setSelectionRange(1,1);element('controlCommand').value='pause';element('controlValue').value='800';element('insertControl').onclick();assert.equal(element('source').value,'A#[pause:800]#B');assert.equal(element('source').selectionStart,14);
 console.log('PASS: parser errors block compile/generation/export, diagnostic Unicode spans and cursor insertion');

 // Neither polling nor navigation may overwrite A's uncommitted source draft.
 element('source').value='本地未保存草稿';run('scriptSourceChanged()');element('source').focus();element('source').setSelectionRange(3,6);
 await run('refresh()');assert.equal(element('source').value,'本地未保存草稿');assert.equal(activeElement,element('source'));assert.equal(element('source').selectionStart,3);
 await run('refresh()');assert.match(element('timelineSummary').textContent,/STALE/);assert(descendants(element('segments')).filter(e=>e.textContent==='参数 / 单段重做').every(e=>e.disabled));assert(element('timelineEvents').children.every(e=>e.disabled));
 let finishParse;nextParse=new Promise(resolve=>finishParse=resolve);const parsing=run('parseScript()');await run("load('B')");finishParse({ast:[{type:'control',command:'p',value:99}],valid:true,diagnostics:[]});await parsing;
 assert.equal(element('source').value,projects.get('B').project.source_text);assert.match(textOf(element('scriptVisual')),/PPT · 2/);assert.equal(textOf(element('scriptVisual')).includes('99'),false);
 await run("load('A')");assert.equal(element('source').value,'本地未保存草稿');assert.equal(element('start').disabled,true);
 // Newer edits invalidate an already pending parse for the same project.
 let finishStale;nextParse=new Promise(resolve=>finishStale=resolve);const stale=run('parseScript()');element('source').value='更新的草稿';run('scriptSourceChanged()');finishStale(parseFixture('A'));await stale;assert.equal(run('scriptDraft().parse'),null);assert.equal(element('source').value,'更新的草稿');
 console.log('PASS: per-project source drafts, parser navigation/revision races and polling focus preservation');

 // Compile is a preview. Applying is the one action that commits project source.
 element('source').value=projects.get('A').project.source_text;run('scriptSourceChanged()');await run('parseScript()');await run('previewScript()');assert.match(element('planSummary').textContent,/预览计划/);assert.equal(requests.filter(r=>r.path==='project/source').length,0);
 const updated=fixture('A');updated.project.source_text=element('source').value;nextApply=updated;await run('applyScript()');assert.equal(run('scriptIsDirty()'),false);assert.equal(run('project.segments[0].status'),'done');
 // A new inference result updates exact timing without unloading playing audio.
 await run("playSegment('A1')");const player=element('continuousAudio'),url=player.src,paused=player.pauseCount;player.currentTime=1.25;element('source').focus();element('source').setSelectionRange(4,9);
 const exact=fixture('A');exact.project.segments[1].status='done';exact.project.segments[1].duration=1;exact.project.timeline.timing_status='exact';exact.project.timeline.segments[1].estimated=false;exact.project.kson.timing_status='exact';projects.set('A',exact);
 await run('refresh()');assert.equal(player.src,url);assert.equal(player.pauseCount,paused);assert.equal(player.currentTime,1.25);assert.equal(activeElement,element('source'));assert.equal(element('source').selectionStart,4);assert.match(element('timelineSummary').textContent,/Exact Timeline/);assert.equal(element('exportKson').disabled,false);assert.equal(run('continuousGapMs()'),800);
 await element('copyKson').onclick();assert.equal(JSON.parse(clipboard).format,'kson');await element('exportKson').onclick();assert(requests.some(r=>r.path==='export-kson'));
 console.log('PASS: compile preview, apply, uninterrupted playback during inference, exact KSON copy/export');

 // Legacy projects are inspectable without silently replacing their old audio.
 const legacy=fixture('L');legacy.project.source_format='legacy';delete legacy.project.ast;delete legacy.project.source_text;projects.set('L',legacy);await run("load('L')");assert.equal(element('source').value,legacy.project.segments.map(s=>s.text).join('\n\n'));assert.match(element('sourceNotice').textContent,/显式/);assert.equal(element('editText').readOnly,true);run("openEditor('L1')");assert.equal(element('editText').readOnly,false);
 console.log('PASS: legacy source reconstruction requires explicit conversion');
})().catch(e=>{console.error(e);process.exitCode=1});
