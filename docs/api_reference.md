# API 参考与调用示例

## 5. Swagger 和接口总览

Oops 基于 FastAPI 构建，启动 HTTP 服务后可以直接在浏览器访问 Swagger UI 调用接口。

本地 Swagger 地址：

```text
http://localhost:8000/docs
```

如果使用 Nginx 反向代理：

```text
http://localhost/docs
```

打开 Swagger 后，可以点击任意接口右侧的 **Try it out**，填写参数后点 **Execute** 直接调用本地服务。常用调试顺序：

```text
1. GET /health                确认服务是否就绪
2. POST /chat                 测试主对话链路
3. GET /knowledge/stats       查看知识库是否已有数据
4. POST /knowledge/upload     上传演示知识库文件
5. POST /search               测试知识库检索、查询改写和重排
6. GET /monitor               查看 Agent 和工具运行指标
7. GET /skills                查看已加载 Skills
8. POST /skills/reload        重新加载 Skills
9. POST /eval/run             运行端到端评测
```

### 5.1 接口总览

| 方法 | 路径 | 参数位置 | 作用 | 适合场景 |
|------|------|----------|------|----------|
| `GET` | `/health` | 无 | 健康检查，返回服务状态和 Agent 统计 | 启动后确认服务可用 |
| `POST` | `/chat` | JSON Body | 主对话接口，完成记忆读取、意图识别、Agent 路由、回复生成、记忆写入 | 业务主链路 |
| `GET` | `/monitor` | 无 | 查看 Agent/工具统计、告警和优化建议 | 观察在线表现 |
| `POST` | `/search` | Query 参数 | 执行知识库检索优化链路：查询改写、并行召回、合并去重、LLM 重排 | 测试 RAG 检索 |
| `GET` | `/skills` | 无 | 查看当前加载的 Skills、匹配关键词和解析错误 | 确认动态能力是否生效 |
| `POST` | `/skills/reload` | 无 | 运行时重新扫描 Skill 目录 | 修改业务规则后热加载 |
| `POST` | `/knowledge/add` | JSON Body | 批量导入文档到 ChromaDB 知识库 | 程序化导入文档 |
| `POST` | `/knowledge/upload` | Form File | 上传 `.txt`、`.md`、`.json` 文件导入知识库 | 手动上传知识库文件 |
| `GET` | `/knowledge/stats` | 无 | 查看知识库文档片段总数 | 确认知识库是否有数据 |
| `POST` | `/eval/run` | 无 | 运行内置意图识别和端到端对话评测 | 演示 LLM-as-Judge 评测 |
| `GET` | `/docs` | 浏览器访问 | Swagger UI | 浏览和调试所有接口 |

### 5.2 Skills 动态能力加载

Oops 支持从目录加载 Skills，用来把业务流程、客服话术、排障 SOP 等规则动态注入 Agent。

默认配置：

```env
OOPS_SKILLS_DIR=./skills
OOPS_SKILLS_MAX_PROMPT_CHARS=5000
```

推荐结构：

```text
skills/refund/SKILL.md
skills/customer_support/SKILL.md
```

`SKILL.md` 示例：

```markdown
---
name: 退款处理流程
description: 退款场景的客服处理规则
keywords: 退款,退费,refund
agents: billing,general
enabled: true
---

# 退款处理流程

- 先确认订单号和支付方式。
- 涉及实际退款操作时转人工审核。
```

查看加载结果：

```bash
curl http://localhost:8000/skills
```

修改 Skill 文件后热加载：

```bash
curl -X POST http://localhost:8000/skills/reload
```

### 5.3 `/health`

用途：确认服务是否初始化完成。

```bash
curl http://localhost:8000/health
```

响应示例：

```json
{
  "status": "ok",
  "agents": {
    "general_0": {
      "total": 0,
      "success_rate": 1.0,
      "avg_ms": 0.0,
      "monitor_penalty": 0.0,
      "routing_score": 1.0
    }
  }
}
```

### 5.4 `/chat`

用途：主对话接口。

请求体：

```json
{
  "message": "我要退款",
  "user_id": "user_001",
  "conv_id": "session_001"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `message` | 是 | 用户输入 |
| `user_id` | 否 | 用户 ID，默认 `anonymous` |
| `conv_id` | 否 | 会话 ID，不传则自动生成 |

返回字段：

| 字段 | 说明 |
|------|------|
| `conv_id` | 会话 ID |
| `response` | Agent 回复 |
| `intent` | 意图识别结果 |
| `agent_type` | 实际处理请求的 Agent |
| `escalated` | 是否触发升级 |
| `latency_ms` | 端到端耗时 |

### 5.5 `/search`

用途：测试 MCP 工具调用和 RAG 检索优化。

Query 参数：

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | 是 | 无 | 用户检索问题 |
| `top_k` | 否 | `5` | 返回结果数量 |

示例：

```bash
curl -X POST "http://localhost:8000/search?query=退款多久到账&top_k=3"
```

### 5.6 `/knowledge/add`

用途：通过 JSON 批量导入知识库。

请求体：

```json
{
  "documents": [
    {
      "title": "退款政策",
      "content": "用户在购买后 7 天内可以申请无理由退款..."
    }
  ]
}
```

### 5.7 `/knowledge/upload`

用途：上传文件导入知识库。

支持格式：

| 格式 | 说明 |
|------|------|
| `.txt` | 整个文件作为一篇文档 |
| `.md` | 整个文件作为一篇文档 |
| `.json` | JSON 数组，格式为 `[{ "title": "...", "content": "..." }]` |

示例：

```bash
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@data/demo_docs/sample_knowledge.json"
```

### 5.8 `/knowledge/stats`

用途：查看知识库片段数量。

```bash
curl http://localhost:8000/knowledge/stats
```

### 5.9 `/monitor`

用途：查看 Agent 和工具在线指标。

```bash
curl http://localhost:8000/monitor
```

返回内容包括：

| 字段 | 说明 |
|------|------|
| `agent_stats` | Agent 调用次数、成功率、延迟、routing_score |
| `tool_stats` | 工具调用次数、成功率、延迟、熔断状态 |
| `active_alerts` | 最近告警 |
| `suggestions` | 优化建议 |

### 5.10 `/eval/run`

用途：运行内置评测。

```bash
curl -X POST http://localhost:8000/eval/run
```

返回内容包括：

| 字段 | 说明 |
|------|------|
| `pass_rate` | 评测通过率 |
| `total` | 评测项总数 |
| `passed` | 通过项数量 |
| `avg_scores` | 平均评分 |
| `regressions` | 回归检测结果 |
| `recommendations` | 优化建议 |
| `results` | 每条评测结果 |

## 6. 使用项目

### 6.1 主对话接口

请求：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我的订单什么时候到？",
    "user_id": "user_001",
    "conv_id": "session_001"
  }'
```

响应示例：

```json
{
  "conv_id": "session_001",
  "response": "请提供订单号，我可以帮您查询订单状态和物流进度。",
  "intent": "query",
  "agent_type": "general",
  "escalated": false,
  "latency_ms": 1234.5
}
```

字段说明：

| 字段 | 含义 |
|------|------|
| `message` | 用户输入 |
| `user_id` | 用户唯一标识，用于隔离记忆和用户画像 |
| `conv_id` | 会话 ID，相同 `conv_id` 表示同一轮多轮对话 |
| `intent` | 识别出的意图 |
| `agent_type` | 实际处理请求的 Agent |
| `escalated` | 是否触发升级/转人工 |
| `latency_ms` | 端到端延迟 |

### 6.2 多轮对话

多轮对话只需要保持同一个 `user_id` 和 `conv_id`。

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "订单号是 A123456",
    "user_id": "user_001",
    "conv_id": "session_001"
  }'
```

系统会从 Redis 读取当前会话最近消息，并从 ChromaDB 读取相关历史和用户画像，拼成上下文传给 Agent。

### 6.3 技术问题示例

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "应用登录一直报 401 错误",
    "user_id": "user_tech",
    "conv_id": "tech_001"
  }'
```

预期会路由到 `technical` Agent。

### 6.4 账单问题示例

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "为什么这个月重复扣款了？我要退款",
    "user_id": "user_bill",
    "conv_id": "bill_001"
  }'
```

预期会路由到 `billing` Agent。

### 6.5 复合问题示例

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "登录报错 401，而且这个月还重复扣款了",
    "user_id": "user_mix",
    "conv_id": "mix_001"
  }'
```

这类问题会触发多 Agent 并行协作，由技术 Agent 和账单 Agent 分别处理后合并回复。

## 7. 知识库使用

Oops 的知识库由 `mcp/knowledge_base.py` 管理，底层使用 ChromaDB collection：

```text
knowledge_base
```

首次启动时，如果知识库为空，会自动导入默认客服文档，包括退款政策、订单查询、账户安全、技术故障排查、会员积分、配送说明。

### 7.1 查看知识库统计

```bash
curl http://localhost:8000/knowledge/stats
```

响应示例：

```json
{
  "total_chunks": 18
}
```

### 7.2 批量导入文档

```bash
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "title": "退换货政策",
        "content": "用户在购买后 7 天内可以申请无理由退货，审核通过后 5-7 个工作日退款。"
      },
      {
        "title": "会员权益",
        "content": "金卡会员享受 9 折优惠，生日当月可获得双倍积分。"
      }
    ]
  }'
```

系统会把长文档切成 500 字左右的片段，并写入 ChromaDB。

### 7.3 上传文件导入知识库

上传 Markdown：

```bash
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@data/demo_docs/troubleshooting.md"
```

上传 JSON：

```bash
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@data/demo_docs/sample_knowledge.json"
```

JSON 格式必须是数组：

```json
[
  {
    "title": "文档标题",
    "content": "文档内容"
  }
]
```

### 7.4 检索知识库

```bash
curl -X POST "http://localhost:8000/search?query=退款需要多久到账&top_k=3"
```

响应示例：

```json
{
  "query": "退款需要多久到账",
  "results": [
    {
      "title": "退款政策",
      "content": "审核通过后，款项将在 5-7 个工作日内退回原支付账户。",
      "score": 0.82,
      "chunk": 0
    }
  ],
  "reranked": true
}
```

`/search` 使用的是完整检索优化链路：

```text
原始查询
  -> LLM 查询改写成多个角度
  -> 多个子查询并行召回 ChromaDB
  -> 合并去重
  -> LLM 重排
  -> 返回 Top-K
```