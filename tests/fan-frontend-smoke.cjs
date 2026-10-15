// Behavioral smoke test: real TSX logic with lightweight hook/API/timer adapters.
// Does not claim to test Steam focus, layout or controller navigation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
let hooks=[],cursor=0,effects=[],tree,requests=[],timers=new Map(),nextTimer=0;
const initial={mode:'curve',fixed:100,points:[47,50,57,64,71,78].map(temp=>({temp,output:100}))};
const snapshot={ok:true,revision:'rev',state:'idle',mode:2,gaming:true,has_transaction:false,
 temperature:57,rpm:[3200,3250],initial,settings:{selected:1,presets:{'1':initial,'2':null,'3':null,'4':null}}};
const react={
 useState(initial){const i=cursor++;if(!(i in hooks))hooks[i]=initial;return[hooks[i],v=>{hooks[i]=v;}];},
 useRef(initial){const i=cursor++;if(!(i in hooks))hooks[i]={current:initial};return hooks[i];},
 useEffect(fn){const i=cursor++;if(!(i in hooks)){hooks[i]={};effects.push(()=>{hooks[i].cleanup=fn();});}}
};
const clone=()=>JSON.parse(JSON.stringify(snapshot));
const api={callable:route=>async(req)=>{
 if(route==='get_fan_status')return clone();
 requests.push(req);
 if(req.method==='fan_save' || req.method==='fan_apply'){
  snapshot.settings.selected=req.slot;snapshot.settings.presets[req.slot]=req.draft;
 }
 if(req.method==='fan_apply'){
  snapshot.applied={slot:req.slot,draft:req.draft};snapshot.state='active';snapshot.has_transaction=true;
 }
 if(req.method==='fan_restore'){snapshot.state='idle';snapshot.has_transaction=false;delete snapshot.applied;}
 return clone();
}};
const jsx=(type,props)=>({type,props});
const context={exports:{},require:name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}:name==='@decky/api'?api:{PanelSection:'section',PanelSectionRow:'row',DialogButton:'button',Focusable:'focusable',ButtonItem:'button',SliderField:'slider',ToggleField:'toggle'},
 setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
 setInterval:()=>0,clearInterval:()=>{},crypto:require('node:crypto')};
const code=ts.transpileModule(fs.readFileSync('src/FanPanel.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code,context);
const Content=context.exports.FanPanel;
function render(){cursor=0;tree=Content();for(const fn of effects.splice(0))fn();}
async function settle(){for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));render();}
function all(node){if(!node||typeof node!=='object')return[];if(Array.isArray(node))return node.flatMap(x=>all(x));return[node,...all(node.props?.children ?? null)];}
const text=n=>Array.isArray(n)?n.map(text).join(''):typeof n==='object'&&n?text(n.props?.children):String(n??'');
const button=label=>all(tree).find(n=>n.type==='button'&&text(n).includes(label));
const slider=label=>all(tree).find(n=>n.type==='slider'&&n.props.label===label);
function fire(){for(const [id,t] of [...timers]){timers.delete(id);t.fn();}}
(async()=>{
 render();await settle();assert.equal(requests.length,0,'mount does not apply');
 assert.equal(button('方案 3'),undefined);assert.equal(button('方案 4'),undefined);
 assert.ok(button('固定输出'));assert.ok(button('温控曲线'));
 button('方案 2').props.onClick();render();
 assert.equal(button('方案 1').props.disabled,false,'saving selection must not disable focused controls');
 await settle();assert.equal(requests.at(-1).method,'fan_save');
 button('固定输出').props.onClick();render();fire();await settle();
 slider('双风扇输出').props.onChange(95);render();fire();await settle();
 assert.equal(requests.at(-1).method,'fan_save');assert.equal(requests.at(-1).draft.fixed,95);
 assert.equal(requests.some(r=>r.method==='fan_apply'),false,'drafts never apply');
 all(tree).find(n=>n.type==='toggle').props.onChange(true);render();
 assert.equal(requests.some(r=>r.method==='fan_apply'),false,'enabling only arms apply');
 button('应用设置').props.onClick();await settle();assert.equal(requests.at(-1).method,'fan_apply');
 all(tree).find(n=>n.type==='toggle').props.onChange(false);await settle();
 assert.equal(requests.at(-1).method,'fan_restore');
 slider('双风扇输出').props.onChange(100);render();
 for(const h of hooks)h?.cleanup?.();await settle();
 assert.equal(requests.at(-1).method,'fan_save','close saves draft without applying');
 const count=requests.length;hooks=[];render();await settle();
 assert.equal(requests.length,count,'reopen never applies');assert.ok(text(button('方案 2')).includes('✓'));
 console.log('Fan frontend passed: draft-only edits, selection, explicit apply, disable restores, close flush, reopen.');
})().catch(e=>{console.error(e);process.exitCode=1;});
