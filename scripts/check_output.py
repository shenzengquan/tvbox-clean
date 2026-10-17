#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前产物校验（GitHub Actions 用）。

单独成文件而不是写内联 heredoc：heredoc 里的 Python 缩进和 shell 缩进
容易冲突，且出错时报错信息很难定位。

校验项：
  1. CRLF 为零      —— 有 CRLF 时 TVBoxOSC 解析 JSON 直接失败
  2. JSON 合法
  3. 四个必备字段齐全
  4. 分类名无成人内容 —— 净化必须真的落到 categories 上。
     2026-10-17 踩过：判定改了但输出没改（白名单漏过滤），
     线上出现 39 个残留色情分类，靠这道校验兜住。
  5. 站名/直播名/解析名无硬词残留
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cat_policy

FIELDS = ("spider", "sites", "lives", "parses")

# 站名/解析名里绝不能出现的词（正常影视站也不会用这些）
HARD_NAME_WORDS = [
    "成人", "无码", "無碼", "里番", "裏番", "女优", "麻豆", "天美传媒",
    "果冻传媒", "精东", "杏吧", "色逼", "黄仓", "淫水", "黑料", "色南国",
    "湿妹", "抖阴", "涩涩", "探花", "约炮", "番号",
]


def main():
    try:
        raw = open("tvbox.json", "rb").read()
    except FileNotFoundError:
        print("未生成 tvbox.json", file=sys.stderr)
        return 1

    # CRLF 会让 TVBoxOSC 解析失败（踩过，文件头 {'\r\n'）
    if b"\r\n" in raw:
        print("产物含 CRLF，TVBoxOSC 会拉取失败", file=sys.stderr)
        return 1

    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"JSON 解析失败: {e}", file=sys.stderr)
        return 1

    for k in FIELDS:
        if k not in d:
            print(f"缺少字段 {k}", file=sys.stderr)
            return 1

    problems = []

    # ---- 分类名检查 ----
    for s in d.get("sites", []):
        for c in (s.get("categories") or []):
            if cat_policy.hit(c, cat_policy.CAT_ADULT_EXACT):
                problems.append(f"分类残留: {str(s.get('name'))[:20]} -> {c}")

    # ---- 站名/直播名/解析名检查 ----
    for k in ("sites", "lives", "parses"):
        for x in d.get(k, []):
            nm = str(x.get("name", ""))
            for w in HARD_NAME_WORDS:
                if w in nm:
                    problems.append(f"{k}名残留: {nm[:24]} -> {w}")
                    break

    if problems:
        print(f"产物含违规内容 {len(problems)} 处：", file=sys.stderr)
        for p in problems[:20]:
            print(f"  ! {p}", file=sys.stderr)
        return 1

    print(f"OK: sites={len(d['sites'])} lives={len(d['lives'])} "
          f"parses={len(d['parses'])} 字节={len(raw)} | 分类/站名残留 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
