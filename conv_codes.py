# -*- coding: utf-8 -*-
"""改枪码清洗：博主的脏 Excel → 规范 JSON
用法:
  python conv_codes.py <输入.xlsx> <输出目录>
输出:
  <输出目录>/codes.json   规范数据（云端发布用）
  <输出目录>/codes_data.py 内嵌数据（打包进 EXE 用）
"""
import json
import re
import sys
import datetime

CODE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z0-9]{18,22})(?![A-Za-z0-9])')
PRICE_RE = re.compile(r'(?<![A-Za-z0-9])(\d{1,4}(?:\.\d)?)\s*([Ww万])(?![A-Za-z0-9])')
CONFIG_RE = re.compile(r'红点|满改|倍镜|弹夹|弹鼓|热成像|握把|枪管|枪口|护木|枪托|瞄具|导轨|腰射|开镜|半改|配件|口径|发|镭射|激光|消音| extens')
TAG_WORDS = ("赛事同款", "后坐力", "满改", "神器", "性价比", "稳定", "高伤", "秒开镜", "腰射",
             "热成像", "黑夜", "专属", "满改红点", "高操控", "省", "交枪", "跑刀", "巨斧", "贫民", "毕业")
DATE_ONLY_RE = re.compile(r'^\d{1,2}\.\d{1,2}$')
JUNK_RE = re.compile(r'新营|借款|加微|私信|薇信|威信|加V|关注公众号|引流|兼职|上分|下分|回收|代打')
GUN_MODE_RE = re.compile(r'([\u4e00-\u9fa5A-Za-z0-9\.\-]+?)[-—－](烽火地带|全面战场|黑潮爆破)')
AD_RE = re.compile(r'炸单|低价|机密|48R|688W|20w|金包|卷死|代肝|回收|出金|包赔|下单')
TIP_RE = re.compile(r'AI识图|空格|失效')

MODES = ("烽火地带", "全面战场", "黑潮爆破")


def is_code(text):
    """18-22位大写字母数字，且字母数字混合（防纯数字/纯字母误判）"""
    if not re.fullmatch(r'[A-Z0-9]{18,22}', text):
        return False
    return any(c.isdigit() for c in text) and any(c.isalpha() for c in text)


def clean_cell(text):
    return re.sub(r'\s+', ' ', text.strip())


def parse_sheet(ws, sheet_default_mode):
    """遍历 sheet，返回 (entries, tips, dropped_ads)"""
    entries, tips, dropped = [], [], 0
    last_gun = ""          # 记住上一个出现的枪名（脏表里枪名常和码错行）
    last_mode = None

    for row in ws.iter_rows(values_only=True):
        row_texts = [clean_cell(str(c)) for c in row if c is not None and clean_cell(str(c))]
        if not row_texts:
            continue
        joined = ' '.join(row_texts)

        # 广告过滤
        if AD_RE.search(joined):
            dropped += 1
            continue
        # 使用提示单独收
        for t in row_texts:
            if TIP_RE.search(t) and not CODE_RE.search(t):
                tips.append(t)

        # 行内模式检测
        row_mode = None
        for m in MODES:
            if m in joined:
                row_mode = m
                break

        # 行内枪名检测（"枪名-模式" 结构）
        gun_in_row = ""
        gm = GUN_MODE_RE.search(joined)
        if gm:
            gun_in_row = gm.group(1)

        for ci, t in enumerate(row_texts):
            for cm in CODE_RE.finditer(t):
                code = cm.group(0)
                if not is_code(code):
                    continue
                # 方案名：码前缀（"方案名----码"）
                before = t[:cm.start()].strip(' -—－')
                name = before if len(before) >= 2 else ""
                if name and (DATE_ONLY_RE.fullmatch(name) or JUNK_RE.search(name)):
                    name = ""
                if name:
                    # 去掉混入的"枪名-模式"后缀（如 ".357左轮-烽火地带"）
                    for m2 in MODES:
                        for suf in ("-" + m2, "—-" + m2, "—" + m2, "－" + m2):
                            if name.endswith(suf):
                                name = name[: -len(suf)].strip(' -—－')
                # 枪名：优先行内"枪名-模式"结构，其次同行短文本，最后用上一个枪名
                gun = gun_in_row
                if not gun:
                    for ot in row_texts:
                        if ot is t:
                            continue
                        if 2 <= len(ot) <= 14 and not CODE_RE.search(ot) and not AD_RE.search(ot) \
                                and not DATE_ONLY_RE.fullmatch(ot) and not JUNK_RE.search(ot):
                            if any(k in ot for k in ("枪", "步枪", "冲锋", "狙击", "射手", "机枪", "手枪", "AK", "M4", "M7", "UZI", "VSS", "SKS", "AUG", "MP5", "SG552", "SVCH", "AS", "93R", "G17", "沙漠")):
                                gun = ot
                                break
                if not gun:
                    gun = last_gun
                else:
                    last_gun = gun
                if name == gun:
                    name = ""
                # 价格：本行找 "30W / 52W / 7万" 形式
                price = ""
                pm = PRICE_RE.search(joined)
                if pm:
                    price = "%s%s" % (pm.group(1), "W")
                # 配置：note 中的配件描述独立成字段（剔除纯日期/垃圾段）
                config_parts = []
                for ot in row_texts:
                    if ot is t or CODE_RE.search(ot):
                        continue
                    if DATE_ONLY_RE.fullmatch(ot) or JUNK_RE.search(ot):
                        continue
                    if CONFIG_RE.search(ot) and len(ot) >= 3:
                        parts = [p.strip() for p in re.split(r'[；;]', ot)
                                 if p.strip() and not DATE_ONLY_RE.fullmatch(p.strip())
                                 and not JUNK_RE.search(p.strip()) and len(p.strip()) >= 3]
                        config_parts += parts
                config = "；".join(dict.fromkeys(config_parts[:3]))
                # 模式
                mode = row_mode or last_mode or sheet_default_mode
                last_mode = mode
                # 备注：同行其他文本（排除已用的）
                notes = [ot for ot in row_texts
                         if ot is not t and ot != gun and ot != name
                         and not CODE_RE.search(ot) and len(ot) >= 4]
                # 标签：从方案名/配置/备注中提取已知卖点词
                tag_src = (name or "") + " " + (config or "") + " " + " ".join(notes[:3])
                tags = []
                for w in TAG_WORDS:
                    if w in tag_src and w not in tags:
                        tags.append(w)
                entries.append({
                    "gun": gun or "未知枪械",
                    "mode": mode,
                    "name": name or "",
                    "code": code,
                    "price": price,
                    "config": config,
                    "tags": tags[:3],
                    "note": "；".join(notes[:3]),
                    "src": clean_cell(ws.title),
                })
    return entries, tips, dropped


def merge_dedup(entries):
    """按码去重，保留信息最全的（字段数最多）"""
    best = {}
    fields = ("gun", "name", "note", "price", "config")
    for e in entries:
        k = e["code"]
        old = best.get(k)
        if old is None:
            best[k] = e
        else:
            score_new = sum(bool(e[f]) for f in fields)
            score_old = sum(bool(old[f]) for f in fields)
            if score_new > score_old:
                best[k] = e
    return list(best.values())


def convert(xlsx_path, out_dir):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    all_entries, tips, dropped = [], [], 0
    for ws in wb.worksheets:
        default_mode = "黑潮爆破" if "黑潮" in ws.title else "烽火地带"
        es, ts, d = parse_sheet(ws, default_mode)
        all_entries += es
        tips += ts
        dropped += d
    codes = merge_dedup(all_entries)
    codes.sort(key=lambda e: (MODES.index(e["mode"]) if e["mode"] in MODES else 9, e["gun"]))
    data = {
        "updated": datetime.date.today().isoformat(),
        "source": "博主同步文档",
        "tips": list(dict.fromkeys(tips))[:3],
        "codes": codes,
    }
    with open(out_dir + "/codes.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(out_dir + "/codes_data.py", "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n# 自动生成于 %s\nDATA = " % data["updated"])
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    return data, dropped


if __name__ == "__main__":
    xlsx, outdir = sys.argv[1], sys.argv[2]
    data, dropped = convert(xlsx, outdir)
    modes = {}
    for c in data["codes"]:
        modes[c["mode"]] = modes.get(c["mode"], 0) + 1
    print("清洗完成:", len(data["codes"]), "条 | 丢弃广告", dropped, "行 | 提示", len(data["tips"]))
    print("按模式:", modes)
    print("样例:")
    for c in data["codes"][:5]:
        print("  [%s] %s | %s | %s | %s" % (c["mode"], c["gun"], c["name"], c["code"], c["note"][:30]))
