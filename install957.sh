#!/bin/bash
# 【V1.0.0.957】服务器端部署：图表每行加「看码 / 编辑 / 停用 / 删除」4 个按钮
set -e
echo "===== 1/5 下载部署包 ====="
cd /tmp && rm -rf d957 && mkdir d957 && cd d957
OK=0
for U in \
  "https://cdn.jsdelivr.net/gh/seven-shao/codex-deploy-945@main/server-957.tar.gz" \
  "https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" \
  "https://ghproxy.net/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" ; do
  echo "  尝试: $U"
  if curl -fsSL --connect-timeout 10 --max-time 60 -o p.tar.gz "$U"; then
    if tar -tzf p.tar.gz >/dev/null 2>&1; then OK=1; echo "  下载成功"; break; fi
  fi
done
if [ "$OK" != "1" ]; then echo "!! 下载失败，请把这段结果告诉 AI"; exit 1; fi

echo "===== 2/5 解包 ====="
tar -xzf p.tar.gz
ls -l updates/sign-all.html _relay_patch.py

echo "===== 3/5 部署前端页面 ====="
sudo cp -n /opt/codex-server/updates/sign-all.html /opt/codex-server/updates/sign-all.html.bak-957 2>/dev/null || true
sudo cp updates/sign-all.html /opt/codex-server/updates/sign-all.html
echo "  已更新 sign-all.html"

echo "===== 4/5 给中转站打补丁（停用支持）====="
sudo env RELAY_PY=/opt/ai-relay/relay.py /usr/bin/python3 _relay_patch.py apply
sudo /usr/bin/python3 -m py_compile /opt/ai-relay/relay.py && echo "  relay.py 语法 OK"
sudo systemctl restart ai-relay
sudo systemctl restart codex-api
sleep 3

echo "===== 5/5 自检 ====="
echo -n "  ai-relay 状态: "; systemctl is-active ai-relay
echo -n "  codex-api 状态: "; systemctl is-active codex-api
echo -n "  中转站健康: "; curl -sk -m 8 https://124.221.2.81/relay/health; echo
echo -n "  授权健康: "; curl -sk -m 8 https://124.221.2.81/codex/healthz; echo
echo -n "  前端新标记数: "; grep -c "V1.0.0.957" /opt/codex-server/updates/sign-all.html || true
echo -n "  relay 新接口数: "; grep -c "admin/keys/set" /opt/ai-relay/relay.py || true
echo "===== 全部完成 ====="