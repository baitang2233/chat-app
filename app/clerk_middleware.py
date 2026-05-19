import os
import functools

import requests
from flask import current_app, g, jsonify, request

CLERK_API_URL_TEMPLATE = "https://{frontend_api}/v1/me"

def get_clerk_config():
    return {
        "publishable_key": current_app.config.get("CLERK_PUBLISHABLE_KEY", os.environ.get("CLERK_PUBLISHABLE_KEY", "")),
        "secret_key": current_app.config.get("CLERK_SECRET_KEY", os.environ.get("CLERK_SECRET_KEY", "")),
        "frontend_api": current_app.config.get("CLERK_FRONTEND_API", os.environ.get("CLERK_FRONTEND_API", "")),
    }









def clerk_required(view_func):
   

    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized", "message": "缺少认证令牌"}), 401

        token = auth_header[len("Bearer "):].strip()
        if not token:
            return jsonify({"error": "Unauthorized", "message": "令牌为空"}), 401

        cfg = get_clerk_config()
        frontend_api = cfg.get("frontend_api")
        secret_key = cfg.get("secret_key")

        if not frontend_api or not secret_key:
            return jsonify({"error": "ServerError", "message": "服务器 Clerk 配置不完整"}), 500

        # 调用 Clerk 后端验证 token
        try:
            me_url = CLERK_API_URL_TEMPLATE.format(frontend_api=frontend_api)
            headers = {
                "Authorization": f"Bearer {token}",
            }
            resp = requests.get(me_url, headers=headers, timeout=10)
        except requests.RequestException as exc:
            return jsonify({"error": "AuthServiceUnavailable", "message": f"认证服务不可用: {str(exc)}"}), 503

        if resp.status_code != 200:
            try:
                err_data = resp.json()
                err_msg = err_data.get("errors", [{}])[0].get("message", "令牌无效或已过期")
            except Exception:
                err_msg = "令牌无效或已过期"
            return jsonify({"error": "Unauthorized", "message": err_msg}), 401

        user_data = resp.json()
        # 注入全局对象 g
        g.current_user = {
            "id": user_data.get("id"),
            "email": user_data.get("email_addresses", [{}])[0].get("email_address") if user_data.get("email_addresses") else None,
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name"),
            "last_name": user_data.get("last_name"),
            "image_url": user_data.get("image_url"),
            "clerk_raw": user_data,
        }

        return view_func(*args, **kwargs)

    return wrapper









def get_current_user():

    return getattr(g, "current_user", None)
