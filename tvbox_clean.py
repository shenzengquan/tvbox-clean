#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvbox_clean.py — TVBox 接口净化工具

用法:
    python tvbox_clean.py <源接口URL或本地json> [选项]

选项:
    --report            只报告不输出（默认行为由 --apply 反转）
    --apply             真正生成干净配置
    --out FILE          输出文件名，默认 tvbox_clean.json
    --blacklist FILE    自定义黑名单词库，默认同目录 blacklist.txt
    --drop-sub URL      整段丢弃来源子接口（name 里含该 URL 的站点全删），可多次
    --keep-sub URL      即使命中子接口也保留（白名单优先），可多次
    --list-groups       只统计分组情况

设计原则:
    1. 宁可错杀，不漏一个 —— 命中即删，但每条都写进报告供人工复核
    2. 黑名单外置，随时可改，不改脚本
    3. 默认只报告，加 --apply 才写文件（防止手滑）
"""

import json
import os
import re
import sys
import argparse
import urllib.parse as up
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BL = os.path.join(HERE, "blacklist.txt")

# 这些字段里的文本会被送去匹配黑名单
# name     : 站点显示名（最可靠）
# group/categories/genre : 内置分类，能反映站点是否有专门分类
# ext      : 爬虫配置，常含 主页url / 分类 / 类型，是识别真成人站的关键
# key      : 弱字段，作者命名随意（曾见 "q女优嗯嗯(3)" 指向正常站），不参与强匹配
STRONG_FIELDS = ("name", "group", "categories", "genre", "indexs")
WEAK_FIELDS = ("ext", "key")


def load_blacklist(path):
    """读词库，返回三个列表: (name_only, strong, weak)。

    [nameonly] 只匹配站点显示名 —— 用于「情色/伦理」这类词：
               出现在普通影视站的筛选项里是正常的，出现在站名里才是问题
    [strong]   任意字段命中即判定违规
    [weak]     任意字段命中即列入待复核
    """
    name_only, strong, weak = [], [], []
    mode = strong
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low == "[nameonly]":
                mode = name_only
                continue
            if low == "[strong]":
                mode = strong
                continue
            if low == "[weak]":
                mode = weak
                continue
            if low == "[source]":
                mode = None
                continue
            if mode is not None:
                mode.append(line)
    return name_only, strong, weak


def fetch(src, timeout=240, retries=3):
    """拉取配置：支持 http(s) URL 或本地路径。返回 (data, raw_text)。

    该接口源站很慢（536KB 需 2 分钟+ 且经常中途断），所以用「按块读 + 断点续传」
    的方式累积下载，直到拿到完整 JSON 为止。
    """
    if not re.match(r"^https?://", src, re.I):
        with open(src, encoding="utf-8-sig") as f:
            text = f.read().strip()
        return json.loads(_strip_noise(text)), text

    import urllib.request
    import time

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 9) TVBox/1.0",
        "Accept": "application/json, */*",
        "Accept-Encoding": "identity",
    }

    buf = b""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(src, headers=headers)
            # 断点续传：已有部分则从该位置继续
            if buf:
                req.add_header("Range", f"bytes={len(buf)}-")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    buf += chunk
            text = buf.decode("utf-8-sig", errors="replace").strip()
            if text.endswith("}") or text.endswith("]"):
                break
        except Exception as e:
            print(f"[!] 第 {attempt} 次拉取中断（已收 {len(buf)} 字节）: {type(e).__name__}")
            if attempt == retries:
                raise
            time.sleep(2)

    text = _strip_noise(text)
    return json.loads(text), text


def _strip_noise(text):
    """容忍 JSONP 包裹 / BOM / 前后垃圾字符。"""
    text = text.strip()
    if text and text[0] not in "{[":
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            text = text[i:j + 1]
    return text


def _join(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(str(i) for i in v)
    if isinstance(v, dict):
        return " ".join(f"{k} {_join(x)}" for k, x in v.items())
    return str(v)


def field_text(site, fields):
    return " ".join(_join(site.get(k)) for k in fields)


def _is_ascii_word(w):
    return bool(re.fullmatch(r"[A-Za-z0-9+_.-]+", w or ""))


def _hit(words, low, base64_like=False):
    """在文本里找命中词。

    纯 ASCII 词加词边界，避免在 base64 / URL / 哈希串里误匹配
    （实测 'FC2' 'Sex' 会命中一大段 base64 里的偶然子串）。
    中文词直接子串匹配。
    """
    for w in words:
        wl = w.lower()
        if _is_ascii_word(w):
            # 边界用 [^a-z0-9] 形式判断，允许 hyphen 结尾
            if re.search(r"(?<![a-z0-9])" + re.escape(wl) + r"(?![a-z0-9])", low):
                return w
        else:
            if wl in low:
                return w
    return None


def match_site(site, name_only, strong, weak):
    """返回 (命中说明, 命中等级, 命中字段组)。

    匹配顺序: 站名专有词 -> 抓取地址 -> 可靠字段(name/分类) -> ext/key。
    命中等级: strong=判定违规, weak=待复核
    """
    nm = _join(site.get("name")).lower()
    h = _hit(name_only, nm)
    if h:
        return h, "strong", f"站名:{h}"

    # 抓取地址（ext.主页url / api）—— type=3 蜘蛛站只靠这个能查出来
    h, why = match_site_url(site)
    if h:
        return h, "strong", why

    # ext 里常见超长 base64，ASCII 长串内容不做匹配
    for fields, tag in ((STRONG_FIELDS, "名称/分类"), (WEAK_FIELDS, "ext/key")):
        low = field_text(site, fields).lower()
        base64_like = bool(re.search(r"[a-z0-9+/]{120,}", low))
        h = _hit(strong, low, base64_like)
        if h:
            return h, "strong", f"{tag}:{h}"
        h = _hit(weak, low, base64_like)
        if h:
            return h, "weak", f"{tag}:{h}"
    return None, None, None


def sub_source(site):
    """从 name 里提取来源子接口 URL（工具合并时写进 name 前缀的）。"""
    n = str(site.get("name", ""))
    m = re.search(r"https?://[^\s\]|]+\.json", n)
    return m.group(0) if m else None


def match_live(live, name_only, strong, weak):
    """直播源检测。直播只有 name 和 url 两个字段，规则同站点套用。

    额外规则: url 里出现 adult/18+/sex 等路径段直接判违规。
    """
    nm = _join(live.get("name")).lower()
    url = _join(live.get("url")).lower()

    # URL 路径里的硬信号
    for pat in ("adult", "18+", "/sex", "porn", "hsck", "mdsp", "dxzb", "deyzb",
                "omzb", "sbjh", "rwcr", "selive", "live18", "181818"):
        if pat in url:
            return pat, "strong", f"URL:{pat}"

    for w in name_only:
        if w.lower() in nm:
            return w, "strong", f"站名:{w}"
    for w in strong:
        if w.lower() in nm:
            return w, "strong", f"站名:{w}"
    for w in weak:
        if w.lower() in nm:
            return w, "weak", f"站名:{w}"
    return None, None, None


# 解析器 URL 专用硬信号。
# 不放词库的 [strong]，是因为这些 ASCII 词若进站点匹配会误伤正常的
# 动漫分类筛选（「情感&科幻&冒险&萝莉&校园」这类 ext 标签串）。
PARSE_URL_SIGNALS = ("loli", "lolita", "porn", "jav", "hentai", "avbo", "18av")

# 站点抓取地址专用信号（作用于 ext 里的 主页url / 站点，以及 api 本身）。
# 这些是域名/路径级证据，词库里放会误伤正常站，所以单独成表。
#   实测样本：
#     jav.sb                          → 春堂资源
#     777mmx.com/yazhouxingai         → 快活影视（亚洲性爱）
#     1av.shop                        → 流云影视
#     777170.xyz/%e6%97%a5%e9%9f%a9..  → 搜视影视（URL 编码的「日韩视频」）
SITE_URL_SIGNALS = (
    "jav", "porn", "hentai", "av18", "18av", "sex", "xingai", "yazhou",
    "loli", "sukebei", "missav", "javbus", "1024", "91pw", "8av", "9av",
    "/av/", "xyz/av", "1av.", "3av.", "5av.", "avshop", "avzy",
)
# URL 编码后的中文路径单独处理：decode 之后再查中文词
URL_DECODE_CN = ("日韩视频", "亚洲情色", "亚洲视频", "成人", "三级", "无码",
                 "伦理", "自拍", "偷拍", "萝莉", "巨乳", "制服", "丝袜",
                 "乱伦", "人妻", "熟女", "群交", "强奸", "调教")


def _url_text(site):
    """提取站点里"真正会被访问的 URL"，不是 ext 的全部文本。

    关键区分（踩过的坑）：
    - ext 里的 URL 是**抓取目标**，出现 jav.sb / yazhouxingai 就是硬证据
    - ext 里的「剧情」筛选项是**标签串**，形如
      情感&科幻&冒险&萝莉&校园 —— 6080电影、纽约影院、囧次元动漫
      这些正经站的这一串里都有「萝莉」，拿它判违规会误杀一片

    所以这里只抓 URL 形态的子串（含 host 的 http(s) 地址 + 路径），
    并把 URL 编码解一次，让 /%e6%97%a5%e9%9f%a9/ 这类路径现形。
    """
    parts = []
    api = _join(site.get("api"))
    ext = site.get("ext")
    for blob in (api, _join(ext)):
        # 只取 http(s)://... 直到空白或引号，这才是真实访问地址
        for m in re.finditer(r"https?://[^\s\"'\\<>|]+", str(blob)):
            parts.append(m.group(0))
    if not parts:
        return ""
    txt = " ".join(parts).lower()
    try:
        txt += " " + up.unquote(txt)
    except Exception:
        pass
    return txt


def match_site_url(site):
    """按站点实际抓取的 URL 判定。返回 (命中, 说明) 或 (None, None)。

    这是给 type=3（jar/py 蜘蛛）站点的第二道防线 ——
    词库只能看名字，而这类站名叫「🧿流云┃影视」完全正常，
    真身在 ext 的主页url 里：https://www.1av.shop/。

    只看 URL 路径/域名，不看 ext 里的筛选项标签串（见 _url_text 说明）。
    """
    txt = _url_text(site)
    if not txt:
        return None, None
    for w in SITE_URL_SIGNALS:
        if w in txt:
            return w, f"抓取地址:{w}"
    # 编码后的中文路径：只在 URL 里查，且只认路径段的词
    for w in URL_DECODE_CN:
        if w in txt:
            return w, f"抓取地址(编码):{w}"
    return None, None


def match_site_url(site):
    """按站点实际抓取地址判定。返回 (命中, 说明) 或 (None, None)。

    这是给 type=3（jar/py 蜘蛛）站点的第二道防线 ——
    词库只能看名字，而这类站名叫「🧿流云┃影视」完全正常，
    真身在 ext 的 主页url 里：https://www.1av.shop/。
    """
    txt = _url_text(site)
    if not txt:
        return None, None
    for w in SITE_URL_SIGNALS:
        if w in txt:
            return w, f"抓取地址:{w}"
    for w in URL_DECODE_CN:
        if w in txt:
            return w, f"抓取地址(编码):{w}"
    return None, None


def match_parse(p, name_only, strong, weak):
    """解析器（parses）检测。

    parses 条目结构为 {name, type, url, ext}：
      - name  是解析器显示名，等同站点 name，走站名专有词
      - url   是解析网关地址，域名里常直接带违规词（如 chaloli.cn）
      - ext   里是 flag 列表（优酷/爱奇艺...），纯白名单内容，不参与匹配

    注意 url 里的 ASCII 词不能加词边界：
    'chaloli.cn' 中 'loli' 前后都是字母/点，加边界会漏检。
    """
    nm = _join(p.get("name")).lower()
    url = _join(p.get("url")).lower()

    for w in name_only:
        if w.lower() in nm:
            return w, "strong", f"解析名:{w}"

    # url 用无边界子串匹配（域名粘连是常态）
    for w in PARSE_URL_SIGNALS:
        if w in url:
            return w, "strong", f"解析URL:{w}"
    for w in strong:
        if w.lower() in url:
            return w, "strong", f"解析URL:{w}"
    for w in strong:
        if w.lower() in nm:
            return w, "strong", f"解析名:{w}"
    for w in weak:
        if w.lower() in url or w.lower() in nm:
            return w, "weak", f"解析:{w}"
    return None, None, None


def clean_site_name(site):
    """把 name 里的 [延迟|来源] 前缀剥掉，输出更干净的显示名。"""
    s = dict(site)
    n = str(s.get("name", ""))
    n2 = re.sub(r"^\s*\[[^\]]*\]\s*", "", n).strip()
    if n2:
        s["name"] = n2
    return s


# ---------------------------------------------------------------- 分类级净化
def apply_probe_policy(data, probe, log=print):
    """按探测结果做两种粒度的净化。

    规则（用户明确要求）：
      1. 接口整体违禁  → 整个接口（sites 条目）删除
      2. 只有部分分类违禁 → 只删对应分类，站点保留

    两条独立的力量叠加：
      (a) 探测结果：分类里有片的色情分类 → 删
      (b) 配置硬编码：categories 里写着色情分类名 → 无论如何都删
          （探测失败/空壳/站点换了域名时，(a) 会漏，靠 (b) 兜底）
    """
    import cat_policy
    sites = data.get("sites") or []

    # ---- 索引：按 (key, api) 和 按 (host, path) 双路查找
    # 同一批色情源会换 key、换名字挂很多入口（OK资源/OK/🍏OK 指向同一 api），
    # 探测时有的入口超时、有的成功。若只按 (key, api) 精确匹配，
    # 超时那个入口就会被放行 —— 实测因此漏了 6 个（OK资源、无尽CC、
    # 鸭鸭资源、索尼-闪电 的色情分类仍在）。
    # 所以再按 api 指纹兜一次：同一台采集器，判定互相通用。
    idx = {}
    for p in probe or []:
        if p.get("ok") and p.get("verdict") in ("adult", "partial"):
            idx[(p.get("key"), str(p.get("api", "")).strip())] = p

    fp_idx = {}
    for p in idx.values():
        fp = cat_policy.api_fingerprint(p.get("api"))
        if not fp:
            continue
        cur = fp_idx.get(fp)
        # 同一指纹多条结果时，"整站违规"优先级最高
        if cur is None or p["verdict"] == "adult":
            fp_idx[fp] = p
        else:
            # 合并需要删的分类：镜像之间删的不一样时取并集
            a = set(cur.get("policy_reason") or []) \
                if isinstance(cur.get("policy_reason"), list) else set()
            b = set(p.get("policy_reason") or []) \
                if isinstance(p.get("policy_reason"), list) else set()
            if b - a:
                cur = dict(cur)
                cur["policy_reason"] = sorted(a | b)
                fp_idx[fp] = cur

    def lookup(s):
        p = idx.get((s.get("key"), str(s.get("api", "")).strip()))
        if p:
            return p
        fp = cat_policy.api_fingerprint(s.get("api"))
        return fp_idx.get(fp) if fp else None

    kept, dropped_site, dropped_cat = [], [], []
    for s in sites:
        p = lookup(s)

        if p and p["verdict"] == "adult":
            dropped_site.append((s, p.get("reason", "")))
            continue

        # 需要删的分类 = 探测判定的 + 配置里硬写的色情分类
        need = set()
        if p and p["verdict"] == "partial":
            need |= set(p.get("policy_reason") or [])
        cs = s.get("categories")
        if isinstance(cs, list):
            need |= {c for c in cs if cat_policy.hit(c, cat_policy.CAT_ADULT_EXACT)}
        # 同时也查 ext 里的分类键（部分 dpy 站把分类写在 ext.分类）
        if not need:
            kept.append(s)
            continue

        if not isinstance(cs, list) or not cs:
            allcats = (p.get("cats") if p else None) or []
            # 白名单必须过滤掉所有色情分类名，不能只删探测点名的那几个。
            # 实测教训：索尼源配置里没有 categories，白名单由探测分类生成，
            # 而探测这一轮只判了「日本伦理」有片（其余判成空壳），
            # 结果 港台三级/韩国伦理/西方伦理/两性课堂 顺着白名单漏了过去。
            safe_list = [c for c in allcats
                         if c not in need
                         and not cat_policy.hit(c, cat_policy.CAT_ADULT_EXACT)]
            if not allcats:
                # 探测没拿到分类，配置里也没有 → 无从手术，整站删
                dropped_site.append(
                    (s, f"含色情分类[{','.join(sorted(need)[:3])}]且无分类信息"))
                continue
            if len(safe_list) < 3:
                dropped_site.append(
                    (s, f"分类违规且剩余分类仅 {len(safe_list)} 个"))
                continue
            s2 = dict(s)
            s2["categories"] = safe_list
            dropped_cat.append((s, sorted(need), safe_list))
            kept.append(s2)
            continue

        new_cs = [c for c in cs if c not in need]
        removed = [c for c in cs if c in need]
        if not removed:
            kept.append(s)
            continue
        if len(new_cs) < 3:
            dropped_site.append((s, f"剔除后分类仅剩 {len(new_cs)} 个"))
            continue
        s2 = dict(s)
        s2["categories"] = new_cs
        dropped_cat.append((s, removed, new_cs))
        kept.append(s2)

    data["sites"] = kept
    return dropped_site, dropped_cat


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("source", help="源接口 URL 或本地 JSON 路径")
    ap.add_argument("--apply", action="store_true", help="真正生成输出文件")
    ap.add_argument("--out", default="tvbox_clean.json")
    ap.add_argument("--blacklist", default=DEFAULT_BL)
    ap.add_argument("--drop-sub", action="append", default=[])
    ap.add_argument("--keep-sub", action="append", default=[])
    ap.add_argument("--clean-names", action="store_true", help="剥掉 name 的 [延迟|来源] 前缀")
    ap.add_argument("--stats", action="store_true", help="只打印结构统计后退出")
    args = ap.parse_args()

    data, raw = fetch(args.source)
    sites = data.get("sites", [])
    print(f"[*] 源: {args.source}")
    print(f"[*] 站点 {len(sites)} | 直播 {len(data.get('lives', []))} "
          f"| 解析 {len(data.get('parses', []))} | 原始 {len(raw)} 字节")

    # 来源子接口统计
    subs = Counter()
    for s in sites:
        u = sub_source(s)
        subs[u or "(主配置/内联)"] += 1
    print("\n[*] 站点来源分布:")
    for k, v in subs.most_common():
        print(f"      {v:5d}  {k}")

    if args.stats:
        return

    name_only, strong, weak = load_blacklist(args.blacklist)
    print(f"\n[*] 词库: 站名专有词 {len(name_only)} 个, 违规词 {len(strong)} 个, 待复核词 {len(weak)} 个")
    print(f"    违规词: {', '.join(strong[:30])}{' ...' if len(strong) > 30 else ''}")

    drop_subs = [d.lower() for d in args.drop_sub]
    keep_subs = [k.lower() for k in args.keep_sub]

    kept, dropped = [], []
    for s in sites:
        src = (sub_source(s) or "").lower()

        # 白名单优先
        if any(k in src for k in keep_subs if k):
            kept.append(s)
            continue

        # 整段丢弃指定子接口
        if any(d in src for d in drop_subs if d):
            dropped.append((s, f"子接口 {src}", "sub"))
            continue

        w, lvl, why = match_site(s, name_only, strong, weak)
        if lvl == "strong":
            dropped.append((s, why, "strong"))
        elif lvl == "weak":
            dropped.append((s, why, "weak"))
        else:
            kept.append(s)

    # ---- 直播源清洗 ----
    lives = data.get("lives", [])
    kept_lives, dropped_lives = [], []
    for l in lives:
        w, lvl, why = match_live(l, name_only, strong, weak)
        if lvl:
            dropped_lives.append((l, why, lvl))
        else:
            kept_lives.append(l)

    # ---- 解析器清洗 ----
    # 早期版本漏掉了 parses，导致「萝莉 | chaloli.cn」这类解析器被原样带出
    parses = data.get("parses", [])
    kept_parses, dropped_parses = [], []
    for p in parses:
        w, lvl, why = match_parse(p, name_only, strong, weak)
        if lvl:
            dropped_parses.append((p, why, lvl))
        else:
            kept_parses.append(p)

    print("\n" + "=" * 70)
    print("丢弃清单 - 站点")
    print("=" * 70)
    for lvl, title in (("sub", "整段丢弃的子接口"),
                       ("strong", "命中违规词"),
                       ("weak", "命中待复核词（建议人工确认）")):
        rows = [(s, r) for s, r, l in dropped if l == lvl]
        if not rows:
            continue
        print(f"\n-- {title}: {len(rows)} 个 --")
        for s, r in rows:
            print(f"   [{r[:22]:<22}] {str(s.get('name'))[:60]}")

    if dropped_lives:
        print("\n" + "=" * 70)
        print("丢弃清单 - 直播源")
        print("=" * 70)
        for l, r, lvl in dropped_lives:
            print(f"   [{r[:18]:<18}] {str(l.get('name'))[:28]:<28} {str(l.get('url'))[:45]}")

    if dropped_parses:
        print("\n" + "=" * 70)
        print("丢弃清单 - 解析器")
        print("=" * 70)
        for p, r, lvl in dropped_parses:
            print(f"   [{r[:18]:<18}] {str(p.get('name'))[:20]:<20} {str(p.get('url'))[:55]}")

    print("\n" + "=" * 70)
    print(f"站点: 保留 {len(kept)} / 丢弃 {len(dropped)} / 原始 {len(sites)}")
    print(f"直播: 保留 {len(kept_lives)} / 丢弃 {len(dropped_lives)} / 原始 {len(lives)}")
    print(f"解析: 保留 {len(kept_parses)} / 丢弃 {len(dropped_parses)} / 原始 {len(parses)}")
    print("=" * 70)

    if not args.apply:
        print("\n[!] 当前为报告模式，未写文件。确认无误后加 --apply 生成配置。")
        return

    # 组装新配置，保留除 sites/lives/parses 外的所有字段
    out = OrderedDict()
    for k, v in data.items():
        if k == "sites":
            out["sites"] = [clean_site_name(s) if args.clean_names else s for s in kept]
        elif k == "lives":
            out["lives"] = kept_lives
        elif k == "parses":
            out["parses"] = kept_parses
        else:
            out[k] = v

    # parses 去重（清洗后仍可能有同名同址的重复项）
    if "parses" in out and isinstance(out["parses"], list):
        seen, uniq = set(), []
        for p in out["parses"]:
            sig = json.dumps(p, ensure_ascii=False, sort_keys=True)
            if sig not in seen:
                seen.add(sig)
                uniq.append(p)
        if len(uniq) != len(out["parses"]):
            print(f"[*] parses 去重: {len(out['parses'])} -> {len(uniq)}")
        out["parses"] = uniq

    # 用 \n 换行 + 字节写入：Windows 文本模式会把 \n 转成 \r\n，
    # 而 TVBoxOSC 某些版本解析 CRLF 的 JSON 会失败
    body = json.dumps(out, ensure_ascii=False, indent=1).replace("\r\n", "\n")
    with open(args.out, "wb") as f:
        f.write(body.encode("utf-8"))

    size = os.path.getsize(args.out)
    print(f"\n[+] 已生成: {args.out} ({size} 字节)")
    print(f"[+] 站点 {len(out.get('sites', []))} | 直播 {len(out.get('lives', []))} "
          f"| 解析 {len(out.get('parses', []))}")
    print("\n[!] 记得原始 spider 地址仍需保留。若担心 jar 内自带违规模块，"
          "见 README 的『spider 处理』一节。")


if __name__ == "__main__":
    main()
