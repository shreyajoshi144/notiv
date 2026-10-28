"""Course memory: knowledge from SEVERAL lectures of ONE course, searchable and reviewable together.

Isolation rules (all enforced through StudyService.course_lectures, then re-applied per lecture):
  * the caller must be a member of the course's workspace (authz.get_course) — missing and not-yours are both 404
  * only PROCESSED lectures whose course_id is exactly this course AND whose workspace is the course's workspace
  * every query below is parameterised by that id list; no lecture outside it can appear in any result
  * study progress/sessions stay private to the user even when the course is shared
Concepts are merged across lectures by normalised key (stemmed title), so "Learning rate" taught in lecture 1 and
revisited in lecture 3 shows up as one concept with both lectures' definitions and evidence.
"""
from typing import Dict, List, Optional

from . import knowledge
from .errors import NotFoundError, ValidationError
from .repo import LectureRepository
from .storage import StorageService
from .study import StudyService

MAX_QUERY = 200


class CourseMemory:
    def __init__(self, repo: LectureRepository, storage: StorageService):
        self.repo, self.storage = repo, storage
        self.study = StudyService(repo, storage)
        self.ws = self.study.ws

    def _course(self, user_id: int, course_id: int):
        course, ids = self.study.course_lectures(user_id, course_id)
        titles = {i: (self.repo.get_lecture(i) or {}).get("title") for i in ids}
        for i in ids:
            knowledge.ensure(self.repo, i)
        return course, ids, titles

    def _tag(self, item: dict, titles: Dict[int, str]) -> dict:
        return {**item, "lecture_title": titles.get(item["lecture_id"])}

    def overview(self, user_id: int, course_id: int) -> dict:
        course, ids, titles = self._course(user_id, course_id)
        return {"course_id": course_id, "name": course["name"],
                "lectures": [{"lecture_id": i, "title": titles[i],
                              "knowledge_items": len(self.repo.list_knowledge([i])),
                              "study_items": len(self.repo.list_study_items([i])),
                              "stale": knowledge.is_stale(self.repo, i)} for i in ids]}

    def knowledge(self, user_id: int, course_id: int, kind: Optional[str] = None, lecture_id: Optional[int] = None,
                  q: Optional[str] = None) -> dict:
        _, ids, titles = self._course(user_id, course_id)
        if kind is not None and kind not in knowledge.KINDS:
            raise ValidationError(f"kind must be one of {', '.join(knowledge.KINDS)}")
        if lecture_id is not None:
            if lecture_id not in ids:
                raise NotFoundError("not found")                              # a lecture of another course is not addressable here
            ids = [lecture_id]
        rows = self.repo.search_knowledge(ids, q, 50) if q and q.strip() else self.repo.list_knowledge(ids, kind)
        if q and kind:
            rows = [r for r in rows if r["kind"] == kind]
        rev = self.repo.reviewed_map(user_id, [r["id"] for r in rows])
        return {"course_id": course_id, "count": len(rows),
                "items": [self._tag(self.study.present_knowledge(r, reviewed=rev.get(r["id"])), titles) for r in rows]}

    def concepts(self, user_id: int, course_id: int, shared_only: bool = False) -> dict:
        """Concepts merged across the course's lectures (by normalised key)."""
        _, ids, titles = self._course(user_id, course_id)
        rows = self.repo.list_knowledge(ids)
        rev = self.repo.reviewed_map(user_id, [r["id"] for r in rows])
        groups: Dict[str, List[dict]] = {}
        for r in rows:
            if r["concept_key"]:
                groups.setdefault(r["concept_key"], []).append(r)
        out = []
        for key, rs in groups.items():
            concept_rows = [r for r in rs if r["kind"] == "concept"]
            if not concept_rows:
                continue
            lectures = sorted({r["lecture_id"] for r in concept_rows})
            if shared_only and len(lectures) < 2:
                continue
            pres = lambda r: self._tag(self.study.present_knowledge(r, reviewed=rev.get(r["id"])), titles)     # noqa: E731
            out.append({"concept_key": key, "title": concept_rows[0]["title"], "lectures_count": len(lectures),
                        "lectures": [{"lecture_id": l, "lecture_title": titles[l],
                                      "knowledge_id": next(r["id"] for r in concept_rows if r["lecture_id"] == l),
                                      "slide_index": next(r["slide_index"] for r in concept_rows if r["lecture_id"] == l)} for l in lectures],
                        "definitions": [pres(r) for r in rs if r["kind"] == "definition"],
                        "formulas": [pres(r) for r in rs if r["kind"] == "formula"],
                        "examples": [pres(r) for r in rs if r["kind"] == "example"],
                        "warnings": [pres(r) for r in rs if r["kind"] == "warning"]})
        out.sort(key=lambda c: (-c["lectures_count"], c["title"].lower()))
        return {"course_id": course_id, "count": len(out), "concepts": out}

    def search(self, user_id: int, course_id: int, q: str, limit: int = 20) -> dict:
        _, ids, titles = self._course(user_id, course_id)
        q = (q or "").strip()
        if not q:
            raise ValidationError("query required")
        if len(q) > MAX_QUERY:
            raise ValidationError(f"query too long (max {MAX_QUERY} characters)")
        limit = min(max(int(limit), 1), 50)
        kn = self.repo.search_knowledge(ids, q, limit)
        rev = self.repo.reviewed_map(user_id, [r["id"] for r in kn])
        match = "all"
        hits = self.repo.search_evidence(ids, q, limit=limit, match="all")
        if not hits and len(q.split()) > 1:
            hits, match = self.repo.search_evidence(ids, q, limit=limit, match="any"), "any"
        by_lec: Dict[int, List[int]] = {}
        for h in hits:
            if h["lecture_id"] in ids:                                        # belt and braces: never surface an out-of-course row
                by_lec.setdefault(h["lecture_id"], []).append(h["evidence_id"])
        presented: Dict[int, dict] = {}
        for lid, eids in by_lec.items():
            for it in self.ws.present_evidence(lid, eids):
                presented[it["evidence_id"]] = it
        evidence = [self._tag(presented[h["evidence_id"]], titles) for h in hits if h["evidence_id"] in presented]
        return {"course_id": course_id, "query": q, "match": match, "lectures_searched": [{"lecture_id": i, "title": titles[i]} for i in ids],
                "knowledge": [self._tag(self.study.present_knowledge(r, reviewed=rev.get(r["id"])), titles) for r in kn], "evidence": evidence}

    def study_items(self, user_id: int, course_id: int, kind: Optional[str] = None, difficulty: Optional[str] = None,
                    reveal: bool = False) -> dict:
        _, ids, titles = self._course(user_id, course_id)
        self.study._ensure_items(ids)
        items = self.repo.list_study_items(ids, kind, difficulty)
        return {"course_id": course_id, "count": len(items),
                "items": [self._tag(self.study.present_item(i, reveal), titles) for i in items]}
