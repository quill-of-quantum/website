import fcntl, hashlib, json, os, re, secrets, threading, time, uuid
from modules.devices import data_store
from modules.devices.profiles import effective_config, schema_for, schema_revision_for, validate_config

BASE_DIR="/home/bbdwz/projects/website/data/devices"; STORE_PATH=os.path.join(BASE_DIR,"devices.json"); PHOTO_ROOT=os.path.join(BASE_DIR,"photos"); DATA_DB_PATH=os.path.join(BASE_DIR,"device_data.sqlite3"); LEGACY_PHOTO_DB_PATH=os.path.join(BASE_DIR,"photos.sqlite3")

class _InterProcessRLock:
    """Serialize devices.json changes across Gunicorn and the BLE gateway."""
    def __init__(self): self.thread_lock=threading.RLock(); self.local=threading.local()
    def __enter__(self):
        self.thread_lock.acquire(); depth=getattr(self.local,"depth",0)
        if depth==0:
            os.makedirs(BASE_DIR,exist_ok=True); handle=open(os.path.join(BASE_DIR,".devices.lock"),"a+")
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX); self.local.handle=handle
        self.local.depth=depth+1; return self
    def __exit__(self,exc_type,exc,tb):
        depth=self.local.depth-1; self.local.depth=depth
        if depth==0:
            handle=self.local.handle; fcntl.flock(handle.fileno(),fcntl.LOCK_UN); handle.close(); del self.local.handle
        self.thread_lock.release()

LOCK=_InterProcessRLock()

def _configure_data_store(): data_store.configure(BASE_DIR,PHOTO_ROOT,DATA_DB_PATH,LEGACY_PHOTO_DB_PATH)

def _empty(): return {"version":2,"pairing":{"enabled":False,"expires_at":0},"devices":{}}

def _load_unlocked():
    if not os.path.exists(STORE_PATH): return _empty()
    try:
        with open(STORE_PATH,"r",encoding="utf-8") as handle: data=json.load(handle)
        if not isinstance(data,dict) or not isinstance(data.get("devices"),dict): return _empty()
        data["version"]=2; data.setdefault("pairing",{"enabled":False,"expires_at":0})
        # v1 的 disabled/rejected 在 v2 中等价于已删除。
        data["devices"]={key:value for key,value in data["devices"].items() if value.get("status") not in {"disabled","rejected"}}
        migrated=False
        for record in data["devices"].values():
            target_revision=schema_revision_for(record.get("device_type")); current_revision=int(record.get("profile_schema_revision",0))
            if current_revision<target_revision:
                record["config"]=effective_config(record.get("device_type"),record.get("config"))
                if str(record.get("device_type"))=="temperature" and current_revision<2<=target_revision:
                    record["config"]["upload_enabled"]=False; record["config"]["photo_enabled"]=False
                if str(record.get("device_type"))=="temperature" and current_revision<3<=target_revision and record["config"].get("ble_mode") not in {"off","preferred"}:
                    record["config"]["ble_mode"]="off"
                record["config_version"]=int(record.get("config_version",0))+1
                record["config_updated_at"]=int(time.time()); record["profile_schema_revision"]=target_revision; record.pop("config_synced_at",None); migrated=True
            reported=record.get("device_config_version"); server_version=int(record.get("config_version",0))
            if isinstance(reported,(int,float)) and not isinstance(reported,bool) and int(reported)==reported and server_version<int(reported)<=2147483646:
                record["config_version"]=int(reported)+1; record["config_updated_at"]=int(time.time()); record["config_recovery_reason"]="device-version-ahead"; record.pop("config_synced_at",None); migrated=True
        if migrated: _save_unlocked(data)
        try:
            _configure_data_store(); data_store.migrate_legacy(data["devices"])
        except Exception:
            pass
        return data
    except Exception: return _empty()

def _save_unlocked(data):
    os.makedirs(BASE_DIR,exist_ok=True); temporary=STORE_PATH+".tmp"
    with open(temporary,"w",encoding="utf-8") as handle: json.dump(data,handle,ensure_ascii=False,indent=2)
    os.replace(temporary,STORE_PATH)

def _hash_secret(value): return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

def _online(record):
    interval=effective_config(record.get("device_type"),record.get("config")).get("sample_interval_sec",300)
    return bool(record.get("last_seen_at") and time.time()-record["last_seen_at"]<=interval+10)

def _public(record):
    item=dict(record); item.pop("secret_hash",None); item["online"]=_online(record)
    item["config"]=effective_config(record.get("device_type"),record.get("config")); item["config_schema"]=schema_for(record.get("device_type"))
    reported=record.get("device_config_version")
    if reported is None and isinstance(record.get("last_status"),dict): reported=record["last_status"].get("config_version")
    item["device_config_version"]=int(reported) if isinstance(reported,(int,float)) and not isinstance(reported,bool) else None
    acknowledged=record.get("config_ack_version")
    item["config_ack_version"]=int(acknowledged) if isinstance(acknowledged,(int,float)) and not isinstance(acknowledged,bool) else None
    item["config_synced"]=item["config_ack_version"] is not None and item["config_ack_version"]==int(record.get("config_version",0))
    if not item["config_synced"]: item["config_synced_at"]=None
    return item

def list_devices():
    with LOCK: return [_public(record) for record in _load_unlocked()["devices"].values()]

def pairing_status():
    with LOCK: return dict(_load_unlocked().get("pairing") or {"enabled":False,"expires_at":0})

def start_pairing():
    with LOCK:
        data=_load_unlocked(); data["pairing"]={"enabled":True,"expires_at":0}; _save_unlocked(data); return dict(data["pairing"])

def stop_pairing():
    with LOCK:
        data=_load_unlocked(); data["pairing"]={"enabled":False,"expires_at":0}; _save_unlocked(data); return dict(data["pairing"])

def _contact(record,remote_ip,communication,kind,status_payload=None):
    record["last_seen_at"]=int(time.time()); record["last_ip"]=remote_ip or ""; record["communication"]=str(communication or "wifi/cpolar")[:40]; record["last_contact_kind"]=kind
    if record["communication"]=="ble": record["last_ble_at"]=record["last_seen_at"]
    if isinstance(status_payload,dict) and status_payload:
        record["last_status"]=status_payload
        reported=status_payload.get("config_version")
        if isinstance(reported,(int,float)) and not isinstance(reported,bool) and int(reported)==reported and reported>=0:
            reported=int(reported); record["device_config_version"]=reported
            # 当前固件没有单独的 config/ack。只有硬件状态接口表示
            # “本周期确实按这个版本运行”，heartbeat 的自报版本不确认同步。
            if kind=="status":
                record["config_ack_version"]=reported; record["config_ack_source"]="status"
                if reported==int(record.get("config_version",0)):
                    if not record.get("config_synced_at"): record["config_synced_at"]=int(time.time())
                else: record.pop("config_synced_at",None)
        ble=status_payload.get("ble")
        if isinstance(ble,dict): record["ble_status"]={str(key)[:60]:value for key,value in ble.items()}

def register(payload,remote_ip):
    device_id=str(payload.get("device_id") or "").strip()[:80]; secret=str(payload.get("device_secret") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,79}",device_id) or len(secret)<16: return None,"invalid-device-identity"
    with LOCK:
        data=_load_unlocked(); existing=data["devices"].get(device_id)
        if existing:
            if not secrets.compare_digest(existing.get("secret_hash",""),_hash_secret(secret)): return None,"unauthorized"
            _contact(existing,remote_ip,payload.get("communication","wifi/cpolar"),"register")
            existing["firmware"]=str(payload.get("firmware") or existing.get("firmware") or "")[:80]
            if isinstance(payload.get("metadata"),dict): existing["metadata"]=payload["metadata"]
            if isinstance(payload.get("capabilities"),list): existing["capabilities"]=payload["capabilities"]
            _save_unlocked(data)
            if existing.get("status")=="approved": _configure_data_store(); data_store.sync_device(existing)
            return _public(existing),None
        if not (data.get("pairing") or {}).get("enabled"): return None,"pairing-not-open"
        now=int(time.time()); revision=schema_revision_for(payload.get("device_type")); initial_config=effective_config(payload.get("device_type"),{})
        record={
            "id":uuid.uuid4().hex,"device_id":device_id,"name":str(payload.get("name") or device_id)[:120],"device_type":str(payload.get("device_type") or "generic")[:80],
            "firmware":str(payload.get("firmware") or "")[:80],"metadata":payload.get("metadata") if isinstance(payload.get("metadata"),dict) else {},
            "capabilities":payload.get("capabilities") if isinstance(payload.get("capabilities"),list) else [],"status":"pending","created_at":now,"last_seen_at":now,
            "last_ip":remote_ip or "","communication":str(payload.get("communication") or "wifi/cpolar")[:40],"last_contact_kind":"register","config":initial_config,"config_version":1 if revision else 0,"profile_schema_revision":revision,
            "last_telemetry":None,"secret_hash":_hash_secret(secret)}
        data["devices"][device_id]=record; _save_unlocked(data); return _public(record),None

def _authenticate_unlocked(data,device_id,secret):
    record=data["devices"].get(str(device_id))
    if not record: return None,"device-not-found"
    if record.get("status")=="pending": return None,"device-pending"
    if record.get("status")!="approved": return None,"device-not-found"
    if not secrets.compare_digest(record.get("secret_hash",""),_hash_secret(secret)): return None,"unauthorized"
    return record,None

def authenticate_device(device_id,secret):
    with LOCK:
        record,error=_authenticate_unlocked(_load_unlocked(),device_id,secret)
        return (_public(record),None) if record else (None,error)

def verify_ble_proof(device_id,nonce,proof):
    """Authenticate BLE without transmitting the device secret.

    ESP uses HMAC-SHA256(key=SHA256(device_secret),
    message=b"bbdwz-ble-v1:" + ASCII nonce).
    """
    nonce=str(nonce or ""); proof=str(proof or "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}",nonce) or not re.fullmatch(r"[0-9a-f]{64}",proof): return None,"unauthorized"
    with LOCK:
        record=_load_unlocked()["devices"].get(str(device_id))
        if not record or record.get("status")!="approved": return None,"device-not-found"
        try: key=bytes.fromhex(record.get("secret_hash",""))
        except ValueError: return None,"unauthorized"
        expected=__import__("hmac").new(key,b"bbdwz-ble-v1:"+nonce.encode("ascii"),hashlib.sha256).hexdigest()
        return (_public(record),None) if secrets.compare_digest(expected,proof) else (None,"unauthorized")

def contact_trusted(device_id,communication="ble",kind="heartbeat",status_payload=None):
    """Gateway-only equivalent of contact after its session authentication."""
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record or record.get("status")!="approved": return None,"device-not-found"
        _contact(record,"",communication,kind,status_payload); _save_unlocked(data); return _public(record),None

def record_telemetry_trusted(device_id,payload,communication="ble"):
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record or record.get("status")!="approved": return None,"device-not-found"
        _contact(record,"",communication,"telemetry"); record["last_telemetry"]={"received_at":int(time.time()),"data":payload if isinstance(payload,dict) else {}}
        _configure_data_store(); data_store.record_telemetry(record,payload); _save_unlocked(data); return _public(record),None

def record_photo_trusted(device_id,photo_id,payload,photo_info,captured_at,captured_at_source,communication="ble"):
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record or record.get("status")!="approved": return None,False,"device-not-found"
        _configure_data_store(); metadata,duplicate=data_store.record_photo(record,photo_id,payload,photo_info,captured_at,captured_at_source)
        _contact(record,"",communication,"photo_duplicate" if duplicate else "photo"); record["last_photo"]=metadata; _save_unlocked(data)
        return metadata,duplicate,None

def note_ble_session(device_id,address,state,error=None):
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record: return None
        now=int(time.time()); record["ble_address"]=str(address or "")[:80]; record["ble_session_state"]=str(state or "")[:40]; record["ble_session_at"]=now
        if state=="authenticated": record["ble_paired"]=True; record["last_ble_at"]=now; record.pop("ble_last_error",None)
        elif error: record["ble_last_error"]=str(error)[:300]
        _save_unlocked(data); return _public(record)

def ble_gateway_status():
    path=os.path.join(BASE_DIR,"ble_gateway.json")
    try:
        with open(path,"r",encoding="utf-8") as handle: value=json.load(handle)
        return value if isinstance(value,dict) else {"running":False,"scanning":False}
    except (OSError,json.JSONDecodeError): return {"running":False,"scanning":False}

def contact(device_id,secret,remote_ip,communication="wifi/cpolar",kind="heartbeat",status_payload=None):
    with LOCK:
        data=_load_unlocked(); record,error=_authenticate_unlocked(data,device_id,secret)
        if error: return None,error
        _contact(record,remote_ip,communication,kind,status_payload); _save_unlocked(data); return _public(record),None

def record_telemetry(device_id,secret,payload,remote_ip,communication="wifi/cpolar"):
    with LOCK:
        data=_load_unlocked(); record,error=_authenticate_unlocked(data,device_id,secret)
        if error: return None,error
        _contact(record,remote_ip,communication,"telemetry"); record["last_telemetry"]={"received_at":int(time.time()),"data":payload if isinstance(payload,dict) else {}}
        _configure_data_store(); data_store.record_telemetry(record,payload)
        _save_unlocked(data); return _public(record),None

def record_photo(device_id,secret,photo_id,payload,photo_info,captured_at,captured_at_source,remote_ip,communication="wifi/cpolar"):
    with LOCK:
        data=_load_unlocked(); record,error=_authenticate_unlocked(data,device_id,secret)
        if error: return None,False,error
        _configure_data_store(); metadata,duplicate=data_store.record_photo(record,photo_id,payload,photo_info,captured_at,captured_at_source)
        _contact(record,remote_ip,communication,"photo_duplicate" if duplicate else "photo"); record["last_photo"]=metadata; _save_unlocked(data)
        return metadata,duplicate,None

def list_photos(device_id,limit=12):
    with LOCK:
        record=_load_unlocked()["devices"].get(str(device_id))
        if not record: return None
        _configure_data_store(); return data_store.list_photos(record["id"],limit)

def get_photo(device_id,photo_id):
    with LOCK:
        record=_load_unlocked()["devices"].get(str(device_id))
        if not record: return None,None
        _configure_data_store()
        photos=data_store.list_photos(record["id"],100)
        match=next((item for item in photos if item.get("photo_id")==str(photo_id)),None)
        return data_store.get_photo(record["id"],match["id"]) if match else (None,None)

def acknowledge_config(device_id,secret,version,remote_ip,communication="wifi/cpolar"):
    with LOCK:
        data=_load_unlocked(); record,error=_authenticate_unlocked(data,device_id,secret)
        if error: return None,error
        if isinstance(version,bool) or not isinstance(version,(int,float)) or int(version)!=version or version<0: return None,"invalid-config-version"
        version=int(version)
        server_version=int(record.get("config_version",0))
        if version>server_version: return None,"invalid-config-version"
        if version<server_version: return None,"stale-config-version"
        status_version=(record.get("last_status") or {}).get("config_version") if isinstance(record.get("last_status"),dict) else None
        if isinstance(status_version,bool) or not isinstance(status_version,(int,float)) or int(status_version)!=server_version:
            return None,"config-status-required"
        _contact(record,remote_ip,communication,"config_ack"); record["device_config_version"]=version; record["config_ack_version"]=version
        record["config_synced_at"]=int(time.time())
        _save_unlocked(data); return _public(record),None

def approve(device_id):
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record: return None
        record["status"]="approved"; record["status_changed_at"]=int(time.time()); _save_unlocked(data); _configure_data_store(); data_store.sync_device(record); return _public(record)

def delete_device(device_id):
    with LOCK:
        data=_load_unlocked(); record=data["devices"].pop(str(device_id),None)
        if not record: return False
        if record.get("status")=="approved": _configure_data_store(); data_store.mark_device_deleted(record)
        _save_unlocked(data); return True

def update_name(device_id,name):
    name=str(name or "").strip()
    if not name: return None,"name-required"
    if len(name)>120: return None,"name-too-long"
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record: return None,"not-found"
        if record.get("name")!=name:
            record["name"]=name; record["name_updated_at"]=int(time.time()); _save_unlocked(data)
            if record.get("status")=="approved": _configure_data_store(); data_store.sync_device(record)
        return _public(record),None

def update_config(device_id,values):
    with LOCK:
        data=_load_unlocked(); record=data["devices"].get(str(device_id))
        if not record: return None,{"device":"not-found"}
        config,errors=validate_config(record.get("device_type"),values,record.get("config"))
        if errors: return None,errors
        if config!=effective_config(record.get("device_type"),record.get("config")):
            record["config"]=config; record["config_version"]=int(record.get("config_version",0))+1; record["config_updated_at"]=int(time.time()); record.pop("config_synced_at",None); _save_unlocked(data)
        return _public(record),{}

def list_data_packets():
    _configure_data_store(); return data_store.list_packets()

def get_data_packet(packet_id):
    _configure_data_store(); return data_store.packet_detail(packet_id)

def delete_data_category(packet_id,category):
    _configure_data_store(); return data_store.delete_category(packet_id,category)

def get_data_category(packet_id,category,limit=100,offset=0):
    _configure_data_store(); return data_store.category_records(packet_id,category,limit,offset)

def delete_data_packet(packet_id):
    _configure_data_store(); return data_store.delete_packet(packet_id)

def get_data_photo(packet_id,record_id):
    _configure_data_store(); return data_store.get_photo(packet_id,record_id)
