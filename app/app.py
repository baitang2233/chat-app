import os
import sqlite3
from datetime import datetime,timezone

import requests
from flask import Flask, jsonify, render_template, request

from clerk_middleware import clerk_required, get_current_user

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "./data/chat.db")
API_KEY = os.environ.get("API_KEY")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-3.5-turbo")
LLM_API_URL = os.environ.get("LLM_API_URL")
















def get_conn():
    """获取数据库连接，timeout=10 防止高并发下快速报错。"""
    return sqlite3.connect(DB_PATH, timeout=10)


def init_db():
    """启动时自动建表。"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with get_conn() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT,
                username TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)
            """
        )

        conn.commit()






def now_str():
    return datetime.now(timezone.utc).isoformat()






def ensure_user_exists(user):
    """确保 Clerk 用户在本地数据库中存在"""
    user_id = user.get("id")
    email = user.get("email")
    username = user.get("username")
    now = now_str()

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO users (id, email, username, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, email, username, now, now)
            )
            conn.commit()
        else:
            cursor.execute(
                "UPDATE users SET email = ?, username = ?, updated_at = ? WHERE id = ?",
                (email, username, now, user_id)
            )
            conn.commit()


# 健康检查
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": now_str()})


CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_FRONTEND_API = os.environ.get("CLERK_FRONTEND_API", "")


@app.route("/")
def index():
    return render_template("index.html", clerk_publishable_key=CLERK_PUBLISHABLE_KEY, clerk_frontend_api=CLERK_FRONTEND_API)




@app.route("/api/auth/me")
@clerk_required
def auth_me():
    """返回当前登录用户信息。"""
    user = get_current_user()
    ensure_user_exists(user)
    return jsonify({"user": user})
#好像写了没用啊(・∀・(・∀・(・∀・*)



@app.route("/api/conversations", methods=["GET"])
@clerk_required
def list_conversations():
    user = get_current_user()
    ensure_user_exists(user)
    user_id = user["id"]

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, created_at FROM conversations WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        ).fetchall()

    conversations = [dict(row) for row in rows]
    return jsonify(conversations)



@app.route("/api/conversations", methods=["POST"])
@clerk_required
def create_conversation():
    user = get_current_user()
    ensure_user_exists(user)
    user_id = user["id"]

    title = "Ciallo～(∠・ω< )⌒★"
    created_at = now_str()

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (user_id, title, created_at) VALUES (?, ?, ?)",
            (user_id, title, created_at),
        )
        conversation_id = cursor.lastrowid
        conn.commit()

    return jsonify({"id": conversation_id, "title": title, "created_at": created_at})






@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
@clerk_required
def delete_conversation(conversation_id):
    user = get_current_user()
    user_id = user["id"]

    with get_conn() as conn:
        cursor = conn.cursor()
        convo = cursor.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id)
        ).fetchone()
        if not convo:
            return jsonify({"error": "Forbidden", "message": "无权限删除该对话"}), 403

        cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        cursor.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()

    return jsonify({"ok": True})

@app.route("/api/conversations/<int:conversation_id>/messages", methods=["GET"])
@clerk_required
def get_messages(conversation_id):
    user = get_current_user()
    user_id = user["id"]

    with get_conn() as conn:
        cursor = conn.cursor()
        convo = cursor.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id)
        ).fetchone()
        if not convo:
            return jsonify({"error": "Forbidden", "message": "无权限访问该对话(・∀・(・∀・(・∀・*)吓人"}), 403

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()

    messages = [dict(row) for row in rows]
    return jsonify(messages)


def update_title_if_first_message(conversation_id, first_user_message):
    """如果当前对话标题还是Ciallo～(∠・ω< )⌒★，就用第一条用户消息前20个字符作为标题。"""
    title = first_user_message.strip()[:20] or "Ciallo～(∠・ω< )⌒★"

    with get_conn() as conn:
        cursor = conn.cursor()
        convo = cursor.execute(
            "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()

        if not convo:
            return

        current_title = convo[0]
        if current_title == "Ciallo～(∠・ω< )⌒★":
            cursor.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conversation_id),
            )
            conn.commit()


def call_llm(messages):
    """调用 OpenAI 接口。"""
    if not API_KEY:
        raise ValueError("缺少 API_KEY 环境变量，话说报错会是怎样的")

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
        return jsonify({"error": "BadRequest", "message": "conversation_id 和 message 不能为空QAQ"}), 400

    with get_conn() as conn:
        cursor = conn.cursor()
        convo = cursor.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id)
        ).fetchone()
        if not convo:
            return jsonify({"error": "Forbidden", "message": "无权限访问该对话"}), 403

        cursor.execute(
            """
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, "user", user_message, now_str()),
        )
        conn.commit()




    update_title_if_first_message(conversation_id, user_message)

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()

    history_messages = [{"role": row["role"], "content": row["content"]} for row in rows]

    try:
        ai_reply = call_llm(history_messages)
    except requests.RequestException as exc:
        return jsonify({"error": "UpstreamError", "message": f"调用模型接口失败: {str(exc)}"}), 502
    except Exception as exc:
        return jsonify({"error": "ServerError", "message": f"服务异常: {str(exc)}"}), 500

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, "assistant", ai_reply, now_str()),
        )
        conn.commit()




    return jsonify({"reply": ai_reply})


@app.route("/api/account", methods=["DELETE"])
@clerk_required
def delete_account():
    """删除当前用户的所有数据（对话、消息、用户记录）。"""
    user = get_current_user()
    user_id = user["id"]

    with get_conn() as conn:
        cursor = conn.cursor()
        # 先删所有消息
        cursor.execute(
            """
            DELETE FROM messages WHERE conversation_id IN (
                SELECT id FROM conversations WHERE user_id = ?
            )
            """,
            (user_id,),
        )
        # 再删所有对话
        cursor.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        # 最后删用户
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    return jsonify({"ok": True, "message": "账号数据已删除"})


# 统一错误处理
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "NotFound", "message": "接口不存在"}), 404
    return render_template("index.html", clerk_publishable_key=CLERK_PUBLISHABLE_KEY, clerk_frontend_api=CLERK_FRONTEND_API)


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "ServerError", "message": "服务器内部错误"}), 500


init_db()


































if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
