# 常见问题排查与测试验证 (FAQ)

## 15. 常见问题

### 15.1 `/health` 返回 503

查看应用日志：

```bash
docker compose logs -f oops
```

重点检查：

- `.env` 是否配置 `ANTHROPIC_API_KEY`
- Redis 是否健康
- ChromaDB 是否健康
- 应用容器是否正在反复重启

### 15.2 ChromaDB 连接失败

查看 ChromaDB 状态：

```bash
docker compose ps chromadb
docker compose logs -f chromadb
curl http://localhost:8001/api/v1/heartbeat
```

应用容器内测试：

```bash
docker exec -it oops-app bash
python - <<'PY'
import chromadb
client = chromadb.HttpClient(host="chromadb", port=8000)
print(client.heartbeat())
PY
```

### 15.3 Redis 认证失败

确认 `.env` 和 `docker-compose.yml` 中使用的密码一致。默认密码是：

```text
oops123
```

测试连接：

```bash
docker exec -it oops-redis redis-cli -a oops123 ping
```

### 15.4 `/search` 没有结果

先确认知识库中有数据：

```bash
curl http://localhost:8000/knowledge/stats
```

如果是 0，可以重新导入演示文档：

```bash
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@data/demo_docs/sample_knowledge.json"
```

再测试：

```bash
curl -X POST "http://localhost:8000/search?query=API如何接入&top_k=3"
```

### 15.5 用户画像查不到

用户画像是异步更新的，并且依赖 LLM 调用成功。排查步骤：

1. 先调用 `/chat`，使用固定 `user_id`
2. 等待几秒
3. 查看 `docker compose logs -f oops` 是否出现 `用户画像已更新`
4. 使用第 8.6 节的 Python 脚本查询 `user_profile`

### 15.6 情景记忆查不到

情景记忆不是每次对话都写入。只有当前会话消息数达到压缩阈值后才写入。默认阈值：

```text
MemoryManager.COMPRESS_AT = 10
```

连续发 11 条以上消息后再查看 `episodic`。

## 16. 推荐验证流程

完整验证可以按这个顺序执行：

```bash
# 1. 启动
docker compose up -d --build

# 2. 健康检查
curl http://localhost:8000/health

# 3. 主对话
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，我想了解退款政策", "user_id": "demo_user", "conv_id": "demo_conv"}'

# 4. 知识库统计
curl http://localhost:8000/knowledge/stats

# 5. 导入演示知识库
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@data/demo_docs/sample_knowledge.json"

# 6. 检索
curl -X POST "http://localhost:8000/search?query=Oops如何接入API&top_k=3"

# 7. 监控
curl http://localhost:8000/monitor

# 8. Skills
curl http://localhost:8000/skills

# 9. 评测
curl -X POST http://localhost:8000/eval/run
```## 14. 停止、重启和清理

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
