# 存储与记忆架构深度解析

## 8. ChromaDB 在项目中的用途

Oops 使用了三个 ChromaDB collection：

| Collection | 模块 | 作用 |
|------------|------|------|
| `knowledge_base` | `mcp/knowledge_base.py` | RAG 知识库文档片段 |
| `episodic` | `memory/conversation_memory.py` | 压缩后的历史对话摘要 |
| `user_profile` | `memory/conversation_memory.py` | 用户画像，包含偏好和关键实体 |

数据写入时机：

| 数据 | 写入时机 |
|------|----------|
| `knowledge_base` | 启动时自动导入默认文档，或调用 `/knowledge/add`、`/knowledge/upload` |
| `episodic` | 当前会话工作记忆超过阈值后自动压缩并写入 |
| `user_profile` | 每次 `/chat` 回复后异步提炼并更新 |

## 9. 在 Docker 中查看 ChromaDB 内容

Compose 中 ChromaDB 容器名是：

```text
oops-chromadb
```

宿主机访问端口是：

```text
http://localhost:8001
```

容器内部端口是：

```text
http://localhost:8000
```

### 9.1 查看 ChromaDB 是否存活

宿主机执行：

```bash
curl http://localhost:8001/api/v1/heartbeat
```

容器内执行：

```bash
docker exec -it oops-chromadb curl http://localhost:8000/api/v1/heartbeat
```

### 9.2 查看所有 collection

```bash
curl http://localhost:8001/api/v1/collections
```

如果 ChromaDB 版本返回 tenant/database 相关错误，可以使用 Python 客户端查看，见下一节。

### 9.3 用 Python 客户端查看 collections

进入应用容器：

```bash
docker exec -it oops-app bash
```

在容器里执行：

```bash
python - <<'PY'
import chromadb

client = chromadb.HttpClient(host="chromadb", port=8000)
print("heartbeat:", client.heartbeat())

collections = client.list_collections()
print("collections:")
for c in collections:
    print("-", c.name, "count=", c.count())
PY
```

预期可以看到：

```text
collections:
- knowledge_base count= ...
- episodic count= ...
- user_profile count= ...
```

### 9.4 查看 `knowledge_base` 文档内容

```bash
docker exec -it oops-app bash
```

执行：

```bash
python - <<'PY'
import chromadb

client = chromadb.HttpClient(host="chromadb", port=8000)
col = client.get_collection("knowledge_base")

data = col.get(limit=10, include=["documents", "metadatas"])
for i, doc_id in enumerate(data["ids"]):
    print("=" * 80)
    print("id:", doc_id)
    print("metadata:", data["metadatas"][i])
    print("document:", data["documents"][i][:500])
PY
```

### 9.5 查询 `knowledge_base`

```bash
docker exec -it oops-app bash
```

执行：

```bash
python - <<'PY'
import chromadb

client = chromadb.HttpClient(host="chromadb", port=8000)
col = client.get_collection("knowledge_base")

result = col.query(
    query_texts=["退款多久到账"],
    n_results=3,
    include=["documents", "metadatas", "distances"],
)

for doc, meta, dist in zip(
    result["documents"][0],
    result["metadatas"][0],
    result["distances"][0],
):
    print("=" * 80)
    print("title:", meta.get("title"))
    print("distance:", dist)
    print("content:", doc[:300])
PY
```

### 9.6 查看用户画像 `user_profile`

先多调用几次 `/chat`，让系统异步生成用户画像：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "我经常咨询会员积分和退款问题，回答请简洁一点", "user_id": "profile_user", "conv_id": "profile_session"}'
```

等待几秒后查看：

```bash
docker exec -it oops-app bash
```

```bash
python - <<'PY'
import json
import chromadb

client = chromadb.HttpClient(host="chromadb", port=8000)
col = client.get_collection("user_profile")

data = col.get(
    where={"user_id": "profile_user"},
    include=["documents", "metadatas"],
)

for i, doc in enumerate(data["documents"]):
    print("=" * 80)
    print("metadata:", data["metadatas"][i])
    print(json.dumps(json.loads(doc), ensure_ascii=False, indent=2))
PY
```

### 9.7 查看情景记忆 `episodic`

情景记忆只有在当前会话消息数量达到压缩阈值后才会写入。默认阈值在 `MemoryManager.COMPRESS_AT` 中，目前是 10 条消息。

可以连续发送多条消息触发压缩：

```bash
for i in $(seq 1 11); do
  curl -s -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"这是第 $i 条测试消息，我想咨询退款和订单问题\", \"user_id\": \"episodic_user\", \"conv_id\": \"episodic_session\"}" > /dev/null
done
```

查看情景记忆：

```bash
docker exec -it oops-app bash
```

```bash
python - <<'PY'
import chromadb

client = chromadb.HttpClient(host="chromadb", port=8000)
col = client.get_collection("episodic")

data = col.get(
    where={"user_id": "episodic_user"},
    include=["documents", "metadatas"],
)

for i, doc in enumerate(data["documents"]):
    print("=" * 80)
    print("metadata:", data["metadatas"][i])
    print("summary:", doc)
PY
```

### 9.8 查看 ChromaDB 持久化文件

ChromaDB 的持久化卷在 Compose 中定义为：

```yaml
volumes:
  chromadb-data:
```

查看 Docker volume：

```bash
docker volume ls | grep chromadb
docker volume inspect oops_chromadb-data
```

查看容器内数据目录：

```bash
docker exec -it oops-chromadb sh
ls -lah /chroma/chroma
find /chroma/chroma -maxdepth 2 -type f | head
```

注意：不建议直接修改这些底层文件。查看和管理数据应优先使用 ChromaDB API 或 Python 客户端。

### 9.9 清空 ChromaDB 数据

谨慎操作。停止服务并删除 volume：

```bash
docker compose down
docker volume rm oops_chromadb-data
docker compose up -d --build
```

如果只想删除某个 collection，可以用 Python 客户端：

```bash
docker exec -it oops-app bash
```

```bash
python - <<'PY'
import chromadb

client = chromadb.HttpClient(host="chromadb", port=8000)
client.delete_collection("knowledge_base")
print("deleted knowledge_base")
PY
```

删除后重启应用，`KnowledgeBase` 会在 collection 为空时重新导入默认文档。

## 10. Redis 工作记忆查看

Redis 容器名：

```text
oops-redis
```

进入 Redis：

```bash
docker exec -it oops-redis redis-cli -a oops123
```

查看 key：

```redis
KEYS *
```

工作记忆 key 格式：

```text
wm:{user_id}:{conv_id}
```

会话服务记录 key 格式：

```text
service_records:{user_id}:{conv_id}
```

查看某个会话最近消息：

```redis
LRANGE wm:user_001:session_001 0 -1
```

查看 TTL：

```redis
TTL wm:user_001:session_001
```

默认 TTL 是 24 小时。

## 11. 四级记忆架构与状态查看

Oops 实现了模拟人类记忆机制的四级记忆架构，由 `memory/conversation_memory.py` 管理：

1. **工作记忆 (Working Memory)**：存放在 Redis，记录当前会话的最近对话上下文，支持毫秒级读写。当超过条数（默认 20 条）时触发自动压缩。
2. **服务经历 (Service Experience / Episodic)**：存放在 ChromaDB，跨会话历史对话经过 LLM 结构化提取后压缩成的服务记录。
3. **用户画像 (User Profile)**：存放在 ChromaDB，异步从对话中提炼出的用户长期偏好、实体及沟通风格。
4. **用户承诺 (User Commitments)**：存放在 ChromaDB，记录人工或系统向用户作出的服务承诺，支持超时判定与自动引导人工客服介入。

### 11.1 查看工作记忆与情景压缩

工作记忆压缩发生在 `memory/conversation_memory.py` 中。默认配置：

```text
WORKING_MAX = 20
COMPRESS_AT = 10
```

当同一个 `user_id + conv_id` 的工作记忆达到 10 条消息时，系统会：

```text
旧消息 -> LLM 摘要 -> Redis service_records
旧消息摘要 -> ChromaDB episodic
最近 5 条消息 -> 继续保留在 Redis wm 列表
```

日志示例：

```text
工作记忆压缩完成: cli_user/5a076f2b-b607-4339-9e9f-f0399862d366，摘要 19 字
```

其中：

```text
user_id = cli_user
conv_id = 5a076f2b-b607-4339-9e9f-f0399862d366
```

### 11.1 查看 Redis 中的会话摘要

进入 Redis：

```bash
docker exec -it oops-redis redis-cli -a oops123
```

查询摘要：

```redis
GET service_records:cli_user:5a076f2b-b607-4339-9e9f-f0399862d366
```

一条命令快速查看：

```bash
docker exec -it oops-redis redis-cli -a oops123 \
  GET service_records:cli_user:5a076f2b-b607-4339-9e9f-f0399862d366
```

### 11.2 查看压缩后仍保留的最近 5 条工作记忆

进入 Redis 后执行：

```redis
LRANGE wm:cli_user:5a076f2b-b607-4339-9e9f-f0399862d366 0 -1
```

一条命令快速查看：

```bash
docker exec -it oops-redis redis-cli -a oops123 \
  LRANGE wm:cli_user:5a076f2b-b607-4339-9e9f-f0399862d366 0 -1
```

说明：

- Redis 使用 `LPUSH` 写入，最新消息在列表前面。
- 代码读取时会 `reversed(raws)` 还原时间顺序。
- 压缩后 Redis 工作记忆列表只保留最近 5 条；更早的内容会以摘要形式进入 Redis `service_records` 和 ChromaDB `episodic`。

### 11.3 查看 ChromaDB 中的情景记忆摘要

如果是全栈部署，应用容器名通常是：

```text
oops-app
```

进入应用容器：

```bash
docker exec -it oops-app bash
```

如果你是用 `docker run --rm` 跑 CLI，容器名可能是随机的。先查看：

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Networks}}\t{{.Status}}'
```

进入对应容器：

```bash
docker exec -it <容器名> bash
```

执行 Python 脚本查询 `episodic`：

```bash
python - <<'PY'
import chromadb

user_id = "cli_user"
conv_id = "5a076f2b-b607-4339-9e9f-f0399862d366"

client = chromadb.HttpClient(host="chromadb", port=8000)
col = client.get_collection("episodic")

data = col.get(
    where={"user_id": user_id},
    include=["documents", "metadatas"],
)

for i, doc in enumerate(data["documents"]):
    meta = data["metadatas"][i]
    if meta.get("conv_id") == conv_id:
        print("=" * 80)
        print("metadata:", meta)
        print("summary:", doc)
        print("full_text_preview:", meta.get("full_text"))
PY
```

字段说明：

| 字段 | 含义 |
|------|------|
| `documents[i]` | LLM 生成的历史对话摘要 |
| `metadata.user_id` | 用户 ID |
| `metadata.conv_id` | 会话 ID |
| `metadata.ts` | 写入时间 |
| `metadata.full_text` | 被压缩的原始旧消息前 500 字预览 |

### 11.4 如果只想看某个用户的所有情景记忆

```bash
docker exec -it oops-app bash
```

```bash
python - <<'PY'
import chromadb

user_id = "cli_user"

client = chromadb.HttpClient(host="chromadb", port=8000)
col = client.get_collection("episodic")

data = col.get(
    where={"user_id": user_id},
    include=["documents", "metadatas"],
)

for i, doc in enumerate(data["documents"]):
    print("=" * 80)
    print("metadata:", data["metadatas"][i])
    print("summary:", doc)
PY
```

### 11.5 Redis 与 ChromaDB 存储区别

| 位置 | 保存内容 | 用途 |
|------|----------|------|
| Redis `service_records:{user_id}:{conv_id}` | 当前会话压缩摘要 | 下一次同会话请求直接拼入 prompt |
| ChromaDB `episodic` | 压缩摘要 + metadata | 跨会话按语义检索相关历史 |
| Redis `wm:{user_id}:{conv_id}` | 最近 5 条消息 | 保持当前对话连贯性 |
