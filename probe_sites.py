# -*- coding: utf-8 -*-
"""探测 TVBox 接口站点的真实分类与内容，揪出"名字正常但内容是色情"的伪装站。

背景
----
静态词库永远堵不住这类站。用户举报的「凤凰影视」：

    {"key": "fh资源", "name": "🧿凤凰┃影视",
     "api": "http://fhapi9.com/api.php/provide/vod/"}

名字里「凤凰」「影视」都是正常词。但它 ac=list 返回的分类是
伦理三级 / 制服丝袜 / 少女萝莉 / 群交淫乱 / 巨乳美乳 —— 纯色情站。

踩过的坑（重要，别再走回头路）
------------------------------
坑 1：把「伦理」「写真」「少女」「网红」当分类信号
      → 正经站也有「伦理片」「写真热舞」「网红」，误杀 40+ 个正常站。

坑 2：只要出现「里番动漫」「港台三级」「两性课堂」就判色情
      → 索尼 / 猫眼 / iKun / 百度 这些正经采集源，分类表里有这些名字，
        但**分类是空的或是错标**。实测猫眼源的「里番动漫」分类里
        返回的是《罪火焦点》《灵魂伴侣》这类科幻片（total=551，全正常）。
        也就是说：分类名可以造假，必须验证分类里到底有什么。

最终判定（三条独立路径，任一成立即删除）
----------------------------------------
A. 色情分类「实证」：分类名命中色情专属词，且该分类**确实有片**，
   再拉该分类的片名验证露骨程度。空壳分类不算。
B. 分类表色情化比例：强色情分类占比 >= 55% 且数量 >= 3。
   正经站即使有 5 个色情分类名，占比也远低于此。
C. 片名实证：分类列表返回的样片里，露骨词命中 >= 5 条，且
   命中条数占样片比例 >= 20%。

路径 A 的二次验证是关键：没有它就会误杀索尼/猫眼/百度。

另一层保险：同域传播
--------------------
同一批色情站经常换名字挂多个入口（「凤凰┃影视」「番号资源」「番外┃采集」
其实是同一个 fhapi9.com）。所以判定为色情后，把它的
**主机 + API 路径**记进 adult_probable.json，
后续遇到同名同域的镜像直接拦，不必再探测。
注意只按"主机 + 路径"匹配，不按裸域名 —— 同一台机器上可能同时放着
正经采集源和色情源（实测确实有），按裸域名封会误杀。

只读探测，不改仓库。输出 probe_result.json 与 adult_probable.json。
"""
import concurrent.futures as cf
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 15

# --------------------------------------------------------------- 词表
# 色情专属分类名。凡是会被正经影视站误用的（伦理片/写真/少女/网红/
# 伦理/三级/两性课堂/里番动漫），一律不单独作数，必须走"实证"路径。
CAT_ADULT_EXACT = [
    "伦理三级", "港台三级", "日本伦理", "韩国伦理", "西方伦理", "欧美伦理",
    "亚洲情色", "情色三级", "三级片", "三级电影",
    "日韩无码", "无码流出", "无码高清", "日本无码", "无码专区", "无码av",
    "有码专区", "无修正", "日本有码",
    "女优系列", "女优明星", "av明星", "av女优", "番号资源", "番号大全",
    "热门女优", "日韩av",
    "口交颜射", "群交淫乱", "强奸乱伦", "强奸迷奸", "重咸口味", "重口激情",
    "中出内射", "肛交专区", "颜射", "口交", "肛交", "乳交", "足交",
    "巨乳美乳", "巨乳专区", "爆乳", "童颜巨乳", "美腿丝袜", "制服丝袜",
    "黑丝诱惑", "丝袜美腿", "制服诱惑", "制服师生",
    "少女萝莉", "极骚萝莉", "萝莉少女", "萝莉专区", "幼齿",
    "国产自拍", "自拍偷拍", "偷拍自拍", "黑料网曝", "网曝黑料",
    "黑料不打烊", "主播网红", "国产主播", "国产传媒", "精品网红",
    "反差母狗", "母狗", "海角社区", "探花", "国产探花", "酒店探花",
    "野战车震", "野战户外", "淫乱学生妹", "校园春色", "嫖妓全过程",
    "成人动漫", "里番动漫", "禁漫", "h动漫",
    "成人视频", "成人专区", "成人电影", "成人头条", "色情", "av",
    "sm调教", "sm重味", "sm虐待", "调教专区", "两性课堂",
    "约炮", "炮友", "onlyfans", "swag",
    "麻豆传媒", "麻豆视频", "91制片厂", "天美传媒", "精东影业",
    "果冻传媒", "星空传媒", "乌鸦传媒", "皇家华人", "蜜桃传媒",
    "兔子先生", "杏吧原创", "国产厂牌", "国产视频", "国产大制作",
    "视频一区", "视频二区", "高清新av", "高清av",
]

# 强色情词（占比统计用，包含上面的相对宽松版）
CAT_STRONG = [
    "三级", "无码", "無碼", "有码", "女优", "av", "番号", "口交", "肛交",
    "颜射", "群交", "淫乱", "强奸", "乱伦", "巨乳", "爆乳", "美乳",
    "萝莉", "幼齿", "自拍", "偷拍", "黑料", "母狗", "里番", "禁漫",
    "成人", "情色", "色情", "调教", "sm", "约炮", "主播", "传媒",
    "两性课堂", "探花", "春色", "野战", "嫖妓", "海角",
]

# 片名露骨词（内容级证据）
NAME_ADULT = [
    "爆操", "内射", "狂干", "沦陷", "淫乱", "肉便器", "失禁", "中出",
    "巨乳", "爆乳", "美乳", "肉棒", "骚穴", "抽插", "口交", "肛交",
    "自慰", "淫荡", "淫水", "绝顶", "絶顶", "狂插", "活塞", "群交",
    "乱伦", "女优", "痴女", "里番", "萝莉", "幼女", "强奸", "调教",
    "肉便", "抽送", "颜射", "乳交", "媚药", "拘束", "淫叫", "呻吟",
]

# 正经影视分类名：出现说明这是正常采集源
CAT_SAFE = [
    "电影", "电视剧", "连续剧", "综艺", "纪录片", "动作片", "喜剧片",
    "爱情片", "科幻片", "恐怖片", "剧情片", "战争片", "国产剧",
    "港台剧", "日韩剧", "欧美剧", "海外剧", "动漫", "动画", "短剧",
    "体育", "少儿", "预告片", "解说", "曲艺",
]


def _s(v):
    return str(v or "").strip()


def _variants(api):
    """探测 URL 候选。

    实测踩过的坑：
    - api 路径里可能带中文（如 /18/人人影视.php），urllib 不转义会在
      http.client 里报 'ascii' codec can't encode —— 必须 quote。
    - 老式站点只认 /api/xml.php，标准 /api.php/provide/vod/ 会 404。
    """
    a = api.strip()
    if not a:
        return []
    outs = []
    base2 = re.sub(r"/at/(xml|json)/?", "/", a)
    base3 = re.sub(r"/from/[^/?]+/", "/", base2)
    # /api/xml.php → /api.php
    base4 = re.sub(r"/api/xml\.php$", "/api.php", base3)
    for b in (a, base2, base3, base4):
        if not b.endswith("/"):
            b += "/"
        for q in ("?ac=list", "?ac=videolist"):
            outs.append(b + q)
    seen, res = set(), []
    for u in outs:
        # 只转义非 ASCII 部分，保留 ://?=& 这些结构字符
        safe = urllib.parse.quote(u, safe=":/?=&%[]#@!$'()*+,;~")
        if safe not in seen:
            seen.add(safe)
            res.append(safe)
    return res


def _hit(text, words):
    """命中判定：<=2 字符的词要求词边界，避免 av 命中 juice 之类。"""
    low = text.lower()
    for w in words:
        wl = w.lower()
        if len(wl) <= 2:
            if re.search(r"(?<![a-z])" + re.escape(wl) + r"(?![a-z])", low):
                return w
        elif wl in low:
            return w
    return None


_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _get_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Linux; Android 12) tvbox"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
        raw = r.read(900 * 1024)
    return json.loads(raw.decode("utf-8", "replace"))


def probe_one(site):
    out = {
        "key": site.get("key"),
        "name": site.get("name"),
        "api": _s(site.get("api")),
        "type": site.get("type"),
        "ok": False,
        "err": None,
        "total": 0,
        "cats": [],
        "cat_src": None,
        "n_cats": 0,
        "exact_hits": [],
        "verified_hits": [],
        "strong_ratio": 0.0,
        "name_hits": [],
        "name_ratio": 0.0,
        "verdict": "unknown",
        "reason": "",
        "base": None,
    }
    if not out["api"] or out["api"].startswith(("./", "csp_")):
        out["err"] = "no-http-api"
        return out

    j, base = None, None
    for url in _variants(out["api"]):
        try:
            j = _get_json(url)
            if isinstance(j, dict) and (j.get("class") or j.get("list")):
                base = url.split("?")[0]
                break
        except Exception as e:
            out["err"] = type(e).__name__ + ":" + str(e)[:60]
            continue
    if base is None:
        return out

    out["ok"] = True
    out["base"] = base
    out["total"] = int(j.get("total") or 0)

    # 分类表：优先 class[]（含 type_id），其次样片 type_name
    cats, cat_ids = [], {}
    for c in (j.get("class") or []):
        n = _s(c.get("type_name"))
        if n:
            cats.append(n)
            if c.get("type_id") is not None:
                cat_ids[n] = c.get("type_id")
    if cats:
        out["cat_src"] = "class"
    else:
        seen = set()
        for it in (j.get("list") or []):
            n = _s(it.get("type_name"))
            if n and n not in seen:
                seen.add(n)
                cats.append(n)
                cat_ids[n] = it.get("type_id")
        if cats:
            out["cat_src"] = "sample"
    out["cats"] = cats
    out["n_cats"] = len(cats)

    out["exact_hits"] = [h for h in (_hit(c, CAT_ADULT_EXACT) for c in cats) if h]
    strong = [c for c in cats if _hit(c, CAT_STRONG)]
    out["strong_ratio"] = round(len(strong) / len(cats), 3) if cats else 0.0
    safe_cnt = sum(1 for c in cats if _hit(c, CAT_SAFE))

    # ---- 内容抽样：整体样片 + 若命中色情分类，再拉该分类里的片名实证
    sample_names = [_s(it.get("vod_name")) for it in (j.get("list") or [])]

    # 分类实证：每个命中的色情分类都实际拉一次，记录片数与片名。
    # 这是区分「空壳分类」和「真色情分类」的唯一可靠办法。
    cat_probe = {}
    if out["exact_hits"]:
        for cn in out["exact_hits"][:6]:
            tid = cat_ids.get(cn)
            if tid is None:
                continue
            try:
                jj = _get_json(f"{base}?ac=detail&t={tid}&pg=1")
                nm = [_s(x.get("vod_name")) for x in (jj.get("list") or [])]
                cat_probe[cn] = {"n": len(nm), "names": nm[:20]}
                if nm:
                    sample_names.extend(nm)
            except Exception:
                cat_probe[cn] = {"n": 0, "names": []}
    out["cat_probe"] = cat_probe

    verified = []
    for cn, p in cat_probe.items():
        if p["n"]:
            hh = [w for w in NAME_ADULT if w.lower() in " ".join(p["names"]).lower()]
            if hh:
                verified.append(f"{cn}({p['n']}片/{len(hh)}命中)")
    out["verified_hits"] = verified

    allnames = " ".join(sample_names).lower()
    out["name_hits"] = [w for w in NAME_ADULT if w.lower() in allnames]
    # 有多少条片名命中
    hit_cnt = sum(1 for nm in sample_names
                  if any(w.lower() in nm.lower() for w in NAME_ADULT))
    out["name_ratio"] = round(hit_cnt / len(sample_names), 3) if sample_names else 0.0

    # ---- 交给 cat_policy 做两种粒度的判定（整站删 / 只删分类）
    # 注意：probe_sites.py 只负责采集证据，判定规则集中在 cat_policy.py，
    # 避免两处规则漂移导致流水线行为不一致。
    try:
        import cat_policy
        gran, why = cat_policy.classify(cats, sample_names, cat_probe)
    except Exception:
        gran, why = None, ""
    out["granularity"] = gran
    out["policy_reason"] = why

    # ---- 兼容旧输出的 verdict 字段
    a = bool(verified) and safe_cnt < 12
    b = len(strong) >= 3 and out["strong_ratio"] >= 0.55
    c = hit_cnt >= 5 and out["name_ratio"] >= 0.20 and safe_cnt < 12

    if gran == "site" or a or b or c:
        out["verdict"] = "adult"
        rs = []
        if gran == "site":
            rs.append(f"整站:{why}")
        if a:
            rs.append(f"A实证{verified}")
        if b:
            rs.append(f"B占比{out['strong_ratio']:.0%}({len(strong)}/{len(cats)})")
        if c:
            rs.append(f"C片名{hit_cnt}/{len(sample_names)}")
        out["reason"] = " ".join(rs)
    elif gran == "cats":
        out["verdict"] = "partial"
        out["reason"] = f"部分分类违规:{why}"
    elif out["exact_hits"]:
        out["verdict"] = "suspect"
        out["reason"] = (f"有色情分类名但未实证(空壳): {out['exact_hits'][:4]}"
                         f" 片名{hit_cnt}/{len(sample_names)}")
    else:
        out["verdict"] = "clean"
    return out


def api_fingerprint(api):
    """返回 (host, path) 指纹，用于识别同一批次色情源的镜像入口。

    只取 host + 去参数后的 path，不取裸域名：同一台机器上可能正经源和
    色情源并存，按裸域名封会误杀。
    """
    try:
        u = urllib.parse.urlsplit(api.strip())
    except Exception:
        return None
    host = (u.hostname or "").lower()
    path = u.path or "/"
    # 归一化：把结尾的 at/xml、from/xxx 之类去掉，指向同一个采集器
    path = re.sub(r"/at/(xml|json)/?$", "/", path)
    path = re.sub(r"/from/[^/]+/?$", "/", path)
    if not path.endswith("/"):
        path += "/"
    return (host, path) if host else None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "tvbox_raw.json")
    d = json.load(open(src, encoding="utf-8"))
    sites = d.get("sites") or []
    targets = [s for s in sites
               if _s(s.get("api")).startswith("http")
               and s.get("type") in (0, 1, 4)]
    print(f"源: {src}")
    print(f"sites {len(sites)}，可探测 {len(targets)}")

    results = []
    with cf.ThreadPoolExecutor(max_workers=40) as ex:
        futs = [ex.submit(probe_one, s) for s in targets]
        for i, f in enumerate(cf.as_completed(futs), 1):
            results.append(f.result())
            if i % 150 == 0:
                print(f"  {i}/{len(targets)}", flush=True)

    # ---- 同域传播：被判定色情的采集器，它的其他镜像也一并标色情
    bad_fps = {api_fingerprint(r["api"]) for r in results
               if r["verdict"] == "adult"}
    bad_fps.discard(None)
    promoted = 0
    for r in results:
        if r["verdict"] in ("adult",):
            continue
        fp = api_fingerprint(r["api"])
        if fp and fp in bad_fps:
            r["verdict"] = "adult"
            r["reason"] = (r.get("reason") or "") + f" [同域传播 {fp[0]}]"
            promoted += 1
    if promoted:
        print(f"\n同域传播补判 {promoted} 个")

    ok = [r for r in results if r["ok"]]
    adult = [r for r in results if r["verdict"] == "adult"]
    susp = [r for r in results if r["verdict"] == "suspect"]
    print(f"\n成功 {len(ok)}/{len(results)} | 色情 {len(adult)} | 可疑 {len(susp)}")

    print("\n===== 判定色情（删除）=====")
    for r in sorted(adult, key=lambda x: -(x["total"] or 0)):
        print(f"  {r['name']}   key={r['key']}  total={r['total']}")
        print(f"      {r['reason'][:150]}")

    print("\n===== 可疑（空壳分类，保留）=====")
    for r in sorted(susp, key=lambda x: -(x["total"] or 0)):
        print(f"  {r['name']}   key={r['key']}  total={r['total']}")
        print(f"      {r['reason'][:120]}")

    p = os.path.join(HERE, "probe_result.json")
    json.dump(results, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- 固化色情采集器指纹，供流水线复用（免去每次重新探测）
    fps = sorted({f"{h}{pa}" for h, pa in bad_fps})
    fp_path = os.path.join(HERE, "adult_probable.json")
    json.dump({"fingerprints": fps, "updated_from": os.path.basename(src)},
              open(fp_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n明细 → {p}")
    print(f"色情采集器指纹 {len(fps)} 条 → {fp_path}")


if __name__ == "__main__":
    main()
