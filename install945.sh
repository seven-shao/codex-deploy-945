#!/bin/bash
# CODEX 服务器端「授权登录逻辑改造」部署脚本（V1.0.0.945）
# 作用：把改好的 api/app.py 与 updates/sign-all.html 装到服务器并重启授权服务，随后自检。
set -e

echo "===== 1/4 下载部署包 ====="
cd /tmp
rm -rf d945 && mkdir d945 && cd d945
OK=0
for U in \
  "https://cdn.jsdelivr.net/gh/seven-shao/codex-deploy-945@main/server-auth945.tar.gz" \
  "https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-auth945.tar.gz" \
  "https://ghproxy.net/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-auth945.tar.gz" \
  "https://gh-proxy.com/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/server-auth945.tar.gz" ; do
  echo "  尝试: $U"
  if curl -fsSL --connect-timeout 10 --max-time 60 -o p.tar.gz "$U"; then
    if tar -tzf p.tar.gz >/dev/null 2>&1; then OK=1; echo "  下载成功: $U"; break; fi
  fi
done
if [ "$OK" != "1" ]; then echo "!! 所有下载地址都失败，请把这个结果告诉我"; exit 1; fi

echo "===== 2/4 解包并安装 ====="
tar -xzf p.tar.gz
ls -l api/app.py updates/sign-all.html
sudo cp -n /opt/codex-server/api/app.py /opt/codex-server/api/app.py.bak-945 2>/dev/null || true
sudo cp api/app.py /opt/codex-server/api/app.py
sudo cp updates/sign-all.html /opt/codex-server/updates/sign-all.html
echo "  文件已就位"

echo "===== 3/4 语法校验 + 重启服务 ====="
sudo /usr/bin/python3 -m py_compile /opt/codex-server/api/app.py && echo "  语法 OK"
sudo systemctl restart codex-api
sleep 3

echo "===== 4/4 自检 ====="
echo -n "  服务状态: "; systemctl is-active codex-api
echo -n "  健康检查: "; curl -s -m 8 http://127.0.0.1:18080/healthz; echo
echo -n "  名称查重接口: "; curl -s -m 8 -X POST http://127.0.0.1:18080/api/check-name -H "Content-Type: application/json" -d '{"name":"部署自检-可删"}'; echo
echo -n "  新代码标记数: "; grep -c "V1.0.0.945" /opt/codex-server/api/app.py || true
echo -n "  外网健康检查: "; curl -sk -m 8 https://124.221.2.81/codex/healthz; echo
echo "===== 全部完成 ====="
