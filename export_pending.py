#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出待 Web→Build 转换账号（SSO 明文 + 关联 build id），供 GHA worker 使用。

GHA 只拿到加密后的 accounts.enc（XOR key 经 KV 分发），转换结果回传 KV，
本机 import_results.py 拉取后用 AES-256-GCM 写回 SQLite 并清 KV。
"""
import base64, hashlib, json, os, sqlite3, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DB = "/var/lib/docker/volumes/grok2api-data/_data/backend.db"
KEY_B64 = os.environ.get("CRED_KEY", "5T9uZIYoAISkGUIRgp3i5i+uDrHi9ok+RmIxwb4qDds=")
OUT = sys.argv[1] if len(sys.argv) > 1 else "/root/grok-convert-gha/accounts.json"

KEY = base64.b64decode(KEY_B64)
aes = AESGCM(KEY)

def dec(b64):
    data = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    return aes.decrypt(data[:12], data[12:], None).decode()

def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT a.id, l.build_account_id, c.encrypted_primary FROM provider_accounts a
        JOIN account_credentials c ON c.account_id=a.id
        JOIN account_provider_links l ON l.web_account_id=a.id
        JOIN provider_accounts b ON b.id=l.build_account_id
        WHERE a.provider='grok_web' AND a.auth_status='active' AND a.enabled=1
          AND b.auth_status='reauthRequired' ORDER BY a.id
    """).fetchall()
    conn.close()
    out = [{"web_id": w, "build_id": b, "sso": dec(e)} for w, b, e in rows]
    json.dump(out, open(OUT, "w"))
    print(f"exported {len(out)} accounts -> {OUT}")

if __name__ == "__main__":
    main()
