"""Notebook API: persistent, owner-scoped content, canvas, exports and revocable read-only shares."""
import re
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Body, Query, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from server.api.deps import get_current_user
from core.lecture.errors import NotFoundError, ValidationError
from server.api import notebooks_service as svc

router=APIRouter(tags=["notebooks"])
def run(fn,*a,**kw):
    try:return fn(*a,**kw)
    except NotFoundError: raise HTTPException(404,"not found")
    except ValidationError as e: raise HTTPException(409 if "Version conflict" in str(e) or "changed elsewhere" in str(e) else 400,str(e))

class NotebookIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    subject: str = Field("", max_length=120)
    workspace_id: Optional[int] = Field(None, gt=0)
    model_config = {"extra": "forbid"}

class SourceIn(BaseModel):
    source_type: str = "lecture"
    source_id: int = Field(..., gt=0)
    label: str = Field("", max_length=300)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

class ItemIn(BaseModel):
    item_type: str = Field(..., max_length=40)
    title: str = Field(..., min_length=1, max_length=240)
    content: Any
    provenance: list = Field(default_factory=list)
    expected_version: Optional[int] = Field(None, ge=1)
    model_config = {"extra": "forbid"}

class CanvasIn(BaseModel):
    state: Dict[str, Any]
    expected_version: Optional[int] = Field(None, ge=1)
    model_config = {"extra": "forbid"}

class ShareIn(BaseModel):
    permission: str = "read"
    expires_in_seconds: Optional[int] = None
    model_config = {"extra": "forbid"}

@router.get("/notebooks")
def notebooks(q:str=Query("",max_length=120),include_archived:bool=False,user:dict=Depends(get_current_user)):return run(svc.list_notebooks,user["id"],q,include_archived)
@router.post("/notebooks",status_code=201)
def notebook_create(body:NotebookIn,user:dict=Depends(get_current_user)):return run(svc.create_notebook,user["id"],body.title,body.description,body.subject,body.workspace_id)
@router.get("/notebooks/{notebook_id}")
def notebook_get(notebook_id:int,user:dict=Depends(get_current_user)):return run(svc.get_notebook,user["id"],notebook_id)
@router.patch("/notebooks/{notebook_id}")
def notebook_patch(notebook_id:int,body:Dict[str,Any]=Body(...),user:dict=Depends(get_current_user)):
 if set(body)-{"title","description","subject","status","workspace_id"}:raise HTTPException(422,"Unsupported fields")
 return run(svc.update_notebook,user["id"],notebook_id,body)
@router.delete("/notebooks/{notebook_id}",status_code=204)
def notebook_delete(notebook_id:int,user:dict=Depends(get_current_user)):run(svc.delete_notebook,user["id"],notebook_id);return Response(status_code=204)
@router.post("/notebooks/{notebook_id}/sources",status_code=201)
def source_add(notebook_id:int,body:SourceIn,user:dict=Depends(get_current_user)):return run(svc.add_source,user["id"],notebook_id,body.source_type,body.source_id,body.label,body.metadata)
@router.delete("/notebooks/{notebook_id}/sources/{source_id}",status_code=204)
def source_delete(notebook_id:int,source_id:int,user:dict=Depends(get_current_user)):run(svc.remove_source,user["id"],notebook_id,source_id);return Response(status_code=204)
@router.post("/notebooks/{notebook_id}/items",status_code=201)
def item_create(notebook_id:int,body:ItemIn,user:dict=Depends(get_current_user)):return run(svc.save_item,user["id"],notebook_id,None,body.item_type,body.title,body.content,body.provenance)
@router.put("/notebooks/{notebook_id}/items/{item_id}")
def item_update(notebook_id:int,item_id:int,body:ItemIn,user:dict=Depends(get_current_user)):return run(svc.save_item,user["id"],notebook_id,item_id,body.item_type,body.title,body.content,body.provenance,body.expected_version)
@router.delete("/notebooks/{notebook_id}/items/{item_id}",status_code=204)
def item_delete(notebook_id:int,item_id:int,user:dict=Depends(get_current_user)):run(svc.delete_item,user["id"],notebook_id,item_id);return Response(status_code=204)
@router.put("/notebooks/{notebook_id}/canvas")
def canvas_save(notebook_id:int,body:CanvasIn,user:dict=Depends(get_current_user)):return run(svc.save_canvas,user["id"],notebook_id,body.state,body.expected_version)
@router.get("/notebooks/{notebook_id}/shares")
def shares_list(notebook_id:int,user:dict=Depends(get_current_user)):return run(svc.list_shares,user["id"],notebook_id)
@router.post("/notebooks/{notebook_id}/shares",status_code=201)
def share_create(notebook_id:int,body:ShareIn,user:dict=Depends(get_current_user)):return run(svc.create_share,user["id"],notebook_id,body.permission,body.expires_in_seconds)
@router.delete("/notebooks/{notebook_id}/shares/{share_id}",status_code=204)
def share_revoke(notebook_id:int,share_id:int,user:dict=Depends(get_current_user)):run(svc.revoke_share,user["id"],notebook_id,share_id);return Response(status_code=204)
@router.get("/shared/notebooks/{token}")
def share_view(token:str):
 if len(token)>200:raise HTTPException(404,"share not found or expired")
 return run(svc.public_share,token)


class GenerateIn(BaseModel):
    source_id: int = Field(..., gt=0)
    output_type: str = Field(..., pattern="^(summary|concepts|definitions|flashcards|quiz|mind_map)$")
    title: Optional[str] = Field(None, max_length=240)
    model_config = {"extra": "forbid"}

@router.post("/notebooks/{notebook_id}/generate", status_code=201)
def generate_notebook_item(notebook_id: int, body: GenerateIn, user: dict = Depends(get_current_user)):
    # Generation is delegated to the existing evidence-grounded lecture/study services. Only linked sources are eligible.
    nb = run(svc.get_notebook, user["id"], notebook_id)
    linked = any(s.get("source_type") == "lecture" and int(s.get("source_id")) == body.source_id for s in nb.get("sources", []))
    if not linked: raise HTTPException(400, "Link this lecture to the notebook before generating from it")
    ai_status = None
    try:
        from server.api.workspace_handlers import ensure_ai_study
        ai_status = ensure_ai_study(user["id"], body.source_id)       # AI study material first (no-op when current)
        if body.output_type == "summary":
            from server.api.workspace_handlers import summary
            content = summary(user["id"], body.source_id)
        elif body.output_type in ("concepts", "definitions"):
            from server.api.study_handlers import knowledge
            content = knowledge(user["id"], body.source_id, "concept" if body.output_type == "concepts" else "definition")
        elif body.output_type == "mind_map":
            # topic and concept nodes with labelled edges from the AI study material; never a map without connections
            from server.api.workspace_handlers import mind_map
            content = mind_map(user["id"], body.source_id)
            if not content.get("nodes") and ai_status and ai_status.get("message"):
                content["message"] = ai_status["message"]
        else:
            from server.api.study_handlers import study_generate, study_items
            kinds = ["flashcard"] if body.output_type == "flashcards" else ["mcq"]
            study_generate(user["id"], body.source_id, kinds)
            content = study_items(user["id"], body.source_id, kind=kinds[0], reveal=True)
    except Exception as exc:
        if isinstance(exc, HTTPException): raise
        if hasattr(exc, "status"): raise HTTPException(exc.status, getattr(exc, "detail", "Generation failed"))
        raise
    if isinstance(content, dict) and ai_status and ai_status.get("status") in ("failed", "no_provider") and content.get("mode") != "ai":
        # say WHY this is verbatim / empty (no credits, rate limit, bad key, ...) instead of implying the lecture had nothing
        content = {**content, "ai_unavailable": ai_status.get("message")}
    title = body.title or (body.output_type.replace("_", " ").title() + " · Lecture " + str(body.source_id))
    ai_used = isinstance(content, dict) and (content.get("mode") == "ai" or (ai_status or {}).get("status") in ("ok", "cached"))
    provenance = [{"source_type":"lecture", "source_id":body.source_id, "output_type":body.output_type,
                   "generated_by": "ai_study" if ai_used else "existing_notiv_grounded_service",
                   **({"ai_status": {k: ai_status[k] for k in ("status", "message", "provider", "model") if k in ai_status}} if ai_status else {})}]
    return run(svc.save_item, user["id"], notebook_id, None, body.output_type, title, content, provenance)

def _filename(title, ext="pdf"):
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", title or "").strip("-._")[:70] or "notiv-notebook"
    return f"{base}.{ext}"

def _pdf_response(data: bytes, name: str) -> Response:
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"',
                    "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

def _render_pdf(fn, *args) -> bytes:
    try:
        from server.api import notebook_pdf
    except ImportError:
        raise HTTPException(501, "PDF export requires reportlab; install it from requirements-prod.txt")
    return getattr(notebook_pdf, fn)(*args)

# Downloads are PDF only: one well-laid-out format instead of eight raw ones.
@router.get("/notebooks/{notebook_id}/items/{item_id}/export")
def export_item(notebook_id: int, item_id: int, format: str = Query("pdf", pattern="^pdf$"), user: dict = Depends(get_current_user)):
    nb = run(svc.get_notebook, user["id"], notebook_id)
    item = next((x for x in nb.get("items", []) if int(x.get("id", 0)) == item_id), None)
    if not item: raise HTTPException(404, "not found")
    return _pdf_response(_render_pdf("item_pdf", item, nb), _filename(item.get("title"), "pdf"))

@router.get("/notebooks/{notebook_id}/export")
def export_notebook(notebook_id: int, format: str = Query("pdf", pattern="^pdf$"), user: dict = Depends(get_current_user)):
    nb = run(svc.get_notebook, user["id"], notebook_id)
    return _pdf_response(_render_pdf("notebook_pdf", nb), _filename(nb.get("title"), "pdf"))
