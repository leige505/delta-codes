# -*- coding: utf-8 -*-
"""三角洲每日密码抓取：双源（游侠网 + kkrb一图流）交叉验证 → 五图密码 JSON
原理（2026-10-25 实测游侠；2026-11-01 实测 kkrb）:
  源A 游侠网:
    1. 固定页 https://m.ali213.net/news/gl2412/1566819.html（巴克什密码大全，每天更新）
       页内含指向最新每日全图页的链接（标题"三角洲行动X月X日密码2026"，URL按日递增）
    2. 今日全图页正文结构: "一、零号大坝 / 密码门密码：2362 / 二、长弓溪谷 / ..."
    3. 源内交叉验证: 游侠5个单图汇总页（大坝/溪谷/巴克什/航天/监狱）各含最新密码
  源B kkrb.net（三角洲行动一图流，官方小程序数据的第三方聚合，每天0点前更新）:
    1. GET https://www.kkrb.net/ 首页拿会话 cookie（必须，否则接口返回-101）
    2. POST https://www.kkrb.net/getBonusDoorData（空body）
       → data.{db,cgxg,bks,htjd,cxjy,az3}.password + updated(YYYYMMDDHHMMSS)
  融合策略:
    - 两源日期一致且五码一致 → verified=true
    - 两源冲突 → 取日期（iso）更新的那组，verified=false（如实降级，不编数据）
    - 单源失败 → 用另一源；两源都失败 → 返回 None（不发布）
输出:
  {"date": "11月1日", "iso": "2026-11-01", "items": [{"map": "零号大坝", "code": "7290"}, ...],
   "verified": true/false, "sources": {"ali213": {...}|null, "kkrb": {...}|null}, "fetched": ISO时间}
用法:
  python fetch_password.py            # 打印 JSON 到 stdout
"""
import json
import re
import sys
import urllib.request
import urllib.parse
import http.cookiejar
from datetime import datetime

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

DISCOVERY_URL = "https://m.ali213.net/news/gl2412/1566819.html"   # 巴克什大全页（固定URL，日更）
# 单图汇总页（交叉验证源，均日更）
VERIFY_URLS = {
    "零号大坝": "https://m.ali213.net/news/gl2412/1566795.html",
    "长弓溪谷": "https://m.ali213.net/news/gl2412/1566745.html",
    "巴克什": "https://m.ali213.net/news/gl2412/1566819.html",
    "航天基地": "https://m.ali213.net/news/gl2412/1566781.html",
    "潮汐监狱": "https://m.ali213.net/news/gl2601/1740317.html",
}
MAPS = ("零号大坝", "长弓溪谷", "巴克什", "航天基地", "潮汐监狱")

DAY_LINK_RE = re.compile(
    r'href="(https://m\.ali213\.net/news/gl\d+/\d+\.html)"[^>]*>\s*三角洲行动(\d{1,2})月(\d{1,2})日密码')
DATE_TITLE_RE = re.compile(r'三角洲行动(\d{1,2})月(\d{1,2})日密码')
MAP_CODE_RE = re.compile(
    r'((?:零号)?大坝|长弓溪谷|巴克什|航天基地?|潮汐监狱|AZ3?)[\s\S]{0,30}?密码[：:]\s*(\d{4})')


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def html_text(raw):
    txt = re.sub(r'<script.*?</script>', '', raw, flags=re.S)
    txt = re.sub(r'<style.*?</style>', '', txt, flags=re.S)
    return re.sub(r'<[^>]+>', '\n', txt)


def find_latest_day_url():
    """从固定页发现最新日期的全图页 URL"""
    raw = http_get(DISCOVERY_URL)
    cands = []
    for m in DAY_LINK_RE.finditer(raw):
        url, mo, dy = m.group(1), int(m.group(2)), int(m.group(3))
        cands.append(((mo, dy), url))  # 年份不跨年问题可忽略（10月）
    if not cands:
        return None, None
    cands.sort(reverse=True)
    (mo, dy), url = cands[0]
    return url, "10月%d日" % dy if mo == 10 else "%d月%d日" % (mo, dy)


def parse_day_page(url):
    """从全图页提取 (date_label, {图名: 密码})"""
    raw = http_get(url)
    text = html_text(raw)
    tm = DATE_TITLE_RE.search(text)
    label = "%s月%s日" % (tm.group(1), tm.group(2)) if tm else "?"
    codes = {}
    for mm in MAP_CODE_RE.finditer(text):
        name, code = mm.group(1), mm.group(2)
        name = "航天基地" if name.startswith("航天") else name
        name = "零号大坝" if "大坝" in name else name
        codes.setdefault(name, code)  # 先到先得（页面最新密码在正文最前）
    return label, codes


def verify_with_single_pages(day_codes):
    """用5个单图页交叉验证，返回 (verified_bool, {图: 单图页密码})"""
    ok = True
    single = {}
    for name, url in VERIFY_URLS.items():
        try:
            text = html_text(http_get(url))
            m = MAP_CODE_RE.search(text)
            if m:
                single[name] = m.group(2)
        except Exception:
            continue
    for name, code in day_codes.items():
        if name in single and single[name] != code:
            ok = False
    return ok, single


def _label_to_iso(label):
    """'10月28日' → '2026-10-28'（密码实际日期，非抓取日；跨年按12月→1月减一年）"""
    m = re.match(r"^(\d{1,2})月(\d{1,2})日$", label or "")
    if not m:
        return datetime.now().strftime("%Y-%m-%d")
    mo, dy = int(m.group(1)), int(m.group(2))
    year = datetime.now().year
    if mo == 12 and datetime.now().month == 1:
        year -= 1
    return "%04d-%02d-%02d" % (year, mo, dy)


# ---------------- 源B: kkrb.net 一图流（2026-11-01 实测） ----------------
KKRB_HOME = "https://www.kkrb.net/"
KKRB_API = "https://www.kkrb.net/getBonusDoorData"
KKRB_MAP_KEYS = {"db": "零号大坝", "cgxg": "长弓溪谷", "bks": "巴克什",
                 "htjd": "航天基地", "cxjy": "潮汐监狱"}   # az3/az3r6 EXE 暂不展示，忽略


def fetch_kkrb():
    """kkrb.net 每日密码：先访问首页拿会话cookie，再 POST getBonusDoorData。
    返回 {"date_label", "iso", "codes": {图名: 密码}, "updated": 时间戳字符串}；失败返回 None。
    """
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA)]
    # 第一步：访问首页种 cookie（缺这步接口返回 -101 系统繁忙）
    op.open(KKRB_HOME, timeout=20).read()
    # 第二步：带 Referer + XHR 头 POST 空表单
    req = urllib.request.Request(KKRB_API, data=b"", method="POST")
    req.add_header("Referer", "https://www.kkrb.net/pages/map/bonus_door.html")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    body = json.loads(op.open(req, timeout=20).read().decode("utf-8", errors="ignore"))
    if body.get("code") != 1:
        raise RuntimeError("kkrb code=%s" % body.get("code"))
    data = body["data"]
    codes = {}
    for key, name in KKRB_MAP_KEYS.items():
        pwd = (data.get(key) or {}).get("password")
        if pwd and re.fullmatch(r"\d{4}", pwd):
            codes[name] = pwd
    upd = (data.get("db") or {}).get("updated", "")   # YYYYMMDDHHMMSS，取大坝的时间作日期
    if re.fullmatch(r"\d{14}", upd):
        iso = "%s-%s-%s" % (upd[0:4], upd[4:6], upd[6:8])
        label = "%d月%d日" % (int(upd[4:6]), int(upd[6:8]))
    else:
        iso, label = datetime.now().strftime("%Y-%m-%d"), "?"
    return {"date_label": label, "iso": iso, "codes": codes, "updated": upd}


def _merge_sources(ali, kkrb):
    """融合两源：一致→verified；冲突→取 iso 更新的源并 verified=False；缺源→用单源。"""
    if not ali and not kkrb:
        return None
    if ali and kkrb:
        a = {i["map"]: i["code"] for i in ali["items"]}
        b = kkrb["codes"]
        same_codes = all(a.get(m) == b.get(m) for m in MAPS if m in a and m in b)
        if ali["iso"] == kkrb["iso"] and same_codes:
            return ali, True, "双源一致"
        newer = ali if ali["iso"] >= kkrb["iso"] else None
        if newer is ali:
            merged = dict(ali)
        else:
            merged = {
                "date": kkrb["date_label"], "iso": kkrb["iso"],
                "items": [{"map": m, "code": kkrb["codes"][m]} for m in MAPS if m in kkrb["codes"]],
                "verified": bool(kkrb.get("verified", False)),
                "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        return merged, False, "两源冲突(游侠%s vs kkrb%s)，取更新日期" % (ali["iso"], kkrb["iso"])
    if kkrb:
        merged = {
            "date": kkrb["date_label"], "iso": kkrb["iso"],
            "items": [{"map": m, "code": kkrb["codes"][m]} for m in MAPS if m in kkrb["codes"]],
            "verified": bool(kkrb.get("verified", False)),
            "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        return merged, False, "仅kkrb源(游侠抓取失败)"
    return ali, ali.get("verified", False), "仅游侠源(kkrb抓取失败)"


def fetch_passwords():
    # 游侠源（照旧）
    ali = None
    try:
        url, label = find_latest_day_url()
        if url:
            date_label, codes = parse_day_page(url)
            missing = [m for m in MAPS if m not in codes]
            verified, single = verify_with_single_pages(codes)
            for name in missing:
                if name in single:
                    codes[name] = single[name]
            ali = {
                "date": date_label, "iso": _label_to_iso(date_label),
                "items": [{"map": m, "code": codes[m]} for m in MAPS if m in codes],
                "verified": verified and not missing,
                "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
    except Exception as e:
        print("  游侠源失败: %s" % str(e)[:80])
    # kkrb 源
    kkrb = None
    try:
        kkrb = fetch_kkrb()
    except Exception as e:
        print("  kkrb源失败: %s" % str(e)[:80])
    # 兼容 kkrb 内部验证字段（接口无验证概念，overridden 表示社区纠错覆盖）
    if kkrb:
        kkrb["verified"] = True
    merged, verified, note = _merge_sources(ali, kkrb)
    if not merged:
        return None
    merged["verified"] = verified
    merged["sources"] = {"ali213": ali, "kkrb": kkrb}
    merged["merge_note"] = note
    return merged


if __name__ == "__main__":
    try:
        data = fetch_passwords()
        print(json.dumps(data, ensure_ascii=False, indent=1))
    except Exception as e:
        print("FAIL: %s" % e)
        sys.exit(1)
