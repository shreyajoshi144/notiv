"""Browser E2E for notebook creation, lecture source linking, note/canvas persistence, export and sharing.
Run: PYTHONPATH=backend:. python tools/e2e_notebooks.py. Requires Chromium and Playwright.
"""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]; SECRET="notiv-e2e-notebooks-secret-at-least-32-bytes"
def main():
 with tempfile.TemporaryDirectory(prefix='notiv-notebook-e2e-') as td:
  env=os.environ.copy();env.update({"PYTHONPATH":f"{ROOT/'backend'}:{ROOT}","NOTIV_AUTH_SECRET":SECRET,"NOTIV_LECTURE_DB":str(Path(td)/'e2e.db'),"NOTIV_STORAGE_DIR":str(Path(td)/'objects'),"NOTIV_INPROCESS_WORKER":"0","NOTIV_ENV":"development"})
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  server=subprocess.Popen([sys.executable,'-m','uvicorn','server.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE);base=f'http://127.0.0.1:{port}'
  try:
   for _ in range(100):
    if server.poll() is not None:raise RuntimeError(server.stderr.read().decode(errors='replace')[-2500:])
    try:
     with urlopen(base+'/healthz',timeout=1):break
    except Exception:time.sleep(.2)
   else:raise RuntimeError('server startup timed out')
   from server.auth_tokens import create_token
   token=create_token(888,SECRET,3600);errors=[];bad=[]
   with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page(viewport={'width':1365,'height':900})
    page.add_init_script("sessionStorage.setItem('notiv_token',"+json.dumps(token)+")")
    page.on('pageerror',lambda e:errors.append(str(e)));page.on('response',lambda r:bad.append(f'{r.status} {r.url}') if r.url.startswith(base) and r.status>=400 else None)
    page.goto(base+'/app/notebooks',wait_until='domcontentloaded');page.get_by_role('heading',name='Notebooks, not loose notes.').wait_for()
    page.locator('#newBtn').click();page.locator('#title').fill('Browser E2E notebook');page.locator('#subject').fill('Testing');page.locator('#createForm button[type=submit]').click();page.get_by_role('heading',name='Browser E2E notebook').wait_for();nb_id=int(page.url.rstrip('/').split('/')[-1])
    result=page.evaluate("async()=>{let r=await fetch('/lectures',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+sessionStorage.getItem('notiv_token')},body:JSON.stringify({title:'E2E source lecture'})});return {status:r.status,data:await r.json()}}")
    if result['status']!=201:raise AssertionError('lecture create failed: '+str(result))
    lid=result['data']['lecture_id'];page.locator('[data-tab="sources"]').click();page.locator('#lectureSearch').fill('E2E source lecture');page.locator('#lectureId').select_option(str(lid));page.locator('#sourceForm button[type=submit]').click();page.get_by_text('E2E source lecture').wait_for()
    page.locator('[data-tab="notes"]').click();page.locator('#newNote2').click();page.locator('#editorTitle').fill('Persistent note');page.locator('#editorContent').fill('Evidence-linked study notes');page.locator('#editorForm button[type=submit]').click();page.get_by_text('Saved at',exact=False).wait_for()
    page.locator('[data-tab="canvas"]').click();page.locator('#addNode').click();page.locator('#canvas textarea').nth(0).fill('Canvas idea survives refresh');page.locator('#addNode').click();page.locator('#canvas textarea').nth(1).fill('Connected evidence node');page.locator('#connectNodes').click();page.locator('#saveCanvas').click();page.get_by_text('Canvas saved.').wait_for();page.locator('#canvas svg line').wait_for()
    page.locator('[data-tab="export"]').click();with_download=page.expect_download();page.locator('[data-export="json"]').click();download=with_download.value
    if not download.suggested_filename.endswith('.json'):raise AssertionError('unexpected export filename '+download.suggested_filename)
    exported_path=Path(td)/download.suggested_filename;download.save_as(exported_path);exported=json.loads(exported_path.read_text());assert exported.get('title')=='Browser E2E notebook', 'export JSON did not preserve notebook title'
    page.reload(wait_until='domcontentloaded');page.get_by_role('heading',name='Browser E2E notebook').wait_for();page.locator('[data-tab="notes"]').click();page.get_by_text('Evidence-linked study notes').wait_for();page.locator('[data-tab="canvas"]').click();page.get_by_text('Canvas idea survives refresh').wait_for();page.locator('#canvas svg line').wait_for()
    page.locator('#shareBtn').click();page.locator('#shareResult:not(.hidden)').wait_for();url=page.locator('#shareResult p').inner_text();share_token=url.rstrip('/').split('/')[-1]
    shared=browser.new_page(viewport={'width':1200,'height':800});shared.on('pageerror',lambda e:errors.append('share: '+str(e)));shared.goto(url,wait_until='domcontentloaded');shared.get_by_role('heading',name='Browser E2E notebook').wait_for();shared.get_by_text('Evidence-linked study notes').wait_for()
    page.once('dialog',lambda dialog:dialog.accept());page.locator('#revokeShare').click();page.get_by_text('Share link revoked.').wait_for();revoked=shared.evaluate("async token=>{const r=await fetch('/shared/notebooks/'+token);return r.status}",share_token)
    if revoked!=404:raise AssertionError('revoked share should return 404, got '+str(revoked))
    if errors or bad:raise AssertionError('browser errors='+repr(errors)+' HTTP errors='+repr(bad))
    print('PASS: notebook created; authorized lecture picker linked an owned lecture; note and connected canvas persisted after reload; JSON export downloaded and parsed; read-only share opened and revocation returned 404.')
    browser.close()
  finally:
   server.terminate()
   try:server.wait(timeout=5)
   except subprocess.TimeoutExpired:server.kill()
if __name__=='__main__':main()
