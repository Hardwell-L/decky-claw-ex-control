const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
let hooks=[],cursor=0,effects=[],tree,requests=[],poll;
const snapshot={ok:true,revision:'rev',features:{turbo:{enabled:false,last:null,error:''}}};
const react={useState(v){let i=cursor++;if(!(i in hooks))hooks[i]=v;return[hooks[i],x=>hooks[i]=x];},useRef(v){let i=cursor++;if(!(i in hooks))hooks[i]={current:v};return hooks[i];},useEffect(f){let i=cursor++;if(!(i in hooks)){hooks[i]={};effects.push(f);}}};
const jsx=(type,props)=>({type,props});
const context={exports:{},require:n=>n==='react'?react:n==='react/jsx-runtime'?{jsx,jsxs:jsx}:n==='@decky/api'?{callable:route=>async req=>{if(route==='mutate'){requests.push(req);snapshot.features.turbo.enabled=req.enabled;}return structuredClone(snapshot);}}:{PanelSectionRow:'row',ToggleField:'toggle'},setInterval:f=>{poll=f;return 1;},clearInterval:()=>{},crypto:require('node:crypto')};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/BootToggle.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,context);
function render(){cursor=0;tree=context.exports.BootToggle({feature:'turbo',label:'Restore Turbo at startup'});effects.splice(0).forEach(f=>f());}
async function settle(){for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));render();}
function all(n){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(all):[n,...all(n.props?.children)];}
const toggle=()=>all(tree).find(n=>n.type==='toggle');
(async()=>{
 render();await settle();assert.equal(requests.length,0);assert.equal(toggle().props.disabled,true);
 snapshot.features.turbo.last=false;await poll();await settle();assert.equal(toggle().props.disabled,false);
 toggle().props.onChange(true);await settle();assert.equal(requests[0].method,'boot_set');assert.equal(requests[0].feature,'turbo');assert.equal(toggle().props.checked,true);
 toggle().props.onChange(false);await settle();assert.equal(toggle().props.checked,false);
 console.log('Boot toggle UI smoke passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
