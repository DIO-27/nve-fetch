#!/usr/bin/env python3
"""Fetch NVE package from GoFile (runs on GitHub Actions)."""
import hashlib
import sys
import time
from pathlib import Path

import requests

GOFILE_API = "https://api.gofile.io"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
LANG = "en-US"
SALT = "12af056dacea0b"
CONTENT_ID = "vDWUmg"
OUT = Path("out")
CHUNK = 256 * 1024
MAX_RETRIES = 8


def log(m):
    print(m, flush=True)


def guest_token():
    last = None
    for i in range(10):
        try:
            r = requests.post(f"{GOFILE_API}/accounts",
                              headers={"User-Agent": UA}, timeout=20).json()
            if r.get("status") == "ok":
                return r["data"]["token"]
            last = r
        except Exception as e:
            last = e
        log(f"[retry] account create failed ({i+1}/10): {last}")
        time.sleep(30)
    raise SystemExit(f"cannot create guest account: {last}")


def website_token(tok, offset=0):
    tw = str(int(time.time() // 14400) + offset)
    return hashlib.sha256(
        f"{UA}::{LANG}::{tok}::{tw}::{SALT}".encode()).hexdigest()


def api_headers(tok, offset=0):
    return {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {tok}",
        "X-Website-Token": website_token(tok, offset),
        "X-BL": LANG,
        "Origin": "https://gofile.io",
        "Referer": "https://gofile.io/",
        "Cookie": f"accountToken={tok}",
    }


def fetch_info(tok):
    url = f"{GOFILE_API}/contents/{CONTENT_ID}"
    params = {"contentFilter": "", "page": 1, "pageSize": 1000,
              "sortField": "createTime", "sortDirection": -1}
    for i in range(10):
        try:
            r = requests.get(url, headers=api_headers(tok), params=params,
                             timeout=30).json()
            if r.get("status") == "ok":
                return r["data"]
            log(f"[retry] content api: {r.get('status')}")
        except Exception as e:
            log(f"[retry] content api err: {e}")
        time.sleep(45)
    raise SystemExit("cannot fetch content info")


def collect(d, base):
    res = []
    if d["type"] == "folder":
        sub = base / d["name"]
        for c in (d.get("children") or {}).values():
            res += collect(c, sub)
    else:
        res.append({"name": d["name"], "link": d["link"], "dir": base,
                    "size": d.get("size", 0)})
    return res


def dl(f, tok):
    f["dir"].mkdir(parents=True, exist_ok=True)
    dest = f["dir"] / f["name"]
    parsed = requests.utils.urlparse(f["link"])
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base_h = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Cookie": f"accountToken={tok}",
        "Referer": "https://gofile.io/",
        "Origin": "https://gofile.io",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        resume = dest.stat().st_size if dest.exists() else 0
        h = dict(base_h)
        if resume:
            h["Range"] = f"bytes={resume}-"
        try:
            r = requests.get(f["link"], headers=h, stream=True,
                             timeout=(30, 300))
            if r.status_code == 416:
                return True
            if r.status_code not in (200, 206):
                log(f"  [err] {f['name']}: HTTP {r.status_code} "
                    f"({attempt}/{MAX_RETRIES})")
                time.sleep(20)
                continue
            mode = "ab" if (resume and r.status_code == 206) else "wb"
            total = -1
            cr = r.headers.get("Content-Range", "")
            if "/" in cr:
                total = int(cr.split("/")[1])
            elif r.headers.get("Content-Length"):
                total = int(r.headers["Content-Length"]) + (
                    resume if r.status_code == 206 else 0)
            done = resume
            with open(dest, mode) as fh:
                for chunk in r.iter_content(CHUNK):
                    if chunk:
                        fh.write(chunk)
                        done += len(chunk)
                        if total > 0 and (done * 100) % 5 == 0:
                            log(f"  {f['name']} {done*100//total}% "
                                f"({done}/{total})")
            log(f"  [done] {f['name']} -> {dest.stat().st_size} bytes "
                f"(remote {total})")
            if total > 0 and dest.stat().st_size != total:
                log(f"  [size-mismatch] retrying")
                continue
            return True
        except Exception as e:
            log(f"  [err] {f['name']}: {type(e).__name__} {e} "
                f"({attempt}/{MAX_RETRIES})")
            time.sleep(20)
    return False


def main():
    tok = guest_token()
    log(f"[ok] guest token: {tok[:8]}...")
    data = fetch_info(tok)
    log(f"[ok] content: {data.get('name')} totalSize={data.get('totalSize')}")
    files = collect(data, OUT)
    log(f"[ok] files: {len(files)}")
    hashes = []
    for f in files:
        log(f"[dl] {f['dir'] / f['name']} ({f['size']} bytes)")
        if not dl(f, tok):
            raise SystemExit(f"download failed: {f['name']}")
        p = f["dir"] / f["name"]
        hashes.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p}")
    (OUT / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n")
    log("[ok] all downloaded, SHA256SUMS.txt written")


if __name__ == "__main__":
    main()
