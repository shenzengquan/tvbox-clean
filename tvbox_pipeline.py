#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvbox_pipeline.py — TVBox 接口自动净化 + 发布流水线

流程:
    1. 从多个镜像并发拉取上游最新配置（源项目每小时更新）
    2. 用 tvbox_clean 的规则清洗（删十八禁内容）
    3. 校验结果（残留检查 + 误杀检查 + 站点数量合理性）
    4. 发布到 GitHub 仓库，得到固定访问地址
    5. 写运行日志

用法:
    python tvbox_pipeline.py                  # 完整流程（拉取->清洗->发布）
    python tvbox_pipeline.py --dry-run        # 只拉取+清洗，不发布
    python tvbox_pipeline.py --local FILE     # 用本地文件替代拉取
    python tvbox_pipeline.py --no-publish     # 同 --dry-run

设计要点:
    - 多镜像竞速：谁先拉完用谁，避免单点故障
    - 内容校验：新配置站点数异常（比上次少很多）就中止，防止把坏数据发出去
    - 幂等：内容没变化就不提交，避免刷 commit
    - 失败不破坏线上版本：任何一步失败，线上还是上一次的好数据
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tvbox_clean as tc

CST = timezone(timedelta(hours=8))
STATE_FILE = os.path.join(HERE, ".pipeline_state.json")
LOG_FILE = os.path.join(HERE, "pipeline.log")

# 上游源（2026-10 实测）
UPSTREAM_RAW = "https://raw.githubusercontent.com/25175/tvyuan/master/tvbox_full.json"
MIRRORS = [
    UPSTREAM_RAW,
    "https://gh-proxy.com/" + UPSTREAM_RAW,
    "https://ghproxy.net/" + UPSTREAM_RAW,
    "https://ghfast.top/" + UPSTREAM_RAW,
    "https://tv.cc0cd.cc.cd",
]

# 发布地址（WorkBuddy 应用托管，实测响应 0.5s）
SITE_URL = "https://tvbox-clean.app.workbuddy.host"
OUT_PATH = "tvbox.json"

# 在 GitHub Actions 里运行时，脚本直接放在仓库根，输出也写仓库根
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
SITE_DIR = HERE if IN_ACTIONS else os.path.join(HERE, "site")
JSDELIVR_BASE = "https://cdn.jsdelivr.net/gh/shenzengquan/tvbox-clean@main"

# 校验阈值：新数据站点数不得低于上次的 80%
MIN_RATIO = 0.80


def log(msg, also_print=True):
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.write("\n")


def fetch_one(url, timeout=90):
    """拉取单个镜像。返回 (url, data, raw_len, elapsed)。"""
    t0 = time.time()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TVBoxClean/1.0",
        "Accept": "application/json, */*",
        "Accept-Encoding": "identity",
    })
    buf = b""
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
    text = tc._strip_noise(buf.decode("utf-8-sig", errors="replace"))
    data = json.loads(text)
    if "sites" not in data or not isinstance(data["sites"], list) or len(data["sites"]) < 100:
        raise ValueError(f"内容不像 TVBox 配置（sites={len(data.get('sites', []))}）")
    return url, data, len(buf), time.time() - t0


def fetch_best():
    """多镜像竞速，返回最先成功的结果。

    注意：不能用 with ThreadPoolExecutor，否则 with 退出时会等所有任务结束，
    竞速就退化成了「等最慢那个」。这里手动管理，拿到第一个就 shutdown(cancel_futures)。
    """
    log(f"开始并发拉取 {len(MIRRORS)} 个镜像…")
    ex = ThreadPoolExecutor(max_workers=len(MIRRORS))
    try:
        futs = {ex.submit(fetch_one, u): u for u in MIRRORS}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                url, data, n, el = fut.result()
            except Exception as e:
                log(f"  镜像失败 {u[:58]}… {type(e).__name__}: {str(e)[:60]}")
                continue
            log(f"  镜像成功 {u[:58]}… {n} 字节 / {el:.1f}s "
                f"({n/max(el,0.01)/1024:.0f} KB/s) sites={len(data['sites'])}")
            return url, data, n, el
        raise RuntimeError("所有镜像均失败")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def do_clean(data, blacklist_path):
    """清洗，返回 (cleaned_dict, stats)。复用 tvbox_clean 的规则。"""
    name_only, strong, weak = tc.load_blacklist(blacklist_path)

    sites = data.get("sites", [])
    lives = data.get("lives", [])

    kept, dropped = [], []
    for s in sites:
        src = (tc.sub_source(s) or "").lower()
        if "clun.top/fun.json" in src:
            dropped.append((s, "子接口 fun.json", "sub"))
            continue
        w, lvl, why = tc.match_site(s, name_only, strong, weak)
        if lvl:
            dropped.append((s, why, lvl))
        else:
            kept.append(s)

    kept_lives, dropped_lives = [], []
    for l in lives:
        w, lvl, why = tc.match_live(l, name_only, strong, weak)
        if lvl:
            dropped_lives.append((l, why, lvl))
        else:
            kept_lives.append(l)

    # 解析器也要清洗：parses 里出现过「萝莉 | chaloli.cn」这种漏网条目
    kept_parses, dropped_parses = [], []
    for p in data.get("parses", []):
        w, lvl, why = tc.match_parse(p, name_only, strong, weak)
        if lvl:
            dropped_parses.append((p, why, lvl))
        else:
            kept_parses.append(p)

    cleaned = {}
    for k, v in data.items():
        if k == "sites":
            cleaned["sites"] = [tc.clean_site_name(s) for s in kept]
        elif k == "lives":
            cleaned["lives"] = kept_lives
        elif k == "parses":
            seen, uniq = set(), []
            for p in kept_parses:
                sig = json.dumps(p, ensure_ascii=False, sort_keys=True)
                if sig not in seen:
                    seen.add(sig)
                    uniq.append(p)
            cleaned["parses"] = uniq
        else:
            cleaned[k] = v

    stats = {
        "sites_in": len(sites), "sites_out": len(kept),
        "lives_in": len(lives), "lives_out": len(kept_lives),
        "parses_in": len(data.get("parses", [])), "parses_out": len(kept_parses),
        "dropped_sites": len(dropped), "dropped_lives": len(dropped_lives),
        "dropped_parses": len(dropped_parses),
        "dropped_detail": [
            {"name": str(x.get("name"))[:60], "reason": why, "level": lvl}
            for x, why, lvl in dropped
        ] + [
            {"name": str(x.get("name"))[:60], "reason": why, "level": "live-" + lvl}
            for x, why, lvl in dropped_lives
        ] + [
            {"name": str(x.get("name"))[:60], "reason": why, "level": "parse-" + lvl}
            for x, why, lvl in dropped_parses
        ],
    }
    return cleaned, stats


def verify(cleaned, stats, prev_state):
    """发布前校验。返回 (ok, reasons)。"""
    problems = []

    # 1. 残留检查
    #    分两类：
    #    - name_residual: 绝不能出现在任何字段（这些词正常影视站也不会用）
    #    - name_only:     只检查站点名/解析器名/直播名。
    #      「萝莉」这类词在正常动漫站的 ext「剧情」筛选项里是标准分类
    #      （情感&科幻&冒险&萝莉&校园），全文扫描必然误报。
    hard_words = ["成人", "無碼", "无码", "里番", "裏番", "女优", "91麻豆",
                  "杏吧", "色逼", "黄仓", "淫水", "黑料", "色南国", "湿妹",
                  "抖阴", "涩涩", "麻豆", "天美传媒", "果冻传媒", "精东",
                  "pornhub", "missav", "sukebei", "javbus", "chaloli"]
    txt = json.dumps(cleaned, ensure_ascii=False).lower()
    hits = [w for w in hard_words if w.lower() in txt]

    nm_only_words = ["萝莉", "蘿莉", "loli", "lolita"]
    for k in ("sites", "lives", "parses"):
        for x in cleaned.get(k, []):
            n = str(x.get("name", "")).lower()
            for w in nm_only_words:
                if w.lower() in n:
                    hits.append(f"{w}(在{k}名称:{str(x.get('name'))[:20]})")

    # 解析器 URL 单独查（域名粘连写法）
    for p in cleaned.get("parses", []):
        u = str(p.get("url", "")).lower()
        if any(s in u for s in ("loli", "lolita", "chaloli")):
            hits.append(f"解析URL:{str(p.get('url'))[:40]}")

    if hits:
        problems.append(f"残留敏感词: {hits}")

    # 2. 数量合理性（防止上游给出残缺数据时误发布）
    prev_sites = (prev_state or {}).get("sites_out")
    if prev_sites and stats["sites_out"] < prev_sites * MIN_RATIO:
        problems.append(
            f"站点数骤降: 上次 {prev_sites} -> 本次 {stats['sites_out']}"
            f"（阈值 {MIN_RATIO:.0%}）"
        )

    # 3. 必备字段
    for k in ("spider", "sites"):
        if k not in cleaned:
            problems.append(f"缺少必备字段 {k}")

    # 4. 误杀抽检：这些正常关键词的站点应保留一定数量
    key_ok = {"哔哩": 5, "豆瓣": 3, "芒果": 1}
    for kw, least in key_ok.items():
        n = sum(1 for s in cleaned["sites"] if kw in str(s.get("name", "")))
        if n < least:
            problems.append(f"疑似过度清洗: '{kw}' 只剩 {n} 个（应≥{least}）")

    return (not problems), problems


def publish(cleaned):
    """写出 tvbox.json / tvbox.min.json。

    本地运行写 site/ 子目录（供自有托管发布）；
    Actions 里脚本在仓库根，直接写仓库根（jsDelivr 路径就是 /tvbox.json）。

    返回 (ok, msg, url)
    """
    site_dir = SITE_DIR
    os.makedirs(site_dir, exist_ok=True)
    out = os.path.join(site_dir, "tvbox.json")
    # 用 \n 换行 + 字节写入，避免 Windows 文本模式把 \n 转成 \r\n。
    # TVBoxOSC 某些版本解析 CRLF 的 JSON 会失败（实测踩过）。
    body = json.dumps(cleaned, ensure_ascii=False, indent=1).replace("\r\n", "\n")
    payload = body.encode("utf-8")   # 不加 BOM

    # 额外生成单行紧凑版：体积小 30%，对老客户端兼容性最好
    min_out = os.path.join(site_dir, "tvbox.min.json")
    min_payload = json.dumps(cleaned, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")

    # 两个文件都没变才算「内容未变化」
    def unchanged(path, data):
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                return f.read() == data
        except Exception:
            return False

    if unchanged(out, payload) and unchanged(min_out, min_payload):
        return True, "内容未变化", SITE_URL + "/tvbox.json"

    with open(out, "wb") as f:
        f.write(payload)
    with open(min_out, "wb") as f:
        f.write(min_payload)

    return True, (f"已写入 tvbox.json ({len(payload)}B) + "
                  f"tvbox.min.json ({len(min_payload)}B)"), SITE_URL + "/tvbox.json"


def gh_urls_for(repo="shenzengquan/tvbox-clean", branch="main", ver=None):
    """返回各访问地址。

    ⚠️ 地址里刻意不写 @main：
    jsDelivr 对「显式分支引用」（@main）做长缓存（最长 12 小时），
    purge 也刷不掉 —— 实测仓库已是新版 396847 字节，@main 连续 6 次
    10 分钟内都仍返回旧版 407695。
    而「省略版本号」写法会解析到默认分支且缓存短：
      200/396847  https://cdn.jsdelivr.net/gh/user/repo/tvbox.json      ← 最新
      200/407695  https://cdn.jsdelivr.net/gh/user/repo@main/tvbox.json ← 旧
    fastly.jsdelivr.net 节点同样返回最新，可作备用。

    注意：不要加 ?v=<指纹> 之类的 query 参数试图穿透缓存 ——
    jsDelivr 在分支引用下不把 query 计入缓存键，实测加与不加返回同一份旧内容。
    """
    base = f"https://cdn.jsdelivr.net/gh/{repo}"
    fastly = f"https://fastly.jsdelivr.net/gh/{repo}"
    return {
        "jsdelivr": base + "/tvbox.json",
        "jsdelivr_min": base + "/tvbox.min.json",
        "fastly": fastly + "/tvbox.json",
        "jsdelivr_branch": f"{base}@{branch}/tvbox.json",
        "raw": f"https://raw.githubusercontent.com/{repo}/{branch}/tvbox.json",
        "repo": f"https://github.com/{repo}",
    }


def data_fingerprint(path):
    """tvbox.json 内容的短指纹，用于拼 ?v= 绕过 CDN 缓存。"""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:10]
    except Exception:
        return None


def push_github(cleaned):
    """推送清洗结果。

    两种运行环境：
    - 本地：走 tvbox_publish（Git Data API），因为本机 git push 不通
    - GitHub Actions：环境里有 GITHUB_ACTIONS=true，直接用 git 命令提交，
      因为 runner 本身就在 GitHub 内网，git push 毫无问题，且不需要 PAT
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return push_via_git(cleaned)

    try:
        import tvbox_publish as tp
    except Exception as e:
        log(f"GitHub 推送跳过（模块加载失败）: {e}")
        return None

    token = tp.load_token()
    if not token:
        log("GitHub 推送跳过：未找到 token"
            f"（设 GITHUB_TOKEN 或写入 {tp.TOKEN_FILE}）")
        return None

    t0 = time.time()
    try:
        ok, msg, urls = tp.publish_to_github(os.path.join(HERE, "site"), token)
    except Exception as e:
        log(f"GitHub 推送异常: {e}")
        return None

    log(f"GitHub: [{time.time()-t0:.1f}s] {'OK' if ok else '失败'} - {msg}")
    return urls if ok else None


def push_via_git(cleaned):
    """GitHub Actions 环境下的提交方式：本地 git 仓库直接 commit + push。"""
    import subprocess

    def run(*cmd, check=True):
        """执行 git 命令。

        注意：cmd 的每个元素都是独立的 argv，绝不能把 "git commit -m 带空格的
        消息" 拼成一个字符串传进来 —— subprocess 不会帮你二次切分，
        结果就是 -m 只吃到了第一个词，commit 静默失败。
        """
        r = subprocess.run(list(cmd), cwd=HERE, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(
                f"{' '.join(cmd)} 失败: {(r.stderr or r.stdout)[:300]}")
        return r.stdout.strip()

    try:
        run("git", "config", "user.name", "github-actions[bot]")
        run("git", "config", "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com")
        # 只 add 确定存在的文件：report.txt 等本地辅助文件不在仓库里，
        # 直接写进 pathspec 会让 git 报 "did not match any files" 而整体失败
        # 用什么触发提交：只看真正的数据文件。
        # - index.html 在 Actions 里不会被重新生成（它只是部署包里的静态页），
        #   拿它做变更判定会永远显示"有变化"
        # - .pipeline_state.json 每次都写新的 last_run，必然变化，
        #   拿它判定等于每天都在提交
        # 结论：只拿 tvbox.json / tvbox.min.json 当判定依据，
        # 状态文件只是「顺路一起提交」，不参与判定。
        data_files = ["tvbox.json", "tvbox.min.json"]
        existing = [f for f in data_files if os.path.exists(os.path.join(HERE, f))]
        run("git", "add", "-A", *existing, ".pipeline_state.json")
        staged = run("git", "diff", "--cached", "--name-only", *existing)
        if not staged:
            log("GitHub: 数据未变化，跳过提交")
            return gh_urls_for()
        log(f"GitHub: 数据有变化 {staged.split()}，提交中…")
        now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
        run("git", "commit", "-m", f"auto update: {now} [skip ci]")
        run("git", "push", "origin", "HEAD:main")
        sha = run("git", "rev-parse", "--short=10", "HEAD")
        log(f"GitHub: OK - 已推送 commit {sha}")
        return gh_urls_for()
    except Exception as e:
        log(f"GitHub: 失败 - {e}")
        # 让 workflow 步骤变红，而不是静默吞掉
        raise


def purge_jsdelivr():
    """刷新 jsDelivr 缓存（@main 分支引用默认缓存最长 12 小时）。"""
    ok = True
    for f in ("tvbox.json", "tvbox.min.json", "index.html"):
        url = f"{JSDELIVR_BASE}/{f}".replace(
            "cdn.jsdelivr.net/gh", "purge.jsdelivr.net/gh")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tvbox-clean"})
            with urllib.request.urlopen(req, timeout=25) as r:
                if r.status != 200:
                    ok = False
        except Exception:
            ok = False
    log(f"jsDelivr 缓存刷新: {'OK' if ok else '失败(等待自动过期)'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只清洗不发布")
    ap.add_argument("--no-publish", action="store_true", help="同 --dry-run")
    ap.add_argument("--no-github", action="store_true", help="只写本地，不推 GitHub")
    ap.add_argument("--local", help="用本地文件替代拉取")
    ap.add_argument("--blacklist", default=os.path.join(HERE, "blacklist.txt"))
    args = ap.parse_args()

    log("=" * 60)
    log("流水线启动")

    state = load_state()

    # --- 1. 取数据 ---
    if args.local:
        log(f"数据来源: 本地文件 {args.local}")
        with open(args.local, encoding="utf-8-sig") as f:
            data = json.load(f)
        src_url, nbytes, elapsed = args.local, os.path.getsize(args.local), 0.0
    else:
        src_url, data, nbytes, elapsed = fetch_best()

    log(f"上游: {src_url}")
    log(f"原始: sites={len(data.get('sites', []))} "
        f"lives={len(data.get('lives', []))} parses={len(data.get('parses', []))}")

    # --- 2. 清洗 ---
    cleaned, stats = do_clean(data, args.blacklist)
    log(f"清洗: 站点 {stats['sites_in']} -> {stats['sites_out']} "
        f"(删 {stats['dropped_sites']}) | "
        f"直播 {stats['lives_in']} -> {stats['lives_out']} "
        f"(删 {stats['dropped_lives']})")

    # --- 3. 校验 ---
    ok, problems = verify(cleaned, stats, state)
    if not ok:
        log("校验失败，中止发布：")
        for p in problems:
            log(f"  ! {p}")
        # 失败也保存一份供排查，但不发布
        with open(os.path.join(HERE, "failed_output.json"), "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=1)
        return 2
    log("校验通过")

    # 落一份本地副本
    local_out = os.path.join(HERE, "tvbox_clean.json")
    with open(local_out, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=1)
    log(f"本地副本: {local_out} ({os.path.getsize(local_out)} 字节)")

    if args.dry_run or args.no_publish:
        log("dry-run 模式，跳过发布")
        return 0

    # --- 4. 发布 ---
    pub_ok, pub_msg, url = publish(cleaned)
    if not pub_ok:
        log(f"发布失败: {pub_msg}")
        return 3
    log(f"发布: {pub_msg}")

    # --- 4b. 提交到 GitHub ---
    # Actions: git commit+push；本地: Git Data API
    gh_urls = None
    if not args.no_github:
        gh_urls = push_github(cleaned)
        # 刷新 CDN 放在最后：只有真的推了新内容才需要，
        # 且要在 commit 之后调，否则刷到的是旧版本
        if gh_urls and not IN_ACTIONS:
            purge_jsdelivr()

    # --- 5. 记状态 ---
    state.update({
        "last_run": datetime.now(CST).isoformat(timespec="seconds"),
        "source_url": src_url,
        "sites_out": stats["sites_out"],
        "lives_out": stats["lives_out"],
        "parses_out": stats["parses_out"],
        "dropped": (stats["dropped_sites"] + stats["dropped_lives"]
                    + stats["dropped_parses"]),
        "publish_url": url,
        "github": gh_urls,
    })
    save_state(state)

    log("=" * 60)
    log("可用地址：")
    if gh_urls:
        log(f"  jsDelivr  {gh_urls['jsdelivr']}")
        log(f"  jsDelivr  {gh_urls['jsdelivr_min']}  (体积小 30%)")
        log(f"  GitHub raw {gh_urls['raw']}")
    log(f"  自有托管   {url}")
    log("流水线完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
