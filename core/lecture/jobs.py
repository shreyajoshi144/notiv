"""Durable job model + stage runner.

A Job (row in `jobs`) runs an ordered list of Stages. Each stage's status
and JSON output are persisted in `job_stages`, so:
  * a retry (automatic after a transient failure, or manual) SKIPS stages
    that already completed and resumes at the failed one — a failure in
    INDEX never re-transcribes;
  * a crashed worker's job is recovered (running -> retrying) and resumes.

Job statuses : queued | running | completed | failed | retrying | cancelled
Stage statuses: running | completed | failed | skipped
Failure policy: PermanentError (bad input, missing dependency) fails the job
immediately; any other exception is transient and the job goes to
`retrying` with exponential backoff until max_attempts is exhausted.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from . import obs
from .errors import NotFoundError, PermanentError
from .repo import LectureRepository

log = obs.get_logger("jobs")
LOST = "lost"                          # this worker no longer owns the job (recovered/re-claimed/cancelled elsewhere)

QUEUED, RUNNING, COMPLETED, FAILED, RETRYING, CANCELLED = (
    "queued", "running", "completed", "failed", "retrying", "cancelled")
SKIPPED = "skipped"
TERMINAL = {COMPLETED, FAILED, CANCELLED}


@dataclass
class Stage:
    name: str
    fn: Callable[["StageContext"], Optional[dict]]
    weight: int = 1


@dataclass
class StageContext:
    repo: LectureRepository
    job: dict
    lecture: dict
    services: Any                       # storage, transcriber, ... (pipeline-defined)
    _stage_outputs: dict = field(default_factory=dict)

    def prior(self, stage: str) -> dict:
        """Output persisted by an earlier stage (possibly in an earlier attempt)."""
        st = self.repo.get_stage(self.job["id"], stage)
        return (st or {}).get("output") or {}

    def progress(self, pct: int) -> None:
        self.repo.update_job(self.job["id"], progress=int(pct), heartbeat_at=time.time())

    def skip(self, reason: str) -> dict:
        return {"skipped": reason}


@dataclass
class JobResult:
    job_id: int
    status: str
    exception: Optional[BaseException] = None


class JobService:
    def __init__(self, repo: LectureRepository, stages: List[Stage], make_services: Callable[[], Any],
                 backoff_base: float = 2.0, on_success: Optional[Callable[[StageContext], None]] = None,
                 clock: Callable[[], float] = time.time, job_type: str = "process_lecture",
                 worker_id: Optional[str] = None, heartbeat_interval: float = 30.0):
        self.repo, self.stages, self.make_services = repo, stages, make_services
        self.backoff_base, self.on_success, self.clock, self.job_type = backoff_base, on_success, clock, job_type
        self.worker_id, self.heartbeat_interval = worker_id, heartbeat_interval

    # ── lifecycle ────────────────────────────────────────────────────────
    def enqueue(self, lecture_id: int, max_attempts: int = 3) -> int:
        lec = self.repo.get_lecture(lecture_id)
        if not lec:
            raise NotFoundError("not found")
        jid, created = self.repo.create_job_once(lecture_id, lec["workspace_id"], self.job_type, max_attempts)
        if created:                      # idempotent: a second enqueue while one is active returns the SAME job
            self.repo.set_status(lecture_id, "queued", stage="Queued", progress_pct=0)
            obs.inc("notiv_jobs_enqueued_total", help="jobs enqueued", type=self.job_type)
        return jid

    def cancel(self, job_id: int) -> bool:
        job = self.repo.get_job(job_id)
        if not job or job["status"] in TERMINAL:
            return False
        self.repo.update_job(job_id, status=CANCELLED, finished_at=self.clock())
        if job["status"] != RUNNING:       # a running job notices between stages
            self.repo.set_status(job["lecture_id"], "failed", stage="Cancelled", error="cancelled")
        return True

    def retry(self, job_id: int) -> bool:
        """Manually retry a failed/cancelled job: grants one more attempt and
        resumes from the first incomplete stage."""
        job = self.repo.get_job(job_id)
        if not job or job["status"] not in (FAILED, CANCELLED):
            return False
        try:
            self.repo.update_job(job_id, status=RETRYING, max_attempts=job["attempt"] + 1, run_after=0,
                                 error=None, error_class=None, finished_at=None)
        except Exception as e:           # noqa: BLE001 — unique active-job index: another job is already active
            if type(e).__name__ in ("IntegrityError", "UniqueViolation"):
                return False
            raise
        self.repo.set_status(job["lecture_id"], "queued", stage="Queued", progress_pct=job["progress"])
        return True

    def run_next(self) -> Optional[JobResult]:
        """Claims one runnable job and runs one attempt of it."""
        job = self.repo.claim_next_job(self.clock(), worker_id=self.worker_id)
        return self._run_attempt(job) if job else None

    def run_job(self, job_id: int) -> Optional[JobResult]:
        job = self.repo.claim_next_job(self.clock(), job_id=job_id, worker_id=self.worker_id)
        return self._run_attempt(job) if job else None

    # ── one attempt ──────────────────────────────────────────────────────
    def _owned(self, job: dict) -> bool:
        try:
            return self.repo.job_owned_by(job["id"], job.get("worker_id"))
        except NotImplementedError:                 # repository without leases: single-worker semantics
            return (self.repo.get_job(job["id"]) or {}).get("status") == RUNNING

    def _heartbeat(self, job: dict):
        """Keeps the lease alive while a (possibly very long) stage runs; stops writing once ownership is lost."""
        stop = threading.Event()
        jid = job["id"]

        def beat():
            while not stop.wait(self.heartbeat_interval):
                try:
                    if self._owned(job):
                        self.repo.update_job(jid, heartbeat_at=self.clock())
                        try:
                            self.repo.beat_worker(job.get("worker_id"))
                        except NotImplementedError:
                            pass
                except Exception:                    # noqa: BLE001 — a DB blip must not kill the stage
                    pass
        t = threading.Thread(target=beat, daemon=True, name=f"notiv-hb-{jid}")
        t.start()
        return stop, t

    def _commit(self, job: dict, jobf: Optional[dict] = None, lecture: Optional[dict] = None,
                stage: Optional[tuple] = None) -> bool:
        """The ONLY way an attempt writes its outcome. One transaction: verify the lease is still ours AND write
        (fencing). False => lease lost, nothing was written. (A separate _owned() check followed by writes is a
        check-then-act race: the job can be recovered/re-claimed between the check and the write.)"""
        try:
            return self.repo.finalize_owned(job["id"], job.get("worker_id"), jobf, lecture, stage)
        except NotImplementedError:                 # repository without leases: single-worker semantics, unfenced
            if not self._owned(job):
                return False
            if stage:
                self.repo.set_stage(job["id"], *stage[:2], error=stage[2], output=stage[3])
            if jobf:
                self.repo.update_job(job["id"], **jobf)
            if lecture:
                self.repo.set_status(job["lecture_id"], lecture["status"], stage=lecture.get("stage"),
                                     progress_pct=lecture.get("progress_pct"), error=lecture.get("error"))
            return True

    def _lost(self, job: dict) -> JobResult:
        obs.inc("notiv_jobs_lost_total", help="attempts abandoned because the lease was lost")
        obs.log_event(log, "job_lease_lost", job_id=job["id"], lecture_id=job["lecture_id"])
        return JobResult(job["id"], LOST)

    def _run_attempt(self, job: dict) -> JobResult:
        jid, lid = job["id"], job["lecture_id"]
        t0 = time.perf_counter()
        lecture = self.repo.get_lecture(lid)
        ctx = StageContext(self.repo, job, lecture, self.make_services())
        total = sum(s.weight for s in self.stages)
        done = 0
        obs.log_event(log, "job_attempt_start", job_id=jid, lecture_id=lid, attempt=job["attempt"], worker=job.get("worker_id"))
        self.repo.set_status(lid, "running", stage="Running", progress_pct=job["progress"])
        for st in self.stages:
            status = (self.repo.get_job(jid) or {}).get("status")
            if status == CANCELLED:
                self.repo.set_status(lid, "failed", stage="Cancelled", error="cancelled")
                return JobResult(jid, CANCELLED)
            if not self._owned(job):
                return self._lost(job)
            prev = self.repo.get_stage(jid, st.name)
            if prev and prev["status"] in ("completed", SKIPPED):
                done += st.weight
                continue
            self.repo.set_stage(jid, st.name, "running")
            self.repo.set_status(lid, "running", stage=st.name, progress_pct=int(100 * done / total))
            self.repo.update_job(jid, heartbeat_at=self.clock())
            stop, hb = self._heartbeat(job)
            st_t = time.perf_counter()
            try:
                out = st.fn(ctx) or {}
            except BaseException as e:                      # noqa: BLE001 — recorded, then policy applied
                if not isinstance(e, Exception):
                    raise
                if not self._owned(job):                    # someone else owns the job now: do not write over them
                    return self._lost(job)
                return self._fail(job, st.name, e)
            finally:
                stop.set()
                obs.observe("notiv_job_stage_seconds", time.perf_counter() - st_t, help="stage duration", stage=st.name)
            if (self.repo.get_job(jid) or {}).get("status") == CANCELLED:      # cancelled while the stage ran
                self.repo.set_status(lid, "failed", stage="Cancelled", error="cancelled")
                return JobResult(jid, CANCELLED)
            done += st.weight
            # FENCED stage result: the lease check and the write are one transaction (no check-then-write window).
            if self.repo.set_stage(jid, st.name, SKIPPED if "skipped" in out else "completed", output=out,
                                   owner=job.get("worker_id") or "") is False:      # `is False`: legacy/wrapped repos may return None
                return self._lost(job)
            self.repo.update_job(jid, progress=int(100 * done / total), heartbeat_at=self.clock())

        if not self._commit(job, {"status": COMPLETED, "progress": 100, "finished_at": self.clock(), "error": None, "error_class": None},
                            {"status": "done", "stage": "Done", "progress_pct": 100}):
            return self._lost(job)                           # lost the lease at the last instant: the new owner finalizes
        if self.on_success:                                   # only the lease holder may clean up (frames are shared per lecture)
            try:
                self.on_success(ctx)
            except Exception as e:                           # noqa: BLE001 — cleanup must never fail a finished job
                obs.log_event(log, "job_cleanup_failed", level=30, job_id=jid, error=obs.public_error(e))
        obs.inc("notiv_jobs_finished_total", help="job attempts by outcome", outcome="completed")
        obs.observe("notiv_job_seconds", time.perf_counter() - t0, help="job attempt duration")
        obs.log_event(log, "job_completed", job_id=jid, lecture_id=lid, seconds=round(time.perf_counter() - t0, 2))
        return JobResult(jid, COMPLETED)

    def _fail(self, job: dict, stage: str, e: Exception) -> JobResult:
        jid, lid = job["id"], job["lecture_id"]
        # full detail goes to the (redacted) log; only a sanitised message is persisted/shown to users
        pub = obs.public_error(e)
        cls = type(e).__name__
        obs.log_event(log, "job_stage_failed", level=40, job_id=jid, lecture_id=lid, stage=stage, attempt=job["attempt"],
                      error_class=cls, error=str(e))
        permanent = isinstance(e, PermanentError)
        if not permanent and job["attempt"] < job["max_attempts"]:
            delay = self.backoff_base * (2 ** (job["attempt"] - 1))
            if not self._commit(job, {"status": RETRYING, "error": f"[{stage}] {pub}", "error_class": cls, "run_after": self.clock() + delay},
                                {"status": "running", "stage": f"Retrying after {stage} failure"}, (stage, "failed", pub, None)):
                return self._lost(job)
            obs.inc("notiv_jobs_finished_total", help="job attempts by outcome", outcome="retrying")
            return JobResult(jid, RETRYING, e)
        if not self._commit(job, {"status": FAILED, "error": f"[{stage}] {pub}", "error_class": cls, "finished_at": self.clock()},
                            {"status": "failed", "stage": "Failed", "error": pub}, (stage, "failed", pub, None)):
            return self._lost(job)
        obs.inc("notiv_jobs_finished_total", help="job attempts by outcome", outcome="failed")
        return JobResult(jid, FAILED, e)
