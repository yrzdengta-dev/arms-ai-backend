# ARMS AI Audit System — Backend

## 技术栈

Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + asyncpg + PostgreSQL + Alembic + Celery + Redis + SSE + MinIO

## 快速启动

### 前置条件

- Docker & Docker Compose
- Python 3.12 (本地开发)

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 按需修改配置
```

### 2. Docker Compose 启动

```bash
docker compose up -d
```

启动服务：
- API: http://localhost:8000
- PostgreSQL: localhost:5432
- Redis: localhost:6379
- MinIO Console: http://localhost:9001

### 3. 数据库迁移

```bash
pip install alembic asyncpg
alembic upgrade head
```

### 4. 查看日志

```bash
docker compose logs -f api
docker compose logs -f worker
```

### 5. 停止

```bash
docker compose down
```

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/ready` | 就绪检查（含数据库连接） |
| POST | `/api/v1/orders/ingest` | 上传单据快照 |
| POST | `/api/v1/orders/batch-ingest` | 批量上传 |
| GET | `/api/v1/orders` | 单据列表（分页+筛选） |
| GET | `/api/v1/orders/stats` | 当前用户统计 |
| GET | `/api/v1/orders/{task_order_id}` | 单据详情 |
| POST | `/api/v1/orders/{task_order_id}/retry` | 重试失败单据 |
| GET | `/api/v1/events/stream` | SSE 事件流 |

## 身份机制（MVP）

当前使用 HTTP 请求头 `X-ARMS-User` 标识用户。这只是 MVP 阶段的临时身份机制，**不是生产级认证**，可以被伪造。

未来将替换为 JWT / SSO 正式身份认证。

身份逻辑封装在 `app/core/identity.py` 的 `get_current_user` 依赖中，替换时只需修改该模块。

## 运行测试

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## 代码质量

```bash
ruff check .
mypy app/
```

## 目录结构

```
backend/
├── app/
│   ├── api/v1/endpoints/   # API 路由
│   ├── core/               # 配置、数据库、身份、状态机
│   ├── models/             # SQLAlchemy 数据模型
│   ├── schemas/            # Pydantic 校验
│   ├── repositories/       # 数据访问层
│   ├── services/           # 业务逻辑层
│   ├── adapters/           # 外部系统适配器
│   │   ├── storage/        # MinIO 对象存储
│   │   ├── pdf/            # PDF 下载和解析
│   │   └── llm/            # AI Provider
│   ├── skills/             # Skill 注册表和 Prompt
│   │   ├── manifests/      # Skill manifest YAML
│   │   └── prompts/        # Prompt 模板
│   ├── workers/            # Celery 配置和任务
│   └── main.py             # FastAPI 入口
├── migrations/             # Alembic 迁移
├── tests/                  # 测试
│   ├── unit/
│   ├── integration/
│   └── acceptance/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .env.example
└── README.md
```
