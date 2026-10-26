# -*- coding: utf-8 -*-
"""三角洲每日密码抓取：游侠网汇总页 → 五图密码 JSON
原理（2026-10-25 实测）:
  1. 固定页 https://m.ali213.net/news/gl2412/1566819.html（巴克什密码大全，每天更新）
     页内含指向最新每日全图页的链接（标题"三角洲行动X月X日密码2026"，URL按日递增）
  2. 今日全图页正文结构: "一、零号大坝 / 密码门密码：2362 / 二、长弓溪谷 / ..."
  3. 交叉验证: 游侠5个单图汇总页（大坝/溪谷/巴克什/航天/监狱）各含最新密码，比对一致才可信
输出:
  {"date": "10月24日", "iso": "2026-10-24", "items": [{"map": "零号大坝", "code": "2362"}, ...],
   "verified": true/false, "fetched": ISO时间}
用法:
  python fetch_password.py            # 打印 JSON 到 stdout
"""
import json
import re
import sys
import urllib.request
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


def fetch_passwords():
    url, label = find_latest_day_url()
    if not url:
        return None
    date_label, codes = parse_day_page(url)
    missing = [m for m in MAPS if m not in codes]
    verified, single = verify_with_single_pages(codes)
    # 校验失败的图用单图页值兜底
    for name in missing:
        if name in single:
            codes[name] = single[name]
    return {
        "date": date_label,
        "iso": datetime.now().strftime("%Y-%m-%d"),
        "items": [{"map": m, "code": codes[m]} for m in MAPS if m in codes],
        "verified": verified and not missing,
        "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


if __name__ == "__main__":
    try:
        data = fetch_passwords()
        print(json.dumps(data, ensure_ascii=False, indent=1))
    except Exception as e:
        print("FAIL: %s" % e)
        sys.exit(1)
