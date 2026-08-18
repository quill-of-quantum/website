import html
import json
from datetime import datetime

from modules.housing.db import list_rooms
from modules.housing.store import DATA_DIR


RESULT_PATH = DATA_DIR / "房源地图.html"
TYPE_STYLE = {
    "wg": {"label": "WG", "color": "#f59e0b"},
    "einzelzimmer": {"label": "Einzelzimmer", "color": "#2563eb"},
    "xzimmer": {"label": "xZimmer", "color": "#16a34a"},
    "unknown": {"label": "未分类", "color": "#6b7280"},
}
DISPLAY_COLUMNS = [
    "房源状态", "记录变化", "Eintrag vom", "房源分类", "地址", "房型/面积", "可入住时间",
    "冷租", "杂费", "暖租", "取暖费包含", "电费包含", "水费包含", "Wi-Fi费用包含",
    "网络接口", "嵌入式厨房", "灶台", "冰箱", "洗碗机", "洗衣机", "烘干机",
    "接受男性", "接受女性", "接受情侣", "接受非吸烟者", "家具", "房东姓名",
    "房东电话", "房东手机", "房东邮箱", "备注", "链接",
]


def _escape(value):
    return html.escape(str(value if value not in (None, "") else "?"))


def _cell(name, value):
    value = str(value if value not in (None, "") else "?")
    css = " flag-no" if value == "x" else " flag-unknown" if value == "?" else ""
    if name == "链接" and value.startswith(("http://", "https://")):
        return f'<td class="col-link"><a href="{html.escape(value, quote=True)}" target="_blank" rel="noopener">{_escape(value)}</a></td>'
    if name == "房东邮箱" and "@" in value:
        return f'<td class="col-email"><a href="mailto:{html.escape(value, quote=True)}">{_escape(value)}</a></td>'
    column_class = {
        "地址": "col-address", "房型/面积": "col-room", "备注": "col-notes",
        "房东邮箱": "col-email", "房东电话": "col-contact", "房东手机": "col-contact",
    }.get(name, "col-compact")
    return f'<td class="{column_class}{css}">{_escape(value)}</td>'


def generate_result_html():
    rooms = list_rooms()
    def entry_date(room):
        try:
            detail = json.loads(room.get("detail_json") or "{}")
            return datetime.strptime(str(detail.get("Eintrag vom") or ""), "%d.%m.%Y")
        except (ValueError, TypeError, json.JSONDecodeError):
            return datetime.min

    # 唯一主排序规则：Eintrag vom 从新到旧；同日用房源 ID 稳定排序。
    rooms.sort(key=lambda room: (entry_date(room), str(room.get("id") or "")), reverse=True)
    table_rows, markers = [], []
    categories = set()
    for room in rooms:
        try:
            detail = json.loads(room.get("detail_json") or "{}")
        except json.JSONDecodeError:
            detail = {}
        rental_type = room.get("rental_type") or "unknown"
        style = TYPE_STYLE.get(rental_type, TYPE_STYLE["unknown"])
        categories.add(rental_type)
        status = "在架" if room.get("status") == "active" else "已下架"
        record_change = room.get("record_change") or "未变化（复用）"
        values = {
            **detail, "房源状态": status, "记录变化": record_change,
            "房源分类": style["label"], "地址": room.get("address") or detail.get("地址"),
            "房型/面积": room.get("room_type_text") or detail.get("房型/面积"), "链接": room.get("url"),
        }
        cells = "".join(_cell(name, values.get(name, "?")) for name in DISPLAY_COLUMNS)
        row_class = "is-delisted" if status == "已下架" else ""
        table_rows.append(
            f'<tr class="{row_class}" data-room-id="{_escape(room["id"])}" data-type="{_escape(rental_type)}" '
            f'data-status="{_escape(status)}" data-change="{_escape(record_change)}">{cells}</tr>'
        )
        if room.get("latitude") is not None and room.get("longitude") is not None:
            markers.append({
                "id": room["id"], "lat": room["latitude"], "lon": room["longitude"],
                "type": rental_type, "color": style["color"], "label": style["label"],
                "status": status, "change": record_change, "address": values.get("地址", "?"),
                "rent": values.get("暖租", "?"), "room": values.get("房型/面积", "?"), "url": room["url"],
            })
    headers = "".join(f"<th>{html.escape(name)}</th>" for name in DISPLAY_COLUMNS)
    type_options = "".join(
        f'<option value="{key}">{TYPE_STYLE.get(key, TYPE_STYLE["unknown"])["label"]}</option>'
        for key in ("wg", "einzelzimmer", "xzimmer", "unknown") if key in categories
    )
    legend = "".join(
        f'<span><i style="background:{style["color"]}"></i>{style["label"]}</span>'
        for key, style in TYPE_STYLE.items() if key in categories
    )
    document = f'''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SW-KA 房源地图与完整表</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body{{margin:0;height:100%;font-family:system-ui,-apple-system,sans-serif;color:#1f2937}}body{{display:flex;flex-direction:column}}#map{{height:52vh;min-height:360px;flex:none}}.legend{{position:absolute;z-index:700;left:48px;top:calc(52vh - 48px);background:#fff;padding:8px 12px;border:1px solid #bbb;border-radius:5px;box-shadow:0 1px 4px #0002}}.legend span{{margin-right:14px;white-space:nowrap}}.legend i{{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:4px}}
#panel{{padding:16px 20px 24px;background:#fff}}.heading{{display:flex;justify-content:space-between;align-items:end;gap:16px}}h1{{font-size:22px;margin:0 0 3px}}.muted{{color:#64748b;font-size:13px}}.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 8px}}.filters input,.filters select,.filters button{{padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#fff}}.filters input{{width:min(420px,100%);flex:1}}.filters button{{cursor:pointer}}.table-wrap{{overflow:auto;max-height:43vh;border:1px solid #d1d5db;margin-top:10px}}
table{{border-collapse:collapse;font-size:13px;width:max-content}}th,td{{border:1px solid #d1d5db;padding:6px 8px;vertical-align:top}}th{{position:sticky;top:0;background:#eef2f7;z-index:2;white-space:nowrap}}tbody tr:nth-child(even){{background:#f8fafc}}tbody tr.is-delisted{{background:#fee2e2!important;color:#7f1d1d}}tbody tr.linked-highlight{{background:#dbeafe!important;outline:2px solid #2563eb;outline-offset:-2px}}.col-compact,.col-contact{{white-space:nowrap}}.col-address{{width:260px;min-width:260px}}.col-room{{width:230px;min-width:230px}}.col-notes{{width:320px;min-width:320px}}.col-email{{width:210px;min-width:210px;overflow-wrap:anywhere}}.col-link{{width:640px;min-width:640px;white-space:nowrap}}a{{color:#1d4ed8}}.flag-no{{background:#fee2e2!important;color:#b91c1c;font-weight:700}}.flag-unknown{{background:#fef3c7!important;color:#92400e;font-weight:700}}
@media(max-width:700px){{#map{{height:45vh}}.legend{{display:none}}#panel{{padding:12px}}.table-wrap{{max-height:48vh}}}}
</style></head><body><div id="map"></div><div class="legend">{legend}</div><section id="panel"><div class="heading"><div><h1>SW-KA 房源地图与完整表</h1><div class="muted">筛选结果与地图标记同步；列表和地图支持双向悬停高亮。</div></div><strong id="count"></strong></div>
<div class="filters"><input id="search" type="search" placeholder="搜索地址、价格、设备、房东或备注…"><select id="type"><option value="">全部房源类型</option>{type_options}</select><select id="status"><option value="">全部状态</option><option value="在架">在架</option><option value="已下架">已下架</option></select><select id="change"><option value="">全部变化</option><option value="changed">已变化</option><option value="unchanged">未变化</option></select><button id="reset">重置</button></div><div class="muted">o = 包含/提供，x = 不包含/不提供，? = 原网页未说明。</div>
<div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody>{''.join(table_rows)}</tbody></table></div></section>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={json.dumps(markers, ensure_ascii=False)},map=L.map('map').setView([49.0069,8.4037],11);L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{attribution:'OpenStreetMap'}}).addTo(map);
const rows=[...document.querySelectorAll('tbody tr')],rowById=new Map(rows.map(r=>[r.dataset.roomId,r])),layers=new Map();
for(const x of data){{const layer=L.circleMarker([x.lat,x.lon],{{radius:8,color:'#fff',weight:2,fillColor:x.color,fillOpacity:.92}}).bindTooltip(`${{x.label}} · ${{x.rent}} · ${{x.room}}`).bindPopup(`<strong>${{x.label}} · ID ${{x.id}}</strong><br>${{x.address}}<br><a target="_blank" href="${{x.url}}">打开原房源</a>`);layer.addTo(map);layer.on('mouseover',()=>highlight(x.id,true,true));layer.on('mouseout',()=>highlight(x.id,false));layers.set(String(x.id),layer)}}
if(data.length>1)map.fitBounds(data.map(x=>[x.lat,x.lon]),{{padding:[25,25]}});
function highlight(id,on,scroll=false){{const row=rowById.get(String(id)),layer=layers.get(String(id));if(row){{row.classList.toggle('linked-highlight',on);if(on&&scroll)row.scrollIntoView({{block:'nearest'}})}}if(layer)layer.setStyle({{radius:on?12:8,weight:on?4:2}})}}
rows.forEach(row=>{{row.onmouseenter=()=>highlight(row.dataset.roomId,true);row.onmouseleave=()=>highlight(row.dataset.roomId,false)}});
function apply(){{const q=search.value.trim().toLowerCase();let visible=0;for(const row of rows){{const changed=row.dataset.change!=='未变化（复用）';const show=(!q||row.innerText.toLowerCase().includes(q))&&(!type.value||row.dataset.type===type.value)&&(!status.value||row.dataset.status===status.value)&&(!change.value||(change.value==='changed'?changed:!changed));row.hidden=!show;const layer=layers.get(row.dataset.roomId);if(layer){{if(show&&!map.hasLayer(layer))layer.addTo(map);if(!show&&map.hasLayer(layer))map.removeLayer(layer)}}if(show)visible++}}count.textContent=`${{visible}} / ${{rows.length}} 个房源`}}
[search,type,status,change].forEach(el=>el.addEventListener(el===search?'input':'change',apply));reset.onclick=()=>{{search.value='';type.value='';status.value='';change.value='';apply()}};apply();setTimeout(()=>map.invalidateSize(),0);
</script></body></html>'''
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(document, encoding="utf-8")
    return RESULT_PATH
