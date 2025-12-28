from flask import Blueprint, render_template, request, jsonify

bp = Blueprint("letter", __name__)

@bp.route("/letter")
def letter_ui():
    """字母游戏页面"""
    return render_template("letter.html")

@bp.route("/api/letter/your-endpoint", methods=["POST"])
def your_api_function():
    """你的API端点"""
    try:
        data = request.get_json()
        # 你的业务逻辑
        
        return jsonify({"status": "success", "result": "your_result"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
