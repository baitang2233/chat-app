import json
import os
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

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
                        reasoning_content TEXT DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)
                """)
                # 兼容旧表：添加 reasoning_content 列
                cur.execute("""
                    DO $$ BEGIN
                        ALTER TABLE messages ADD COLUMN reasoning_content TEXT DEFAULT '';
                    EXCEPTION WHEN duplicate_column THEN NULL;
                    END $$
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
                "SELECT id, role, content, reasoning_content, created_at FROM messages WHERE conversation_id = %s ORDER BY id ASC",
                (conversation_id,),
            )
            messages = cur.fetchall()
    finally:
        conn.close()

    for m in messages:
        m["created_at"] = m["created_at"].isoformat() if m["created_at"] else None

    return jsonify(messages)


# ── 聊天（流式传输） ──

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


@app.route("/api/chat", methods=["POST"])
@clerk_required
def chat():
    user = get_current_user()
    user_id = user["id"]

    body = request.get_json(silent=True) or {}
    conversation_id = body.get("conversation_id")
    user_message = (body.get("message") or "").strip()
    enable_thinking = body.get("thinking", False)

    if not conversation_id or not user_message:
        return jsonify({"error": "BadRequest", "message": "conversation_id 和 message 不能为空"}), 400

    # 验证权限并保存用户消息
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

    def generate():
        """SSE 流式生成器"""
        if not API_KEY:
            yield f"data: {json.dumps({'error': '缺少 API_KEY 环境变量'})}\n\n"
            return

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": MODEL_NAME,
            "messages": history_messages,
            "temperature": 0.7,
            "stream": True,
        }

        if enable_thinking:
            payload["enable_thinking"] = True

        full_content = ""
        full_reasoning = ""

        try:
            resp = requests.post(LLM_API_URL, headers=headers, json=payload, timeout=120, stream=True)
            resp.raise_for_status()

            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    reasoning = delta.get("reasoning_content", "")
                    if reasoning:
                        full_reasoning += reasoning
                        yield f"data: {json.dumps({'type': 'reasoning', 'content': reasoning})}\n\n"

                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"

                except json.JSONDecodeError:
                    continue

            # 流结束，保存到数据库
            if full_content:
                conn2 = get_conn()
                try:
                    with conn2:
                        with conn2.cursor() as cur2:
                            cur2.execute(
                                "INSERT INTO messages (conversation_id, role, content, reasoning_content, created_at) VALUES (%s, %s, %s, %s, %s)",
                                (conversation_id, "assistant", full_content, full_reasoning, now_str()),
                            )
                finally:
                    conn2.close()

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except requests.RequestException as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': f'调用模型接口失败: {str(exc)}'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': f'服务异常: {str(exc)}'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── 搜索对话 ──

@app.route("/api/conversations/search", methods=["GET"])
@clerk_required
def search_conversations():
    user = get_current_user()
    user_id = user["id"]
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify([])

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT DISTINCT c.id, c.title, c.created_at
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.user_id = %s AND (c.title ILIKE %s OR m.content ILIKE %s)
                   ORDER BY c.id DESC LIMIT 20""",
                (user_id, f"%{query}%", f"%{query}%"),
            )
            results = cur.fetchall()
    finally:
        conn.close()

    for r in results:
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None

    return jsonify(results)


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
for _attempt in range(10):
    try:
        init_db()
        break
    except Exception as e:
        print(f"等待数据库就绪... ({e})")
        time.sleep(3)
else:
    print("警告: 数据库初始化失败，服务将在首次请求时重试")
