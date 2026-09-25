#!/usr/bin/env bash
# GHA runner 环境准备：Chrome + Xvfb + mihomo(127.0.0.1:7890) 复刻本机出口
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq xvfb >/dev/null
# Chrome
if ! command -v google-chrome >/dev/null; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  sudo apt-get install -y -qq /tmp/chrome.deb >/dev/null
fi
# mihomo
if ! command -v mihomo >/dev/null; then
  curl -sL https://github.com/MetaCubeX/mihomo/releases/latest/download/mihomo-linux-amd64-compatible.gz -o /tmp/mihomo.gz
  gunzip -f /tmp/mihomo.gz && sudo install -m755 /tmp/mihomo /usr/local/bin/mihomo
fi
python -m pip install -q patchright curl_cffi cryptography pyyaml
python -m patchright install chromium >/dev/null 2>&1 || true
# Xvfb
Xvfb :99 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
sleep 2
# 从 KV 拉 mihomo 配置，改写端口为 7890 并启动
python3 - <<'PY'
import os, urllib.request, yaml
url = (f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}"
       f"/storage/kv/namespaces/{os.environ['KV_NAMESPACE_ID']}/values/web2build:mihomo-config")
req = urllib.request.Request(url, headers={"X-Auth-Email": os.environ["CLOUDFLARE_API_EMAIL"],
    "X-Auth-Key": os.environ["CLOUDFLARE_API_KEY"]})
cfg = yaml.safe_load(urllib.request.urlopen(req, timeout=30).read().decode())
cfg["mixed-port"] = 7890
cfg["bind-address"] = "127.0.0.1"
cfg["allow-lan"] = False
cfg.pop("external-controller", None)
yaml.safe_dump(cfg, open("/tmp/mihomo-gha.yaml","w"), allow_unicode=True)
PY
mihomo -d /tmp -f /tmp/mihomo-gha.yaml >/tmp/mihomo.log 2>&1 &
sleep 6
echo "=== exit ip via 7890 ==="
curl -s -x http://127.0.0.1:7890 --max-time 20 https://api.ipify.org || echo "proxy not ready"
echo
