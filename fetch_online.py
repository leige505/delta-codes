# -*- coding: utf-8 -*-
"""改枪码在线抓取：博主的腾讯文档（公开链接）→ 规范 JSON
原理（2026-10-25 实测打通）:
  1. GET docs.qq.com/dop-api/opendoc?tab=<tabId>&...&outformat=1 公开可达（无需登录）
  2. 返回 JSONP，clientVars.collab_client_vars.initialAttributedText.text[0]
     .block_datas[0].related_sheet = base64(zlib(protobuf)) 的整表快照
  3. protobuf 里单元格文本明文可提取（方案名----枪码 / 价格 / 配置 顺序流）
  4. 复用 conv_codes 的正则/标签/过滤逻辑清洗
用法:
  python fetch_online.py <输出目录>        # 生成 codes.json + codes_data.py
注意:
  - endrow/block_end_row 必须 >= 5000，否则只给首屏 255 行
  - t= 参数可省；返回 retcode 200026 时多为参数缺失，用完整参数模板重试
"""
import base64
import datetime
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

DOC_ID = "DVWVCSnF2bGRJWXdk"
# footerDomStr 里的 sheet 清单（公开页面可查）
TABS = [
    ("BB08J2", "烽火地带"),   # 烽火地带+全面战场
    ("gks9xr", "黑潮爆破"),
    ("oi4av2", "全面战场"),   # 第三个 sheet
]
PAGE_URL = "https://docs.qq.com/sheet/" + DOC_ID + "?tab={tab}"
URL_TMPL = ("https://docs.qq.com/dop-api/opendoc?tab={tab}&u=&noEscape=1"
            "&enableSmartsheetSplit=1&startrow=0&endrow=5000&needSheetState=1"
            "&sliceStates=1&&block_end_col=31&block_end_row=5000"
            "&block_start_col=0&block_start_row=0&id=" + DOC_ID +
            "&normal=1&outformat=1&wb=1&nowb=0&callback=clientVarsCallback&xsrf=")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://docs.qq.com/sheet/" + DOC_ID,
}

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from conv_codes import (CODE_RE, PRICE_RE, CONFIG_RE, TAG_WORDS, DATE_ONLY_RE,
                        JUNK_RE, GUN_MODE_RE, AD_RE, TIP_RE, MODES,
                        is_code, merge_dedup)

# ---------------- protobuf 通用解析 ----------------

def _read_varint(buf, i):
    shift = 0
    val = 0
    while i < len(buf):
        b = buf[i]
        val |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("varint truncated")


def _parse_fields(buf):
    """解析 protobuf wire format，失败返回 None"""
    fields = []
    i = 0
    n = len(buf)
    while i < n:
        try:
            tag, i = _read_varint(buf, i)
        except ValueError:
            return None
        fno, wt = tag >> 3, tag & 7
        if fno == 0:
            return None
        if wt == 0:
            try:
                v, i = _read_varint(buf, i)
            except ValueError:
                return None
            fields.append((fno, wt, v))
        elif wt == 2:
            try:
                ln, i = _read_varint(buf, i)
            except ValueError:
                return None
            if i + ln > n:
                return None
            fields.append((fno, wt, buf[i:i + ln]))
            i += ln
        elif wt == 5:
            if i + 4 > n:
                return None
            fields.append((fno, wt, buf[i:i + 4]))
            i += 4
        elif wt == 1:
            if i + 8 > n:
                return None
            fields.append((fno, wt, buf[i:i + 8]))
            i += 8
        else:
            return None
    return fields


READABLE_RE = re.compile(r'[\x20-\x7E\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{2,}')


def _is_readable_text(b):
    if len(b) < 2:
        return False
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if READABLE_RE.fullmatch(s) is None:
        return False
    # 纯数字/单字符的数值字段不算文本单元格
    return not re.fullmatch(r'\d{1,4}', s)


def extract_texts(buf, out):
    """递归提取 protobuf 中所有可读文本（保持原顺序）"""
    fields = _parse_fields(buf)
    if fields is None:
        try:
            s = buf.decode("utf-8")
            if READABLE_RE.search(s):
                out.append(s)
        except UnicodeDecodeError:
            pass
        return
    for fno, wt, v in fields:
        if wt == 2:
            if _is_readable_text(v):
                out.append(v.decode("utf-8"))
            else:
                extract_texts(v, out)


# ---------------- 在线抓取 ----------------

def fetch_tab(tab, timeout=30):
    """抓一个 sheet，返回解压后的 protobuf bytes"""
    url = URL_TMPL.format(tab=tab)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="ignore")
    a = raw.find("(")
    b = raw.rfind(")")
    if a < 0 or b < 0:
        raise RuntimeError("not JSONP (tab=%s): %s" % (tab, raw[:80]))
    d = json.loads(raw[a + 1:b])
    try:
        b64 = d["clientVars"]["collab_client_vars"]["initialAttributedText"] \
                  ["text"][0]["block_datas"][0]["related_sheet"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("no related_sheet (tab=%s)" % tab)
    return zlib_decompress_b64(b64)


def zlib_decompress_b64(b64):
    return zlib_decompress(base64.b64decode(b64))


def zlib_decompress(blob):
    import zlib
    return zlib.decompress(blob)


def texts_from_tab(tab):
    blob = fetch_tab(tab)
    out = []
    extract_texts(blob, out)
    return out


TEXTS_ARR_RE = re.compile(
    r'"texts":\[(?:"(?:[^"\\]|\\.)*"(?:,"(?:[^"\\]|\\.)*")*)\]')


def ssr_texts_from_tab(tab, timeout=30):
    """SSR 页面 HTML 里的渲染文本（首屏 ~255 行，价格以显示文本 '59W' 出现）"""
    url = PAGE_URL.format(tab=tab)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="ignore")
    dec = urllib.parse.unquote(raw)
    m = TEXTS_ARR_RE.search(dec)
    if not m:
        return []
    return json.loads("[" + m.group(0)[len('"texts":['):-1] + "]")


# ---------------- 文本流 → entries（复用 conv_codes 清洗规则） ----------------

def parse_texts(texts, sheet_mode):
    """文本流解析：含码串找 gun/mode/name，向前找价格/配置"""
    entries, tips, dropped = [], [], 0
    last_gun = ""
    last_mode = sheet_mode
    for idx, t in enumerate(texts):
        t = re.sub(r'\s+', ' ', t.strip())
        if not t or AD_RE.search(t):
            if AD_RE.search(t):
                dropped += 1
            continue
        if TIP_RE.search(t) and not CODE_RE.search(t):
            tips.append(t)
        cm = CODE_RE.search(t)
        if not cm:
            continue
        code = cm.group(0)
        if not is_code(code):
            continue
        # 方案名 = 码前缀
        before = t[:cm.start()].strip(' -—－')
        name = before if len(before) >= 2 else ""
        if name and (DATE_ONLY_RE.fullmatch(name) or JUNK_RE.search(name)):
            name = ""
        if name:
            # 去掉混入的"枪名-模式"后缀
            for m2 in MODES:
                for suf in ("-" + m2, "—-" + m2, "—" + m2, "－" + m2):
                    if name.endswith(suf):
                        name = name[: -len(suf)].strip(' -—－')
        # gun/mode：串内 "枪名-模式-码" 结构
        gun, mode = "", None
        gm = GUN_MODE_RE.search(t[:cm.start()] + "-")
        if gm:
            gun = gm.group(1).strip('.-—－ ')
        for m in MODES:
            if m in t:
                mode = m
                break
        if not gun:
            # 向前找最近的枪名格
            for j in range(idx - 1, max(idx - 8, -1), -1):
                ot = texts[j].strip()
                if 2 <= len(ot) <= 14 and not CODE_RE.search(ot) and not PRICE_RE.search(ot) \
                        and not JUNK_RE.search(ot) and not DATE_ONLY_RE.fullmatch(ot):
                    if any(k in ot for k in ("枪", "步枪", "冲锋", "狙击", "射手", "机枪",
                                             "手枪", "AK", "M4", "M7", "UZI", "VSS", "SKS",
                                             "AUG", "MP5", "SG552", "SVCH", "AS", "93R",
                                             "G17", "沙漠")):
                        gun = ot
                        break
        if not gun:
            gun = last_gun
        else:
            last_gun = gun
        if name == gun:
            name = ""
        if mode is None:
            mode = last_mode
        last_mode = mode
        # 价格/配置：向前窗口找
        price, config = "", ""
        cfg_parts = []
        for j in range(idx - 1, max(idx - 6, -1), -1):
            ot = texts[j].strip()
            if not ot or CODE_RE.search(ot) or AD_RE.search(ot):
                continue
            if not price:
                pm = PRICE_RE.search(ot)
                if pm and len(ot) <= 8:
                    price = pm.group(1) + "W"
                    continue
            if CONFIG_RE.search(ot) and 3 <= len(ot) <= 30:
                for p in re.split(r'[；;]', ot):
                    p = p.strip()
                    if p and len(p) >= 3 and not DATE_ONLY_RE.fullmatch(p) \
                            and not JUNK_RE.search(p):
                        cfg_parts.append(p)
        config = "；".join(dict.fromkeys(cfg_parts[:3]))
        tag_src = (name or "") + " " + (config or "")
        tags = [w for w in TAG_WORDS if w in tag_src]
        entries.append({
            "gun": gun or "未知枪械",
            "mode": mode,
            "name": name or "",
            "code": code,
            "price": price,
            "config": config,
            "tags": tags[:3],
            "note": "",
            "src": "线上同步",
        })
    return entries, tips, dropped


def load_local_cache(out_dir):
    """本地缓存（Excel 版 + 上次线上版合并），用于补线上缺失的价格
    优先级：data/codes.json（Excel 全量版）> out_dir/codes.json（上次线上）"""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    cache = {}
    for p in (os.path.join(out_dir, "codes.json"),
              os.path.join(here, "codes.json"),
              os.path.join(here, "..", "data", "codes.json")):
        try:
            with open(p, encoding="utf-8") as f:
                for c in json.load(f).get("codes", []):
                    old = cache.get(c["code"])
                    if old is None or sum(bool(c.get(k)) for k in ("price", "config", "name")) \
                            > sum(bool(old.get(k)) for k in ("price", "config", "name")):
                        cache[c["code"]] = c
        except Exception:
            continue
    return cache


def load_excel_base():
    """Excel 基准数据（gun/mode 权威来源，从桌面原始xlsx经conv_codes生成）"""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "_excel_base", "codes.json")
    try:
        with open(p, encoding="utf-8") as f:
            return {c["code"]: c for c in json.load(f).get("codes", [])}
    except Exception:
        return {}


def enrich(entries, cache, excel_base):
    """用本地缓存修正线上字段：gun/mode以Excel版为权威（结构化解析更可靠），
    price/config取双方更全的。2026-10-25 粉丝反馈巨浪/Vector枪码错配后加。"""
    hit = 0
    for e in entries:
        old = cache.get(e["code"])
        xb = excel_base.get(e["code"])
        auth = xb or old      # gun/mode 权威：Excel 优先，其次缓存
        if not old:
            old = {}
        if auth and auth.get("gun") and auth["gun"] != e["gun"]:
            e["gun"] = auth["gun"]         # 枪名以结构化解析为准
            hit += 1
        if auth and auth.get("mode") and e["mode"] != auth["mode"] and auth["mode"] != "烽火地带":
            e["mode"] = auth["mode"]
        if not e["price"] and old.get("price"):
            e["price"] = old["price"]
        if not e["config"] and old.get("config"):
            e["config"] = old["config"]
    return hit


# 人工钉死的修正（粉丝实测：该码进游戏是维克托，博主文档里巨浪/Vector挤在相邻位置）
MANUAL_FIX = {
    "6K9TN6404NMBQ80T2GMT3": {"gun": "Vector冲锋枪", "name": "", "price": "",
                              "config": "红点/70发/弹夹和倍镜可以自行更换；综合实力平均能腰能开镜"},
}


def fetch_all(out_dir="."):
    all_entries, tips, dropped = [], [], 0
    cache = load_local_cache(out_dir)
    excel_base = load_excel_base()
    for tab, default_mode in TABS:
        try:
            texts = texts_from_tab(tab)
        except Exception as e:
            print("  [warn] tab %s 抓取失败: %s" % (tab, e))
            continue
        es, ts, d = parse_texts(texts, default_mode)
        # SSR 渲染文本补价格（首屏 ~255 行内有价格的直接补）
        try:
            ssr = parse_texts(ssr_texts_from_tab(tab), default_mode)[0]
        except Exception:
            ssr = []
        ssr_map = {e["code"]: e for e in ssr}
        ssr_hits = 0
        for e in es:
            s = ssr_map.get(e["code"])
            if s:
                if not e["price"] and s["price"]:
                    e["price"] = s["price"]
                    ssr_hits += 1
                if not e["config"] and s["config"]:
                    e["config"] = s["config"]
        cache_hits = enrich(es, cache, excel_base)
        # 人工钉死条目最后应用（优先级最高）
        for e in es:
            fix = MANUAL_FIX.get(e["code"])
            if fix:
                e.update(fix)
                hit_mark = True
        all_entries += es
        tips += ts
        dropped += d
        print("  tab %s(%s): %d 条 | SSR补价 %d | 本地补价 %d"
              % (tab, default_mode, len(es), ssr_hits, cache_hits))
    codes = merge_dedup(all_entries)
    codes.sort(key=lambda e: (MODES.index(e["mode"]) if e["mode"] in MODES else 9, e["gun"]))
    data = {
        "updated": datetime.date.today().isoformat(),
        "source": "线上同步",
        "tips": list(dict.fromkeys(tips))[:3],
        "codes": codes,
    }
    # 每日密码（游侠网，失败不影响改枪码数据）
    try:
        import fetch_password
        pw = fetch_password.fetch_passwords()
        if pw and pw.get("items"):
            data["passwords"] = pw
            print("  密码: %s %s (verified=%s)"
                  % (pw.get("date"), " ".join(i["map"][:2] + i["code"] for i in pw["items"]), pw.get("verified")))
        else:
            print("  密码: 抓取失败，跳过")
    except Exception as e:
        print("  密码: 异常 %s" % str(e)[:60])
    return data, dropped


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    print("开始线上抓取…")
    data, dropped = fetch_all(out_dir)
    with open(out_dir + "/codes.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(out_dir + "/codes_data.py", "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n# 自动生成于 %s\nDATA = " % data["updated"])
        # repr 而非 json.dumps：数据含布尔（passwords.verified），JSON 的 true 不是合法 Python
        f.write(repr(data))
    modes = {}
    for c in data["codes"]:
        modes[c["mode"]] = modes.get(c["mode"], 0) + 1
    print("线上清洗完成:", len(data["codes"]), "条 | 丢弃广告", dropped, "段 | 提示", len(data["tips"]))
    print("按模式:", modes)
    print("有价格:", sum(1 for c in data["codes"] if c["price"]),
          "| 有配置:", sum(1 for c in data["codes"] if c["config"]),
          "| 有标签:", sum(1 for c in data["codes"] if c["tags"]))
    for c in data["codes"][:5]:
        print("  [%s] %s | %s | %sW | %s" % (c["mode"], c["gun"], c["name"], c["price"].rstrip("W"), c["config"][:24]))


if __name__ == "__main__":
    main()
