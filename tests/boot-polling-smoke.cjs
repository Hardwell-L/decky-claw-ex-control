const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
let reads=0,intervals=0,rev=0,mutations=[];
const snapshot=()=>({ok:true,revision:String(rev),features:{}});
const context={exports:{},require:n=>n==='@decky/api'?{callable:route=>async req=>{if(route==='get_boot_status'){reads++;await new Promise(r=>setImmediate(r));return snapshot();}assert.equal(req.expected_revision,String(rev));rev++;mutations.push(req.feature);return snapshot();}}:{},setInterval:()=>{intervals++;return 1;},clearInterval:()=>{intervals--;},crypto:require('node:crypto')};
const code=ts.transpileModule(fs.readFileSync('src/BootToggle.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code+'\nexports.store={subscribe,refreshShared,changeBoot};',context);
const store=context.exports.store;
(async()=>{
 let a=0,b=0;
 const offA=store.subscribe(()=>a++),offB=store.subscribe(()=>b++);
 await Promise.all([store.refreshShared(),store.refreshShared()]);
 assert.equal(reads,1,'two toggles share one read');assert.equal(intervals,1);assert.equal(a,1);assert.equal(b,1);
 await Promise.all([store.changeBoot('cpu',true),store.changeBoot('turbo',true)]);
 assert.deepEqual(mutations,['cpu','turbo'],'writes serialized with fresh revisions');
 offA();assert.equal(intervals,1);offB();assert.equal(intervals,0,'last unmount stops polling');
 console.log('Shared boot polling and serialized mutation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
