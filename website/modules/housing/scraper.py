import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


LOGIN_URL = "https://www.sw-ka.de/login/#user_login"
CATALOG_BASE = "https://www.sw-ka.de/de/wohnen/zimmervermittlung/privatzimmer_suchen/"
# 网站的 Zimmertyp: alle 对应纯总目录。不能保留 search_arr_filter_1=ka，
# 否则会变成 Karlsruhe 子集（当前少 8 条）。
CATALOG_ALL_URL = CATALOG_BASE


def classify_rental_type(summary):
    value = re.sub(r"\s+", " ", summary or "").lower()
    if re.search(r"\bwg\b|wg-zimmer|wohngemeinschaft", value):
        return "wg"
    if "einzelzimmer" in value or re.search(r"\bsingle\b", value):
        return "einzelzimmer"
    return "xzimmer"


def _room_id(url):
    values = parse_qs(urlparse(url).query).get("id", [])
    return values[0] if values else ""


def _catalog_links(page):
    return page.eval_on_selector_all(
        "a[href]",
        """els => els.map(a => { try { return new URL(a.getAttribute('href'), location.href).href; } catch { return ''; } })
          .filter(url => { try { const u = new URL(url); return /privatzimmer_suchen/i.test(u.pathname) && u.searchParams.has('id'); } catch { return false; } })""",
    )


def _login(page, catalog_url, username, password, progress):
    progress("login_check", "正在检查已有登录会话", url=catalog_url)
    page.goto(catalog_url, wait_until="domcontentloaded", timeout=60000)
    if _catalog_links(page):
        progress("login_ok", "已有登录会话有效")
        return
    if not username or not password:
        raise RuntimeError("登录状态失效，且后台设置中尚未配置网站账户和密码")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    progress("login_form", "会话无效，正在填写登录表单")
    secret = page.locator('input[type="password"]').first
    if not secret.count():
        raise RuntimeError("未找到 SW-KA 登录表单")
    form = secret.locator("xpath=ancestor::form[1]")
    user = form.locator(
        'input[type="email"], input[name*="email" i], input[name*="user" i], input[type="text"]'
    ).first
    if not user.count():
        raise RuntimeError("登录表单中未找到账号输入框")
    user.fill(username)
    secret.fill(password)
    submit = form.locator(
        'button:has-text("LOGIN"), input[type="submit"], button[type="submit"], a:has-text("LOGIN")'
    ).first
    if submit.count():
        progress("login_submit", "正在点击登录表单的 LOGIN 按钮")
        submit.click()
    else:
        progress("login_submit", "未找到登录按钮，正在通过 Enter 提交")
        secret.press("Enter")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(2500)
    page.goto(catalog_url, wait_until="domcontentloaded", timeout=60000)
    if not _catalog_links(page):
        messages = page.locator(
            '.alert:visible, .error:visible, [role="alert"]:visible, .form-error:visible'
        ).all_inner_texts()
        detail = "；".join(re.sub(r"\s+", " ", value).strip() for value in messages if value.strip())
        progress(
            "login_failed", "提交后仍未发现房源链接",
            page_url=page.url, site_message=(detail[:500] if detail else "网站未显示可识别的错误提示"),
        )
        suffix = f"：{detail[:300]}" if detail else ""
        raise RuntimeError(f"自动登录失败，请核对账户密码或网站是否要求额外验证{suffix}")
    progress("login_ok", "账户密码自动登录成功")


def _extract_page(page):
    return page.evaluate(
        """() => {
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const absolute = value => { try { return new URL(value, location.href); } catch { return null; } };
          const found = new Map();
          const add = (value, container) => {
            const url = absolute(value); if (!url || !/privatzimmer_suchen/i.test(url.pathname) || !url.searchParams.has('id')) return;
            const id = url.searchParams.get('id') || ''; if (!id) return;
            const summary = clean((container || document.body).innerText);
            const previous = found.get(id);
            if (!previous || summary.length > previous.summary.length) found.set(id, {id, url:url.href, summary});
          };
          document.querySelectorAll('a[href]').forEach(a => add(a.getAttribute('href'), a.closest('tr,article') || a.parentElement));
          document.querySelectorAll('tr, article, [data-href], [onclick]').forEach(row => {
            const direct = row.getAttribute('data-href') || '';
            if (direct) add(direct, row);
            const onclick = row.getAttribute('onclick') || '';
            const match = onclick.match(/(?:location(?:\.href)?\s*=|open\s*\()\s*['\"]([^'\"]*?[?&]id=\d+[^'\"]*)/i)
              || row.outerHTML.match(/([/?][^'\"<>]*?[?&]id=\d+[^'\"<>]*)/i);
            if (match) add(match[1], row);
          });
          return {items:[...found.values()], rawCount:document.querySelectorAll('a[href*="id="], [data-href*="id="], [onclick*="id="]').length};
        }""",
    )


def set_page_number(url, number):
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["cpage"] = [str(number)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def scrape(config, profile_dir, progress=None):
    progress = progress or (lambda *_args, **_kwargs: None)
    rooms = {}
    profile_dir.mkdir(parents=True, exist_ok=True)
    progress("browser_start", "正在启动无头 Chromium")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=True, locale="de-DE",
            viewport={"width": 1440, "height": 900}, chromium_sandbox=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            progress("browser_ready", "无头 Chromium 已启动")
            base_url = CATALOG_ALL_URL
            _login(page, base_url, config.get("username"), config.get("password"), progress)
            progress("catalog_all", "开始检查 Zimmertyp: alle 总目录", discovered=0)
            seen_pages = set()
            consecutive_no_new = 0
            for page_number in range(0, 100):
                url = base_url if page_number == 0 else set_page_number(base_url, page_number)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                canonical = page.url.split("#", 1)[0]
                if canonical in seen_pages:
                    continue
                seen_pages.add(canonical)
                extracted = _extract_page(page)
                page_items = extracted["items"]
                before = len(rooms)
                for item in page_items:
                    if item["id"]:
                        item["summary"] = re.sub(r"\s+", " ", item["summary"]).strip()
                        item["rental_type"] = classify_rental_type(item["summary"])
                        rooms[item["id"]] = item
                added_on_page = len(rooms) - before
                progress(
                    "catalog_page",
                    f"Alle 第 {page_number + 1} 页：原始 {extracted['rawCount']}，唯一 {len(page_items)}，新增 {added_on_page}，累计 {len(rooms)}",
                    page=page_number + 1, raw_links=extracted["rawCount"], page_rooms=len(page_items),
                    page_added=added_on_page, discovered=len(rooms), url=canonical,
                )
                consecutive_no_new = consecutive_no_new + 1 if added_on_page == 0 else 0
                if not page_items or consecutive_no_new >= 2:
                    break
            progress("catalog_all_done", "Alle 总目录检查完成并已分类", pages=len(seen_pages), discovered=len(rooms))
        finally:
            progress("browser_close", "正在关闭无头 Chromium", discovered=len(rooms))
            context.close()
    return rooms


def scrape_details(config, profile_dir, rooms, room_ids, progress=None, item_callback=None):
    progress = progress or (lambda *_args, **_kwargs: None)
    if not room_ids:
        return {}
    results = {}
    progress("details_start", f"准备抓取 {len(room_ids)} 个房源详情", detail_total=len(room_ids))
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=True, locale="de-DE",
            viewport={"width": 1440, "height": 900}, chromium_sandbox=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _login(page, CATALOG_ALL_URL, config.get("username"), config.get("password"), progress)
            for index, room_id in enumerate(room_ids, 1):
                room = rooms[room_id]
                try:
                    page.goto(room["url"], wait_until="domcontentloaded", timeout=60000)
                    detail = page.evaluate("""() => {
                      const clean=s=>(s||'').replace(/\s+/g,' ').trim();
                      const root=document.querySelector('main,#content,.main-content,article')||document.body;
                      const fields={};
                      root.querySelectorAll('dl').forEach(dl=>[...dl.querySelectorAll('dt')].forEach(dt=>{const dd=dt.nextElementSibling;if(dd&&dd.tagName==='DD')fields[clean(dt.innerText).replace(/:$/,'')]=clean(dd.innerText)}));
                      root.querySelectorAll('table tr').forEach(tr=>{const c=[...tr.querySelectorAll('th,td')].map(x=>clean(x.innerText));if(c.length===2&&c[0]&&c[1])fields[c[0].replace(/:$/,'')]=c[1]});
                      root.querySelectorAll('p,div').forEach(el=>{if(el.children.length>3)return;const text=clean(el.innerText);const m=text.match(/^([^:]{2,40}):\s*(.+)$/);if(m&&m[2].length<500&&!(m[1] in fields))fields[m[1]]=m[2]});
                      return {title:clean((root.querySelector('h1,h2')||{}).innerText),fields,full_text:clean(root.innerText),html:document.documentElement.outerHTML};
                    }""")
                    results[room_id] = detail
                    if item_callback:
                        item_callback(room_id, detail)
                    progress("detail", f"详情 {index}/{len(room_ids)} 成功：ID {room_id}", detail_index=index, detail_total=len(room_ids), room_id=room_id)
                except Exception as exc:
                    progress("detail_error", f"详情 {index}/{len(room_ids)} 失败：ID {room_id} · {exc}", detail_index=index, detail_total=len(room_ids), room_id=room_id)
        finally:
            context.close()
    return results
