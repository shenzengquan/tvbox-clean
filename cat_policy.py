# -*- coding: utf-8 -*-
"""分类级净化：把「整站违规」和「部分分类违规」分开处理。

用户规则
--------
1. 接口整体违禁  → 整个接口删除（sites 条目整体移除）
2. 某接口只有部分 contents/categories 违禁 → 只删对应条目

落地方式
--------
判定来源只有两种，分别对应两个粒度：

  粒度 A：整站违规（drops 列表）
      实测这个采集源本身就是色情站（分类表里绝大多数是色情分类，
      或用色情专属分类且该分类真有片）。
      → 整个 sites 条目删除。

  粒度 B：分类违规（cat_drops）
      采集源本身是正经影视站，但分类表里夹着少数色情分类
      （如正经源里有个「里番动漫」分类，里面真有片）。
      → 站点保留，把这个分类名从 categories 字段里剔除，
        并在 ext 里标注，让客户端不再展示该分类。

注意：早期版本把「出现色情分类名」直接当整站违规，误杀了
索尼/猫眼/百度/iKun 等正经源 —— 它们的色情分类是空壳。
所以粒度 B 必须建立在「该分类里确实有片且片名露骨」的证据上。

本模块只负责读写分类清单，不负责网络探测（见 probe_sites.py）。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 分类词表
# 色情专属分类名。故意不含「伦理片」「写真」「三级」「少女」「网红」——
# 这些在正经影视站里是正常分类名，单独出现不能定罪。
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

# 片名露骨词（内容级证据，用于确认分类里真的有违规片）
NAME_ADULT = [
    "爆操", "内射", "狂干", "沦陷", "淫乱", "肉便器", "失禁", "中出",
    "巨乳", "爆乳", "美乳", "肉棒", "骚穴", "抽插", "口交", "肛交",
    "自慰", "淫荡", "淫水", "绝顶", "絶顶", "狂插", "活塞", "群交",
    "乱伦", "女优", "痴女", "里番", "萝莉", "幼女", "强奸", "调教",
    "肉便", "抽送", "颜射", "乳交", "媚药", "拘束", "淫叫", "呻吟",
]

# 正经分类名（用于判断站点主体是否正常）
CAT_SAFE = [
    "电影", "电视剧", "连续剧", "综艺", "纪录片", "动作片", "喜剧片",
    "爱情片", "科幻片", "恐怖片", "剧情片", "战争片", "国产剧",
    "港台剧", "日韩剧", "欧美剧", "海外剧", "动漫", "动画", "短剧",
    "体育", "少儿", "预告片", "解说",
]

# 允许一个色情分类留在站点里的上限（超过就认为站点主体已违规）
MAX_BAD_CAT_KEEP = 2


def hit(text, words):
    """命中判定。<=2 字符的 ASCII 词要求词边界，避免 av 命中 juice。"""
    low = str(text or "").lower()
    for w in words:
        wl = w.lower()
        if len(wl) <= 2 and re.fullmatch(r"[a-z0-9]+", wl):
            if re.search(r"(?<![a-z])" + re.escape(wl) + r"(?![a-z])", low):
                return w
        elif wl in low:
            return w
    return None


def classify(cats, sample_names=(), cat_probe=None):
    """给一个采集源分类，返回 (粒度, 说明)。

    参数
    ----
    cats         : 全站分类名列表
    sample_names : 分类列表接口返回的样片名
    cat_probe    : {分类名: {"n": 片数, "names": [片名...]}}
                   实测该分类里到底有什么。没有证据的分类一律不判违规。

    为什么必须有 cat_probe
    ----------------------
    正经采源（索尼/猫眼/百度/iKun）的分类表里挂着「港台三级」「里番动漫」
    这类名字，但**分类是空的**，或者客户端点进去会回落到别的分类。
    只看名字就删，会把 40 多个正常站误判。所以要求"分类里真有片"。

    返回:
        ("site", 理由)     整个接口违规，删站点
        ("cats", [分类...]) 只有这些分类违规，删分类
        (None, "")         正常
    """
    cats = [c for c in cats if str(c).strip()]
    if not cats:
        return None, ""

    cat_probe = cat_probe or {}
    bad = [c for c in cats if hit(c, CAT_ADULT_EXACT)]
    safe = [c for c in cats if hit(c, CAT_SAFE)]
    bad_ratio = len(bad) / len(cats)

    def has_content(c):
        """该色情分类里是否真有片。"""
        p = cat_probe.get(c)
        return bool(p and p.get("n"))

    def is_dirty(c):
        """该色情分类里是否真有露骨片。"""
        p = cat_probe.get(c)
        if not p or not p.get("n"):
            return False
        nm = " ".join(p.get("names") or []).lower()
        return bool(hit(nm, NAME_ADULT))

    dirty_cats = [c for c in bad if is_dirty(c)]
    content_cats = [c for c in bad if has_content(c)]

    # ---- 粒度 A：整个接口违规
    # 1) 色情分类占比过高（无论有没有实证，占比过半就是色情源）
    if bad_ratio >= 0.55 and len(bad) >= 3:
        return "site", f"色情分类占比 {bad_ratio:.0%}（{len(bad)}/{len(cats)}）"
    # 2) 一个正常分类都没有，且至少 2 个色情分类有内容
    if len(content_cats) >= 2 and not safe:
        return "site", f"无正常分类且 {len(content_cats)} 个色情分类有内容"
    # 3) 片名层面：样片露骨比例很高
    names = [n for n in sample_names if str(n).strip()]
    if names:
        dirty = sum(1 for n in names if hit(n, NAME_ADULT))
        if dirty >= 5 and dirty / len(names) >= 0.20 and not safe:
            return "site", f"样片露骨 {dirty}/{len(names)}"

    # ---- 粒度 B：只删真有内容的色情分类
    # 注意：判据是「分类里有片」，不是「片名够露骨」。
    # 实测索尼源的「港台三级」里是《喜爱夜蒲》、《西方伦理》里是
    # 《美人鱼2026》《VMX前戏特辑》，片名不一定命中露骨词表，
    # 但分类本身已经是明确的成人内容入口，必须删。
    # 早期版本要求"片名露骨"才删，结果索尼/OK/牛牛/无尽等 30+ 个站点的
    # 港台三级、韩国伦理、两性课堂 全部残留 —— 属于漏网，不是保守。
    if content_cats:
        return "cats", content_cats

    # 只有名字、没有内容 → 不处理（空壳分类，正经站常见）
    return None, ""


def drop_cats_from_site(site, cats_to_drop, log=print):
    """从单个站点里剔除指定分类。

    categories 字段直接过滤；ext 若是字符串且含这些分类名，
    无法安全按分类粒度删除（ext 是爬虫配置脚本/JSON），
    这时宁可直接整站删除，避免"点进去还是色情"。
    """
    s = dict(site)
    dropped = []

    cs = s.get("categories")
    if isinstance(cs, list):
        cs = [c for c in cs if c not in cats_to_drop]
        s["categories"] = cs

    # ext 里带分类名的，无法手术式剔除 → 交由调用方决定整站删
    ext = s.get("ext")
    ext_txt = json.dumps(ext, ensure_ascii=False) if not isinstance(ext, str) else ext
    still = [c for c in cats_to_drop if c in str(ext_txt)]
    return s, still


def filter_ext_cats(ext, cats_to_drop):
    """把分类名从 ext 里过滤掉。

    ext 可能是：
      - JSON 字符串：{"分类url": "...", "分类": "1,2,3"}
      - dict（已解析）
      - 纯文本配置
    分类通常出现在 分类url / 分类 / type / 筛选 等键里，
    以中文逗号或 & 分隔。这里按"去掉该分类名 + 分隔符"的方式清理。

    返回 (新 ext, 仍残留的分类列表)。残留非空说明没法安全过滤，
    调用方应改为整站删除。
    """
    if ext is None:
        return ext, []

    if isinstance(ext, (dict, list)):
        txt = json.dumps(ext, ensure_ascii=False)
    else:
        txt = str(ext)

    orig = txt
    for c in cats_to_drop:
        # 去掉 "分类名" 以及紧跟的分隔符（中文/英文逗号、&、|）
        txt = re.sub(re.escape(c) + r"\s*[,，&|]\s*", "", txt)
        txt = re.sub(r"[,，&|]\s*" + re.escape(c) + r"(?=[,，&|\"'\]}]|$)", "", txt)

    still = [c for c in cats_to_drop if c in txt]
    if txt == orig:
        return ext, still
    if isinstance(ext, dict):
        try:
            return json.loads(txt), still
        except Exception:
            return ext, still
    return txt, still


def load_fingerprints(path=None):
    """读取已固化的色情采集器指纹（host+path）。"""
    path = path or os.path.join(HERE, "adult_probable.json")
    if not os.path.exists(path):
        return set()
    try:
        d = json.load(open(path, encoding="utf-8"))
        return set(d.get("fingerprints") or [])
    except Exception:
        return set()


def api_fingerprint(api):
    """返回 'host/path/' 指纹，识别同批次色情源的镜像入口。

    不按裸域名：实测同一台机器上正经源与色情源并存，按域名封会误杀。
    """
    import urllib.parse as up
    try:
        u = up.urlsplit(str(api or "").strip())
    except Exception:
        return None
    host = (u.hostname or "").lower()
    path = u.path or "/"
    path = re.sub(r"/at/(xml|json)/?$", "/", path)
    path = re.sub(r"/from/[^/]+/?$", "/", path)
    if not path.endswith("/"):
        path += "/"
    return f"{host}{path}" if host else None
