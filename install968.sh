#!/bin/bash
# 【V1.0.0.968】服务器端部署：签发控制台版面调整（把「客户与计费」整块搬到右上，与「管理口令」并排）
set -e
EXPECT_MD5=356e483e0ffee260e5cdb963387a9627
echo "===== 1/4 下载新版页面 ====="
cd /tmp && rm -rf d968 && mkdir d968 && cd d968
OK=0
for U in \
  "https://cdn.jsdelivr.net/gh/seven-shao/codex-deploy-945@main/sign-all.html" \
  "https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/sign-all.html" \
  "https://ghproxy.net/https://raw.githubusercontent.com/seven-shao/codex-deploy-945/main/sign-all.html" ; do
  echo "  尝试: $U"
  if curl -fsSL --connect-timeout 10 --max-time 60 -o sign-all.html "$U"; then OK=1; echo "  下载成功"; break; fi
done
if [ "$OK" != "1" ]; then echo "!! 下载失败，把这段结果告诉 AI"; exit 1; fi
echo -n "  下载到的文件校验值: "; md5sum sign-all.html | cut -d" " -f1
echo "===== 2/4 备份并替换 ====="
STAMP=$(date +%Y%m%d-%H%M%S)
sudo cp -n /opt/codex-server/updates/sign-all.html "/opt/codex-server/updates/sign-all.html.bak-$STAMP" || true
sudo cp sign-all.html /opt/codex-server/updates/sign-all.html
echo "  已替换（备份 sign-all.html.bak-$STAMP）"
echo "===== 3/4 校验 ====="
GOT=$(md5sum /opt/codex-server/updates/sign-all.html | cut -d" " -f1)
echo "  服务器上现在的校验值: $GOT"
echo "  期望的校验值        : $EXPECT_MD5"
if [ "$GOT" = "$EXPECT_MD5" ]; then echo "  ✅ 完全一致"; else echo "  ❌ 不一致，请告诉 AI"; exit 2; fi
echo "===== 4/4 自检 ====="
echo -n "  新版标记 968 数量: "; grep -c "V1.0.0.968" /opt/codex-server/updates/sign-all.html || true
echo -n "  线上页面字节数: "; wc -c < /opt/codex-server/updates/sign-all.html
echo -n "  页面可访问性: "; curl -sk -o /dev/null -w "HTTP=%{http_code}\n" https://124.221.2.81/updates/sign-all.html
echo "===== 全部完成 ====="