# AI 聊天助手 (Chat App)

一个基于 Flask + DeepSeek API 的 AI 聊天 Web 应用，支持多轮对话、深度思考、流式输出，并通过 Docker Swarm 集群实现高可用部署。

![AI 聊天助手界面](https://chat.wafy123.me)

## 📋 项目概述

| 项目 | 说明 |
|------|------|
| **线上地址** | [https://chat.wafy123.me](https://chat.wafy123.me) |
| **后端框架** | Flask (Python 3.11) |
| **AI 模型** | DeepSeek（支持深度思考模式） |
| **数据库** | PostgreSQL 16 |
| **认证** | Clerk（JWT 本地验证） |
| **反向代理** | Caddy（自动 HTTPS） |
| **容器编排** | Docker Swarm（3 节点集群） |
| **CI/CD** | GitHub Actions → GHCR → 自动部署 |

## ✨ 功能特性

- **多轮 AI 对话**：基于 DeepSeek 大语言模型，支持上下文连续对话
- **深度思考模式**：可开启 AI 推理链展示，查看 AI 的思考过程（可折叠）
- **流式输出 (SSE)**：实时逐字显示 AI 回复，体验流畅
- **对话管理**：新建对话、删除对话、对话列表切换
- **全文搜索**：支持按标题/内容搜索历史对话
- **Markdown 渲染**：AI 回复支持 Markdown 格式化 + LaTeX 数学公式
- **用户认证**：集成 Clerk 第三方认证，安全的 JWT 本地验证
- **响应式设计**：深色主题 UI，适配桌面和移动端

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      GitHub Actions CI/CD                    │
│         Test (lint+安全扫描) → Build → Push → Deploy         │
└──────────────────────────┬──────────────────────────────────┘
                           │ SSH 部署
                           ▼
┌─────────────────── Docker Swarm 集群 ───────────────────────┐
│                                                              │
│  ┌─── Manager 节点 ────────────────────────────────────┐    │
│  │  Caddy (1 副本)          ← 自动 HTTPS + 反向代理     │    │
│  │  PostgreSQL (1 副本)     ← 数据持久化                │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─── Worker 节点 ×2 ──────────────────────────────────┐    │
│  │  Web Service (3 副本)    ← Flask + Gunicorn/Gevent   │    │
│  │  (轮询负载均衡)                                       │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  Overlay Network: 10.0.10.0/24                               │
└──────────────────────────────────────────────────────────────┘
```

### 请求流转

```
用户浏览器 → Cloudflare DNS → Caddy (TLS 终止 + 负载均衡)
    → Web Service (Flask) → DeepSeek API (SSE 流式)
                          → PostgreSQL (数据持久化)
                          → Clerk API (JWT 验证)
```

## 📁 项目结构

```
.
├── app/
│   ├── app.py                 # Flask 后端主程序
│   ├── clerk_middleware.py     # Clerk JWT 认证中间件
│   ├── Dockerfile             # 应用容器镜像构建
│   ├── requirements.txt       # Python 依赖
│   └── templates/
│       └── index.html         # 前端单页应用 (SPA)
├── .github/
│   └── workflows/
│       └── ci-cd.yml          # CI/CD 流水线配置
├── docker-compose.yml         # 本地开发环境
├── docker-compose.swarm.yml   # 生产 Swarm 部署
├── Caddyfile                  # Caddy 反向代理配置
└── data/                      # 本地开发数据目录
```

## 🔧 技术栈详解

### 后端

| 技术 | 用途 |
|------|------|
| **Flask** | 轻量 Web 框架，提供 RESTful API |
| **Gunicorn + Gevent** | WSGI 服务器，gevent 异步 worker 支持 SSE 长连接 |
| **psycopg2** | PostgreSQL 数据库驱动 |
| **PyJWT** | Clerk JWT Token 本地验证（RS256 签名） |
| **requests** | 调用 DeepSeek LLM API |

### 前端

| 技术 | 用途 |
|------|------|
| **原生 JS (SPA)** | 单页应用，无框架依赖 |
| **Clerk.js** | 用户认证 UI 组件 |
| **marked.js** | Markdown 渲染 |
| **KaTeX** | LaTeX 数学公式渲染 |
| **SSE (EventSource)** | 实时接收 AI 流式回复 |

### 基础设施

| 技术 | 用途 |
|------|------|
| **Docker Swarm** | 容器编排，3 节点集群 (1 Manager + 2 Worker) |
| **Caddy** | 反向代理，自动 Let's Encrypt TLS 证书 |
| **PostgreSQL 16** | 关系型数据库，存储用户、对话、消息 |
| **GHCR** | GitHub Container Registry，容器镜像仓库 |
| **GitHub Actions** | CI/CD 自动化流水线 |

## 🗄️ 数据库设计

```sql
-- 用户表
CREATE TABLE users (
    id TEXT PRIMARY KEY,            -- Clerk 用户 ID
    email TEXT,
    username TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- 对话表
CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,             -- 默认为首条消息内容
    created_at TIMESTAMPTZ NOT NULL
);

-- 消息表
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,              -- 'user' 或 'assistant'
    content TEXT NOT NULL,
    reasoning_content TEXT DEFAULT '',  -- 深度思考推理内容
    created_at TIMESTAMPTZ NOT NULL
);
```

## 🌐 API 接口

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/` | 渲染主页 | 否 |
| GET | `/api/health` | 健康检查 | 否 |
| GET | `/api/auth/me` | 获取当前用户信息 | ✅ |
| GET | `/api/conversations` | 获取对话列表 | ✅ |
| POST | `/api/conversations` | 创建新对话 | ✅ |
| DELETE | `/api/conversations/<id>` | 删除对话 | ✅ |
| GET | `/api/conversations/<id>/messages` | 获取对话消息 | ✅ |
| POST | `/api/chat` | 发送消息 (SSE 流式响应) | ✅ |
| GET | `/api/conversations/search?q=` | 搜索对话 | ✅ |
| DELETE | `/api/account` | 删除用户账号 | ✅ |

## 🚀 部署说明

### 环境要求

- Docker 29.x+
- Docker Swarm 集群 (至少 3 节点)
- 域名 + Cloudflare DNS（指向 Manager 节点公网 IP）

### GitHub Secrets 配置

CI/CD 部署需要在 GitHub 仓库中配置以下 Secrets：

| Secret | 说明 |
|--------|------|
| `API_KEY` | DeepSeek API Key |
| `LLM_API_URL` | DeepSeek API 地址 |
| `MODEL_NAME` | 使用的模型名称 |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 |
| `CLERK_PUBLISHABLE_KEY` | Clerk 前端公钥 |
| `CLERK_SECRET_KEY` | Clerk 后端密钥 |
| `CLERK_FRONTEND_API` | Clerk Frontend API 地址 |
| `SSH_PRIVATE_KEY` | 部署用 SSH 私钥 |
| `SSH_USER` | SSH 用户名 |
| `SWARM_MANAGER_IP` | Swarm Manager 节点公网 IP |

### CI/CD 流水线

推送到 `master` 分支自动触发：

```
1. Test       → flake8 代码检查 + bandit 安全扫描
2. Build      → Docker 多平台构建 (amd64/arm64) → 推送到 GHCR
3. Deploy     → SSH 连接 Manager → docker stack deploy 滚动更新
```

部署特性：
- **滚动更新**：每次更新 1 个副本，先启后停 (start-first)，更新失败自动回滚
- **健康检查**：Web 服务 `/api/health` 端点 + PostgreSQL `pg_isready`
- **资源限制**：CPU 1.0 核 / 内存 512MB 上限
- **Caddy Config 轮转**：每次部署生成新的 config 名称，解决 Swarm config 不可变问题

### 本地开发

```bash
# 克隆仓库
git clone git@github.com:baitang2233/chat-app.git
cd chat-app

# 配置环境变量（在 app 目录下创建 .env 文件）
cat > app/.env << EOF
API_KEY=your_deepseek_api_key
LLM_API_URL=https://api.deepseek.com/v1/chat/completions
MODEL_NAME=deepseek-reasoner
POSTGRES_PASSWORD=chatapp
DATABASE_URL=postgresql://chatapp:chatapp@db:5432/chatapp
CLERK_PUBLISHABLE_KEY=your_clerk_key
CLERK_SECRET_KEY=your_clerk_secret
CLERK_FRONTEND_API=your_clerk_frontend_api
EOF

# 启动本地开发环境
docker-compose up --build
```

访问 http://localhost 即可使用。

## 🔒 安全设计

- **认证**：Clerk JWT 本地验证（RS256），JWKS 公钥缓存 + 自动轮转
- **授权**：每个 API 均验证用户身份，用户只能访问自己的数据
- **SQL 注入防护**：全部使用参数化查询
- **级联删除**：删除用户时自动清理关联对话和消息
- **CI 安全扫描**：bandit 自动检测 Python 代码安全漏洞
