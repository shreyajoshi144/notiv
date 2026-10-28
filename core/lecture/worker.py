"""Background worker: polls the DATABASE for runnable jobs (queued/retrying and due), so jobs survive restarts and
any number of workers can share one database (claiming is atomic and stamps a lease owner).

In-process for development (`worker.start()`), or as its own process: `python -m core.lecture.worker`.

Production behaviour (Sprint 5):
  * periodic recovery of jobs whose worker died (stale heartbeat) — retried, or failed after max attempts;
  * worker heartbeat row + queue metrics; periodic maintenance sweep (outbox, stale uploads, retention);
  * SIGTERM/SIGINT: finish the current attempt, then exit (no half-written stage);
  * config validated at start (`settings.validate_or_raise("worker")`) — a mis-configured worker refuses to run.
For multi-host scale replace polling with a broker that merely *wakes* workers; job rows stay the source of truth.
"""
import signal
import threading
import time
from typing import Optional

from . import config, obs, pipeline

log = obs.get_logger("worker")
HEARTBEAT_TIMEOUT = 15 * 60
RECOVER_EVERY = 60.0
MAINTAIN_EVERY = 15 * 60.0
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_wake = threading.Event()


def run_pending(js, max_jobs: Optional[int] = None) -> int:
    """Runs due jobs until none are runnable. Returns attempts executed."""
    n = 0
    while max_jobs is None or n < max_jobs:
        if js.run_next() is None:
            return n
        n += 1
    return n


def _maintain(repo, storage) -> None:
    try:
        from . import maintenance
        maintenance.run_cleanup(repo, storage)
    except Exception as e:                       # noqa: BLE001
        obs.log_event(log, "maintenance_failed", level=40, error=obs.public_error(e))


def _loop(poll: float):
    repo, storage = config.get_repo(), config.get_storage()
    js = pipeline.make_job_service(repo, storage)
    last_recover = last_maint = 0.0
    obs.log_event(log, "worker_started", poll=poll)
    while not _stop.is_set():
        try:
            now = time.time()
            if now - last_recover >= RECOVER_EVERY:       # resume jobs a crashed worker left running
                n = repo.recover_stale_jobs(now - HEARTBEAT_TIMEOUT)
                if n:
                    obs.log_event(log, "jobs_recovered", level=30, count=n)
                    obs.inc("notiv_jobs_recovered_total", n, help="stale jobs recovered")
                try:
                    repo.beat_worker(info={"pid": __import__("os").getpid()})
                    q = repo.queue_stats()
                    obs.gauge("notiv_queue_oldest_runnable_age_seconds", q["oldest_runnable_age_s"], help="age of oldest runnable job")
                    for st, c in q["by_status"].items():
                        obs.gauge("notiv_jobs", c, help="jobs by status", status=st)
                except NotImplementedError:
                    pass
                last_recover = now
            if now - last_maint >= MAINTAIN_EVERY:
                _maintain(repo, storage)
                last_maint = now
            if js.run_next() is None:
                _wake.wait(poll)
                _wake.clear()
        except Exception as e:                    # noqa: BLE001
            obs.log_event(log, "worker_loop_error", level=40, error=obs.public_error(e))
            time.sleep(poll)                      # DB hiccup: back off, keep the worker alive
    obs.log_event(log, "worker_stopped")


def start(poll: float = 2.0) -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(poll,), daemon=True, name="notiv-lecture-worker")
        _thread.start()


def wake() -> None:
    _wake.set()


def stop() -> None:
    _stop.set()
    _wake.set()


def main() -> None:
    from . import settings
    obs.setup_logging()
    settings.validate_or_raise("worker")
    signal.signal(signal.SIGTERM, lambda *_: stop())
    signal.signal(signal.SIGINT, lambda *_: stop())
    _loop(float(settings.env("NOTIV_WORKER_POLL_SECONDS", "2")))


if __name__ == "__main__":
    main()
