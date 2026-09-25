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
  URL=$(curl -s https://api.github.com/repos/MetaCubeX/mihomo/releases/latest | grep -oE 'https://[^"]*mihomo-linux-amd64-v[0-9.]+\.gz' | head -1)
  [ -z "$URL" ] && URL=$(curl -s https://api.github.com/repos/MetaCubeX/mihomo/releases/latest | grep -oE 'https://[^"]*linux-amd64[^"]*\.gz' | head -1)
  echo "mihomo url: $URL"
  curl -sL "$URL" -o /tmp/mihomo.gz
  gunzip -f /tmp/mihomo.gz && sudo install -m755 /tmp/mihomo /usr/local/bin/mihomo
  mihomo -v || true
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
       f"/storage/kv/namespaces/{os.environ['KV_NAMESPACE_ID']}/values/web2build:mihomo-config-gha")
req = urllib.request.Request(url, headers={"X-Auth-Email": os.environ["CLOUDFLARE_API_EMAIL"],
    "X-Auth-Key": os.environ["CLOUDFLARE_API_KEY"]})
cfg = yaml.safe_load(urllib.request.urlopen(req, timeout=30).read().decode())
cfg["mixed-port"] = 7890
cfg["bind-address"] = "127.0.0.1"
cfg["allow-lan"] = False
cfg.pop("external-controller", None)
yaml.safe_dump(cfg, open("/tmp/mihomo-gha.yaml","w"), allow_unicode=True)
PY
if [ "${USE_PROXY}" = "1" ]; then
echo "=== GHA direct exit ip ==="
  GHA_IP=$(curl -s --max-time 20 https://api.ipify.org)
  echo "GHA_IP=$GHA_IP"
  # 加白 GHA 出口 IP 到备用 trial 账号（第二/第三组，未绑本机 IP）
  python3 - "$GHA_IP" <<'PY' || echo "whitelist failed"
  import os, sys, json, urllib.request, yaml
  ip = sys.argv[1]
  # 备用账号 access_token 从 KV 读（web2build:trial-alt 预先上传）
  ACCOUNT=os.environ["CLOUDFLARE_ACCOUNT_ID"]; NS=os.environ["KV_NAMESPACE_ID"]
  KEY=os.environ["CLOUDFLARE_API_KEY"]; EMAIL=os.environ["CLOUDFLARE_API_EMAIL"]
  def kvget(k):
      url=f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/storage/kv/namespaces/{NS}/values/{k}"
      req=urllib.request.Request(url,headers={"X-Auth-Email":EMAIL,"X-Auth-Key":KEY})
      return urllib.request.urlopen(req,timeout=30).read()
  alt = json.loads(kvget("web2build:trial-alt").decode())
  tok, aid = alt["access_token"], alt["trial_account_id"]
  base=f"https://proxyscrape.com/v2/v4/account/{aid}/datacenter_shared/whitelist"
  req=urllib.request.Request(base+"?type=set&ip[]="+ip, headers={"Authorization":f"Bearer {tok}",
      "User-Agent":"Mozilla/5.0","Origin":"https://proxyscrape.com"})
  try:
      r=urllib.request.urlopen(req,timeout=25)
      print("whitelist", ip, r.status, r.read().decode()[:120])
  except Exception as e:
      print("whitelist err:", e)
  # 重写 config 只含备用账号节点
  cfg = yaml.safe_load(kvget("web2build:mihomo-config").decode())
  user = alt["proxy_username"]
  keep=[p for p in cfg["proxies"] if user in p.get("ws-opts",{}).get("path","")]
  print("alt-account nodes:", len(keep))
  cfg["proxies"]=keep
  for g in cfg.get("proxy-groups",[]):
      g["proxies"]=[p["name"] for p in keep]
      g["interval"]=60; g["lazy"]=False
  cfg["mixed-port"]=7890; cfg["bind-address"]="127.0.0.1"; cfg["allow-lan"]=False
  cfg.pop("external-controller",None)
  yaml.safe_dump(cfg, open("/tmp/mihomo-gha.yaml","w"), allow_unicode=True)
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
