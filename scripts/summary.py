#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Actions 运行摘要（显示在 workflow 运行页顶部）。"""

import json
import os
import sys

STATE = ".pipeline_state.json"


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

    lines += [
        "",
        "**接口地址**",
        "```",
        f"https://cdn.jsdelivr.net/gh/{repo}@main/tvbox.json",
        f"https://cdn.jsdelivr.net/gh/{repo}@main/tvbox.min.json",
        "```",
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
