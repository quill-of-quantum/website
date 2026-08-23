import html
from datetime import datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from modules.housing.db import format_listing_duration


TYPE_LABELS = {"wg": "WG", "einzelzimmer": "Einzelzimmer", "xzimmer": "xZimmer", "unknown": "未分类"}
CHANGE_LABELS = {"added": "新上架", "delisted": "新下架", "relisted": "重新上架", "updated": "重新上架"}
MAIL_COLUMNS = [
    "变化", "记录时间", "房源分类", "面积房型", "可入住时间", "冷租", "暖租", "房东姓名",
    "邮箱", "电话", "手机", "地址", "上架持续时间", "备注", "网址", "非常规提示",
]


def notification_title(changes):
    kinds = {item.get("change") for item in changes}
    has_up = bool(kinds & {"added", "relisted", "updated"})
    has_down = "delisted" in kinds
    if has_up and has_down:
        return "SW-KA 房源上架与下架通知"
    if has_down:
        return "SW-KA 房源下架通知"
    return "SW-KA 房源上架通知"


def _value(value):
    value = str(value or "").strip()
    return value if value else "?"


def _minute(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return _value(value)


def unusual_notes(detail):
    notes = []
    tenant_labels = {
        "接受男性": "男性", "接受女性": "女性", "接受情侣": "情侣", "接受非吸烟者": "非吸烟者",
    }
    tenant = {key: _value(detail.get(key)) for key in tenant_labels}
    tenant_known = {key: value for key, value in tenant.items() if value in ("o", "x")}
    if len(tenant_known) == len(tenant) and len(set(tenant_known.values())) == 1:
        notes.append("租客条件整组同值，不据此判断限制")
    else:
        accepted = [key for key, value in tenant.items() if value == "o"]
        rejected = [key for key, value in tenant.items() if value == "x"]
        if len(accepted) == 1 and len(rejected) == len(tenant) - 1:
            notes.append(f"可能仅接受{tenant_labels[accepted[0]]}")

    equipment_labels = {
        "嵌入式厨房": "嵌入式厨房", "灶台": "灶台", "冰箱": "冰箱",
        "洗碗机": "洗碗机", "洗衣机": "洗衣机", "烘干机": "烘干机",
    }
    equipment = {key: _value(detail.get(key)) for key in equipment_labels}
    provided = [key for key, value in equipment.items() if value == "o"]
    missing = [key for key, value in equipment.items() if value == "x"]
    if not provided:
        notes.append("设备整组无明确提供项，可能未填写")
    elif len(provided) >= 2:
        for key in ("冰箱", "灶台", "洗衣机"):
            if key in missing:
                notes.append(f"其他设备已填写但无{equipment_labels[key]}")

    if _value(detail.get("可入住时间")) == "?":
        notes.append("未说明可入住时间")
    if _value(detail.get("冷租")) == "?" or _value(detail.get("暖租")) == "?":
        notes.append("租金信息不完整")
    contacts = [_value(detail.get(key)) for key in ("房东邮箱", "房东电话", "房东手机")]
    if all(value == "?" for value in contacts):
        notes.append("无房东联系方式")
    return notes


def build_notification(changes, room_records):
    rows = []
    for change in changes:
        room = room_records.get(str(change.get("id"))) or {}
        detail = room.get("detail") or {}
        url = _value(room.get("url") or change.get("url"))
        email = _value(detail.get("房东邮箱"))
        row = {
            "变化": CHANGE_LABELS.get(change.get("change"), str(change.get("change") or "?")),
            "记录时间": _minute(change.get("recorded_at")),
            "房源分类": TYPE_LABELS.get(room.get("rental_type") or change.get("rental_type"), "未分类"),
            "面积房型": _value(detail.get("房型/面积") or room.get("room_type_text")),
            "可入住时间": _value(detail.get("可入住时间")), "冷租": _value(detail.get("冷租")),
            "暖租": _value(detail.get("暖租")), "房东姓名": _value(detail.get("房东姓名")),
            "邮箱": email, "电话": _value(detail.get("房东电话")), "手机": _value(detail.get("房东手机")),
            "地址": _value(detail.get("地址") or room.get("address")),
            "上架持续时间": format_listing_duration(
                room.get("listing_duration_seconds"), room.get("listing_started_source") == "website"
            ),
            "备注": _value(detail.get("备注")), "网址": url,
            "非常规提示": "；".join(unusual_notes(detail)) or "未发现明显非常规项",
        }
        rows.append(row)

    plain_lines = ["SW-KA 房源变化通知", ""]
    for row in rows:
        plain_lines.append(" | ".join(f"{key}: {row[key]}" for key in MAIL_COLUMNS))

    def escaped(value):
        return html.escape(str(value or "?"))

    def fact(label, value):
        return f'''<td class="fact"><span class="label">{escaped(label)}</span><br><strong>{escaped(value)}</strong></td>'''

    def card(row):
        is_down = row["变化"] == "新下架"
        accent = "#dc2626" if is_down else "#059669"
        pale = "#fef2f2" if is_down else "#ecfdf5"
        email_value = row["邮箱"]
        email_link = (
            f'<a href="mailto:{html.escape(email_value, quote=True)}">{escaped(email_value)}</a>'
            if "@" in email_value else escaped(email_value)
        )
        url = row["网址"]
        open_button = (
            f'<a class="button" style="background:{accent}" href="{html.escape(url, quote=True)}">打开原房源</a>'
            if url.startswith(("http://", "https://")) else ""
        )
        warning = row["非常规提示"]
        warning_class = "normal" if warning == "未发现明显非常规项" else "warning"
        address = row["地址"]
        map_button = (
            f'<a class="map-button" href="https://www.google.com/maps/search/?api=1&amp;query={quote_plus(address)}">在 Google Maps 搜索地址</a>'
            if address != "?" else ""
        )
        return f'''<div class="card" style="border-left-color:{accent}">
  <div class="card-head" style="background:{pale}">
    <span class="badge" style="background:{accent}">{escaped(row['变化'])}</span>
    <strong class="type">{escaped(row['房源分类'])}</strong>
    <span class="time">记录于 {escaped(row['记录时间'])}</span>
  </div>
  <div class="card-body">
    <table class="facts primary-facts" role="presentation"><tr>{fact('面积房型', row['面积房型'])}{fact('可入住时间', row['可入住时间'])}</tr></table>
    <table class="facts price-facts" role="presentation"><tr>{fact('冷租', row['冷租'])}{fact('暖租', row['暖租'])}</tr></table>
    {f'<div class="duration"><span class="label">上架持续时间</span><br><strong>{escaped(row["上架持续时间"])}</strong></div>' if is_down else ''}
    <div class="section"><span class="label">房东刊登地址</span><div class="address">{escaped(address)}</div>{map_button}</div>
    <div class="section"><span class="label">房东与联系方式</span><div class="contact"><strong>{escaped(row['房东姓名'])}</strong><br>{email_link}<br>电话：{escaped(row['电话'])}　手机：{escaped(row['手机'])}</div></div>
    <div class="section"><span class="label">备注</span><div>{escaped(row['备注'])}</div></div>
    <div class="{warning_class}"><strong>非常规提示：</strong>{escaped(warning)}</div>
    <div class="action">{open_button}<div class="raw-url">{escaped(url)}</div></div>
  </div>
</div>'''

    groups = []
    for label in ("新上架", "重新上架", "新下架"):
        group_rows = [row for row in rows if row["变化"] == label]
        if group_rows:
            groups.append(f'<h2 class="group-title">{label} <span>{len(group_rows)}</span></h2>')
            groups.extend(card(row) for row in group_rows)
    other_rows = [row for row in rows if row["变化"] not in {"新上架", "重新上架", "新下架"}]
    if other_rows:
        groups.append(f'<h2 class="group-title">其他变化 <span>{len(other_rows)}</span></h2>')
        groups.extend(card(row) for row in other_rows)

    counts = {label: sum(row["变化"] == label for row in rows) for label in ("新上架", "重新上架", "新下架")}
    summary = " · ".join(f"{label} {count}" for label, count in counts.items() if count)
    title = notification_title(changes)
    html_body = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{escaped(title)}</title><style>
body{{margin:0;background:#f1f5f9;color:#1f2937;font-family:Arial,"Microsoft YaHei",sans-serif}}.shell{{max-width:680px;margin:auto;padding:24px 12px}}.intro{{background:#0f172a;color:#fff;padding:22px;border-radius:12px}}.intro h1{{font-size:22px;margin:0 0 8px}}.intro p{{margin:0;color:#cbd5e1}}.group-title{{font-size:17px;margin:24px 2px 10px}}.group-title span{{font-size:12px;background:#e2e8f0;padding:3px 8px;border-radius:999px}}.card{{background:#fff;border:1px solid #dbe3ec;border-left:5px solid;border-radius:10px;margin:0 0 14px;overflow:hidden;box-shadow:0 2px 5px #0f172a12}}.card-head{{padding:11px 14px}}.badge{{display:inline-block;color:#fff;font-size:12px;font-weight:bold;padding:4px 8px;border-radius:999px}}.type{{margin-left:8px}}.time{{float:right;color:#64748b;font-size:12px;padding-top:4px}}.card-body{{padding:15px}}.facts{{width:100%;border-collapse:separate;border-spacing:8px 0;margin:0 -8px 10px}}.fact{{width:50%;background:#f8fafc;padding:11px;border-radius:7px}}.primary-facts .fact strong{{font-size:15px}}.price-facts .fact{{background:#ecfeff;border:1px solid #a5f3fc}}.price-facts .fact strong{{font-size:19px;color:#0f766e}}.duration{{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;padding:10px;border-radius:7px;margin:4px 0 12px}}.label{{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}.section{{border-top:1px solid #e2e8f0;padding-top:11px;margin-top:11px;line-height:1.55}}.address{{font-weight:bold;margin:3px 0 8px}}.contact a{{color:#1d4ed8}}.map-button{{display:inline-block;color:#1d4ed8!important;background:#eff6ff;border:1px solid #bfdbfe;text-decoration:none;font-size:12px;font-weight:bold;padding:7px 10px;border-radius:6px}}.warning,.normal{{margin-top:13px;padding:10px;border-radius:7px;font-size:13px}}.warning{{background:#fff7ed;color:#9a3412;border:1px solid #fed7aa}}.normal{{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}}.action{{margin-top:16px}}.button{{display:inline-block;color:#fff!important;text-decoration:none;font-weight:bold;padding:10px 16px;border-radius:7px}}.raw-url{{margin-top:9px;color:#94a3b8;font-size:10px;word-break:break-all}}.foot{{color:#64748b;font-size:11px;line-height:1.5;margin:18px 4px}}
@media(max-width:520px){{.shell{{padding:10px 6px}}.intro{{border-radius:8px}}.time{{float:none;display:block;margin-top:7px}}.hero strong,.hero span{{display:block;text-align:left}}.hero span{{margin-top:6px}}.fact{{display:block;width:auto;margin-bottom:7px}}.facts tr{{display:block}}}}
</style></head><body><div class="shell"><div class="intro"><h1>{escaped(title)}</h1><p>{escaped(summary or str(len(rows)) + ' 条变化')}</p></div>{''.join(groups)}<p class="foot">“非常规提示”使用同组相对判断：整组同值或全部未填写不会被机械判断为限制。此邮件来自单次搜索的新变化，与网页中保留 8 小时的变化标签相互独立。</p></div></body></html>'''
    return "\n".join(plain_lines), html_body, rows
