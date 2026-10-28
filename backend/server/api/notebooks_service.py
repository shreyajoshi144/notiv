"""Persistent notebook domain operations. Every private operation scopes by owner_id."""
import hashlib, json, secrets, time
from core.lecture import store
from core.lecture.errors import NotFoundError, ValidationError

MAX_CONTENT = 1_000_000
ITEM_TYPES = {"note", "summary", "concepts", "definitions", "flashcards", "quiz", "mind_map", "presentation", "study_guide", "brainstorm"}

def _now(): return time.time()
def _json(value, max_bytes=MAX_CONTENT):
    raw=json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > max_bytes: raise ValidationError("Content is too large")
    return raw
def _obj(row):
    if not row: return None
    d=dict(row)
    for k in ("content_json", "provenance_json", "metadata_json", "state_json"):
        if k in d:
            try: d[k.removesuffix("_json") if k.endswith("_json") else k]=json.loads(d.pop(k))
            except (ValueError, TypeError): d[k]=None
    return d
def _notebook(c, uid, nid):
    r=c.execute("SELECT * FROM notebooks WHERE id=? AND owner_id=?",(nid,uid)).fetchone()
    if not r: raise NotFoundError("not found")
    return dict(r)
def list_notebooks(uid, q="", include_archived=False):
    c=store.conn(); sql="SELECT n.*, (SELECT COUNT(*) FROM notebook_sources s WHERE s.notebook_id=n.id AND s.owner_id=n.owner_id) source_count, (SELECT COUNT(*) FROM notebook_items i WHERE i.notebook_id=n.id AND i.owner_id=n.owner_id AND i.deleted_at IS NULL) item_count FROM notebooks n WHERE n.owner_id=?"
    args=[uid]
    if not include_archived: sql+=" AND n.status='active'"
    if q.strip(): sql+=" AND (lower(n.title) LIKE ? OR lower(n.subject) LIKE ? OR lower(n.description) LIKE ?)"; pat="%"+q.strip().lower()+"%"; args += [pat,pat,pat]
    sql+=" ORDER BY n.updated_at DESC, n.id DESC LIMIT 200"
    return {"items":[dict(r) for r in c.execute(sql,args).fetchall()]}
def create_notebook(uid, title, description="", subject="", workspace_id=None):
    title=title.strip()
    if not title or len(title)>200: raise ValidationError("Title must be 1–200 characters")
    now=_now(); c=store.conn()
    if workspace_id is not None:
        member=c.execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?",(workspace_id,uid)).fetchone()
        if not member: raise NotFoundError("not found")
    cur=c.execute("INSERT INTO notebooks(owner_id,workspace_id,title,description,subject,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(uid,workspace_id,title,description.strip()[:2000],subject.strip()[:120],"active",now,now)); c.commit()
    return get_notebook(uid,cur.lastrowid)
def get_notebook(uid,nid):
    c=store.conn(); n=_notebook(c,uid,nid)
    n["sources"]=[dict(r) for r in c.execute("SELECT id,source_type,source_id,label,metadata_json,created_at FROM notebook_sources WHERE notebook_id=? AND owner_id=? ORDER BY id",(nid,uid)).fetchall()]
    for s in n["sources"]:
        try:s["metadata"]=json.loads(s.pop("metadata_json"))
        except Exception:s["metadata"]={}
    n["items"]=[_obj(r) for r in c.execute("SELECT * FROM notebook_items WHERE notebook_id=? AND owner_id=? AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC",(nid,uid)).fetchall()]
    canvas=c.execute("SELECT state_json,version,updated_at FROM notebook_canvas_state WHERE notebook_id=? AND owner_id=?",(nid,uid)).fetchone()
    n["canvas"]={"nodes":[],"edges":[]} if not canvas else json.loads(canvas["state_json"])
    n["canvas_version"]=canvas["version"] if canvas else 0
    return n
def update_notebook(uid,nid,patch):
    allowed={"title","description","subject","status","workspace_id"}; patch={k:v for k,v in patch.items() if k in allowed}
    if not patch: return get_notebook(uid,nid)
    c=store.conn(); _notebook(c,uid,nid)
    if "title" in patch:
        patch["title"]=str(patch["title"]).strip()
        if not patch["title"] or len(patch["title"])>200: raise ValidationError("Title must be 1–200 characters")
    if "status" in patch and patch["status"] not in ("active","archived"): raise ValidationError("Invalid lifecycle status")
    if "workspace_id" in patch and patch["workspace_id"] is not None:
        try: patch["workspace_id"]=int(patch["workspace_id"])
        except (TypeError,ValueError): raise ValidationError("workspace_id must be an integer or null")
        if patch["workspace_id"]<=0: raise ValidationError("workspace_id must be positive")
        member=c.execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?",(patch["workspace_id"],uid)).fetchone()
        if not member: raise NotFoundError("not found")
    for k in ("description","subject"):
        if k in patch: patch[k]=str(patch[k]).strip()[:2000 if k=="description" else 120]
    patch["updated_at"]=_now()
    if patch.get("status")=="archived": patch["archived_at"]=_now()
    elif patch.get("status")=="active": patch["archived_at"]=None
    c.execute("UPDATE notebooks SET "+", ".join(k+"=?" for k in patch)+" WHERE id=? AND owner_id=?",(*patch.values(),nid,uid)); c.commit(); return get_notebook(uid,nid)
def delete_notebook(uid,nid):
    c=store.conn(); _notebook(c,uid,nid)
    # Explicit dependent deletes work consistently on SQLite and PostgreSQL, including legacy DBs without FKs.
    for table in ("notebook_sources","notebook_items","notebook_canvas_state","notebook_shares"):
        c.execute(f"DELETE FROM {table} WHERE notebook_id=? AND owner_id=?",(nid,uid))
    c.execute("DELETE FROM notebooks WHERE id=? AND owner_id=?",(nid,uid)); c.commit()
def add_source(uid,nid,source_type,source_id,label="",metadata=None):
    c=store.conn(); _notebook(c,uid,nid)
    if source_type=="lecture":
        row=c.execute("SELECT id,title,status FROM lectures WHERE id=? AND owner_id=?",(source_id,uid)).fetchone()
        if not row: raise NotFoundError("not found")
        label=label.strip() or row["title"] or f"Lecture {source_id}"
        metadata={**(metadata or {}),"status":row["status"]}
    else: raise ValidationError("Only owned lecture sources are supported currently")
    now=_now()
    # Idempotent linking without relying on catching a uniqueness exception. This is
    # important for PostgreSQL, where a failed statement aborts the transaction.
    existing=c.execute("SELECT id FROM notebook_sources WHERE notebook_id=? AND owner_id=? AND source_type=? AND source_id=?",(nid,uid,source_type,source_id)).fetchone()
    if existing:
        sid=existing["id"]
    else:
        cur=c.execute("INSERT INTO notebook_sources(notebook_id,owner_id,source_type,source_id,label,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",(nid,uid,source_type,source_id,label[:300],_json(metadata or {},20000),now)); sid=cur.lastrowid
    c.execute("UPDATE notebooks SET updated_at=? WHERE id=? AND owner_id=?",(now,nid,uid)); c.commit()
    return dict(c.execute("SELECT * FROM notebook_sources WHERE id=? AND owner_id=?",(sid,uid)).fetchone())
def remove_source(uid,nid,sid):
    c=store.conn(); _notebook(c,uid,nid); cur=c.execute("DELETE FROM notebook_sources WHERE id=? AND notebook_id=? AND owner_id=?",(sid,nid,uid))
    if cur.rowcount==0: raise NotFoundError("not found")
    c.execute("UPDATE notebooks SET updated_at=? WHERE id=? AND owner_id=?",(_now(),nid,uid)); c.commit()
def save_item(uid,nid,item_id,item_type,title,content,provenance=None,expected_version=None):
    c=store.conn(); _notebook(c,uid,nid)
    if item_type not in ITEM_TYPES: raise ValidationError("Unsupported notebook item type")
    title=title.strip()
    if not title or len(title)>240: raise ValidationError("Item title must be 1–240 characters")
    cj=_json(content); pj=_json(provenance or [],100000)
    now=_now()
    if item_id:
        old=c.execute("SELECT version FROM notebook_items WHERE id=? AND notebook_id=? AND owner_id=? AND deleted_at IS NULL",(item_id,nid,uid)).fetchone()
        if not old: raise NotFoundError("not found")
        if expected_version is not None and int(expected_version)!=old["version"]: raise ValidationError("Version conflict; reload before saving")
        version=old["version"]+1
        c.execute("UPDATE notebook_items SET item_type=?,title=?,content_json=?,provenance_json=?,version=?,updated_at=? WHERE id=? AND notebook_id=? AND owner_id=?",(item_type,title,cj,pj,version,now,item_id,nid,uid)); iid=item_id
    else:
        cur=c.execute("INSERT INTO notebook_items(notebook_id,owner_id,item_type,title,content_json,provenance_json,version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",(nid,uid,item_type,title,cj,pj,now,now)); iid=cur.lastrowid
    c.execute("UPDATE notebooks SET updated_at=? WHERE id=? AND owner_id=?",(now,nid,uid)); c.commit()
    return _obj(c.execute("SELECT * FROM notebook_items WHERE id=? AND owner_id=?",(iid,uid)).fetchone())
def delete_item(uid,nid,iid):
    c=store.conn(); _notebook(c,uid,nid); cur=c.execute("UPDATE notebook_items SET deleted_at=?,updated_at=? WHERE id=? AND notebook_id=? AND owner_id=? AND deleted_at IS NULL",(_now(),_now(),iid,nid,uid))
    if not cur.rowcount: raise NotFoundError("not found")
    c.commit()
def save_canvas(uid,nid,state,expected_version=None):
    c=store.conn(); _notebook(c,uid,nid)
    if not isinstance(state,dict) or not isinstance(state.get("nodes",[]),list) or not isinstance(state.get("edges",[]),list): raise ValidationError("Canvas must contain node and edge lists")
    if len(state.get("nodes",[]))>500 or len(state.get("edges",[]))>1000: raise ValidationError("Canvas exceeds node/connection limits")
    node_ids=set()
    for node in state.get("nodes",[]):
        if not isinstance(node,dict) or not isinstance(node.get("id"),str) or not node.get("id") or len(node.get("id"))>100: raise ValidationError("Each canvas node needs a valid ID")
        if node["id"] in node_ids: raise ValidationError("Canvas node IDs must be unique")
        node_ids.add(node["id"])
        if len(str(node.get("text","")))>5000: raise ValidationError("Canvas node text is too long")
        for axis in ("x","y"):
            if axis in node:
                try: pos=float(node[axis])
                except (TypeError,ValueError): raise ValidationError("Canvas positions must be numeric")
                if not __import__("math").isfinite(pos) or abs(pos)>100000: raise ValidationError("Canvas positions are outside supported bounds")
    for edge in state.get("edges",[]):
        if not isinstance(edge,dict) or edge.get("from") not in node_ids or edge.get("to") not in node_ids or edge.get("from")==edge.get("to"):
            raise ValidationError("Canvas connections must link two distinct existing nodes")
    raw=_json({"nodes":state.get("nodes",[]),"edges":state.get("edges",[])},500000); now=_now()
    old=c.execute("SELECT version FROM notebook_canvas_state WHERE notebook_id=? AND owner_id=?",(nid,uid)).fetchone()
    if old and expected_version is not None and int(expected_version)!=old["version"]: raise ValidationError("Canvas changed elsewhere; reload before saving")
    version=(old["version"]+1) if old else 1
    if old:c.execute("UPDATE notebook_canvas_state SET state_json=?,version=?,updated_at=? WHERE notebook_id=? AND owner_id=?",(raw,version,now,nid,uid))
    else:c.execute("INSERT INTO notebook_canvas_state(notebook_id,owner_id,state_json,version,updated_at) VALUES(?,?,?,?,?)",(nid,uid,raw,version,now))
    c.execute("UPDATE notebooks SET updated_at=? WHERE id=? AND owner_id=?",(now,nid,uid));c.commit();return {"state":json.loads(raw),"version":version,"updated_at":now}
def list_shares(uid,nid):
    c=store.conn();_notebook(c,uid,nid)
    return {"items":[dict(r) for r in c.execute("SELECT id,permission,expires_at,revoked_at,created_at FROM notebook_shares WHERE notebook_id=? AND owner_id=? ORDER BY created_at DESC",(nid,uid)).fetchall()]}
def create_share(uid,nid,permission="read",expires_in_seconds=None):
    c=store.conn();_notebook(c,uid,nid)
    if permission!="read":raise ValidationError("Only read-only sharing is enabled until editor permission enforcement is implemented")
    exp=None
    if expires_in_seconds is not None:
        if not 300<=int(expires_in_seconds)<=2592000:raise ValidationError("Share expiry must be between 5 minutes and 30 days")
        exp=_now()+int(expires_in_seconds)
    token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode()).hexdigest(); now=_now()
    cur=c.execute("INSERT INTO notebook_shares(notebook_id,owner_id,token_hash,permission,expires_at,created_at) VALUES(?,?,?,?,?,?)",(nid,uid,digest,"read",exp,now));c.commit()
    return {"id":cur.lastrowid,"token":token,"permission":"read","expires_at":exp,"created_at":now}
def revoke_share(uid,nid,sid):
    c=store.conn();_notebook(c,uid,nid);cur=c.execute("UPDATE notebook_shares SET revoked_at=? WHERE id=? AND notebook_id=? AND owner_id=? AND revoked_at IS NULL",(_now(),sid,nid,uid))
    if not cur.rowcount:raise NotFoundError("not found")
    c.commit()
def public_share(token):
    digest=hashlib.sha256(token.encode()).hexdigest(); c=store.conn(); row=c.execute("SELECT s.*,n.title,n.description,n.subject FROM notebook_shares s JOIN notebooks n ON n.id=s.notebook_id AND n.owner_id=s.owner_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at>?) AND n.status='active'",(digest,_now())).fetchone()
    if not row:raise NotFoundError("share not found or expired")
    nid=row["notebook_id"]; uid=row["owner_id"]
    items=[_obj(r) for r in c.execute("SELECT item_type,title,content_json,provenance_json,version,updated_at FROM notebook_items WHERE notebook_id=? AND owner_id=? AND deleted_at IS NULL ORDER BY updated_at DESC",(nid,uid)).fetchall()]
    sources=[{"source_type":r["source_type"],"label":r["label"]} for r in c.execute("SELECT source_type,label FROM notebook_sources WHERE notebook_id=? AND owner_id=? ORDER BY id",(nid,uid)).fetchall()]
    canvas=c.execute("SELECT state_json FROM notebook_canvas_state WHERE notebook_id=? AND owner_id=?",(nid,uid)).fetchone()
    return {"title":row["title"],"description":row["description"],"subject":row["subject"],"permission":"read","sources":sources,"items":items,"canvas":json.loads(canvas["state_json"]) if canvas else {"nodes":[],"edges":[]}}
