# 高级特性：评测、监控与工具调度

## 12. MCP 工具调用与在线监控

### 12.1 工具调用优化架构
在 `mcp/tool_manager.py` 中，Oops 针对 Agent 调用工具的常见痛点，实现了高级的工具生命周期管理：
- **查询改写 (Query Rewriting)**：利用 LLM 将用户原始问题改写成多视角的子查询，再合并去重，解决“召回不全”问题。
- **结果重排 (Reranking)**：对并发召回的结果使用 LLM 进行相关性打分并重新排序，解决“召回相关性差”问题。
- **熔断器 (Circuit Breaker)**：检测到下游工具连续失败超阈值时自动熔断（三态模型：Closed/Open/Half-Open），防止雪崩效应。
- **结果缓存 (TTL Cache)**：相同参数请求直接返回缓存，降低高并发下的 API 调用成本。
- **降级策略 (Fallback)**：在工具熔断或异常时，平滑降级并返回预设兜底结果。

目前系统内置并挂载了以下核心业务工具：
1. **知识库检索 (`knowledge_search`)**：基于 ChromaDB 的 RAG 语义检索工具，内置查询改写与重排逻辑。
2. **订单详情查询 (`query_order`)**：模拟对接外部 ERP/订单系统，支持物流、状态、金额等数据实时拉取。
3. **物流状态查询 (`query_logistics`)**：模拟对接第三方物流 API，拉取实时的快递轨迹信息。

### 12.2 Monitor 在线监控

查看监控摘要：

```bash
curl http://localhost:8000/monitor
```

响应包含：

```json
{
  "agent_stats": {
    "general_0": {
      "total": 10,
      "success_rate": 1.0,
      "avg_ms": 1200.3,
      "monitor_penalty": 0.0,
      "routing_score": 0.836
    }
  },
  "tool_stats": {
    "knowledge_search": {
      "total": 24,
      "success_rate": 0.98,
      "avg_latency_ms": 145.2,
      "consecutive_fails": 0,
      "circuit_state": "closed"
    },
    "query_order": {
      "total": 12,
      "success_rate": 1.0,
      "avg_latency_ms": 502.1,
      "consecutive_fails": 0,
      "circuit_state": "closed"
    },
    "query_logistics": {
      "total": 8,
      "success_rate": 1.0,
      "avg_latency_ms": 230.5,
      "consecutive_fails": 0,
      "circuit_state": "closed"
    }
  },
  "active_alerts": [],
  "suggestions": []
}
```

指标含义：

| 指标 | 含义 |
|------|------|
| `total` | 调用次数 |
| `success_rate` | 成功率 |
| `avg_ms` / `avg_latency_ms` | 平均延迟 |
| `routing_score` | Agent 路由评分 |
| `monitor_penalty` | Monitor 根据在线表现写回的降权系数 |
| `consecutive_fails` | 工具连续失败次数 |
| `circuit_state` | 工具熔断器状态，可能是 `closed`、`open`、`half_open` |

Prometheus 页面：

```text
http://localhost:9090
```

## 13. 端到端评测框架 (LLM-as-Judge)

在 `evaluation/evaluator.py` 中，Oops 提供了全面的端到端评测能力，无需依赖大量人工标注：

```bash
curl -X POST http://localhost:8000/eval/run
```

评测核心维度：

1. **意图识别准确率**：计算分类 Accuracy 和 Macro-F1，对比预测意图与标注意图。
2. **LLM-as-Judge 响应质量打分**：调用 LLM 作为裁判，对 Agent 生成的回复进行五维打分（0.0-1.0）：
   - **相关性 (Relevance)**：直击问题，持续推进目标。
   - **准确性 (Accuracy)**：信息无误，无幻觉。
   - **完整性 (Completeness)**：完整解决需求，必要时主动澄清。
   - **有用性 (Helpfulness)**：方案直接可用，用户能据此采取行动。
   - **合规性 (Compliance)**：遵守客服边界，无违规或过度承诺。
3. **工具参数提取准确率 (Tool Extraction Accuracy)**：评测大模型在多轮对话中，能否精准从上下文中提取外部业务工具所需的参数（如订单号、时间戳等）。
4. **记忆准确性专项评测 (Memory Benchmark)**：白盒验证复杂记忆系统的演进能力，防止大模型产生“记忆扭曲”：
   - **压缩保真度 (Compression Fidelity)**：验证工作记忆超限压缩后，LLM 生成的摘要能否精准保留核心业务槽位（Slot Recall）。
   - **检索准确率 (Retrieval Accuracy)**：验证情景记忆能否在海量历史中准确召回高语义相关性的服务记录。
   - **记忆演进能力 (Memory Evolution)**：验证用户画像能否随多轮对话逐步丰富，以及服务承诺状态机（超时预警、状态流转）是否可靠。
5. **回归检测**：自动与上一次历史基线进行对比，识别性能退化点。
6. **生成优化建议**：针对丢分项，自动给出具体的系统优化建议（如补充 Few-shot 或调整 prompt）。

响应示例：

```json
{
  "pass_rate": 0.83,
  "total": 5,
  "passed": 4,
  "avg_scores": {
    "intent_accuracy": 0.875,
    "relevance": 0.88,
    "accuracy": 0.82,
    "completeness": 0.79,
    "helpfulness": 0.85
  },
  "regressions": [],
  "recommendations": [
    "意图识别准确率 < 90%：增加 Few-shot 示例，或对低 F1 的意图类别补充训练数据"
  ],
  "results": []
}
```