from flask import Blueprint, render_template, jsonify, request

bp = Blueprint("aurora", __name__, url_prefix="/aurora")


@bp.route("/")
def aurora_page():
    return render_template("aurora.html")


@bp.route("/api/status")
def aurora_status():
    return jsonify({
        "status": "placeholder",
        "message": "Aurora API integration is not wired yet."
    })


@bp.route("/api/social")
def aurora_social():
    query = request.args.get("q", "aurora")
    return jsonify({
        "status": "placeholder",
        "query": query,
        "message": "Social search integration is not wired yet."
    })
