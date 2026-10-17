#!/usr/bin/env bash
# 刷新 jsDelivr 缓存。
# @main 这类分支引用默认缓存最长 12 小时，不刷的话客户端会拿到昨天的配置。
set -u

REPO="${GITHUB_REPOSITORY:-shenzengquan/tvbox-clean}"
fail=0

for f in tvbox.json tvbox.min.json index.html; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 \
    "https://purge.jsdelivr.net/gh/${REPO}@main/${f}" || echo "000")
  echo "purge ${f} -> ${code}"
  [ "$code" = "200" ] || fail=1
done

if [ "$fail" = "1" ]; then
  echo "部分文件刷新失败，等待 CDN 自动过期（最长 12 小时）"
fi
exit 0
