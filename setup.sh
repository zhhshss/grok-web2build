#!/usr/bin/env bash
# GHA runner 环境准备：Chrome + Xvfb + 可选 mihomo(127.0.0.1:7890)
# USE_PROXY=1 时复刻本机 mihomo+vless 出口；否则 GHA 直连（干净美国 IP）。
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq xvfb >/dev/null
if ! command -v google-chrome >/dev/null; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  sudo apt-get install -y -qq /tmp/chrome.deb >/dev/null
fi
python -m pip install -q patchright curl_cffi cryptography pyyaml
python -m patchright install chromium >/dev/null 2>&1 || true
Xvfb :99 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
sleep 2

if [ "${USE_PROXY}" = "1" ]; then
  if ! command -v mihomo >/dev/null; then
    URL=$(curl -s https://api.github.com/repos/MetaCubeX/mihomo/releases/latest | grep -oE 'https://[^"]*linux-amd64[^"]*\.gz' | head -1)
    echo "mihomo url: $URL"
    curl -sL "$URL" -o /tmp/mihomo.gz
    gunzip -f /tmp/mihomo.gz && sudo install -m755 /tmp/mihomo /usr/local/bin/mihomo
  fi
  python3 - <<'PY'
import os, urllib.request, yaml
url = (f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}"
       f"/storage/kv/namespaces/{os.environ['KV_NAMESPACE_ID']}/values/web2build:mihomo-config-gha")
req = urllib.request.Request(url, headers={"X-Auth-Email": os.environ["CLOUDFLARE_API_EMAIL"],
    "X-Auth-Key": os.environ["CLOUDFLARE_API_KEY"]})
cfg = yaml.safe_load(urllib.request.urlopen(req, timeout=30).read().decode())
cfg["mixed-port"] = 7890
cfg["bind-address"] = "127.0.0.1"
cfg["allow-lan"] = False
cfg.pop("external-controller", None)
yaml.safe_dump(cfg, open("/tmp/mihomo-gha.yaml", "w"), allow_unicode=True)
PY
  mihomo -d /tmp -f /tmp/mihomo-gha.yaml >/tmp/mihomo.log 2>&1 &
  sleep 8
  echo "=== exit ip via 7890 ==="
  curl -s -x http://127.0.0.1:7890 --max-time 20 https://api.ipify.org || echo "proxy not ready"
  echo
else
  echo "=== USE_PROXY=0, GHA 直连 ==="
  curl -s --max-time 20 https://api.ipify.org; echo
fi
