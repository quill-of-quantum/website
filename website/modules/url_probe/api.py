import os

from flask import Blueprint, Response, jsonify, render_template, request

from .service import cancel_probe_job, get_probe_job, start_probe_job
from .subdomain import cancel_discovery, get_discovery, start_discovery


bp = Blueprint("url_probe", __name__)
MAX_SPEED_TEST_BYTES = 10 * 1024 * 1024


@bp.get("/url-probe")
def page():
    return render_template("url_probe.html")


@bp.get("/api/url-probe/speed/ping")
def speed_ping():
    return jsonify({"success": True})


@bp.get("/api/url-probe/speed/download")
def speed_download():
    try:
        size = int(request.args.get("bytes", 5 * 1024 * 1024))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "测试数据大小无效"}), 400
    if not 1 <= size <= MAX_SPEED_TEST_BYTES:
        return jsonify({"success": False, "error": "测试数据大小必须在 1 B 到 10 MiB 之间"}), 400
    response = Response(os.urandom(size), mimetype="application/octet-stream")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Length"] = str(size)
    return response


@bp.post("/api/url-probe/speed/upload")
def speed_upload():
    content_length = request.content_length
    if content_length is not None and content_length > MAX_SPEED_TEST_BYTES:
        return jsonify({"success": False, "error": "上传测试数据不能超过 10 MiB"}), 413
    body = request.get_data(cache=False)
    if len(body) > MAX_SPEED_TEST_BYTES:
        return jsonify({"success": False, "error": "上传测试数据不能超过 10 MiB"}), 413
    response = jsonify({"success": True, "data": {"received_bytes": len(body)}})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/url-probe/run")
def run():
    payload = request.get_json(silent=True) or {}
    try:
        job = start_probe_job(payload.get("base_url"), payload.get("rule"), payload.get("timeout", 5))
    except (TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    return jsonify({"success": True, "data": job}), 202


@bp.get("/api/url-probe/jobs/<job_id>")
def status(job_id):
    job = get_probe_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "探测任务不存在或已过期"}), 404
    return jsonify({"success": True, "data": job})


@bp.post("/api/url-probe/jobs/<job_id>/cancel")
def cancel(job_id):
    job = cancel_probe_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "探测任务不存在或已过期"}), 404
    return jsonify({"success": True, "data": job})


@bp.post("/api/url-probe/subdomains/run")
def subdomain_run():
    payload = request.get_json(silent=True) or {}
    try:
        job = start_discovery(payload.get("domain"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "data": job}), 202


@bp.get("/api/url-probe/subdomains/jobs/<job_id>")
def subdomain_status(job_id):
    job = get_discovery(job_id)
    if not job:
        return jsonify({"success": False, "error": "发现任务不存在或已过期"}), 404
    return jsonify({"success": True, "data": job})


@bp.post("/api/url-probe/subdomains/jobs/<job_id>/cancel")
def subdomain_cancel(job_id):
    job = cancel_discovery(job_id)
    if not job:
        return jsonify({"success": False, "error": "发现任务不存在或已过期"}), 404
    return jsonify({"success": True, "data": job})
