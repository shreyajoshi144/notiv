"""MP3 / MP4 lectures through the same AI study pipeline as PDFs (offline: transcripts are test INPUT and the answer model
is a FAKE — these prove plumbing, grounding and storage, never real transcription or AI quality; see
tools/e2e_media.py for the live check).

Regressions covered:
  * study material from a recording had no source references: every reference was derived from page/slide numbers,
    which transcript evidence of an audio-only lecture does not have -> timestamps derived from the cited evidence
  * AI summary sections of a recording had no start/end, so the lecture page could not show or seek to the moment
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core.lecture import ai_study, knowledge
from core.lecture.align import Segment
from core.lecture.study import StudyService
from core.lecture.testkit import Env, segments
from core.lecture.workspace import WorkspaceService
from core.test_ai_study import FakeChain, eid
from core.test_hardening import make_mp3, make_mp4

SEGS = [Segment(0.5, 4.0, "Today we look at caching in distributed systems."),
        Segment(4.0, 9.0, "A cache stores frequently used data in memory to reduce database load."),
        Segment(9.0, 14.0, "A write-through cache updates the cache and the database at the same time."),
        Segment(14.0, 19.0, "Consistency means all nodes see the same data at the same time.")]


def map_out(user):
    cache, wt, cons = eid(user, "A cache stores"), eid(user, "write-through"), eid(user, "Consistency means")
    return {"sections": [{"title": "Caching", "summary": "Caches keep hot data in memory so the database is hit less often, "
                          "and write-through caches keep both copies in step.", "evidence_ids": [cache, wt]},
                         {"title": "Consistency", "summary": "Consistency is about every node returning the same data.",
                          "evidence_ids": [cons]}],
            "concepts": [{"name": "Cache", "source_definition": "A cache keeps frequently used data in memory to reduce database load.",
                          "definition_evidence_ids": [cache], "explanation": "A fast storage layer.", "mentioned_in": [cache]},
                         {"name": "Write-through cache", "source_definition": "It updates the cache and the database together.",
                          "definition_evidence_ids": [wt], "explanation": "Writes go to both.", "mentioned_in": [wt]},
                         {"name": "Consistency", "source_definition": "All nodes see the same data at the same time.",
                          "definition_evidence_ids": [cons], "explanation": "Reads return the latest write.", "mentioned_in": [cons]}],
            "relations": [{"source": "Write-through cache", "target": "Cache", "label": "is a type of", "evidence_ids": [wt]},
                          {"source": "Write-through cache", "target": "Consistency", "label": "preserves", "evidence_ids": [wt, cons]}],
            "examples": [], "comparisons": []}


def study_out(user):
    cache, wt = eid(user, "A cache stores"), eid(user, "write-through")
    return {"flashcards": [{"question": "Why add a cache?", "answer": "To serve hot data from memory and reduce database load.",
                            "type": "explain", "evidence_ids": [cache]}],
            "quiz": [{"question": "What does a write-through cache do on a write?", "options": [
                "Updates cache and database together", "Skips the database", "Deletes the cache", "Writes only to disk"],
                "correct_index": 0, "explanation": "Both are updated at the same time.", "evidence_ids": [wt]}]}


def synth_out(user):
    import re
    ids = [int(x) for x in re.findall(r"E(\d+)", user)]
    return {"title": "Caching and consistency", "overview": "The talk explains caching and consistency. It shows why caches "
            "reduce load. It describes write-through caches. It defines consistency. It links the two ideas.",
            "key_takeaways": [{"text": "Caches reduce database load.", "evidence_ids": ids[:1]}],
            "topics": [{"name": "Caching", "summary": "Caches and consistency.", "section_titles": ["Caching", "Consistency"],
                        "concepts": ["Cache", "Write-through cache", "Consistency"]}]}


class MediaBase(unittest.TestCase):
    MEDIA = "mp3"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)
        path = os.path.join(self.tmp, "talk." + self.MEDIA)
        (make_mp3 if self.MEDIA == "mp3" else make_mp4)(path, 20)
        self.lid = self.env.lecture(media=path)
        self.env.process(self.lid, segments(*SEGS))
        p = mock.patch.dict(os.environ, {"NOTIV_AI_STUDY": "auto"}); p.start(); self.addCleanup(p.stop)
        r = ai_study.ensure(self.env.repo, self.lid, FakeChain(map_out, study_out, synth_out), force=True, max_wait=0)
        self.assertEqual(r["status"], "ok", r)
        knowledge.regenerate(self.env.repo, self.lid)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMp3Study(MediaBase):
    MEDIA = "mp3"

    def test_every_reference_is_a_real_timestamp(self):
        ai = ai_study.load(self.env.repo, self.lid)
        caching = ai["sections"][0]
        self.assertEqual(caching["pages"], [])                           # an audio lecture has no pages ...
        self.assertEqual(caching["times"], [4.0, 9.0])                   # ... its references are the transcript times
        for group in ("sections", "concepts", "flashcards", "quiz", "takeaways", "topics", "relations"):
            for it in ai[group]:
                self.assertTrue(it["times"], (group, it))
                self.assertTrue(set(it["times"]) <= {s.start for s in SEGS}, (group, it["times"]))

    def test_summary_sections_carry_seekable_moments(self):
        su = WorkspaceService(self.env.repo, self.env.storage).summary(1, self.lid)
        self.assertEqual(su["mode"], "ai")
        s = su["sections"][0]
        self.assertEqual((s["start"], s["end"], s["seek_seconds"]), (4.0, 14.0, 4.0))
        self.assertTrue(all(e["citation"]["seek_seconds"] is not None for e in s["evidence"]))

    def test_concepts_flashcards_quiz_and_map_from_the_transcript(self):
        st = StudyService(self.env.repo, self.env.storage)
        k = st.knowledge(1, self.lid)
        c = {i["title"]: i for i in k["items"] if i["kind"] == "concept"}
        self.assertEqual(c["Write-through cache"]["start"], 9.0)
        self.assertTrue(c["Cache"]["source_supported"])
        st.generate_items(1, self.lid, ["flashcard", "mcq"])
        items = st.list_items(1, self.lid, reveal=True)["items"]
        self.assertEqual(sorted(i["kind"] for i in items), ["flashcard", "mcq"])
        self.assertTrue(all(e["citation"]["seek_seconds"] is not None for i in items for e in i["evidence"]))
        mm = WorkspaceService(self.env.repo, self.env.storage).mind_map(1, self.lid)
        names = {n["id"]: n["text"] for n in mm["nodes"]}
        self.assertIn(("Write-through cache", "is a type of", "Cache"), [(names[e["from"]], e["label"], names[e["to"]]) for e in mm["edges"]])
        self.assertTrue(all(n["source"]["times"] for n in mm["nodes"]))

    def test_other_users_cannot_read_it(self):
        from core.lecture.errors import NotFoundError
        with self.assertRaises(NotFoundError):
            WorkspaceService(self.env.repo, self.env.storage).summary(2, self.lid)


class TestMp4Study(TestMp3Study):
    MEDIA = "mp4"


class TestMediaFailuresAreHonest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recording_without_speech_fails_with_a_clear_message(self):
        path = os.path.join(self.tmp, "silence.mp3")
        make_mp3(path, 5)
        lid = self.env.lecture(media=path)
        with self.assertRaises(Exception) as cm:
            self.env.process(lid, segments())
        self.assertIn("no speech", str(cm.exception))
        lec = self.env.repo.get_lecture(lid)
        self.assertEqual(lec["status"], "failed")
        self.assertIn("no speech", lec["error"])

    def test_unavailable_answer_model_leaves_transcript_study_honest(self):
        path = os.path.join(self.tmp, "talk.mp3")
        make_mp3(path, 20)
        lid = self.env.lecture(media=path)
        self.env.process(lid, segments(*SEGS))
        with mock.patch.dict(os.environ, {"NOTIV_AI_STUDY": "auto"}):
            from core.lecture import llm
            r = ai_study.ensure(self.env.repo, lid, llm.LLMChain([]), force=True)
        self.assertEqual(r["status"], "no_provider")
        su = WorkspaceService(self.env.repo, self.env.storage).summary(1, lid)
        self.assertEqual(su["mode"], "extractive")                       # verbatim fallback, never fabricated prose

    def test_video_whose_audio_ends_before_the_picture_is_not_called_damaged(self):
        """Regression (live run): an MP4 with 45.0 s of picture and 39.4 s of sound failed with "audio extraction is
        truncated ... the file is damaged". Decoded audio must be judged against the AUDIO track, not the container."""
        from core.test_hardening import ff
        path = os.path.join(self.tmp, "screen.mp4")
        ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=20", "-f", "lavfi", "-i",
           "sine=frequency=220:sample_rate=48000:duration=12", "-c:a", "aac", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
        from core.lecture import media
        info = media.probe(path)
        self.assertGreater(info["duration"], 19)
        self.assertLess(info["audio_duration"], 13)
        lid = self.env.lecture(media=path)
        self.env.process(lid, segments(Segment(0.5, 5.0, "The sound stops before the picture does."),
                                       Segment(5.0, 11.5, "That is normal for screen recordings and edited videos.")))
        self.assertEqual(self.env.repo.get_lecture(lid)["status"], "done")
        self.assertEqual(len(self.env.repo.viewer_data(lid)["segments"]), 2)

    def test_unsupported_media_is_rejected_at_upload(self):
        from core.lecture import validation
        from core.lecture.errors import ValidationError
        for name in ("talk.wav", "talk.m4a", "talk.mov", "talk.webm"):
            p = os.path.join(self.tmp, name)
            open(p, "wb").write(b"\x00" * 64)
            with self.assertRaises(ValidationError) as cm:
                validation.validate_file(p, name)
            self.assertIn("PDF, PPTX, MP3, MP4", str(cm.exception))


class TestNotebookSaysWhyAiIsMissing(unittest.TestCase):
    """Regression (live run): with no usable AI provider the notebook saved empty flashcards / quiz / map items that only
    said "No flashcards could be built from this lecture's evidence yet" — implying the recording had nothing in it."""
    def setUp(self):
        from core.lecture import config
        from fastapi.testclient import TestClient
        from server.auth_tokens import create_token
        from server.main import app
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)
        config.configure(self.env.repo, self.env.storage)
        self.addCleanup(config.configure)
        path = os.path.join(self.tmp, "talk.mp3")
        make_mp3(path, 20)
        self.lid = self.env.lecture(user=31, media=path)
        self.env.process(self.lid, segments(*SEGS))
        self.c = TestClient(app, raise_server_exceptions=False)
        os.environ.setdefault("NOTIV_AUTH_SECRET", "media-study-test-" + "k7Qz" * 10)
        self.h = {"Authorization": "Bearer " + create_token(31, os.environ["NOTIV_AUTH_SECRET"])}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saved_items_carry_the_provider_reason(self):
        from core.lecture import config, llm

        class NoCredits(llm.Provider):
            name = "openai"
            def configured(self): return True
            def installed(self): return True
            def complete(self, *a): raise llm.LLMError("quota_exhausted")
        nb = self.c.post("/notebooks", headers=self.h, json={"title": "Revision"}).json()
        self.c.post(f"/notebooks/{nb['id']}/sources", headers=self.h, json={"source_type": "lecture", "source_id": self.lid}).raise_for_status()
        with mock.patch.dict(os.environ, {"NOTIV_AI_STUDY": "auto"}), \
             mock.patch.object(config, "get_llm_chain", return_value=llm.LLMChain([NoCredits("gpt-6-astra")], cooldown=0)):
            for t in ("summary", "flashcards", "mind_map"):
                r = self.c.post(f"/notebooks/{nb['id']}/generate", headers=self.h, json={"source_id": self.lid, "output_type": t})
                self.assertEqual(r.status_code, 201, r.text)
                self.assertIn("no API credits", r.json()["content"]["ai_unavailable"], t)
                self.assertEqual(r.json()["provenance"][0]["ai_status"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
