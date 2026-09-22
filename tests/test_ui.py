"""Dependency-free UI contract tests; run: python -m unittest discover -s tests -p test_ui.py -v."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "studio" / "static"


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ShellTests(unittest.TestCase):
    def test_accessible_self_contained_navigation_shell(self):
        path = STATIC / "index.html"
        self.assertTrue(path.exists(), "The accessible application shell has not been built")
        markup = Markup(path.read_text(encoding="utf-8"))
        ids = [attrs["id"] for _, attrs in markup.elements if "id" in attrs]
        self.assertEqual(len(ids), len(set(ids)), "DOM IDs must be unique")
        for view in ("overview", "workspaces", "workbench", "history", "integrations", "setup"):
            self.assertIn("view-" + view, ids)
            self.assertTrue(any(a.get("href") == "#" + view for t, a in markup.elements if t == "a"))
        self.assertTrue(any(t == "main" and a.get("id") == "main" for t, a in markup.elements))
        self.assertTrue(any(a.get("href") == "#main" for _, a in markup.elements))
        scripts = [a for t, a in markup.elements if t == "script"]
        self.assertEqual(scripts, [{"src": "/static/app.js", "defer": None}])
        self.assertFalse(any(a.get("src", "").startswith("http") or a.get("href", "").startswith("http") for _, a in markup.elements))
        self.assertTrue(any(t == "meta" and a.get("name") == "viewport" for t, a in markup.elements))


    def test_responsive_dashboard_surfaces_have_no_remote_dependencies(self):
        source = (STATIC / "styles.css").read_text(encoding="utf-8")
        for selector in (".hero{", ".stats-grid{", ".workbench-grid{", ".integration-grid{", ".tabs{", ".guardrail-strip{"):
            self.assertIn(selector, source)
        self.assertIn("@media(max-width:760px)", source)
        self.assertIn("prefers-reduced-motion", source)
        self.assertNotIn("@import", source)
        self.assertNotIn("https://", source)


class ClientTests(unittest.TestCase):
    def run_js(self, source):
        self.assertTrue((STATIC / "app.js").exists(), "API client is not implemented")
        node = shutil.which("node")
        if not node:
            self.skipTest("Optional JS runtime tests need Node; static checks remain dependency-free")
        script = "const ui = require(" + json.dumps(str(STATIC / "app.js")) + ");\n" + source
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_authenticated_same_origin_transport_and_error(self):
        result = self.run_js("""
(async () => {
 const calls = [];
 const client = ui.createApi(async (url, options) => {
  calls.push({url, ...options});
  return {ok: calls.length < 3, status: calls.length < 3 ? 200 : 401,
   json: async () => calls.length < 3 ? {projects:0} : {detail:'Authentication required'}};
 });
 client.setToken(' session-token ');
 await client.request('/api/summary');
 client.setToken('');
 await client.request('/api/projects', {method:'POST',body:{name:'<unsafe>'}});
 let error, rejected;
 try {await client.request('/api/settings')} catch(e) {error=e.message}
 try {await client.request('https://example.com/steal')} catch(e) {rejected=e.message}
 console.log(JSON.stringify({calls,error,rejected}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual(len(result["calls"]), 3)
        self.assertEqual(result["calls"][0]["headers"]["Authorization"], "Bearer session-token")
        self.assertNotIn("Authorization", result["calls"][1]["headers"])
        self.assertEqual(result["calls"][0]["credentials"], "same-origin")
        self.assertEqual(result["calls"][0]["cache"], "no-store")
        self.assertEqual(result["calls"][0]["redirect"], "error")
        self.assertEqual(json.loads(result["calls"][1]["body"]), {"name": "<unsafe>"})
        self.assertIn("Authentication required", result["error"])
        self.assertTrue(result["rejected"])

    def test_sample_is_explicitly_labeled_and_api_persisted(self):
        result = self.run_js("""
(async () => {
 const calls = [];
 const documents = [];
 const client = {request:async(path,options={})=>{
  calls.push({path, ...options});
  if(path==='/api/projects')return {id:'sample-1',...options.body};
  if(options.method==='POST'){const doc={id:String(documents.length),...options.body}; documents.push(doc);return doc;}
  return {items:documents};
 }};
 const project = await ui.createSampleWorkspace(client);
 console.log(JSON.stringify({project,calls}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertTrue(result["project"]["name"].startswith("Sample"))
        self.assertEqual(result["calls"][0]["path"], "/api/projects")
        writes = [call for call in result["calls"][1:] if call.get("method") == "POST"]
        self.assertEqual(len(writes), 2)
        for call in writes:
            self.assertEqual(call["path"], "/api/projects/sample-1/documents")
            self.assertIn("sample", call["body"]["title"].lower())
            self.assertIn("sample", call["body"]["content"].lower())
        self.assertEqual(result["calls"][-1]["path"], "/api/projects/sample-1/documents")
        self.assertNotIn("method", result["calls"][-1])

    def test_untrusted_text_never_uses_html_sinks_or_persistence(self):
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "localStorage", "sessionStorage", "indexedDB", "eval("):
            self.assertNotIn(forbidden, source)
        result = self.run_js("""
global.document = {createElement: tag => ({tag,textContent:'',className:''})};
console.log(JSON.stringify(ui.el('p','answer','<img src=x onerror=alert(1)>')));
""")
        self.assertEqual(result["textContent"], "<img src=x onerror=alert(1)>")

    def test_run_payload_requires_explicit_live_consent(self):
        result = self.run_js("""
const base={prompt:'  What is the release policy?  ',mode:'local',engine:'pydantic-ai',top_k:'3',reference:''};
const local=ui.buildRunPayload({...base,allow_paid:true},{live_enabled:false});
const live=ui.buildRunPayload({...base,mode:'live',engine:'instructor',allow_paid:true},{live_enabled:true});
const failures=[];
for (const [values,settings] of [
 [{...base,mode:'live',allow_paid:false},{live_enabled:true}],
 [{...base,mode:'live',allow_paid:true},{live_enabled:false}],
 [{...base,mode:'live',allow_paid:true},null],
 [{...base,mode:'local',engine:'instructor'},{}],
 [{...base,prompt:'   '},{}], [{...base,engine:'arbitrary'},{}], [{...base,top_k:'0'},{}]
]) {try {ui.buildRunPayload(values,settings)} catch(e){failures.push(e.message)}}
console.log(JSON.stringify({local,live,failures}));
""")
        self.assertEqual(result["local"], {"prompt": "What is the release policy?", "mode": "local", "engine": "pydantic-ai", "top_k": 3, "reference": "", "allow_paid": False})
        self.assertTrue(result["live"]["allow_paid"])
        self.assertEqual(result["live"]["engine"], "instructor")
        self.assertEqual(len(result["failures"]), 7)

    def test_evaluation_unknown_is_not_a_pass(self):
        result = self.run_js("console.log(JSON.stringify([ui.evaluationStatus({passed:null,score:null}),ui.evaluationStatus({passed:false,score:0}),ui.evaluationStatus({passed:true,score:1})]));")
        self.assertEqual(result, ["Not evaluated", "Below threshold", "Passed"])

    def test_catalog_reconciles_declared_total_without_fake_connections(self):
        result = self.run_js("""
const data={items:[{id:'haystack',status:'available'},{id:'otel',status:'unconfigured'}],total:2};
let mismatch=false,duplicate=false;
try {ui.validateCatalog({...data,total:10})}catch(e){mismatch=true}
try {ui.validateCatalog({items:[data.items[0],data.items[0]],total:2})}catch(e){duplicate=true}
console.log(JSON.stringify({count:ui.validateCatalog(data).length,mismatch,duplicate,
 urls:[ui.safeRepoUrl('https://github.com/deepset-ai/haystack'),ui.safeRepoUrl('javascript:alert(1)'),ui.safeRepoUrl('https://github.com.evil.test/org/repo'),ui.safeRepoUrl('https://user:password@github.com/org/repo')]}));
""")
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["mismatch"])
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["urls"], ["https://github.com/deepset-ai/haystack", None, None, None])

    def test_scores_preserve_evidence_precision_and_inputs_match_api_limits(self):
        result = self.run_js("console.log(JSON.stringify([ui.formatScore(0.751239),ui.formatScore(0),ui.formatScore(null)]));")
        self.assertEqual(result, ["0.751239", "0", "—"])
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        for maximum in ("maxlength: '100'", "maxlength: '50000'", "maxlength: '8000'"):
            self.assertIn(maximum, source)

    def test_partial_api_failure_does_not_hide_real_workspace_data(self):
        result = self.run_js("""
(async () => {
 const client={request:async path=>{if(path==='/api/integrations')throw new Error('Catalog unavailable');return path==='/api/projects'?{items:[{id:'real-project'}]}:{status:'ok'};}};
 const snapshot=await ui.readStudioSnapshot(client);
 console.log(JSON.stringify({project:snapshot.values.projects.items[0].id,catalog:snapshot.values.integrations??null,errors:snapshot.errors.map(e=>e.message)}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual(result["project"], "real-project")
        self.assertIsNone(result["catalog"])
        self.assertEqual(result["errors"], ["Integrations: Catalog unavailable"])

    def test_failed_run_history_load_is_not_an_empty_success(self):
        result = self.run_js("console.log(JSON.stringify([ui.hasRunData({loaded:true,projectId:'one',workspaceLoaded:false}),ui.hasRunData({loaded:true,projectId:'',workspaceLoaded:false}),ui.hasRunData({loaded:true,projectId:'one',workspaceLoaded:true}),ui.hasRunData({loaded:false,projectId:'',workspaceLoaded:false})]));")
        self.assertEqual(result, [False, True, True, False])
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("draft.mode = 'local'", source, "A live selection must not silently become local after a refresh")

    def test_license_copy_does_not_mislabel_source_available_tools(self):
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("An open-source foundation", source)
        self.assertIn("A ten-tool foundation", source)

    def test_empty_metrics_are_not_fabricated(self):
        result = self.run_js("console.log(JSON.stringify([ui.formatMetric(null),ui.formatMetric(0),ui.formatMetric(1250)]));")
        self.assertEqual(result, ["—", "0", "1,250"])


@unittest.skipUnless(os.environ.get("STUDIO_UI_E2E") == "1", "Opt-in real-browser test; requires running local API and Chromium")
class BrowserTests(unittest.TestCase):
    def test_real_browser_workflow(self):
        """Uses an isolated Chromium profile; mutation requires STUDIO_UI_MUTATE=1."""
        candidates = [
            os.environ.get("STUDIO_UI_BROWSER", ""),
            "C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            shutil.which("chromium") or "",
            shutil.which("google-chrome") or "",
        ]
        browser = next((value for value in candidates if value and Path(value).is_file()), None)
        self.assertIsNotNone(browser, "Set STUDIO_UI_BROWSER to a Chromium executable")
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node 22+ is required for native WebSocket CDP tests")
        output = Path(os.environ.get("STUDIO_UI_SCREENSHOTS", str(Path(tempfile.gettempdir()) / "studio-ui-evidence")))
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="studio-ui-browser-", ignore_cleanup_errors=True) as profile:
            process = subprocess.Popen([
                browser, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file = Path(profile) / "DevToolsActivePort"
                for _ in range(200):
                    if port_file.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(port_file.exists(), "Chromium did not expose its isolated CDP port")
                env = dict(os.environ, CDP_PORT=port_file.read_text().splitlines()[0], UI_EVIDENCE=str(output))
                result = subprocess.run([node, "-e", BROWSER_SCRIPT], capture_output=True, text=True, env=env, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["errors"], [])
                self.assertEqual(len(report["views"]), 6)
                self.assertFalse(report["mobileOverflow"])
                print("\nBrowser evidence: " + json.dumps(report, ensure_ascii=True))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


BROWSER_SCRIPT = r"""
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.STUDIO_UI_URL || 'http://127.0.0.1:8765';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const target=await (await fetch(`http://127.0.0.1:${process.env.CDP_PORT}/json/new?about:blank`,{method:'PUT'})).json();
 const ws=new WebSocket(target.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
 let counter=0;const pending=new Map();const errors=[];const requests=[];
 ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const callback=pending.get(message.id);if(callback){pending.delete(message.id);message.error?callback.reject(new Error(JSON.stringify(message.error))):callback.resolve(message.result)}}else if(message.method==='Runtime.exceptionThrown'){errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text)}else if(message.method==='Network.requestWillBeSent'){requests.push(message.params.request)}};
 const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++counter;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
 const evaluate=async expression=>{const result=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result?.value;};
 const wait=async(expression,timeout=15000)=>{const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await sleep(100)}throw new Error(`Timed out: ${expression}. Visible error: ${await evaluate("document.getElementById('error-banner')?.textContent")}`)};
 const screenshot=async name=>{const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(process.env.UI_EVIDENCE,name+'.png'),Buffer.from(shot.data,'base64'));};
 const click=async(selector,text)=>evaluate(`(()=>{const node=[...document.querySelectorAll(${JSON.stringify(selector)})].find(node=>node.textContent.trim()===${JSON.stringify(text)});if(!node)throw new Error('Missing button: '+${JSON.stringify(text)});node.click();})()`);
 const fill=async(selector,value)=>evaluate(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw new Error('Missing field');node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));})()`);
 const assert=(value,message)=>{if(!value)throw new Error(message)};
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Network.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
 await cdp('Page.navigate',{url:base});
 await wait("document.querySelector('.hero') && !document.getElementById('refresh-button').disabled");
 assert(requests.filter(request=>request.method==='POST').length===0,'Page load must never auto-seed or run');
 await screenshot('overview-desktop');
 const views=[];
 for(const view of ['overview','workspaces','workbench','history','integrations','setup']){
  await evaluate(`document.querySelector('[data-view="${view}"]').click()`);
  await wait(`!document.getElementById('view-${view}').hidden`);
  views.push(view);
 }
 const report={views,errors,catalogCards:await evaluate("document.querySelectorAll('.integration-card').length"),apiError:await evaluate("document.getElementById('error-banner').textContent"),mutated:false};
 if(process.env.STUDIO_UI_MUTATE==='1'){
  await evaluate("document.querySelector('[data-view=overview]').click()");
  await click('#view-overview button','Add sample workspace');
  await wait("location.hash==='#workspaces' && document.querySelectorAll('.document-row').length===2",30000);
  const projectId=await evaluate("document.getElementById('workspace-select').value");
  const sources=await evaluate(`fetch('/api/projects/${projectId}/documents').then(r=>r.json())`);
  assert(sources.items.length===2 && sources.items.every(doc=>doc.title.startsWith('Sample')),'Sample import must be labeled and persisted');
  await click('#view-workspaces button','Add document');
  await fill('#modal [name=title]','Sample · UI safety check');
  const unsafe='<img src=x onerror="window.uiInjected=true"> Plain text source for the sample UI check.';
  await fill('#modal [name=content]',unsafe);
  await evaluate("document.querySelector('#modal form').requestSubmit()");
  await wait("!document.getElementById('modal').open && document.querySelectorAll('.document-row').length===3");
  assert(await evaluate("document.querySelectorAll('.document-content img').length===0 && !window.uiInjected"),'Document markup was executed');
  await evaluate("window.confirm=()=>true; [...document.querySelectorAll('.document-row')].find(row=>row.textContent.includes('Sample · UI safety check')).querySelector('button').click()");
  await wait("document.querySelectorAll('.document-row').length===2");
  await evaluate("document.querySelector('[data-view=workbench]').click()");
  assert(await evaluate("document.querySelector('#run-form [name=engine] option[value=instructor]').disabled"),'Local mode must disable Instructor');
  await fill('#run-prompt','What does the sample release policy require before launch?');
  assert(await evaluate("document.querySelector('[name=mode][value=live]').disabled"),'Acceptance test must never enable live inference');
  await screenshot('workbench-ready-desktop');
  await evaluate("document.getElementById('run-form').requestSubmit()");
  await wait("!!document.querySelector('.evidence-panel')",90000);
  report.runStatus=await evaluate("document.querySelector('.evidence-meta .badge').textContent");
  report.runId=await evaluate("document.querySelector('.run-id').textContent.replace('RUN ','')");
  for(const tab of ['Citations','Evaluations','Trace','Output']){
   await click('#view-workbench [role=tab]',tab);
   assert(await evaluate(`!document.getElementById('evidence-${tab.toLowerCase()}').hidden`),'Evidence tab failed');
  }
  await screenshot('workbench-evidence-desktop');
  await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:process.env.UI_EVIDENCE});
  await click('#view-workbench button','Export JSON');
  await wait("document.getElementById('toast').textContent.includes('exported')");
  const exported=await evaluate(`fetch('/api/runs/${report.runId}/export').then(r=>r.json())`);
  assert(exported.id===report.runId,'Exported run differs from selected evidence');
  await evaluate("document.querySelector('[data-view=history]').click()");
  await fill('[name=history-query]','no-matching-prompt-ui-test');
  assert(await evaluate("document.getElementById('history-content').textContent.includes('No matching runs')"),'History filter failed');
  await fill('[name=history-query]','');
  assert(await evaluate("document.querySelectorAll('#history-content .run-row').length>0"),'Persisted run missing from history');
  report.mutated=true;report.projectId=projectId;
 }
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 const overflow=[];
 for(const view of views){
  await evaluate(`document.querySelector('[data-view="${view}"]').click();window.scrollTo(0,0)`);
  await sleep(100);
  if(await evaluate('document.documentElement.scrollWidth>window.innerWidth'))overflow.push(view);
  if(view==='overview'||view==='workbench')await screenshot(view+'-mobile');
 }
 report.mobileOverflow=overflow.length>0;report.overflowViews=overflow;report.screenshots=process.env.UI_EVIDENCE;
 assert(errors.length===0,'Runtime errors: '+errors.join('; '));
 console.log(JSON.stringify(report));ws.close();
})().catch(error=>{console.error(error.stack);process.exit(1)});
"""


if __name__ == "__main__":
    unittest.main()
