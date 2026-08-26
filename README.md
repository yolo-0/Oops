# Oops 完整使用指南

本文档涵盖 Oops 的业务背景、主要功能、部署与启动、API 调用、知识库管理、ChromaDB 数据查看、监控评测及常见问题排障。

## 业务痛点与破局之道

相较于传统"套壳 ChatGPT"的智能客服产品，本系统针对企业级落地场景中最棘手的四大痛点进行了深度攻克：

### 🌟 1. 痛点一：模型幻觉
**问题**：Agent 生成虚假、矛盾或过时的信息，严重损害用户信任。

**根本原因**：知识库更新延迟、RAG 检索结果质量参差不齐、模型在检索失败时倾向于自动"补全"而非明确拒答、上下文存在大量噪声。

**解决方案**：
* 构建"准实时"知识库同步机制，保障数据新鲜度
* 检索质量深度优化——引入查询改写（Query Rewrite）与 LLM 重排（Rerank）
* 引入可信度评分机制，置信度低于阈值时自动触发拒答
* 意图识别层配备"兜底追问"策略，规避歧义输入
* 记忆压缩提取关键槽点并存储到memory中，避免噪声持续注入 

### 🌟 2. 痛点二：响应缓慢与服务雪崩
**问题**：响应迟缓，用户体验极差。

**根本原因**：向量检索延迟高、串联架构导致各阶段耗时逐级叠加、高并发场景下请求排队使整体吞吐量雪崩式下降。

**解决方案**：
* **异步并行处理架构**：在业务主轴上将"意图识别"与"知识库检索"链路解耦并行；底层子任务中，意图识别内的 Embedding 计算与 LLM 推理以及RAG 的多路子查询都通过异步并发调度，整体延迟降低 20% 以上。
* **断路器与快速降级**：知识库检索超过 10 秒即触发单次降级兜底；连续超时 5 次直接熔断全局断路器，有效保护后端线程池不被压垮。
* **语义缓存**：高频重复查询在向量缓存层直接拦截，100ms 内原样返回，大幅降低后端压力。

### 🌟 3. 痛点三：Token 成本爆炸
**问题**：LLM 推理成本是传统客服系统的数倍，难以规模化落地。

**根本原因**：超长上下文持续累积、重复查询反复消耗、昂贵旗舰模型被无差别高频调用。

**解决方案**：
* 接入 ChromaDB 语义缓存，可降低 70% 的重复查询 Token 消耗
* 多轮对话上下文压缩——将历史消息提炼为"用户画像（偏好与习惯）"及"服务总结"存档，大幅缩减 Prompt 长度
* 强制输出约束——通过 `max_tokens` 限制生成长度，并利用结构化格式约束杜绝无效 Token 输出
* 混合模型部署——简单任务与复杂任务分级路由至不同成本梯次的模型，极致控制推理成本


## 系统流程图

```mermaid
flowchart TD
    U(["👤 用户请求"]):::user

    subgraph 语义缓存层
        SC{"🗄️ 语义缓存\n命中？"}:::cache
    end

    subgraph 并行处理层
        direction LR
        subgraph 意图识别链路
            direction TB
            EMB["📐 Embedding\n向量相似度"]:::fast
            PAT["🔍 Pattern\n正则匹配"]:::fast
            LLM_I["🤖 LLM 意图推理\n(Fast Model)"]:::fast
            EMB & PAT & LLM_I --> FUSE["⚡ 三路融合\n置信度评分"]:::fast
        end
        subgraph 知识库检索链路
            direction TB
            RW["✍️ 查询改写\n(Fast Model)"]:::fast
            PR["🔀 并行多路召回\nasyncio.gather"]:::fast
            RK["🏆 LLM 重排\n(Fast Model)"]:::fast
            RW --> PR --> RK
        end
    end

    subgraph 记忆层
        MEM["🧠 四级记忆上下文\nRedis 工作记忆\nChromaDB 情景/画像/承诺"]:::mem
    end

    subgraph 断路器保护
        CB{"⚡ 断路器\n状态检查"}:::cb
        DG["⬇️ 快速降级\n兜底应答"]:::cb
    end

    subgraph Agent 路由层
        direction LR
        ORC["🎛️ AgentOrchestrator\n动态 Skills 注入"]:::orch
        GA["💬 GeneralAgent\n(Fast Model)"]:::agent
        TA["🔧 TechnicalAgent\n(Sonnet)"]:::agent
        BA["💳 BillingAgent\n(Sonnet)"]:::agent
        ESC["🚨 EscalationAgent\n人工转接"]:::agent
    end

    subgraph 生成与后处理
        GEN["✨ LLM 最终回复生成"]:::gen
        MC["📝 记忆写回\n异步压缩持久化"]:::gen
    end

    A(["📤 返回用户"]):::user

    U --> SC
    SC -- "命中 ✅\n< 100ms" --> A
    SC -- "未命中" --> 并行处理层
    SC -- "未命中" --> 记忆层
    并行处理层 --> CB
    记忆层 --> CB
    CB -- "OPEN 熔断" --> DG --> A
    CB -- "CLOSED 正常" --> ORC
    FUSE --> ORC
    RK --> ORC
    MEM --> ORC
    ORC --> GA & TA & BA & ESC
    GA & TA & BA --> GEN
    GEN --> MC
    GEN --> A

    classDef user fill:#6c63ff,color:#fff,stroke:none
    classDef cache fill:#f59e0b,color:#fff,stroke:none
    classDef fast fill:#10b981,color:#fff,stroke:none
    classDef mem fill:#3b82f6,color:#fff,stroke:none
    classDef cb fill:#ef4444,color:#fff,stroke:none
    classDef orch fill:#8b5cf6,color:#fff,stroke:none
    classDef agent fill:#06b6d4,color:#fff,stroke:none
    classDef gen fill:#ec4899,color:#fff,stroke:none
```

## 项目预览
### 1. 前端交互效果
系统提供了现代化的高级用户界面，支持暗黑模式以及响应式交互：

![前端效果 1](presentation/frontend-1.png)
![前端效果 2](presentation/frontend-2.png)
![前端效果 3](presentation/frontend-3.png)

### 2. 评测与监控报告 
- 📊 **[端到端多维评测报告 (Evaluation Result)](presentation/evaluation_result.json)**：展示了意图识别准确率、记忆准确度、工具抽取准确度以及 LLM-as-Judge 响应质量的五维得分。
- 📈 **[Agent 路由与工具监控报告 (Monitor Result)](presentation/moniter_result.json)**：包含了三类 Agent 和三大外部工具的线上真实调用次数、成功率及延迟表现。
- 🌲 **[动态技能树注入报告 (Summary Result)](presentation/summery_result.json)**：展示了基于本地知识的动态 SOP 规则实时加载的命中与激活状态。


---
## 1. 项目结构

```text
Oops/
├── api/main.py                    # FastAPI 入口路由
├── core/
│   ├── intent_recognizer.py       # 三路融合意图识别与降级
│   └── skill_loader.py            # 动态业务规则加载
├── agents/agent_orchestrator.py   # 多 Agent 路由与 Prompt 动态编排
├── memory/conversation_memory.py  # 四级记忆管理器 (Redis + ChromaDB)
├── mcp/
│   ├── tool_manager.py            # 高级工具调度 (改写/重排/熔断/缓存)
│   ├── semantic_cache.py          # 语义缓存
│   ├── knowledge_base.py          # ChromaDB RAG 知识库检索
│   └── business_tools.py          # 外部系统业务接口模拟对接
├── evaluation/
│   ├── evaluator.py               # LLM-as-Judge 端到端多维质量评测
│   └── memory_evaluator.py        # 记忆准确性专项评测
├── monitor/performance_monitor.py # Agent/工具在线监控与告警
├── frontend/                      # Vue 3 现代化前端交互界面
├── skills/                        # 业务规则与 SOP (Markdown 热加载)
├── data/demo_docs/                # 演示知识库文档
└── docker-compose.yml             # 全栈环境一键编排
```

## 2. 环境准备

### 2.1 必需依赖

- Docker
- Docker Compose
- Anthropic API Key，或兼容 Anthropic 协议的第三方 API Key

### 2.2 配置 `.env`

复制示例文件：

```bash
cp .env.example .env
```

最少需要配置：

```env
ANTHROPIC_API_KEY=your_api_key
```

如果使用 DeepSeek 这类 Anthropic 兼容接口，可以配置：

```env
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro
ANTHROPIC_API_KEY=your_deepseek_key
```

Docker Compose 场景下，Redis 和 ChromaDB 的连接由 `docker-compose.yml` 覆盖为容器内地址。通常不需要手动改：

```env
REDIS_PASSWORD=oops123
CHROMA_HOST=localhost
CHROMA_PORT=8001
```

### 2.3 全栈部署和 run 开发模式的区别

Oops 常用两种 Docker 启动方式：`docker compose up` 全栈部署，以及 `docker run` 开发模式。两者最大的区别是：**全栈部署会同时启动应用和依赖服务；run 开发模式通常只手动运行一个应用容器，依赖服务需要提前启动**。

| 对比项 | Docker Compose 全栈部署 | Docker run 开发模式 |
|--------|--------------------------|----------------------|
| 启动命令 | `docker compose up -d --build` | `docker run ... oops ...` |
| 启动内容 | Oops、Redis、ChromaDB、Prometheus、Nginx | 只启动你指定的单个容器 |
| Redis/ChromaDB | 自动启动并加入同一网络 | 必须先执行 `docker compose up -d redis chromadb` |
| 容器网络 | Compose 自动创建并管理 | 需要手动指定 `--network oops_oops-network` |
| 服务名解析 | 应用可直接访问 `redis`、`chromadb` | 只有加入同一网络后才可访问 `redis`、`chromadb` |
| 代码更新 | 通常需要 rebuild 或重启服务 | 挂载 `-v "$(pwd):/workspace"` 后，代码修改可直接生效，重启容器即可 |
| 适合场景 | 演示、联调、完整部署、HTTP API 服务 | 本地开发、调试 CLI、临时覆盖环境变量 |
| 常见问题 | API Key 或依赖健康检查失败 | 忘记启动 Redis/ChromaDB，导致 `redis:6379 Name or service not known` |

选择建议：

- 想完整体验 HTTP API、Swagger、Nginx、Prometheus：用 **Docker Compose 全栈部署**。
- 想调试源码或 CLI，并且希望本地改代码后快速重跑：用 **Docker run 开发模式**。
- 如果只是跑 CLI，最省心的方式是 `docker compose run --rm oops python api/main.py --cli`，它会自动使用 Compose 网络。

## 3. Docker Compose 全栈部署

推荐使用此方式启动完整服务。

```bash
docker compose up -d --build
```

查看服务状态：

```bash
docker compose ps
```

查看应用日志：

```bash
docker compose logs -f oops
```

看到 Oops 启动日志并且健康检查通过后，服务可用。

启动后的端口：

| 服务 | 容器名 | 宿主机端口 | 容器内端口 | 用途 |
|------|--------|------------|------------|------|
| Oops API | `oops-app` | `8000` | `8000` | 主 API 服务 |
| Nginx | `oops-nginx` | `80` | `80` | 反向代理 |
| ChromaDB | `oops-chromadb` | `8001` | `8000` | 向量数据库 |
| Redis | `oops-redis` | `6379` | `6379` | 工作记忆 |
| Prometheus | `oops-prometheus` | `9090` | `9090` | 监控数据 |

健康检查：

```bash
curl http://localhost:8000/health
```

Swagger 文档：

```text
http://localhost:8000/docs
```

也可以通过 Nginx 访问：

```bash
curl http://localhost/health
```

## 4. Docker Run 开发模式

开发时可以只用 Compose 启动依赖，然后用 `docker run` 挂载当前代码目录。

先启动 Redis 和 ChromaDB：

```bash
docker compose up -d redis chromadb
```

构建镜像：

```bash
docker compose build --no-cache oops
```

启动 HTTP 服务：

```bash
docker run -it --rm \
  --network oops_oops-network \
  -p 8000:8000 \
  -e ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic" \
  -e ANTHROPIC_API_KEY="your_key" \
  -e ANTHROPIC_MODEL="deepseek-v4-pro" \
  -e REDIS_URL="redis://:oops123@redis:6379/0" \
  -e CHROMA_HOST="chromadb" \
  -e CHROMA_PORT="8000" \
  -e CHROMA_PERSIST_DIRECTORY="/workspace/data/chroma" \
  -v "$(pwd):/workspace" \
  -w /workspace \
  oops
```

CLI 交互模式：

```bash
docker run -it --rm \
  --network oops_oops-network \
  -e ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic" \
  -e ANTHROPIC_API_KEY="your_key" \
  -e ANTHROPIC_MODEL="deepseek-v4-pro" \
  -e REDIS_URL="redis://:oops123@redis:6379/0" \
  -e CHROMA_HOST="chromadb" \
  -e CHROMA_PORT="8000" \
  -v "$(pwd):/workspace" \
  -w /workspace \
  oops \
  python api/main.py --cli
```

## 14. 停止、重启和清理

停止服务：

```bash
docker compose stop
```

重启服务：

```bash
docker compose restart oops
```

停止并删除容器，但保留数据卷：

```bash
docker compose down
```

停止并删除容器和数据卷：

```bash
docker compose down -v
```

重新构建并启动：

```bash
docker compose up -d --build
```

## 📚 详细文档导航 (Documentation)

为了方便查阅，我们将项目的详细架构说明和 API 参考文档进行了分类归档。**推荐在快速体验后，深入阅读以下文档：**

- 🔗 [API 参考与调用示例 (API Reference)](docs/api_reference.md)
  - 详细的 `/chat`, `/knowledge`, `/monitor`, `/eval` 等接口说明
  - 丰富的 `curl` 调用示例和多轮对话演示
- 🔗 [存储与记忆架构解析 (Memory & Storage)](docs/memory_and_storage.md)
  - 四级记忆架构底层的具体实现机制
  - 如何在 Docker 容器中查看/调试 ChromaDB 和 Redis 数据
- 🔗 [高级特性：评测、监控与工具 (Advanced Features)](docs/advanced_features.md)
  - 端到端评测框架 (LLM-as-Judge) 与 Memory Benchmark 白盒记忆评测引擎
  - MCP 工具调度机制 (改写/缓存/重排/熔断) 与在线监控平台
- 🔗 [常见问题排查 (FAQ)](docs/faq.md)
  - 各类环境报错排查（如 503、Redis 认证失败）与详细的测试验证流程日志\n
