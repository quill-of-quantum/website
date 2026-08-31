import json,re,time
from flask import Blueprint,Response,current_app,jsonify,render_template,request,send_file,session
from modules.auth.user_store import is_admin_user,user_exists
from modules.devices import photo_service,storage

bp=Blueprint("devices",__name__)

def _admin_required(): return bool(session.get("logged_in") and user_exists(session.get("user")) and is_admin_user(session.get("user")))
def _device_headers(): return request.headers.get("X-Device-ID","").strip(),request.headers.get("X-Device-Secret","").strip(),request.headers.get("X-Device-Transport","wifi/cpolar").strip()[:40]
def _device_error(error): return jsonify({"ok":False,"error":error}),{"device-not-found":404,"device-pending":409,"unauthorized":401}.get(error,400)

@bp.route("/1/devices")
def devices_page():
    if not _admin_required(): return jsonify({"require_login":True}),403
    return render_template("devices.html",user=session.get("user"))

@bp.route("/1/devices/database")
def database_page():
    if not _admin_required(): return jsonify({"require_login":True}),403
    return render_template("device_database.html",user=session.get("user"))

@bp.route("/1/api/devices")
def admin_list():
    if not _admin_required(): return jsonify({"require_login":True}),403
    return jsonify({"ok":True,"devices":storage.list_devices(),"pairing":storage.pairing_status(),"ble_gateway":storage.ble_gateway_status()})

@bp.route("/1/api/devices/stream")
def admin_stream():
    if not _admin_required(): return jsonify({"require_login":True}),403
    def generate():
        previous=None; last_send=0
        while True:
            payload={"ok":True,"devices":storage.list_devices(),"pairing":storage.pairing_status(),"ble_gateway":storage.ble_gateway_status()}
            serialized=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
            now=time.time()
            if serialized!=previous:
                yield f"data: {serialized}\n\n"; previous=serialized; last_send=now
            elif now-last_send>=15:
                yield ": keep-alive\n\n"; last_send=now
            time.sleep(1)
    return Response(generate(),mimetype="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@bp.route("/1/api/devices/pairing/start",methods=["POST"])
def pairing_start():
    if not _admin_required(): return jsonify({"require_login":True}),403
    return jsonify({"ok":True,"pairing":storage.start_pairing()})

@bp.route("/1/api/devices/pairing/stop",methods=["POST"])
def pairing_stop():
    if not _admin_required(): return jsonify({"require_login":True}),403
    return jsonify({"ok":True,"pairing":storage.stop_pairing()})

@bp.route("/1/api/devices/<device_id>/approve",methods=["POST"])
def approve(device_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    record=storage.approve(device_id)
    return jsonify({"ok":True,"device":record}) if record else (jsonify({"ok":False,"error":"not-found"}),404)

@bp.route("/1/api/devices/<device_id>",methods=["DELETE"])
def delete_device(device_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    if not storage.delete_device(device_id): return jsonify({"ok":False,"error":"not-found"}),404
    return jsonify({"ok":True,"device_id":device_id,"deleted":True})

@bp.route("/1/api/devices/<device_id>/name",methods=["PUT"])
def update_name(device_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    payload=request.get_json(silent=True) or {}; record,error=storage.update_name(device_id,payload.get("name"))
    if error:
        status=404 if error=="not-found" else 422
        return jsonify({"ok":False,"error":error}),status
    return jsonify({"ok":True,"device":record})

@bp.route("/1/api/devices/<device_id>/config",methods=["PUT"])
def config(device_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    payload=request.get_json(silent=True) or {}; record,errors=storage.update_config(device_id,payload.get("values",{}))
    if not record:
        status=404 if errors.get("device")=="not-found" else 422
        return jsonify({"ok":False,"error":"config-validation-error","fields":errors}),status
    return jsonify({"ok":True,"device":record})

@bp.route("/1/api/devices/<device_id>/photos")
def admin_photos(device_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    photos=storage.list_photos(device_id,12)
    if photos is None: return jsonify({"ok":False,"error":"not-found"}),404
    return jsonify({"ok":True,"photos":photos})

@bp.route("/1/api/devices/<device_id>/photos/<photo_id>")
def admin_photo_file(device_id,photo_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    metadata,path=storage.get_photo(device_id,photo_id)
    if not path: return jsonify({"ok":False,"error":"not-found"}),404
    return send_file(path,mimetype="image/jpeg",conditional=True,max_age=86400,download_name=f"{photo_id}.jpg")

@bp.route("/1/api/devices/database/packets")
def database_packets():
    if not _admin_required(): return jsonify({"require_login":True}),403
    return jsonify({"ok":True,"packets":storage.list_data_packets()})

@bp.route("/1/api/devices/database/packets/<packet_id>")
def database_packet(packet_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    packet=storage.get_data_packet(packet_id)
    return jsonify({"ok":True,"packet":packet}) if packet else (jsonify({"ok":False,"error":"not-found"}),404)

@bp.route("/1/api/devices/database/packets/<packet_id>",methods=["DELETE"])
def database_delete_packet(packet_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    if not storage.delete_data_packet(packet_id): return jsonify({"ok":False,"error":"not-found"}),404
    return jsonify({"ok":True,"deleted":True,"packet_id":packet_id})

@bp.route("/1/api/devices/database/packets/<packet_id>/categories/<category>",methods=["DELETE"])
def database_delete_category(packet_id,category):
    if not _admin_required(): return jsonify({"require_login":True}),403
    deleted=storage.delete_data_category(packet_id,category)
    if not deleted: return jsonify({"ok":False,"error":"not-found"}),404
    return jsonify({"ok":True,"deleted":deleted,"packet_id":packet_id,"category":category})

@bp.route("/1/api/devices/database/packets/<packet_id>/categories/<category>")
def database_category(packet_id,category):
    if not _admin_required(): return jsonify({"require_login":True}),403
    try: result=storage.get_data_category(packet_id,category,request.args.get("limit",100),request.args.get("offset",0))
    except (TypeError,ValueError): return jsonify({"ok":False,"error":"invalid-pagination"}),400
    return jsonify({"ok":True,**result})

@bp.route("/1/api/devices/database/packets/<packet_id>/records/<int:record_id>/file")
def database_photo_file(packet_id,record_id):
    if not _admin_required(): return jsonify({"require_login":True}),403
    metadata,path=storage.get_data_photo(packet_id,record_id)
    if not path: return jsonify({"ok":False,"error":"not-found"}),404
    return send_file(path,mimetype="image/jpeg",conditional=True,max_age=86400,download_name=f"{metadata.get('photo_id') or record_id}.jpg")

@bp.route("/api/device/v1/register",methods=["POST"])
def device_register():
    payload=request.get_json(silent=True) or {}; record,error=storage.register(payload,request.remote_addr)
    if error: return jsonify({"ok":False,"error":error}),{"pairing-not-open":403,"unauthorized":401}.get(error,400)
    return jsonify({"ok":True,"status":record["status"],"device_id":record["device_id"],"config":record["config"],"config_version":record["config_version"]})

@bp.route("/api/device/v1/heartbeat",methods=["POST"])
def heartbeat():
    device_id,secret,transport=_device_headers(); payload=request.get_json(silent=True) or {}
    record,error=storage.contact(device_id,secret,request.remote_addr,transport,"heartbeat",payload)
    if error: return _device_error(error)
    return jsonify({"ok":True,"status":"approved","server_time":int(time.time()),"config":record["config"],"config_version":record["config_version"]})

@bp.route("/api/device/v1/status",methods=["POST"])
def device_status():
    if request.content_length and request.content_length>16384: return jsonify({"ok":False,"error":"payload-too-large"}),413
    device_id,secret,transport=_device_headers(); payload=request.get_json(silent=True) or {}
    if not isinstance(payload.get("components",{}),dict): return jsonify({"ok":False,"error":"invalid-payload"}),400
    record,error=storage.contact(device_id,secret,request.remote_addr,transport,"status",payload)
    if error: return _device_error(error)
    return jsonify({"ok":True,"received":True,"server_time":int(time.time()),"config":record["config"],"config_version":record["config_version"],"config_synced":record["config_synced"]})

@bp.route("/api/device/v1/telemetry",methods=["POST"])
def telemetry():
    if request.content_length and request.content_length>65536: return jsonify({"ok":False,"error":"payload-too-large"}),413
    device_id,secret,transport=_device_headers()
    try: record,error=storage.record_telemetry(device_id,secret,request.get_json(silent=True) or {},request.remote_addr,transport)
    except Exception:
        current_app.logger.exception("保存设备遥测失败")
        return jsonify({"ok":False,"error":"server-busy"}),503
    if error: return _device_error(error)
    return jsonify({"ok":True,"received":True,"server_time":int(time.time()),"config":record["config"],"config_version":record["config_version"]})

@bp.route("/api/device/v1/photo",methods=["POST"])
def photo_upload():
    if request.mimetype!="image/jpeg": return jsonify({"ok":False,"error":"unsupported-media-type"}),415
    if request.content_length is not None and request.content_length>photo_service.MAX_PHOTO_BYTES: return jsonify({"ok":False,"error":"payload-too-large"}),413
    device_id,secret,transport=_device_headers(); _,error=storage.authenticate_device(device_id,secret)
    if error:
        if error=="device-pending": return jsonify({"ok":False,"error":error}),403
        return _device_error(error)
    photo_id=request.headers.get("X-Photo-ID","").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}",photo_id): return jsonify({"ok":False,"error":"invalid-photo-id"}),400
    captured,error=photo_service.normalize_captured_at(request.headers.get("X-Captured-At"))
    if error: return jsonify({"ok":False,"error":error}),400
    payload=request.stream.read(photo_service.MAX_PHOTO_BYTES+1)
    photo_info,error=photo_service.inspect_jpeg(payload)
    if error:
        status=413 if error=="payload-too-large" else 422
        return jsonify({"ok":False,"error":error}),status
    try:
        metadata,duplicate,error=storage.record_photo(device_id,secret,photo_id,payload,photo_info,captured[0],captured[1],request.remote_addr,transport)
    except Exception:
        current_app.logger.exception("保存设备照片失败")
        return jsonify({"ok":False,"error":"server-busy"}),503
    if error:
        if error=="device-pending": return jsonify({"ok":False,"error":error}),403
        return _device_error(error)
    record,_=storage.authenticate_device(device_id,secret)
    return jsonify({"ok":True,"photo_id":photo_id,"duplicate":duplicate,"photo":metadata,"config":record["config"],"config_version":record["config_version"]})

@bp.route("/api/device/v1/config")
def device_config():
    device_id,secret,transport=_device_headers(); record,error=storage.contact(device_id,secret,request.remote_addr,transport,"config")
    if error: return _device_error(error)
    return jsonify({"ok":True,"config":record["config"],"config_version":record["config_version"]})

@bp.route("/api/device/v1/config/ack",methods=["POST"])
def device_config_ack():
    device_id,secret,transport=_device_headers(); payload=request.get_json(silent=True) or {}
    record,error=storage.acknowledge_config(device_id,secret,payload.get("config_version"),request.remote_addr,transport)
    if error:
        if error=="invalid-config-version": return jsonify({"ok":False,"error":error}),400
        if error in {"stale-config-version","config-status-required"}: return jsonify({"ok":False,"error":error}),409
        return _device_error(error)
    return jsonify({"ok":True,"config_version":record["config_version"],"config_synced":record["config_synced"],"synced_at":record.get("config_synced_at")})
