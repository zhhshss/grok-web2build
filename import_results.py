#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 KV 拉 GHA 转换结果，AES-256-GCM 加密写回 SQLite，成功后清理 KV key。

用法: python3 import_results.py [--dry-run]
环境: CLOUDFLARE_* / KV_NAMESPACE_ID / XOR_KEY_B64（同 GHA secrets），CRED_KEY 可选
"""
import base64, hashlib, json, os, secrets, sqlite3, sys, time, urllib.request
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DB = "/var/lib/docker/volumes/grok2api-data/_data/backend.db"
KEY_B64 = os.environ.get("CRED_KEY", "5T9uZIYoAISkGUIRgp3i5i+uDrHi9ok+RmIxwb4qDds=")
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
CF_ACCOUNT = os.environ["CLOUDFLARE_ACCOUNT_ID"]; CF_NS = os.environ["KV_NAMESPACE_ID"]
CF_KEY = os.environ["CLOUDFLARE_API_KEY"]; CF_EMAIL = os.environ["CLOUDFLARE_API_EMAIL"]
XOR_KEY = base64.b64decode(os.environ["XOR_KEY_B64"])
aes = AESGCM(base64.b64decode(KEY_B64))

def xor(d): return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(d))
def enc(p):
    if not p: return ""
    n = secrets.token_bytes(12)
    return base64.b64encode(n + aes.encrypt(n, p.encode(), None)).decode().rstrip("=")
def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
def hash_token(r): return hashlib.sha256(r.encode()).hexdigest()
def ikey(provider, uid, tid, email, sk):
    if uid and uid.strip(): s = "|".join([provider,"user",uid.strip(),(tid or "").strip()])
    elif email and email.strip(): s = "|".join([provider,"email",email.strip().lower(),(tid or "").strip()])
    else: s = "|".join([provider,"source",sk.strip()])
    return hashlib.sha256(s.encode()).hexdigest()
def decode_claims(tok):
    try:
        p = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    except Exception: return {}

def cf(method, path, data=None, raw=False):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/storage/kv/namespaces/{CF_NS}{path}"
    req = urllib.request.Request(url, data=data, method=method,
        headers={"X-Auth-Email": CF_EMAIL, "X-Auth-Key": CF_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read() if raw else json.loads(r.read())

def main():
    dry = "--dry-run" in sys.argv
    # 列出 result keys
    res = cf("GET", f"/keys?prefix=web2build:result:")
    keys = [k["name"] for k in res.get("result", [])]
    print(f"found {len(keys)} result keys")
    applied = cleaned = 0
    conn = sqlite3.connect(DB, timeout=30); conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    for key in keys:
        raw = cf("GET", f"/values/{key}", raw=True)
        results = json.loads(xor(base64.b64decode(raw)).decode())
        print(f"{key}: {len(results)} results")
        for r in results:
            bid = r["build_id"]
            claims = decode_claims(r.get("id_token") or r.get("access_token",""))
            uid, email, tid = claims.get("sub",""), claims.get("email",""), claims.get("team_id","")
            exp_in = r.get("expires_in") or 3600
            exp_at = datetime.fromtimestamp(time.time()+exp_in, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
            sk = "sso-build:" + hash_token(r["access_token"])
            name = email or f"build{bid}"
            ik = ikey("grok_build", uid, tid, email, sk)
            nw = now()
            if dry:
                print(f"  [dry] build {bid} {email} exp={exp_at[:19]}"); continue
            cur.execute("SELECT id FROM provider_accounts WHERE identity_key=?", (ik,))
            row = cur.fetchone()
            if row:
                bid = row[0]
                cur.execute("""UPDATE provider_accounts SET name=?,email=?,user_id=?,team_id=?,enabled=1,
                    auth_status='active',failure_count=0,cooldown_until=NULL,last_error=NULL,updated_at=? WHERE id=?""",
                    (name,email,uid,tid,nw,bid))
                cur.execute("""UPDATE account_credentials SET auth_type='oauth',client_id=?,encrypted_primary=?,encrypted_refresh=?,
                    expires_at=?,refresh_due_at=?,last_refresh_error='',refresh_permanent=0,updated_at=? WHERE account_id=?""",
                    (CLIENT_ID,enc(r["access_token"]),enc(r.get("refresh_token","")),exp_at,exp_at,nw,bid))
            else:
                cur.execute("""INSERT INTO provider_accounts
                    (identity_key,provider,name,email,user_id,team_id,source_key,enabled,auth_status,priority,
                     max_concurrent,minimum_remaining,failure_count,created_at,updated_at,build_route_mode)
                    VALUES (?,?,?,?,?,?,?,1,'active',1,8,0,0,?,?,'auto')""",
                    (ik,"grok_build",name,email,uid,tid,sk,nw,nw))
                bid = cur.lastrowid
                cur.execute("""INSERT INTO account_credentials
                    (account_id,auth_type,client_id,encrypted_primary,encrypted_refresh,expires_at,refresh_due_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (bid,"oauth",CLIENT_ID,enc(r["access_token"]),enc(r.get("refresh_token","")),exp_at,exp_at,nw))
            cur.execute("""INSERT INTO account_provider_links (web_account_id,build_account_id,created_at)
                VALUES (?,?,?) ON CONFLICT(web_account_id) DO UPDATE SET build_account_id=excluded.build_account_id""",
                (r["web_id"],bid,nw))
            applied += 1
        if not dry:
            cf("DELETE", f"/values/{key}")
            cleaned += 1
    if not dry:
        conn.commit()
        # 清 accounts.enc
        try: cf("DELETE", "/values/web2build:accounts")
        except Exception: pass
    conn.close()
    print(f"applied={applied} cleaned_keys={cleaned} dry={dry}")

if __name__ == "__main__":
    main()
