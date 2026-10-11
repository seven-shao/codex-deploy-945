#!/bin/bash
# 【V1.0.0.957】TAT 专用（root 权限执行，不需要 sudo）
# 修复：0 余额客户登录被「额度锁」挡死 → 登录门禁不再卡额度，
#       客户可凭「授权码 + 客户名称 + 手机号」完成首次绑定；
#       额度只在真正调用模型时（/api/meter/precheck、/api/meter/charge）校验。
cd /tmp && rm -rf c957 && mkdir c957 && cd c957
OK=0
for U in \
  "https://cdn.jsdelivr.net/gh/seven-shao/codex-deploy-945@main/server-957.tar.gz" \
  "https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" \
  "https://ghproxy.net/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" \
  "https://gh-proxy.com/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-957.tar.gz" ; do
  echo "尝试: $U"
  if curl -fsSL --connect-timeout 10 --max-time 90 -o p.tar.gz "$U"; then
    if tar -tzf p.tar.gz >/dev/null 2>&1; then OK=1; echo "下载成功: $U"; break; fi
  fi
done
[ "$OK" = "1" ] || { echo "!! 所有下载地址均失败"; exit 1; }

echo "--- 解包 ---"
tar -xzf p.tar.gz
ls -l api/app.py

echo "--- 备份旧后端 ---"
cp -n /opt/codex-server/api/app.py /opt/codex-server/api/app.py.bak-957 2>/dev/null || true

echo "--- 覆盖后端 ---"
cp api/app.py /opt/codex-server/api/app.py
/usr/bin/python3 -m py_compile /opt/codex-server/api/app.py && echo "app.py 语法 OK"

echo "--- 重启授权服务 ---"
systemctl restart codex-api
sleep 3

echo "===== 自检 ====="
echo "codex-api 状态: $(systemctl is-active codex-api)"
echo "授权健康: $(curl -sk -m 8 https://124.221.2.81/codex/healthz)"
echo "额度锁修复标记数: $(grep -c 'V1.0.0.957' /opt/codex-server/api/app.py)"
echo "旧额度锁残留(应为0): $(grep -c 'balance = check_balance_lock' /opt/codex-server/api/app.py)"
echo "===== 全部完成 ====="
