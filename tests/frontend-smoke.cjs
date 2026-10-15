// Behavioral smoke test: real TSX logic with lightweight hook/API/timer adapters.
// Does not claim to test Steam focus, layout or controller navigation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
let hooks=[],cursor=0,effects=[],tree,requests=[],timers=new Map(),nextTimer=0;
const snapshot={ok:true,revision:'rev',state:'idle',gaming:true,has_transaction:false,
 turbo_enabled:true,ui:{name:'powersave',independent:true},presets:{default:null,balanced:null,powersave:{p_max_khz:2500000,e_max_khz:1800000}},policies:{}};
for(let i=0;i<10;i++)snapshot.policies['policy'+i]={group:i<2?'P':'E',min:400000,hw_min:400000,hw_max:i<2?4700000:3400000,max:i<2?4700000:3400000};
const react={
 useState(initial){const i=cursor++;if(!(i in hooks))hooks[i]=initial;return[hooks[i],v=>{hooks[i]=v;}];},
 useRef(initial){const i=cursor++;if(!(i in hooks))hooks[i]={current:initial};return hooks[i];},
 useEffect(fn){const i=cursor++;if(!(i in hooks)){hooks[i]={};effects.push(()=>{hooks[i].cleanup=fn();});}}
};
const clone=()=>JSON.parse(JSON.stringify(snapshot));
const api={definePlugin:f=>f,callable:route=>async(req)=>{
 if(route==='get_status')return clone();
 requests.push(req);
 if(req.method==='set_turbo') snapshot.turbo_enabled=req.enabled;
 else if(req.method==='configure') {
  snapshot.presets[req.name]={p_max_khz:req.p_max_khz,e_max_khz:req.e_max_khz};
  snapshot.ui={name:req.name,independent:req.independent};snapshot.has_transaction=true;
 } else if(req.method==='save_ui')snapshot.ui={name:req.name,independent:req.independent};
 else if(req.method==='restore')snapshot.has_transaction=false;
 return clone();
}};
const jsx=(type,props)=>({type,props});
const context={exports:{},require:name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}:name==='@decky/api'?api:{PanelSection:'section',PanelSectionRow:'row',DialogButton:'button',Focusable:'focusable',ButtonItem:'button',SliderField:'slider',ToggleField:'toggle'},
 setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
 setInterval:()=>0,clearInterval:()=>{},crypto:require('node:crypto')};
const code=ts.transpileModule(fs.readFileSync('src/index.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code + '\nexports.Content = Content;',context);
const Content=context.exports.Content;
function render(){cursor=0;tree=Content();for(const fn of effects.splice(0))fn();}
async function settle(){for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));render();}
function all(node){if(!node||typeof node!=='object')return[];if(Array.isArray(node))return node.flatMap(x=>all(x));return[node,...all(node.props?.children ?? null)];}
const text=n=>Array.isArray(n)?n.map(text).join(''):typeof n==='object'&&n?text(n.props?.children):String(n??'');
const button=label=>all(tree).find(n=>n.type==='button'&&text(n).includes(label));
const slider=label=>all(tree).find(n=>n.type==='slider'&&n.props.label===label);
function fire(){for(const [id,t] of [...timers]){timers.delete(id);t.fn();}}
(async()=>{
 render();await settle();
 assert.equal(requests.length,0,'mount must never apply');
 assert.ok(text(button('省电')).includes('✓'),'saved selection restored');
 assert.equal(slider('E 核最高频率').props.value,1800,'saved separate mode and value restored');
 slider('E 核最高频率').props.onChange(1900);render();
 slider('E 核最高频率').props.onChange(2000);render();
 assert.equal(requests.length,0,'wait for debounce');fire();await settle();
 assert.equal(requests.length,1,'rapid edits merged');assert.equal(requests[0].e_max_khz,2000000);
 button('默认').props.onClick();await settle();
 assert.equal(requests.at(-1).method,'save_ui','empty default selection does not apply');
 assert.ok(slider('P 核最高频率'),'default editable');
 slider('P 核最高频率').props.onChange(3200);render();fire();await settle();
 assert.equal(requests.at(-1).method,'configure');assert.equal(requests.at(-1).name,'default');
 slider('E 核最高频率').props.onChange(2100);render();button('恢复系统原始配置').props.onClick();await settle();fire();await settle();
 assert.equal(requests.at(-1).method,'restore','restore cancels queued auto apply');
 slider('P 核最高频率').props.onChange(3100);render();
 for(const h of hooks)h?.cleanup?.();
 await settle();
 assert.equal(requests.at(-1).p_max_khz,3100000,'close flushes final edit');
 const count=requests.length;hooks=[];render();await settle();
 assert.equal(requests.length,count,'reopen does not reapply');assert.ok(text(button('默认')).includes('✓'));
 const toggle=all(tree).find(n=>n.type==='toggle');
 assert.equal(toggle.props.checked,true);toggle.props.onChange(false);await settle();
 assert.equal(requests.at(-1).method,'set_turbo');assert.equal(requests.at(-1).enabled,false);
 assert.equal(all(tree).find(n=>n.type==='toggle').props.checked,false);
 console.log('Frontend behaviors passed: remembered selection/mode, no mount apply, editable default, debounce, restore cancellation, close flush.');
})().catch(e=>{console.error(e);process.exitCode=1;});
