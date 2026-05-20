import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, jsonify, render_template, request

from clerk_middleware import clerk_required, get_current_user

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://chatapp:chatapp@db:5432/chatapp")
API_KEY = os.environ.get("API_KEY")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-3.5-turbo")
LLM_API_URL = os.environ.get("LLM_API_URL")


def get_conn():
    """获取 PostgreSQL 连接。"""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """启动时自动建表。"""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT,
                        username TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id SERIAL PRIMARY KEY,
                        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)
                """)
    finally:
        conn.close()


def now_str():
    return datetime.now(timezone.utc).isoformat()


def ensure_user_exists(user):
    """确保 Clerk 用户在本地数据库中存在"""
    user_id = user.get("id")
    email = user.get("email")
    username = user.get("username")
    now = now_str()

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "INSERT INTO users (id, email, username, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, email, username, now, now),
                    )
                else:
                    cur.execute(
                        "UPDATE users SET email = %s, username = %s, updated_at = %s WHERE id = %s",
                        (email, username, now, user_id),
                    )
    finally:
        conn.close()


# ── 健康检查 ──
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": now_str()})


CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_FRONTEND_API = os.environ.get("CLERK_FRONTEND_API", "")


@app.route("/")
def index():
    return render_template(
        "index.html",
        clerk_publishable_key=CLERK_PUBLISHABLE_KEY,
        clerk_frontend_api=CLERK_FRONTEND_API,
    )


@app.route("/api/auth/me")
@clerk_required
def auth_me():
    """返回当前登录用户信息。"""
    user = get_current_user()
    ensure_user_exists(user)
    return jsonify({"user": user})


# ── 对话 CRUD ──

@app.route("/api/conversations", methods=["GET"])
@clerk_required
def list_conversations():
    user = get_current_user()
    ensure_user_exists(user)
    user_id = user["id"]

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, created_at FROM conversations WHERE user_id = %s ORDER BY id DESC",
                (user_id,),
            )
            conversations = cur.fetchall()
    finally:
        conn.close()

    # 序列化 datetime
    for c in conversations:
        c["created_at"] = c["created_at"].isoformat() if c["created_at"] else None

    return jsonify(conversations)


@app.route("/api/conversations", methods=["POST"])
@clerk_required
def create_conversation():
    user = get_current_user()
    ensure_user_exists(user)
    user_id = user["id"]

    title = "Ciallo～(∠・ω< )⌒★"
    created_at = now_str()

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversations (user_id, title, created_at) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, title, created_at),
                )
                conversation_id = cur.fetchone()[0]
    finally:
        conn.close()

    return jsonify({"id": conversation_id, "title": title, "created_at": created_at})


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
@clerk_required
def delete_conversation(conversation_id):
    user = get_current_user()
    user_id = user["id"]

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM conversations WHERE id = %s AND user_id = %s",
                    (conversation_id, user_id),
                )
                if not cur.fetchone():
                    return jsonify({"error": "Forbidden", "message": "无权限删除该对话"}), 403
                cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conversation_id,))
                cur.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
    finally:
        conn.close()

    return jsonify({"ok": True})


@app.route("/api/conversations/<int:conversation_id>/messages", methods=["GET"])
@clerk_required
def get_messages(conversation_id):
    user = get_current_user()
    user_id = user["id"]

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM conversations WHERE id = %s AND user_id = %s",
                (conversation_id, user_id),
            )
            if not cur.fetchone():
                return jsonify({"error": "Forbidden", "message": "无权限访问该对话"}), 403
            cur.execute(
                "SELECT id, role, content, created_at FROM messages WHERE conversation_id = %s ORDER BY id ASC",
                (conversation_id,),
            )
            messages = cur.fetchall()
    finally:
        conn.close()

    for m in messages:
        m["created_at"] = m["created_at"].isoformat() if m["created_at"] else None

    return jsonify(messages)


# ── 聊天 ──

def update_title_if_first_message(conversation_id, first_user_message):
    """如果当前对话标题还是默认的，就用第一条用户消息前20个字符作为标题。"""
    title = first_user_message.strip()[:20] or "Ciallo～(∠・ω< )⌒★"

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT title FROM conversations WHERE id = %s", (conversation_id,))
                row = cur.fetchone()
                if row and row[0] == "Ciallo～(∠・ω< )⌒★":
                    cur.execute(
                        "UPDATE conversations SET title = %s WHERE id = %s",
                        (title, conversation_id),
                    )
    finally:
        conn.close()


def call_llm(messages):
    """调用 OpenAI 兼容接口。"""
    if not API_KEY:
        raise ValueError("缺少 API_KEY 环境变量")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.7,
    }

    resp = requests.post(LLM_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


@app.route("/api/chat", methods=["POST"])
@clerk_required
def chat():
    user = get_current_user()
    user_id = user["id"]

    body = request.get_json(silent=True) or {}
    conversation_id = body.get("conversation_id")
    user_message = (body.get("message") or "").strip()

    if not conversation_id or not user_message:
        return jsonify({"error": "BadRequest", "message": "conversation_id 和 message 不能为空"}), 400

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM conversations WHERE id = %s AND user_id = %s",
                    (conversation_id, user_id),
                )
                if not cur.fetchone():
                    return jsonify({"error": "Forbidden", "message": "无权限访问该对话"}), 403
                cur.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (%s, %s, %s, %s)",
                    (conversation_id, "user", user_message, now_str()),
                )
    finally:
        conn.close()

    update_title_if_first_message(conversation_id, user_message)

    # 读取历史消息
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT role, content FROM messages WHERE conversation_id = %s ORDER BY id ASC",
                (conversation_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    history_messages = [{"role": r["role"], "content": r["content"]} for r in rows]

    try:
        ai_reply = call_llm(history_messages)
    except requests.RequestException as exc:
        return jsonify({"error": "UpstreamError", "message": f"调用模型接口失败: {str(exc)}"}), 502
    except Exception as exc:
        return jsonify({"error": "ServerError", "message": f"服务异常: {str(exc)}"}), 500

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (%s, %s, %s, %s)",
                    (conversation_id, "assistant", ai_reply, now_str()),
                )
    finally:
        conn.close()

    return jsonify({"reply": ai_reply})


# ── 删除账号 ──

@app.route("/api/account", methods=["DELETE"])
@clerk_required
def delete_account():
    """删除当前用户的所有数据（对话、消息、用户记录）。"""
    user = get_current_user()
    user_id = user["id"]

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = %s)",
                    (user_id,),
                )
                cur.execute("DELETE FROM conversations WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    finally:
        conn.close()

    return jsonify({"ok": True, "message": "账号数据已删除"})


# ── 错误处理 ──

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "NotFound", "message": "接口不存在"}), 404
    return render_template("index.html", clerk_publishable_key=CLERK_PUBLISHABLE_KEY, clerk_frontend_api=CLERK_FRONTEND_API)


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "ServerError", "message": "服务器内部错误"}), 500


# 启动时自动建表（带重试，等待 PostgreSQL 启动）
import time

for _attempt in range(10):
    try:
        init_db()
        break
    except Exception as e:
        print(f"等待数据库就绪... ({e})")
        time.sleep(3)
else:
    print("警告: 数据库初始化失败，服务将在首次请求时重试")
