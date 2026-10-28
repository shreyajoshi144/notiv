from fastapi import APIRouter, Depends, HTTPException, status

from server.api.deps import get_current_user
from server.schemas.chat import ChatRequest, ChatResponse, SearchResponse, SearchHit, SourceChunk
from server.services import meeting_service, rag_service

router = APIRouter(tags=["chat"])


def _owned_meeting(meeting_id: int, user: dict) -> dict:
    try:
        return meeting_service.get_meeting(meeting_id, user["id"])
    except meeting_service.NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except meeting_service.ForbiddenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, user: dict = Depends(get_current_user)):
    meeting = _owned_meeting(body.meeting_id, user)
    history = [turn.model_dump() for turn in body.history]
    try:
        result = rag_service.chat(meeting, body.question, history=history)
    except rag_service.MeetingNotReadyError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return ChatResponse(
        meeting_id=body.meeting_id,
        question=body.question,
        answer=result["answer"],
        sources=[SourceChunk(**s) for s in result["sources"]],
    )


@router.get("/meetings/{meeting_id}/search", response_model=SearchResponse)
def search(meeting_id: int, q: str, k: int = 5, user: dict = Depends(get_current_user)):
    meeting = _owned_meeting(meeting_id, user)
    try:
        hits = rag_service.search(meeting, q, k=k)
    except rag_service.MeetingNotReadyError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return SearchResponse(
        meeting_id=meeting_id,
        query=q,
        results=[SearchHit(**h) for h in hits],
    )
