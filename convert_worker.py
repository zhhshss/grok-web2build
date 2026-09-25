#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GHA worker：Web→Build device flow 转换。

复刻本机出口：mihomo(127.0.0.1:7890) + vless trial 链路（trial 白名单绑定的
正是该出口指纹）。patchright 非 headless Chrome 过 CF JSD 完成 approve，
curl_cffi poll token。结果（access/refresh/expires）XOR 加密写 KV。
"""
import asyncio, base64, hashlib, json, os, sys, time, secrets, urllib.request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROXY_URL = os.environ.get("CONVERT_PROXY", "http://127.0.0.1:7890")
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"

XOR_KEY = base64.b64decode(os.environ["XOR_KEY_B64"])
CF_ACCOUNT = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CF_NS = os.environ["KV_NAMESPACE_ID"]
CF_KEY = os.environ["CLOUDFLARE_API_KEY"]
CF_EMAIL = os.environ["CLOUDFLARE_API_EMAIL"]

def xor(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data))

def kv_put(key, raw: bytes):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/storage/kv/namespaces/{CF_NS}/values/{key}"
    req = urllib.request.Request(url, data=raw, method="PUT",
        headers={"X-Auth-Email": CF_EMAIL, "X-Auth-Key": CF_KEY, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status

async def approve_via_browser(sso, user_code):
    from patchright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=False,
            proxy={"server": PROXY_URL},
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"])
        try:
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                locale="zh-CN", viewport={"width": 1280, "height": 800})
            await ctx.add_cookies([
                {"name": "sso", "value": sso, "domain": ".x.ai", "path": "/", "secure": True, "httpOnly": True},
                {"name": "sso-rw", "value": sso, "domain": ".x.ai", "path": "/", "secure": True, "httpOnly": True}])
            page = await ctx.new_page()
            await page.goto("https://accounts.x.ai/oauth2/device", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            otp = page.locator("input[data-input-otp]")
            if await otp.count() == 0:
                print("  [browser] no otp", flush=True); return False
            await otp.click()
            for ch in user_code.replace("-", ""):
                await page.keyboard.type(ch, delay=100)
            await page.wait_for_timeout(1200)
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
                    await page.locator("button[type=submit]:has-text('继续')").click()
            except Exception as e:
                print(f"  [browser] nav: {str(e)[:80]}", flush=True)
            await page.wait_for_timeout(4000)
            print(f"  [browser] consent: {page.url} | {await page.title()}", flush=True)
            if "consent" not in page.url:
                return False
            allow = page.locator("button[type=submit]:has-text('允许'), button[type=submit]:has-text('Allow'), button[type=submit]:has-text('Authorize')").first
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=20000):
                    await allow.click(timeout=10000)
                await page.wait_for_timeout(3000)
                print(f"  [browser] final: {page.url}", flush=True)
                return True
            except Exception as e:
                print(f"  [browser] allow: {str(e)[:100]}", flush=True)
                return False
        except Exception as e:
            print(f"  [browser] err: {type(e).__name__} {str(e)[:150]}", flush=True)
            return False
        finally:
            await browser.close()

def convert_one(sso):
    from curl_cffi import requests as cr
    P = {"http": PROXY_URL, "https": PROXY_URL}
    s = cr.Session(impersonate="chrome131")
    s.cookies.set("sso", sso, domain=".x.ai"); s.cookies.set("sso-rw", sso, domain=".x.ai")
    r = s.post("https://auth.x.ai/oauth2/device/code", data={"client_id": CLIENT_ID, "scope": SCOPE}, proxies=P, timeout=30)
    if r.status_code != 200:
        return {"ok": False, "err": f"device/code {r.status_code}"}
    dev = r.json(); uc, dc, interval = dev["user_code"], dev["device_code"], dev.get("interval") or 5
    r = s.post("https://auth.x.ai/oauth2/device/verify", data={"user_code": uc}, proxies=P, timeout=30, allow_redirects=False)
    if r.status_code == 401:
        return {"ok": False, "err": "sso unauthorized", "reject": True}
    if r.status_code not in (200, 302, 303):
        return {"ok": False, "err": f"verify {r.status_code}"}
    if not asyncio.run(approve_via_browser(sso, uc)):
        return {"ok": False, "err": "browser approve failed"}
    deadline = time.time() + 90
    while time.time() < deadline:
        time.sleep(interval)
        r = s.post("https://auth.x.ai/oauth2/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID, "device_code": dc}, proxies=P, timeout=30)
        try:
            payload = r.json()
        except Exception:
            continue
        if r.status_code == 200 and payload.get("access_token"):
            return {"ok": True, "token": payload}
        if payload.get("error") not in ("authorization_pending", "slow_down"):
            return {"ok": False, "err": f"token {payload.get('error')}"}
    return {"ok": False, "err": "poll timeout"}

def main():
    accounts = json.loads(xor(base64.b64decode(open("accounts.enc", "rb").read())).decode())
    shard_i, shard_n = int(os.environ.get("SHARD_INDEX", "0")), int(os.environ.get("SHARD_COUNT", "1"))
    accounts = [a for i, a in enumerate(accounts) if i % shard_n == shard_i]
    mx = int(os.environ.get("MAX_ACCOUNTS", "0"))
    if mx > 0:
        accounts = accounts[:mx]
    print(f"shard {shard_i}/{shard_n}: {len(accounts)}", flush=True)
    results = []
    for a in accounts:
        w, b, sso = a["web_id"], a["build_id"], a["sso"]
        print(f"[web {w} build {b}] converting...", flush=True)
        try:
            res = convert_one(sso)
        except Exception as e:
            res = {"ok": False, "err": f"{type(e).__name__}: {e}"}
        if res.get("ok"):
            t = res["token"]
            results.append({"web_id": w, "build_id": b, "access_token": t["access_token"],
                            "refresh_token": t.get("refresh_token", ""), "id_token": t.get("id_token", ""),
                            "expires_in": t.get("expires_in", 3600)})
            print(f"  -> OK", flush=True)
        else:
            print(f"  -> FAIL: {res.get('err')}", flush=True)
        time.sleep(2)
    ts = int(time.time())
    key = f"web2build:result:{ts}-s{shard_i}"
    kv_put(key, base64.b64encode(xor(json.dumps(results).encode())))
    kv_put("web2build:status", json.dumps({"ts": ts, "shard": shard_i, "done": len(results), "total": len(accounts)}).encode())
    print(f"uploaded {len(results)} results -> {key}", flush=True)

if __name__ == "__main__":
    main()
