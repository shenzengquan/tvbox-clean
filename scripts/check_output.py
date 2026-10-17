#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前产物校验（GitHub Actions 用）。

单独成文件而不是写内联 heredoc：heredoc 里的 Python 缩进和 shell 缩进
容易冲突，且出错时报错信息很难定位。
"""

import json
import sys

FIELDS = ("spider", "sites", "lives", "parses")


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

    print(f"OK: sites={len(d['sites'])} lives={len(d['lives'])} "
          f"parses={len(d['parses'])} 字节={len(raw)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
