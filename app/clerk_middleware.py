"""
Clerk 认证中间件 — 使用 PyJWT 本地验证 session token（JWT）。
通过 Clerk 的 JWKS 端点获取公钥来验证 token 签名，避免每次请求都调用外部 API。
"""
import os
import time
import functools
import threading

import jwt
import requests
from flask import g, jsonify, request

# ── 配置 ──
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_FRONTEND_API = os.environ.get("CLERK_FRONTEND_API", "")  # 例如 https://polished-duck-8.clerk.accounts.dev
CLERK_JWKS_URL = f"{CLERK_FRONTEND_API}/.well-known/jwks.json"

# ── JWKS 缓存 ──
_jwks_cache = {
    "keys": None,
    "fetched_at": 0,
    "lock": threading.Lock(),
}
JWKS_CACHE_TTL = 3600  # 1 小时


def _fetch_jwks():
    """从 Clerk 的 JWKS 端点获取公钥列表，带缓存。"""
    now = time.time()
    with _jwks_cache["lock"]:
        if _jwks_cache["keys"] and (now - _jwks_cache["fetched_at"]) < JWKS_CACHE_TTL:
            return _jwks_cache["keys"]

    try:
        resp = requests.get(CLERK_JWKS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        keys = data.get("keys", [])
        with _jwks_cache["lock"]:
            _jwks_cache["keys"] = keys
            _jwks_cache["fetched_at"] = time.time()
        return keys
    except Exception:
        # 如果请求失败但有旧缓存，返回旧的
        with _jwks_cache["lock"]:
            if _jwks_cache["keys"]:
                return _jwks_cache["keys"]
        raise


def _get_public_key(token):
    """根据 JWT header 中的 kid 查找对应的公钥。"""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError:
        return None

    kid = unverified_header.get("kid")
    if not kid:
        return None

    jwks_keys = _fetch_jwks()
    for key_data in jwks_keys:
        if key_data.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)

    # kid 不在缓存中，可能密钥轮转了，强制刷新
    with _jwks_cache["lock"]:
        _jwks_cache["fetched_at"] = 0
    jwks_keys = _fetch_jwks()
    for key_data in jwks_keys:
        if key_data.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)

    return None


def _verify_token(token):
    """验证 Clerk session JWT 并返回 payload。"""
    public_key = _get_public_key(token)
    if not public_key:
        return None, "无法获取公钥或 kid 不匹配"

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_aud": False,   # Clerk session tokens 通常没有 aud
                "verify_iss": True,
                "require": ["sub", "iat", "exp"],
            },
            issuer=CLERK_FRONTEND_API,
            leeway=10,  # 10 秒时钟偏差容忍
        )
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, "令牌已过期"
    except jwt.InvalidIssuerError:
        return None, "令牌签发方不匹配"
    except jwt.InvalidTokenError as e:
        return None, f"令牌无效: {str(e)}"


def _get_user_info(user_id):
    """通过 Clerk Backend API 获取用户详细信息。"""
    if not CLERK_SECRET_KEY:
        return None

    try:
        resp = requests.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def clerk_required(view_func):
    """Flask 装饰器：验证 Clerk session token。"""

    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized", "message": "缺少认证令牌"}), 401

        token = auth_header[len("Bearer "):].strip()
        if not token:
            return jsonify({"error": "Unauthorized", "message": "令牌为空"}), 401

        if not CLERK_FRONTEND_API:
            return jsonify({"error": "ServerError", "message": "服务器 Clerk 配置不完整"}), 500

        # 验证 JWT
        payload, err = _verify_token(token)
        if not payload:
            return jsonify({"error": "Unauthorized", "message": err}), 401

        user_id = payload.get("sub")
        if not user_id:
            return jsonify({"error": "Unauthorized", "message": "令牌缺少用户标识"}), 401

        # 尝试获取用户详细信息
        user_data = _get_user_info(user_id)

        if user_data:
            g.current_user = {
                "id": user_data.get("id"),
                "email": (user_data.get("email_addresses", [{}])[0].get("email_address")
                          if user_data.get("email_addresses") else None),
                "username": user_data.get("username"),
                "first_name": user_data.get("first_name"),
                "last_name": user_data.get("last_name"),
                "image_url": user_data.get("image_url"),
            }
        else:
            # 回退：仅用 JWT payload 中的信息
            g.current_user = {
                "id": user_id,
                "email": None,
                "username": None,
                "first_name": None,
                "last_name": None,
                "image_url": None,
            }

        return view_func(*args, **kwargs)

    return wrapper


def get_current_user():
    return getattr(g, "current_user", None)
