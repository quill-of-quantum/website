from modules.housing.notifications import build_notification, notification_title, unusual_notes


def test_relative_anomaly_rules_do_not_overinterpret_whole_groups():
    all_same = {
        "接受男性": "x", "接受女性": "x", "接受情侣": "x", "接受非吸烟者": "x",
        "嵌入式厨房": "x", "灶台": "x", "冰箱": "x", "洗碗机": "x",
        "洗衣机": "x", "烘干机": "x", "可入住时间": "现在", "冷租": "400 €",
        "暖租": "500 €", "房东邮箱": "a@example.com",
    }
    notes = unusual_notes(all_same)
    assert "租客条件整组同值，不据此判断限制" in notes
    assert "设备整组无明确提供项，可能未填写" in notes


def test_relative_anomaly_rules_flag_only_male_and_missing_fridge():
    detail = {
        "接受男性": "o", "接受女性": "x", "接受情侣": "x", "接受非吸烟者": "x",
        "嵌入式厨房": "o", "灶台": "o", "冰箱": "x", "洗碗机": "o",
        "洗衣机": "o", "烘干机": "x", "可入住时间": "ab 01.09.2026",
        "冷租": "500 €", "暖租": "650 €", "房东电话": "123",
    }
    notes = unusual_notes(detail)
    assert "可能仅接受男性" in notes
    assert "其他设备已填写但无冰箱" in notes


def test_notification_contains_requested_columns_and_links():
    changes = [{
        "id": "42", "change": "added", "rental_type": "wg",
        "recorded_at": "2026-08-18T10:23:44Z", "url": "https://example.test/42",
    }]
    records = {"42": {"rental_type": "wg", "url": "https://example.test/42", "address": "Kaiserstraße 1 76133 Karlsruhe", "detail": {
        "房型/面积": "20 m² - WG-Zimmer", "可入住时间": "ab sofort", "冷租": "400 €",
        "暖租": "520 €", "房东姓名": "Muster", "房东邮箱": "m@example.test",
        "房东电话": "1", "房东手机": "2", "备注": "Test",
    }}}
    text, rendered, rows = build_notification(changes, records)
    assert rows[0]["变化"] == "新上架"
    assert rows[0]["记录时间"] == "2026-08-18 12:23"
    assert "面积房型" in rendered and "非常规提示" in rendered
    assert 'href="https://example.test/42"' in rendered
    assert "google.com/maps/search/" in rendered and "Kaiserstra%C3%9Fe+1" in rendered
    assert notification_title(changes) == "SW-KA 房源上架通知"
    assert notification_title([{"change": "delisted"}]) == "SW-KA 房源下架通知"
    assert notification_title([{"change": "added"}, {"change": "delisted"}]) == "SW-KA 房源上架与下架通知"
    assert "SW-KA 房源变化通知" in text
