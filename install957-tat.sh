#!/bin/bash
# 【V1.0.0.957】TAT 专用（root 权限执行，不需要 sudo）：图表每行加「看码/编辑/停用/删除」
cd /tmp && rm -rf d957 && mkdir d957 && cd d957
OK=0
for U in \
  "https://cdn.jsdelivr.net/gh/seven-shao/codex-deploy-945@main/server-957.tar.gz" \
  "https://ghproxy.net/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" \
  "https://gh-proxy.com/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" ; do
  echo "尝试: $U"
  if curl -fsSL --connect-timeout 10 --max-time 90 -o p.tar.gz "$U"; then
    if tar -tzf p.tar.gz >/dev/null 2>&1; then OK=1; echo "下载成功"; break; fi
  fi
done
[ "$OK" = "1" ] || { echo "!! 所有下载地址失败"; exit 1; }

echo "--- 解包 ---"
tar -xzf p.tar.gz
ls -l updates/sign-all.html _relay_patch.py

echo "--- 部署前端页面 ---"
cp -n /opt/codex-server/updates/sign-all.html /opt/codex-server/updates/sign-all.html.bak-957 2>/dev/null || true
cp updates/sign-all.html /opt/codex-server/updates/sign-all.html

echo "--- 给中转站打补丁（停用支持）---"
RELAY_PY=/opt/ai-relay/relay.py /usr/bin/python3 _relay_patch.py apply
/usr/bin/python3 -m py_compile /opt/ai-relay/relay.py && echo "relay.py 语法 OK"

echo "--- 重启服务 ---"
systemctl restart ai-relay
systemctl restart codex-api
sleep 3

echo "===== 自检 ====="
echo "ai-relay 状态: $(systemctl is-active ai-relay)"
echo "codex-api 状态: $(systemctl is-active codex-api)"
echo "中转站健康: $(curl -sk -m 8 https://124.221.2.81/relay/health)"
echo "授权健康: $(curl -sk -m 8 https://124.221.2.81/codex/healthz)"
echo "前端新标记数: $(grep -c 'V1.0.0.957' /opt/codex-server/updates/sign-all.html)"
echo "relay新接口数: $(grep -c 'admin/keys/set' /opt/ai-relay/relay.py)"
echo "===== 全部完成 ====="