#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Actions 运行摘要（显示在 workflow 运行页顶部）。"""

import hashlib
import json
import os
import sys

STATE = ".pipeline_state.json"


def fingerprint(path="tvbox.json"):
    """数据指纹，拼到 ?v= 后面绕过 jsDelivr 缓存。"""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:10]
    except Exception:
        return None


def main():
    repo = os.environ.get("REPO", "shenzengquan/tvbox-clean")
    lines = ["### TVBox 净化结果", ""]

    try:
        s = json.load(open(STATE, encoding="utf-8"))
        lines += [
            f"- 站点 **{s.get('sites_out', '?')}** 个",
            f"- 直播源 **{s.get('lives_out', '?')}** 个",
            f"- 解析器 **{s.get('parses_out', '?')}** 个",
            f"- 本轮删除 **{s.get('dropped', '?')}** 项",
            f"- 运行时间 {s.get('last_run', '?')}",
        ]
        err = s.get("error")
        if err:
            lines += ["", f"> ⚠️ {err}"]
    except Exception as e:
        lines.append(f"未读到状态文件：{e}")

    base = f"https://cdn.jsdelivr.net/gh/{repo}"
    fastly = f"https://fastly.jsdelivr.net/gh/{repo}"

    lines += [
        "",
        "**接口地址**（推荐，始终返回最新数据）",
        "```",
        f"{base}/tvbox.json",
        f"{base}/tvbox.min.json",
        "```",
        "",
        f"备用节点：`{fastly}/tvbox.json`",
        "",
        "> 地址里**不要加 `@main`**。jsDelivr 对显式分支引用做长缓存（最长 12 小时），",
        "> purge 也刷不掉；省略版本号的写法缓存短，实测每次都是最新。",
    ]

    out = "\n".join(lines) + "\n"
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
