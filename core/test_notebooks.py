"""Persistence and authorization regression tests for the notebook milestone."""
import os, tempfile, unittest, json

SECRET='n'*40
class TestNotebookApi(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  from core.lecture import config
  from core.lecture.testkit import Env
  from fastapi.testclient import TestClient
  from server.auth_tokens import create_token
  from server.main import app
  cls.tmp=tempfile.mkdtemp(prefix='notiv-notebooks-'); cls.env=Env(cls.tmp); config.configure(cls.env.repo,cls.env.storage)
  os.environ['NOTIV_AUTH_SECRET']=SECRET
  cls.client=TestClient(app,raise_server_exceptions=False)
  cls.h={'Authorization':'Bearer '+create_token(11,SECRET)};cls.other={'Authorization':'Bearer '+create_token(22,SECRET)}
 @classmethod
 def tearDownClass(cls):
  from core.lecture import config
  config.configure()
 def test_full_persistent_crud_canvas_export_share(self):
  r=self.client.post('/notebooks',headers=self.h,json={'title':'Algorithms','subject':'CS','description':'Exam prep'});self.assertEqual(r.status_code,201,r.text);nb=r.json();nid=nb['id']
  self.assertEqual(self.client.get(f'/notebooks/{nid}',headers=self.other).status_code,404)
  lid=self.env.lecture(user=11,title='Trees and graphs')
  sr=self.client.post(f'/notebooks/{nid}/sources',headers=self.h,json={'source_type':'lecture','source_id':lid});self.assertEqual(sr.status_code,201,sr.text)
  self.assertEqual(self.client.post(f'/notebooks/{nid}/sources',headers=self.other,json={'source_type':'lecture','source_id':lid}).status_code,404)
  item=self.client.post(f'/notebooks/{nid}/items',headers=self.h,json={'item_type':'note','title':'Revision','content':'Binary tree notes','provenance':[{'lecture_id':lid,'timestamp':12.4}]});self.assertEqual(item.status_code,201,item.text);iid=item.json()['id']
  updated=self.client.put(f'/notebooks/{nid}/items/{iid}',headers=self.h,json={'item_type':'note','title':'Revision','content':'Edited notes','provenance':[],'expected_version':1});self.assertEqual(updated.status_code,200,updated.text);self.assertEqual(updated.json()['version'],2)
  item_export=self.client.get(f'/notebooks/{nid}/items/{iid}/export?format=md',headers=self.h);self.assertEqual(item_export.status_code,200);self.assertIn(b'Edited notes',item_export.content)
  conflict=self.client.put(f'/notebooks/{nid}/items/{iid}',headers=self.h,json={'item_type':'note','title':'Revision','content':'Stale','provenance':[],'expected_version':1});self.assertEqual(conflict.status_code,409)
  canvas={'nodes':[{'id':'a','text':'Idea','x':30,'y':40},{'id':'b','text':'Evidence','x':300,'y':80}],'edges':[{'from':'a','to':'b'}]}
  cr=self.client.put(f'/notebooks/{nid}/canvas',headers=self.h,json={'state':canvas});self.assertEqual(cr.status_code,200,cr.text);self.assertEqual(cr.json()['state'],canvas)
  # New API read confirms durable rows, not only the response object.
  loaded=self.client.get(f'/notebooks/{nid}',headers=self.h).json();self.assertEqual(loaded['canvas'],canvas);self.assertEqual(loaded['items'][0]['content'],'Edited notes')
  self.assertEqual(self.client.get(f'/notebooks/{nid}/export?format=md',headers=self.h).status_code,200)
  self.assertEqual(self.client.get(f'/notebooks/{nid}/export?format=json',headers=self.h).status_code,200)
  for fmt in ('txt','csv','svg','docx','pdf','pptx'):
   exported=self.client.get(f'/notebooks/{nid}/export?format={fmt}',headers=self.h)
   self.assertEqual(exported.status_code,200,f'{fmt}: {exported.text[:200]}')
   self.assertGreater(len(exported.content),0,fmt)
  share=self.client.post(f'/notebooks/{nid}/shares',headers=self.h,json={'permission':'read','expires_in_seconds':3600});self.assertEqual(share.status_code,201,share.text);token=share.json()['token']
  public=self.client.get('/shared/notebooks/'+token);self.assertEqual(public.status_code,200,public.text);self.assertEqual(public.json()['permission'],'read')
  self.assertEqual(self.client.delete(f'/notebooks/{nid}/shares/{share.json()["id"]}',headers=self.other).status_code,404)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}/shares/{share.json()["id"]}',headers=self.h).status_code,204)
  self.assertEqual(self.client.get('/shared/notebooks/'+token).status_code,404)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}',headers=self.other).status_code,404)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}',headers=self.h).status_code,204)
  self.assertEqual(self.client.get(f'/notebooks/{nid}',headers=self.h).status_code,404)
 def test_private_endpoint_ownership_and_export_integrity(self):
  nb=self.client.post('/notebooks',headers=self.h,json={'title':'Isolation notebook'}).json();nid=nb['id']
  item=self.client.post(f'/notebooks/{nid}/items',headers=self.h,json={'item_type':'note','title':'Private source note','content':'Visible text','provenance':[{'lecture_id':42,'timestamp':18.5}]}).json();iid=item['id']
  self.assertEqual(self.client.put(f'/notebooks/{nid}/items/{iid}',headers=self.other,json={'item_type':'note','title':'Takeover','content':'bad','provenance':[],'expected_version':1}).status_code,404)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}/items/{iid}',headers=self.other).status_code,404)
  self.assertEqual(self.client.put(f'/notebooks/{nid}/canvas',headers=self.other,json={'state':{'nodes':[],'edges':[]}}).status_code,404)
  self.assertEqual(self.client.get(f'/notebooks/{nid}/export?format=json',headers=self.other).status_code,404)
  self.assertEqual(self.client.get(f'/notebooks/{nid}/items/{iid}/export?format=md',headers=self.other).status_code,404)
  self.assertEqual(self.client.post(f'/notebooks/{nid}/shares',headers=self.other,json={'permission':'read'}).status_code,404)
  for fmt,magic in [('docx',b'PK\x03\x04'),('pptx',b'PK\x03\x04'),('pdf',b'%PDF-')]:
   r=self.client.get(f'/notebooks/{nid}/export?format={fmt}',headers=self.h)
   self.assertEqual(r.status_code,200,f'{fmt}: {r.text[:200]}');self.assertTrue(r.content.startswith(magic),fmt)
  svg=self.client.get(f'/notebooks/{nid}/export?format=svg',headers=self.h)
  self.assertEqual(svg.status_code,200);self.assertIn(b'<svg',svg.content);self.assertIn(b'Isolation notebook',svg.content)
  md=self.client.get(f'/notebooks/{nid}/export?format=md',headers=self.h)
  self.assertIn(b'Visible text',md.content);self.assertIn(b'lecture_id',md.content);self.assertIn(b'timestamp',md.content)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}',headers=self.h).status_code,204)
 def test_lecture_picker_api_returns_only_owned_lectures(self):
  own=self.env.lecture(user=11,title='Picker owned lecture')
  foreign=self.env.lecture(user=22,title='Picker private lecture')
  r=self.client.get('/lectures?limit=200&offset=0',headers=self.h)
  self.assertEqual(r.status_code,200,r.text)
  ids={int(x.get('id',x.get('lecture_id'))) for x in r.json()['items']}
  self.assertIn(own,ids)
  self.assertNotIn(foreign,ids)
  ui=self.client.get('/app/notebooks').text;self.assertIn('/static/ui/notebooks.js',ui)
 def test_canvas_validation_and_linking_are_idempotent(self):
  nb=self.client.post('/notebooks',headers=self.h,json={'title':'Canvas validation'}).json();nid=nb['id']
  lid=self.env.lecture(user=11,title='Idempotent source')
  a=self.client.post(f'/notebooks/{nid}/sources',headers=self.h,json={'source_type':'lecture','source_id':lid})
  b=self.client.post(f'/notebooks/{nid}/sources',headers=self.h,json={'source_type':'lecture','source_id':lid})
  self.assertEqual(a.status_code,201,a.text);self.assertEqual(b.status_code,201,b.text);self.assertEqual(a.json()['id'],b.json()['id'])
  invalid={'nodes':[{'id':'one','text':'A','x':0,'y':0}],'edges':[{'from':'one','to':'missing'}]}
  self.assertEqual(self.client.put(f'/notebooks/{nid}/canvas',headers=self.h,json={'state':invalid}).status_code,400)
  valid={'nodes':[{'id':'one','text':'A','x':0,'y':0},{'id':'two','text':'B','x':220,'y':0}],'edges':[{'from':'one','to':'two'}]}
  saved=self.client.put(f'/notebooks/{nid}/canvas',headers=self.h,json={'state':valid})
  self.assertEqual(saved.status_code,200,saved.text)
  second=self.client.put(f'/notebooks/{nid}/canvas',headers=self.h,json={'state':valid,'expected_version':1})
  self.assertEqual(second.status_code,200,second.text)
  stale=self.client.put(f'/notebooks/{nid}/canvas',headers=self.h,json={'state':valid,'expected_version':1})
  self.assertEqual(stale.status_code,409)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}',headers=self.h).status_code,204)
 def test_generation_requires_source_link(self):
  nb=self.client.post('/notebooks',headers=self.h,json={'title':'Grounding guard'}).json();nid=nb['id']
  lid=self.env.lecture(user=11,title='Unlinked lecture')
  r=self.client.post(f'/notebooks/{nid}/generate',headers=self.h,json={'source_id':lid,'output_type':'summary'})
  self.assertEqual(r.status_code,400);self.assertIn('Link this lecture',r.text)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}',headers=self.h).status_code,204)
 def test_workspace_association_is_membership_checked(self):
  from core.lecture import store
  wid=store.create_workspace(11,"Study workspace")
  r=self.client.post('/notebooks',headers=self.h,json={'title':'Workspace notebook','workspace_id':wid});self.assertEqual(r.status_code,201,r.text);self.assertEqual(r.json()['workspace_id'],wid)
  self.assertEqual(self.client.post('/notebooks',headers=self.h,json={'title':'Foreign workspace','workspace_id':999999}).status_code,404)
 def test_notebook_ui_and_asset_routes(self):
  r=self.client.get("/app/notebooks");self.assertEqual(r.status_code,200);self.assertIn("/static/ui/notebooks.js",r.text)
  self.assertEqual(self.client.get("/static/assets/notiv-hero.jpg").status_code,200)
  self.assertEqual(self.client.get("/share/anything").status_code,200)
 def test_empty_library_and_validation(self):
  r=self.client.get('/notebooks?q=unlikely-notiv-query-xyz',headers=self.h);self.assertEqual(r.status_code,200);self.assertEqual(r.json()['items'],[])
  r=self.client.post('/notebooks',headers=self.h,json={'title':'   '});self.assertIn(r.status_code,(400,422))
  r=self.client.post('/notebooks',headers=self.h,json={'title':'bad','unknown':'x'});self.assertEqual(r.status_code,422)
 def test_expired_share_fails_closed(self):
  nb=self.client.post('/notebooks',headers=self.h,json={'title':'Share expiry'}).json(); nid=nb['id']
  share=self.client.post(f'/notebooks/{nid}/shares',headers=self.h,json={'permission':'read','expires_in_seconds':300})
  self.assertEqual(share.status_code,201,share.text); token=share.json()['token']
  from core.lecture import store
  c=store.conn();c.execute("UPDATE notebook_shares SET expires_at=? WHERE id=?",(0,share.json()['id']));c.commit()
  self.assertEqual(self.client.get('/shared/notebooks/'+token).status_code,404)
  self.assertEqual(self.client.delete(f'/notebooks/{nid}',headers=self.h).status_code,204)
if __name__=='__main__':unittest.main()
