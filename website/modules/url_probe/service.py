from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import threading
import time
import uuid
from urllib.parse import urljoin

from .client import normalize_url, probe_url


MAX_TARGETS = 50
MAX_WORKERS = 6
JOB_TTL_SECONDS = 3600
_jobs = {}
_jobs_lock = threading.Lock()


def _build_numeric_targets(base_url, rule):
    template = str(rule.get("template", "{n}")).strip() or "{n}"
    if "{n}" not in template:
        raise ValueError("数字规则必须包含 {n}")
    try:
        start = int(rule.get("start", 1))
        end = int(rule.get("end", 10))
        step = int(rule.get("step", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("起始值、结束值和步长必须是整数") from exc
    if step == 0 or (end - start) * step < 0:
        raise ValueError("步长方向与起止范围不一致")

    values = list(range(start, end + (1 if step > 0 else -1), step))
    if len(values) > MAX_TARGETS:
        raise ValueError(f"一次最多探测 {MAX_TARGETS} 个 URL")
    return [urljoin(f"{base_url.rstrip('/')}/", template.replace("{n}", str(n))) for n in values]


def _build_custom_targets(base_url, rule):
    entries = rule.get("entries", [])
    if isinstance(entries, str):
        entries = entries.splitlines()
    entries = [str(item).strip() for item in entries if str(item).strip()]
    if not entries:
        raise ValueError("请至少输入一条自定义规则")
    if len(entries) > MAX_TARGETS:
        raise ValueError(f"一次最多探测 {MAX_TARGETS} 个 URL")

    base = f"{base_url.rstrip('/')}/"
    return [normalize_url(item) if "://" in item else urljoin(base, item.lstrip("/")) for item in entries]


def build_targets(base_url, rule):
    base_url = normalize_url(base_url)
    rule = rule if isinstance(rule, dict) else {}
    mode = rule.get("mode", "numeric")
    if mode == "numeric":
        targets = _build_numeric_targets(base_url, rule)
    elif mode == "custom":
        targets = _build_custom_targets(base_url, rule)
    else:
        raise ValueError("不支持的规则类型")
    return list(dict.fromkeys(targets))


def run_probe(base_url, rule, timeout=5):
    targets = build_targets(base_url, rule)
    timeout = max(1, min(float(timeout), 15))
    results = [None] * len(targets)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(targets))) as executor:
        futures = {executor.submit(probe_url, url, timeout): index for index, url in enumerate(targets)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def _job_payload(job):
    results = [item for item in job["results"] if item is not None]
    return {
        "job_id": job["id"],
        "state": job["state"],
        "total": len(job["targets"]),
        "completed": len(results),
        "exists": sum(1 for item in results if item["exists"]),
        "results": results,
    }


def _cleanup_jobs():
    cutoff = time.time() - JOB_TTL_SECONDS
    for job_id in [key for key, value in _jobs.items() if value["updated_at"] < cutoff]:
        _jobs.pop(job_id, None)


def _run_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return

    executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(job["targets"])))
    pending = {}
    next_index = 0
    try:
        while True:
            with _jobs_lock:
                cancelled = job["cancelled"]
            while not cancelled and next_index < len(job["targets"]) and len(pending) < MAX_WORKERS:
                future = executor.submit(probe_url, job["targets"][next_index], job["timeout"])
                pending[future] = next_index
                next_index += 1

            if not pending:
                break

            done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                result = future.result()
                with _jobs_lock:
                    job["results"][index] = result
                    job["updated_at"] = time.time()

            with _jobs_lock:
                cancelled = job["cancelled"]
            if cancelled:
                for future in pending:
                    future.cancel()
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        with _jobs_lock:
            job["state"] = "cancelled" if job["cancelled"] else "completed"
            job["updated_at"] = time.time()


def start_probe_job(base_url, rule, timeout=5):
    targets = build_targets(base_url, rule)
    timeout = max(1, min(float(timeout), 15))
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "state": "running",
        "cancelled": False,
        "targets": targets,
        "results": [None] * len(targets),
        "timeout": timeout,
        "updated_at": time.time(),
    }
    with _jobs_lock:
        _cleanup_jobs()
        _jobs[job_id] = job
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return _job_payload(job)


def get_probe_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _job_payload(job) if job else None


def cancel_probe_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if job["state"] == "running":
            job["cancelled"] = True
            job["state"] = "cancelled"
            job["updated_at"] = time.time()
        return _job_payload(job)
