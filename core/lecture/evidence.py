"""Evidence service. Evidence is the citable unit: a transcript segment (with
timestamps, slide and source recording) or a slide's text (with the source
deck). Every future AI artifact (summary, answer, note, key moment, study
item) must reference evidence ids rather than free text."""
from typing import List, Optional

from . import authz
from .repo import LectureRepository


class EvidenceService:
    def __init__(self, repo: LectureRepository):
        self.repo = repo

    @staticmethod
    def build_records(slides: List[dict], slide_ids: dict, aligned: List[dict], segment_ids: List[int],
                      media_asset_id: Optional[int], deck_asset_id: Optional[int]) -> List[dict]:
        """Pure function: slides + aligned transcript -> evidence records."""
        recs = []
        for s in slides:
            if s["text"].strip():
                recs.append(dict(source_type="slide", source_id=s["index"], text=s["text"].strip(),
                                 slide_index=s["index"], slide_id=slide_ids.get(s["index"]),
                                 source_asset_id=deck_asset_id,
                                 # OCR'd text is recognised, not extracted: carry Tesseract's confidence (0-1)
                                 **({"confidence": round(s["ocr_confidence"] / 100, 3)}
                                    if s.get("text_source") == "ocr" and s.get("ocr_confidence") is not None else {})))
        for g, sid in zip(aligned, segment_ids):
            if g["text"].strip():
                recs.append(dict(source_type="transcript", source_id=sid, text=g["text"], start=g["start"],
                                 end=g["end"], slide_index=g["slide_index"],
                                 slide_id=slide_ids.get(g["slide_index"]) if g["slide_index"] is not None else None,
                                 source_asset_id=media_asset_id, confidence=g.get("confidence")))
        return recs

    def replace_for_lecture(self, lecture_id, slides, slide_ids, aligned, segment_ids, media_asset_id, deck_asset_id):
        return self.repo.replace_evidence(
            lecture_id, self.build_records(slides, slide_ids, aligned, segment_ids, media_asset_id, deck_asset_id))

    def create(self, user_id: int, lecture_id: int, source_type: str, source_id: int, text: str, **kw) -> int:
        """For derived artifacts created later (notes, key moments...). Indexed immediately."""
        authz.get_lecture(self.repo, user_id, lecture_id)
        eid = self.repo.add_evidence(lecture_id, source_type, source_id, text, **kw)
        self.repo.index_evidence(eid)
        return eid

    def get(self, user_id: int, evidence_id: int) -> dict:
        return authz.get_evidence(self.repo, user_id, evidence_id)

    def list(self, user_id: int, lecture_id: int) -> List[dict]:
        authz.get_lecture(self.repo, user_id, lecture_id)
        return self.repo.list_evidence(lecture_id)

    def resolve(self, user_id: int, evidence_id: int) -> dict:
        """Everything a client needs to jump to the evidence: the record, its
        source asset (storage key, type), the seek time and the slide."""
        ev = self.get(user_id, evidence_id)
        asset = self.repo.get_asset(ev["source_asset_id"]) if ev["source_asset_id"] else None
        return {"evidence": ev,
                "asset": None if not asset else {k: asset[k] for k in ("id", "type", "storage_key", "original_filename")},
                "seek_seconds": ev["start"], "slide_index": ev["slide_index"], "slide_id": ev["slide_id"]}
