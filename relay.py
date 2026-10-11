# -*- coding: utf-8 -*-
"""AI 中转站（OpenAI 兼容）—— 路线C 的服务器端，v2 支持按模型名自动路由。

作用：CODEX 桌面端只填「中转地址 http://124.221.2.81/relay/v1 + 中转 Key」，
      所有大模型请求先到这台服务器，由服务器按模型名决定真实上游并转发，
      同时按「客户 + 模型」累计 token 用量（收费依据）。

上游 key 两种来源：
  · 透传(pass)   : 客户端自带的 key 原样用于上游（推荐先用这种，无需在服务器存 key）
  · 映射(mapped) : 在 keys.json 的 clients 里把中转 Key 映射到上游真 key
"""
import json
import os
import threading
import time
import uuid

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE, "keys.json")
USAGE_FILE = os.path.join(BASE, "usage.json")
_lock = threading.Lock()

# 各平台真实上游（按模型名自动选）
UPSTREAMS = {
    "deepseek": "https://api.deepseek.com/v1",
    "zhipu":    "https://open.bigmodel.cn/api/paas/v4",
    "qwen":     "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "minimax":  "https://api.minimax.chat/v1",
    "kimi":     "https://api.moonshot.cn/v1",
    "doubao":   "https://ark.cn-beijing.volces.com/api/v3",
    "openai":   "https://api.openai.com/v1",
    # 【V1.0.0.969】补齐平台 Key 池要用到的平台
    "hunyuan":  "https://api.hunyuan.cloud.tencent.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "apibox":   "https://api.apibox.cc/v1",
    "apibox-gemini": "https://api.apibox.cc/v1",
}

# 【V1.0.0.969】平台缺 Key 时的兜底平台（例：平台池里只有 apibox 的 Key，gpt 系就走 apibox）
FALLBACK_PID = {
    "openai": ["apibox"],
    "apibox": ["openai"],
    "apibox-gemini": ["apibox"],
}

# 【V1.0.0.969】探活失败时的内置模型清单（仅作兜底展示，能探活就以官方清单为准）
CATALOG = {
    "deepseek": [("deepseek-chat", "DeepSeek Chat"), ("deepseek-reasoner", "DeepSeek Reasoner")],
    "zhipu": [("glm-4.6", "智谱 GLM-4.6"), ("glm-4-plus", "智谱 GLM-4 Plus"), ("glm-4-flash", "智谱 GLM-4 Flash"), ("glm-4v", "智谱 GLM-4V（识图）")],
    "qwen": [("qwen-max", "通义千问 Qwen Max"), ("qwen-plus", "通义千问 Qwen Plus"), ("qwen-turbo", "通义千问 Qwen Turbo"), ("qwen-vl-max", "通义千问 Qwen-VL Max（识图）")],
    "kimi": [("moonshot-v1-8k", "Kimi Moonshot 8K"), ("moonshot-v1-32k", "Kimi Moonshot 32K"), ("moonshot-v1-128k", "Kimi Moonshot 128K")],
    "doubao": [("doubao-pro-32k", "豆包 Pro 32K"), ("doubao-lite-32k", "豆包 Lite 32K")],
    "hunyuan": [("hunyuan-turbo", "腾讯混元 Turbo"), ("hunyuan-pro", "腾讯混元 Pro")],
    "minimax": [("abab6.5s-chat", "MiniMax abab6.5s")],
    "openrouter": [("nvidia/nemotron-3-super-120b-a12b:free", "OpenRouter 免费层")],
}

# 模型名前缀 -> 上游平台
PREFIX = [
    (("deepseek",), "deepseek"),
    (("glm",), "zhipu"),
    (("qwen",), "qwen"),
    (("minimax", "abab"), "minimax"),
    (("moonshot", "kimi"), "kimi"),
    (("doubao",), "doubao"),
    # 【V1.0.0.969】补齐：混元 / OpenRouter / Gemini 走 APIBox / gpt 系优先走 APIBox（有 Key 的平台）
    (("hy3", "hy4", "hunyuan"), "hunyuan"),
    (("or-", "openrouter/"), "openrouter"),
    (("gemini",), "apibox-gemini"),
    (("gpt", "o1", "o3", "o4", "chatgpt"), "apibox"),
]

DEFAULTS = {
    "upstream": UPSTREAMS["deepseek"],
    "upstreams": {},
    "includeUsage": True,
    "clients": {},
    # 【防白嫖】requireClientKey=true 时，客户端 Key 必须在 clients 白名单里，否则中转站自己直接拒绝（不再转发给上游）
    "requireClientKey": False,
    # 【防白嫖】accessToken 非空时，请求头 X-Relay-Token 必须与它一致（总口令，可留空不用）
    "accessToken": "",
    # 【第二期】授权与计量服务地址（留空 = 不启用记账，行为与老版本完全一致）
    "licenseApi": "",
    # 【第二期】转发前是否调用预检额度（建议 true，可防透支）
    "licensePrecheck": True,
    # 【Key 管理后台】管理口令：非空时 /admin/* 接口才可用（页面用 x-relay-token 传它）
    "adminToken": "",
    # 【V1.0.0.969 用户要求·平台统一 Key 池】{平台标识: 上游真 Key}
    # 授权码（CX1.）客户不带上游 Key 时，按模型名从这里取真 Key → 客户端可用所有「已配 Key 且能正常调用」的模型
    "upstreamKeys": {},
    # /v1/models 聚合结果缓存秒数（探活较慢，缓存避免每次请求都探）
    "modelCacheTtl": 600,
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    if not isinstance(cfg.get("clients"), dict):
        cfg["clients"] = {}
    if not isinstance(cfg.get("upstreams"), dict):
        cfg["upstreams"] = {}
    return cfg


def pick_upstream(cfg, model):
    """按模型名决定真实上游地址，返回 (平台名, baseURL)。"""
    m = str(model or "").strip().lower()
    for pid, url in (cfg.get("upstreams") or {}).items():
        if m.startswith(str(pid).lower()):
            return pid, str(url).rstrip("/")
    for prefixes, pid in PREFIX:
        for p in prefixes:
            if m.startswith(p):
                return pid, (cfg.get("upstreams") or {}).get(pid, UPSTREAMS.get(pid, cfg.get("upstream")))
    return "default", str(cfg.get("upstream") or UPSTREAMS["deepseek"]).rstrip("/")


def platform_key(cfg, pid):
    """【V1.0.0.969】取某平台的统一上游 Key（平台 Key 池）。"""
    keys = cfg.get("upstreamKeys") or {}
    k = str(keys.get(pid) or "").strip()
    if k:
        return k
    # 老配置兼容：默认平台（cfg.upstream）用 codeUpstreamKey
    dflt = str(cfg.get("upstream") or "").rstrip("/")
    for p, url in UPSTREAMS.items():
        if str(url).rstrip("/") == dflt and p == pid:
            return str(cfg.get("codeUpstreamKey") or "").strip()
    return ""


def default_pid(cfg):
    """cfg.upstream 对应的平台标识。"""
    dflt = str(cfg.get("upstream") or "").rstrip("/")
    for p, url in UPSTREAMS.items():
        if str(url).rstrip("/") == dflt:
            return p
    return "deepseek"


def resolve_upstream(cfg, model):
    """按模型名定平台；若该平台没有 Key，则兜底到 FALLBACK_PID 里有 Key 的平台。
    返回 (平台, baseURL, 上游Key)。"""
    pid, base = pick_upstream(cfg, model)
    key = platform_key(cfg, pid)
    if key:
        return pid, base, key
    for alt in FALLBACK_PID.get(pid, []):
        ak = platform_key(cfg, alt)
        if ak:
            return alt, str((cfg.get("upstreams") or {}).get(alt) or UPSTREAMS.get(alt) or base).rstrip("/"), ak
    return pid, base, ""


def add_usage(client, model, usage):
    """按客户 + 模型累计 token 用量。"""
    if not usage:
        return
    p = int(usage.get("prompt_tokens") or 0)
    c = int(usage.get("completion_tokens") or 0)
    t = int(usage.get("total_tokens") or (p + c))
    if p == 0 and c == 0:
        return
    with _lock:
        try:
            with open(USAGE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        rec = d.setdefault(client or "anonymous", {"total": {}, "byModel": {}})
        tot = rec.setdefault("total", {})
        for k, v in (("prompt_tokens", p), ("completion_tokens", c), ("total_tokens", t), ("requests", 1)):
            tot[k] = int(tot.get(k) or 0) + v
        m = rec.setdefault("byModel", {}).setdefault(model or "unknown", {})
        for k, v in (("prompt_tokens", p), ("completion_tokens", c), ("total_tokens", t), ("requests", 1)):
            m[k] = int(m.get(k) or 0) + v
        rec["lastAt"] = int(time.time())
        try:
            with open(USAGE_FILE + ".tmp", "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(USAGE_FILE + ".tmp", USAGE_FILE)
        except Exception:
            pass


def pick_client(cfg, key):
    """返回 (客户标识, 上游 key)。"""
    clients = cfg.get("clients") or {}
    rec = clients.get(key)
    if isinstance(rec, dict):
        return key, (rec.get("upstreamKey") or key)
    if isinstance(rec, str):
        return key, rec
    # 【V1.0.0.949】KEY 绑定在授权码中：授权码客户统一走系统默认的上游 Key
    # 【V1.0.0.969】配了「平台 Key 池」时返回空 Key → 交给 chat() 按模型名取对应平台的真 Key，
    #               于是客户端可用所有已配 Key 的平台模型（不再局限于 DeepSeek 一家）。
    if str(key).startswith("CX1."):
        if cfg.get("upstreamKeys"):
            return key, ""
        dflt = str(cfg.get("codeUpstreamKey") or "").strip()
        if dflt:
            return key, dflt
    return (key or "anonymous"), key


def used_tokens(client):
    """该客户已累计消耗的 tokens（配额判断用）。"""
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        rec = d.get(client) or {}
        tot = rec.get("total") or {}
        return int(tot.get("total_tokens") or 0)
    except Exception:
        return 0


_CODE_LIC = {}
_CODE_TTL = 300


def resolve_license_code(cfg, code):
    """【V1.0.0.949 用户要求】KEY 绑定在授权码中：
    客户只填授权码即可用，中转站拿授权码去授权服务换取 license_id，
    客户无需再单独输入任何中转 Key。"""
    code = str(code or "").strip()
    if not code:
        return 0
    now = time.time()
    hit = _CODE_LIC.get(code)
    if hit and hit[1] > now:
        return hit[0]
    base = str(cfg.get("licenseApi") or "").rstrip("/")
    if not base:
        return 0
    try:
        r = requests.post(
            base + "/api/resolve-code",
            json={"code": code},
            headers={"x-resolve-token": str(cfg.get("resolveToken") or "")},
            timeout=8,
        )
    except Exception:
        return 0
    if r.status_code != 200:
        return 0
    try:
        lic = int((r.json() or {}).get("license_id") or 0)
    except Exception:
        lic = 0
    if lic:
        _CODE_LIC[code] = (lic, now + _CODE_TTL)
    return lic


def _license_of(cfg, key):
    """取出该中转 Key 对应的授权编号（keys.json 里配 licenseId）"""
    rec = (cfg.get("clients") or {}).get(key)
    if isinstance(rec, dict):
        try:
            return int(rec.get("licenseId") or 0)
        except Exception:
            return 0
    # 【V1.0.0.949】KEY 绑定在授权码中：授权码本身即中转 Key
    if str(key).startswith("CX1."):
        return resolve_license_code(cfg, key)
    return 0


def license_precheck(cfg, license_id):
    """转发前预检额度：余额为 0 直接拒绝，不让客户把额度用成负数"""
    base = str(cfg.get("licenseApi") or "").rstrip("/")
    if not base or not license_id:
        return
    try:
        r = requests.post(
            base + "/api/meter/precheck",
            json={"license_id": license_id, "estimated_tokens": 0},
            timeout=8,
        )
    except Exception:
        # 授权服务不可达时放行，避免把客户整体挡在门外（只影响风控，不影响可用性）
        return
    if r.status_code == 402:
        raise HTTPException(status_code=402, detail="quota_exhausted")
    if r.status_code == 403:
        raise HTTPException(status_code=403, detail="license_invalid")


def license_charge(cfg, license_id, request_id, model, usage):
    """转发成功后记账：写入 TOKEN 流水并扣减余额（失敗不影响本次响应）"""
    base = str(cfg.get("licenseApi") or "").rstrip("/")
    if not base or not license_id or not usage:
        return
    det = usage.get("prompt_tokens_details")
    cached = 0
    if isinstance(det, dict):
        try:
            cached = int(det.get("cached_tokens") or 0)
        except Exception:
            cached = 0
    body = {
        "license_id": license_id,
        "request_id": request_id,
        "model": model or "",
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "cached_tokens": cached,
        "success": True,
    }
    try:
        requests.post(base + "/api/meter/charge", json=body, timeout=10)
    except Exception:
        pass


def check_access(cfg, key, request):
    """网关鉴权：白名单 + 总口令，不通过则 401（中转站自己拒绝，绝不转发给上游）。"""
    tok = str(cfg.get("accessToken") or "").strip()
    if tok:
        got = (request.headers.get("x-relay-token") or "").strip()
        if got != tok:
            raise HTTPException(status_code=401, detail="relay_token_required")
    if cfg.get("requireClientKey"):
        clients = cfg.get("clients") or {}
        allow = set(k for k in clients.keys() if not str(k).startswith("_"))
        if not key or key not in allow:
            # 【V1.0.0.949】KEY 绑定在授权码中：授权码本身即中转 Key
            _lic = resolve_license_code(cfg, key) if str(key).startswith("CX1.") else 0
            if not _lic:
                raise HTTPException(status_code=401, detail="client_key_not_allowed")
            if cfg.get("licensePrecheck", True):
                license_precheck(cfg, _lic)
            return
        # 【V1.0.0.973 用户要求】被停用的 Key 立刻拒绝（控制台点「停用」后客户马上无法调用）
        _rec0 = clients.get(key)
        if isinstance(_rec0, dict) and _rec0.get("enabled") is False:
            raise HTTPException(status_code=403, detail="key_disabled")
        # 【配额】客户买的 TOKEN 用完就停止使用
        rec = clients.get(key) or {}
        try:
            quota = int(rec.get("quotaTokens") or 0)
        except Exception:
            quota = 0
        if quota > 0:
            used = used_tokens(key)
            if used >= quota:
                raise HTTPException(status_code=403, detail="quota_exhausted")
        # 【第二期】接入授权服务：余额为 0 就不放行
        if cfg.get("licensePrecheck", True):
            license_precheck(cfg, _license_of(cfg, key))


app = FastAPI(title="AI Relay", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    cfg = load_cfg()
    return {
        "ok": True,
        "version": "1.0.0.949",
        "routes": {p: UPSTREAMS[p] for p in UPSTREAMS},
        "default": cfg.get("upstream"),
        "clients": len(cfg.get("clients") or {}),
        # 【V1.0.0.969】平台 Key 池：哪些平台已配 Key（只报平台名，不报 Key 本身）
        "poolPlatforms": sorted([p for p, v in (cfg.get("upstreamKeys") or {}).items() if str(v or "").strip()]),
        "licenseApi": cfg.get("licenseApi"),
    }


_MODEL_CACHE = {"at": 0, "data": None, "src": {}}


def _probe_platform(cfg, pid, base, key):
    """探活某个平台：/models 返回 200 就用它的官方清单，否则退回内置清单。
    返回 (模型 id 列表, 来源说明)。"""
    ids = []
    src = ""
    try:
        r = requests.get(base.rstrip("/") + "/models",
                         headers={"Authorization": "Bearer " + key}, timeout=10)
        if r.status_code == 200:
            j = r.json()
            for it in (j.get("data") or []):
                mid = it.get("id") if isinstance(it, dict) else str(it)
                if mid:
                    ids.append(mid)
            src = "live"
    except Exception:
        src = "probe_fail"
    if not ids:
        ids = [x[0] for x in CATALOG.get(pid, [])]
        src = src or "catalog"
        src = "catalog" if ids else src
    return ids, src


def _list_models(cfg, force=False):
    """【V1.0.0.969】汇总所有「已配平台 Key」的可用模型，带缓存。"""
    now = time.time()
    ttl = int(cfg.get("modelCacheTtl") or 600)
    if (not force) and _MODEL_CACHE["data"] is not None and (now - _MODEL_CACHE["at"]) < ttl:
        return _MODEL_CACHE["data"], _MODEL_CACHE["src"]
    keys = cfg.get("upstreamKeys") or {}
    pids = [p for p in keys if str(keys[p] or "").strip()]
    dpid = default_pid(cfg)
    if dpid not in pids and platform_key(cfg, dpid):
        pids.append(dpid)
    data, src = [], {}
    for pid in pids:
        base = str((cfg.get("upstreams") or {}).get(pid) or UPSTREAMS.get(pid) or cfg.get("upstream"))
        pkey = platform_key(cfg, pid)
        if not pkey:
            continue
        ids, how = _probe_platform(cfg, pid, base, pkey)
        src[pid] = "%s(%d)" % (how, len(ids))
        for mid in ids:
            data.append({"id": mid, "object": "model", "owned_by": pid})
    if not data:
        # 一个平台 Key 都没有 → 保持老行为（只取默认上游的清单）
        base = str(cfg.get("upstream") or UPSTREAMS["deepseek"]).rstrip("/")
        ids, how = _probe_platform(cfg, dpid, base, str(cfg.get("codeUpstreamKey") or ""))
        src[dpid] = "%s(%d)" % (how, len(ids))
        for mid in ids:
            data.append({"id": mid, "object": "model", "owned_by": dpid})
    _MODEL_CACHE["at"] = now
    _MODEL_CACHE["data"] = data
    _MODEL_CACHE["src"] = src
    return data, src


@app.get("/v1/models")
def models(request: Request):
    cfg = load_cfg()
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    check_access(cfg, key, request)
    force = str(request.query_params.get("refresh") or "") in ("1", "true", "yes")
    data, src = _list_models(cfg, force=force)
    return JSONResponse({"object": "list", "data": data, "count": len(data), "sources": src})


@app.post("/v1/chat/completions")
def chat(payload: dict, request: Request):
    cfg = load_cfg()
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    check_access(cfg, key, request)
    client, upkey = pick_client(cfg, key)
    model = payload.get("model")
    rid = uuid.uuid4().hex          # 本次请求唯一号，记账去重用
    license_id = _license_of(cfg, key)
    # 【V1.0.0.969】平台统一 Key 池：客户（授权码）不带上游 Key 时，按模型名取对应平台的真 Key
    pid, up_base, upkey = resolve_upstream(cfg, model)
    if not str(upkey or "").strip():
        raise HTTPException(
            status_code=503,
            detail="model_not_available: 模型 %s 所属平台 %s 未配置平台上游 Key" % (model, pid),
        )
    url = up_base + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + upkey}

    stream = bool(payload.get("stream"))
    if stream and cfg.get("includeUsage", True):
        so = payload.get("stream_options")
        if not isinstance(so, dict):
            so = {}
        so["include_usage"] = True
        payload["stream_options"] = so

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    if not stream:
        try:
            r = requests.post(url, data=data, headers=headers, timeout=600)
        except Exception as e:
            raise HTTPException(status_code=502, detail="upstream_unreachable: %s" % e)
        try:
            j = r.json()
        except Exception:
            return JSONResponse({"error": r.text[:500]}, status_code=r.status_code)
        if isinstance(j, dict) and j.get("usage"):
            add_usage(client, model, j["usage"])
            license_charge(cfg, license_id, rid, model, j["usage"])
        return JSONResponse(j, status_code=r.status_code)

    def gen():
        usage = None
        pending = b""
        try:
            with requests.post(url, data=data, headers=headers, stream=True, timeout=600) as r:
                if r.status_code != 200:
                    yield r.content
                    return
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    yield chunk
                    pending += chunk
                    while b"\n\n" in pending:
                        ev, pending = pending.split(b"\n\n", 1)
                        for ln in ev.split(b"\n"):
                            if not ln.startswith(b"data:"):
                                continue
                            s = ln[5:].strip()
                            if not s or s == b"[DONE]":
                                continue
                            try:
                                j = json.loads(s.decode("utf-8", "replace"))
                                if isinstance(j, dict) and j.get("usage"):
                                    usage = j["usage"]
                            except Exception:
                                pass
        except Exception as e:
            yield ("data: " + json.dumps({"error": "relay_stream_error: %s" % e}) + "\n\n").encode("utf-8")
        finally:
            if usage:
                add_usage(client, model, usage)
                license_charge(cfg, license_id, rid, model, usage)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/usage")
def usage():
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            return JSONResponse(json.load(f))
    except Exception:
        return JSONResponse({})


# ==================== 【Key 管理后台】 ====================
# 页面：https://<服务器>/updates/keys-admin.html
# 口令：keys.json 里的 adminToken（留空 = 关闭下面这些接口）
# 说明：改完立即生效，不需要重启服务。


def save_cfg(cfg):
    """原子写回 keys.json（里面都是敏感 Key，权限保持 600）。"""
    tmp = CFG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_FILE)
    try:
        os.chmod(CFG_FILE, 0o600)
    except Exception:
        pass


def _admin_guard(cfg, request):
    want = str(cfg.get("adminToken") or "").strip()
    if not want:
        raise HTTPException(status_code=403, detail="admin_disabled")
    got = (request.headers.get("x-relay-token") or "").strip()
    if got != want:
        raise HTTPException(status_code=401, detail="admin_token_invalid")


def _mask(k):
    k = str(k or "")
    if len(k) <= 14:
        return k
    return k[:10] + "..." + k[-4:]


def _load_usage():
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _key_rows(cfg, reveal=False):
    clients = cfg.get("clients") or {}
    usage = _load_usage()
    rows = []
    for k, rec in clients.items():
        if str(k).startswith("_"):
            continue
        if isinstance(rec, str):
            rec = {"name": "", "upstreamKey": rec}
        if not isinstance(rec, dict):
            rec = {}
        u = (usage.get(k) or {}).get("total") or {}
        up = str(rec.get("upstreamKey") or "").strip()
        try:
            quota = int(rec.get("quotaTokens") or 0)
        except Exception:
            quota = 0
        try:
            lic = int(rec.get("licenseId") or 0)
        except Exception:
            lic = 0
        rows.append({
            "keyMasked": _mask(k),
            "key": (str(k) if reveal else ""),
            "keyTail": str(k)[-6:],
            "name": rec.get("name") or "",
            "mode": ("映射（用你的上游Key）" if up else "透传（用客户自带Key）"),
            "licenseId": lic,
            "quotaTokens": quota,
            "usedTokens": int(u.get("total_tokens") or 0),
            "requests": int(u.get("requests") or 0),
            "lastAt": (usage.get(k) or {}).get("lastAt") or 0,
            "note": rec.get("note") or "",
            # 【V1.0.0.973 用户要求】返回启用状态，供控制台按钮正确显示「启用/停用」
            "enabled": (rec.get("enabled") is not False),
        })
    rows.sort(key=lambda r: -int(r.get("usedTokens") or 0))
    return rows


@app.get("/admin/keys")
def admin_keys(request: Request):
    cfg = load_cfg()
    _admin_guard(cfg, request)
    reveal = str(request.query_params.get("reveal") or "").strip().lower() in ("1", "true", "yes")
    clients = cfg.get("clients") or {}
    return {
        "ok": True,
        "version": "1.0.0.949",
        "upstream": cfg.get("upstream"),
        "requireClientKey": bool(cfg.get("requireClientKey")),
        "quotaEnabled": bool(cfg.get("quotaEnabled")),
        "licenseApi": cfg.get("licenseApi") or "",
        "adminTokenSet": bool(str(cfg.get("adminToken") or "").strip()),
        "total": len([k for k in clients.keys() if not str(k).startswith("_")]),
        "rows": _key_rows(cfg, reveal),
    }


@app.post("/admin/keys/add")
def admin_keys_add(payload: dict, request: Request):
    cfg = load_cfg()
    _admin_guard(cfg, request)
    name = str(payload.get("name") or "").strip() or "未命名"
    key = str(payload.get("key") or "").strip()
    if not key:
        key = "sk-" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
    clients = cfg.setdefault("clients", {})
    if not isinstance(clients, dict):
        clients = {}
        cfg["clients"] = clients
    rec = clients.get(key)
    rec = dict(rec) if isinstance(rec, dict) else {}
    rec["name"] = name
    rec["upstreamKey"] = str(payload.get("upstreamKey") or "").strip()
    try:
        rec["licenseId"] = int(payload.get("licenseId") or 0)
    except Exception:
        rec["licenseId"] = 0
    try:
        rec["quotaTokens"] = int(payload.get("quotaTokens") or 0)
    except Exception:
        rec["quotaTokens"] = 0
    if payload.get("note"):
        rec["note"] = str(payload.get("note"))
    clients[key] = rec
    save_cfg(cfg)
    return {"ok": True, "key": key, "name": name}


@app.post("/admin/keys/set")
def admin_keys_set(payload: dict, request: Request):
    """【V1.0.0.973 用户要求】控制台「停用/启用」「编辑（改名/改配额）」统一走这里。"""
    cfg = load_cfg()
    _admin_guard(cfg, request)
    key = str(payload.get("key") or "").strip()
    tail = str(payload.get("tail") or "").strip()
    clients = cfg.get("clients") or {}
    target = ""
    if key and key in clients:
        target = key
    elif tail:
        for k in list(clients.keys()):
            if str(k).endswith(tail):
                target = k
                break
    if not target:
        raise HTTPException(status_code=404, detail="key_not_found")
    rec = clients.get(target)
    if isinstance(rec, str):
        rec = {"upstreamKey": rec}
    rec = dict(rec) if isinstance(rec, dict) else {}
    if "enabled" in payload:
        rec["enabled"] = bool(payload.get("enabled"))
    nm = str(payload.get("name") or "").strip()
    if nm:
        rec["name"] = nm
    if "quotaTokens" in payload:
        try:
            rec["quotaTokens"] = int(payload.get("quotaTokens") or 0)
        except Exception:
            pass
    clients[target] = rec
    cfg["clients"] = clients
    save_cfg(cfg)
    return {"ok": True, "key": _mask(target), "name": rec.get("name") or "",
            "enabled": rec.get("enabled") is not False}


@app.post("/admin/keys/del")
def admin_keys_del(payload: dict, request: Request):
    cfg = load_cfg()
    _admin_guard(cfg, request)
    key = str(payload.get("key") or "").strip()
    tail = str(payload.get("tail") or "").strip()
    clients = cfg.get("clients") or {}
    target = ""
    if key and key in clients:
        target = key
    elif tail:
        for k in list(clients.keys()):
            if str(k).endswith(tail):
                target = k
                break
    if not target:
        raise HTTPException(status_code=404, detail="key_not_found")
    rec = clients.get(target)
    nm = rec.get("name") if isinstance(rec, dict) else ""
    del clients[target]
    save_cfg(cfg)
    return {"ok": True, "deleted": _mask(target), "name": nm}


@app.post("/admin/keys/check")
def admin_keys_check(request: Request):
    cfg = load_cfg()
    _admin_guard(cfg, request)
    return {"ok": True, "version": "1.0.0.949"}
