"""
亮点：多轮对话记忆管理

四级记忆架构，模拟人类记忆机制：
  1. 工作记忆（Redis）—— 当前会话的最近 N 条消息，毫秒级读写
  2. 服务经历（ChromaDB）—— 跨会话的历史对话（压缩为服务记录），按语义相似度检索
  3. 用户画像（ChromaDB）—— 从对话中提炼的长期偏好和实体
  4. 用户承诺（ChromaDB）—— 记录人工/系统写入的服务承诺，支持超时判定与自动引导人工介入

关键设计：
  - 上下文构建时四级记忆融合，按重要性 + 时效性排序
  - 工作记忆超过阈值时自动压缩（LLM 结构化提取服务记录），防止 context 爆炸
  - 所有 Embedding 直接使用 ChromaDB 内置模型生成，无外部 API 依赖
"""
import re
import hashlib
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import chromadb
import redis.asyncio as redis
import instructor
from pydantic import BaseModel, Field
from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content, messages_create

logger = logging.getLogger(__name__)


class MsgRole(Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"


class UserProfileUpdate(BaseModel):
    communication_style: str = Field(description="用户倾向的对话风格：简洁/详细/专业/口语，无则空字符串")
    preferred_channel: str = Field(description="用户倾向的沟通渠道：在线客服/人工客服，无则空字符串")
    tech_proficiency: str = Field(description="用户对技术的理解能力：小白/普通/熟练/专家，无则空字符串")
    notes: List[str] = Field(description="用户明确表达的其他偏好，无则空列表") 


class ServiceRecords(BaseModel):     
    issue_types: List[str] = Field(description="技术问题/账务问题/配送问题/服务问题/售后问题/其他问题")
    handel_flow: str = Field(description="对话中，客服和用户一起推进问题解决的关键流程提取总结。")
    submitted_materials: List[str] = Field(description="用户提交的图片、截图、订单号等辅助材料，必须是用户明确提到的，无则空列表")
    extracted_slots: Dict[str, str] = Field(default_factory=dict, description="对话中提取的核心业务槽位键值对（如联系电话、收货地址、工单号、预约时间、产品、型号、订单号等）。")
    escalation_events: str = Field(description="'是' 或 '否'，只要用户提到‘转人工’、‘人工客服’、‘找人工’、‘投诉’、‘经理’、‘supervisor’，则为‘是’")
    resolution_status: str = Field(description="'正在解决' 或 '已解决' 或 '等待客户反馈' 或 '已关闭'，对应每个问题的状态，如果不清楚则为空") 
 
@dataclass
class Message:
    role:       MsgRole
    content:    str
    timestamp:  datetime = field(default_factory=datetime.now)
    metadata:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    """传给 Agent 的完整上下文。"""
    recent_messages:  List[Message]   # 工作记忆：最近对话
    relevant_history: List[str]       # 情景记忆：语义相关的历史片段
    user_profile:     Dict[str, Any]  # 用户画像：偏好、常用实体
    service_records:  str             # 当前服务记录（压缩后）
    commitments:      List[Dict[str, Any]] = field(default_factory=list)  # 用户承诺记录

    @staticmethod
    def _clean(text: str) -> str:
        """移除 Unicode 代理字符，防止编码错误。"""
        return text.encode("utf-8", errors="ignore").decode("utf-8")

    def to_prompt_text(self) -> str:
        """将记忆上下文格式化为 LLM 可用的文本。"""
        parts = []
        if self.service_records:
            parts.append(f"[服务记录]\n{self._clean(self.service_records)}")
        if self.relevant_history:
            parts.append("[相关历史]\n" + "\n".join(f"- {self._clean(h)}" for h in self.relevant_history[:3]))
        if self.user_profile:
            parts.append(f"[用户画像]\n{json.dumps(self.user_profile, ensure_ascii=False)}")
        if self.commitments:
            parts.append(f"[用户承诺]\n{json.dumps(self.commitments, ensure_ascii=False)}")
            
        if self.recent_messages:
            parts.append("[最近对话]")
            for m in self.recent_messages:
                parts.append(f"{m.role.value}: {self._clean(m.content)}")

        # 检查是否需要人工客服介入
        has_pending = any(c.get("status") in ("待受理", "已过期") for c in self.commitments)
        if has_pending:
            parts.append("\n[系统指令]：检测到用户有“待受理”或“已过期”的服务承诺，请务必主动向用户说明情况，并主动询问是否需要转接人工客服处理。")

        return "\n\n".join(parts)

class MemoryManager:
    """
    四级记忆管理器。

    工作记忆存 Redis（TTL 24h），情景记忆、用户画像和用户承诺存 ChromaDB（持久化）。
    """

    WORKING_MAX   = 20    # 工作记忆最大条数，超过则触发压缩
    COMPRESS_AT   = 10   # 达到此条数时压缩，保留摘要 + 最近 5 条
    HISTORY_TOP_K = 5     # 情景记忆检索返回条数
 
    def __init__(
        self,
        redis_url:    str = "redis://localhost:6379/0",
        chroma_host:  str = "localhost",
        chroma_port:  int = 8000,
        chroma_path:  str = "./data/chroma",
        api_key:      str = "",
        base_url:     Optional[str] = None,
        model:        str = "claude-3-5-sonnet-20241022",
        fast_model:   str = "claude-3-haiku-20240307",
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = instructor.from_anthropic(
            AsyncAnthropic(**kwargs),
            mode=instructor.Mode.ANTHROPIC_JSON
        )
        self._model      = model
        self._fast_model = fast_model

        self._redis = redis.from_url(redis_url, decode_responses=True)

        # 屏蔽 ChromaDB 因 posthog 版本不兼容导致的 telemetry 报错
        logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

        # ChromaDB：优先连接独立服务（docker compose 模式），连不上则降级为本地嵌入式
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            chroma = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            chroma.heartbeat()  # 测试连接
            logger.info(f"ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"ChromaDB 服务不可用，使用本地嵌入式模式: {chroma_path}")
            chroma = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 情景记忆：存储历史对话片段
        self._episodic = chroma.get_or_create_collection("episodic")
        # 用户画像：存储提炼出的偏好和实体
        self._profile  = chroma.get_or_create_collection("user_profile")
        # 用户承诺：存储人工写入的服务承诺
        self._commitments = chroma.get_or_create_collection("commitments")

    # ── 写入 ──────────────────────────────────────────────────────────────────

    async def add_message(
        self,
        user_id: str,
        conv_id: str,
        role:    MsgRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """将一条消息写入工作记忆，超阈值时自动压缩。"""
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        clean_metadata = {
            self._safe_text(k): self._safe_metadata_value(v)
            for k, v in (metadata or {}).items()
        }
        msg = Message(role=role, content=self._safe_text(content), metadata=clean_metadata)
        key = self._wm_key(user_id, conv_id)

        # 追加到 Redis 列表（左推，最新在前）
        await self._redis.lpush(key, json.dumps({
            "role":      msg.role.value,
            "content":   msg.content,
            "ts":        msg.timestamp.isoformat(),
            "metadata":  msg.metadata,
        }))
        await self._redis.expire(key, 86400)  # 24h TTL

        # 超过压缩阈值时触发压缩
        if await self._redis.llen(key) >= self.COMPRESS_AT:
            await self._compress(user_id, conv_id)

    @staticmethod
    def _unique(items: List[Any]) -> List[Any]:
        return list(dict.fromkeys(items))

    def _extract_entities(self, message: str) -> Dict[str, List[str]]:
        """用规则提取高价值实体，避免每次识别都额外调用 LLM。"""
        message = self._safe_text(message)
        return {
            "order_id": self._unique(re.findall(r"(?:订单号?|order(?:_id)?|#)\s*[:：#]?\s*([A-Za-z0-9_-]{4,32})", message, re.I)),
            "date": self._unique(re.findall(r"(今天|明天|昨天|本周|这周|下周|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", message)),
            "amount": self._unique(re.findall(r"((?:¥|￥)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:元|块|rmb|cny|usd|美元))", message, re.I)),
            "error_code": self._unique(re.findall(r"\b([45]\d{2}|[A-Z][A-Z0-9_-]{2,16})\b", message)),
        }

    async def update_profile(self, user_id: str, conv_id: str) -> None:
        """
        从当前工作记忆中提炼用户偏好，更新用户画像。
        用 LLM 提炼偏好，然后存入 ChromaDB（ChromaDB 内置 embedding，不依赖外部 API）。
        """
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        messages = await self._get_working_memory(user_id, conv_id)
        if not messages:
            return

        text = self._safe_text("\n".join(f"{m.role.value}: {m.content}" for m in messages[-10:]))
        
        old_profile = await self._get_profile(user_id)
        old_profile_str = json.dumps(old_profile, ensure_ascii=False) if old_profile else "无"
        
        prompt = f"""请分析以下最新对话，并结合用户的旧画像，提取并更新用户的长效画像。
注意：如果是用户技术水平发生明显进步，或者表达了新的偏好，请务必推翻旧的设定，进行动态更新演进。

【旧画像】:
{old_profile_str}

【最新对话】:
{text}
"""
        prompt = self._safe_text(prompt)

        try:
            # pyrefly: ignore [not-async]
            resp = await messages_create(
                self._client,
                model=self._fast_model, max_tokens=1024, temperature=0.0,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
                response_model=UserProfileUpdate,
            )
            profile_data = {
                "communication_style": resp.communication_style,
                "preferred_channel": resp.preferred_channel,
                "tech_proficiency": resp.tech_proficiency,
                "notes": resp.notes,
                "entities": self._extract_entities(text)
            }

            doc_id = f"{user_id}_profile_{conv_id}"
            doc_text = self._safe_text(json.dumps(profile_data, ensure_ascii=False))

            try:
                await asyncio.to_thread(self._profile.delete, ids=[doc_id])
            except Exception:
                pass

            # 直接传 documents，让 ChromaDB 内置模型生成 embedding（不依赖 Voyage API）
            await asyncio.to_thread(
                self._profile.add,
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{"user_id": user_id, "conv_id": conv_id,
                            "ts": datetime.now().isoformat()}],
            )
            logger.info(f"用户画像已更新: {user_id}")
        except Exception as ex:
            logger.warning(f"更新用户画像失败: {ex}")

    async def add_commitment(self, user_id: str, data: Dict[str, Any]) -> None:
        """
        人工写入用户承诺记录，存入 ChromaDB，以供对话中进行相似度检索和超时检查。
        data 应包含：commitment_id, type, source, content, deadline, status, notes
        """
        try:
            user_id = self._safe_text(user_id)
            c_id = self._safe_text(data.get("commitment_id", hashlib.md5(time.time().hex().encode()).hexdigest()))
            
            # 使用 content 和 type 作为向量化的主文本
            doc_text = self._safe_text(f"{data.get('type', '')} - {data.get('content', '')}")
            
            # 元数据需为标量类型（str, int, float, bool）
            meta = {
                "user_id": user_id,
                "commitment_id": c_id,
                "type": self._safe_text(data.get("type", "")),
                "source": self._safe_text(data.get("source", "人工写入")),
                "content": self._safe_text(data.get("content", "")),
                "deadline": self._safe_text(data.get("deadline", "")),
                "status": self._safe_text(data.get("status", "待受理")),
                "notes": self._safe_text(data.get("notes", "")),
                "ts": self._safe_text(data.get("ts", datetime.now().isoformat()))
            }
            
            # 如果存在则删除旧记录（覆盖更新）
            try:
                await asyncio.to_thread(self._commitments.delete, ids=[c_id])
            except Exception:
                pass

            await asyncio.to_thread(
                self._commitments.add,
                ids=[c_id],
                documents=[doc_text],
                metadatas=[meta],
            )
            logger.info(f"写入用户承诺完成: {user_id}/{c_id}")
        except Exception as ex:
            logger.warning(f"写入用户承诺失败: {ex}")

    # ── 读取 ──────────────────────────────────────────────────────────────────

    async def get_context(self, user_id: str, conv_id: str, query: str = "") -> MemoryContext:
        """
        构建完整的记忆上下文。

        query 用于从情景记忆中检索语义相关的历史片段。
        """
        # 1. 工作记忆（当前会话最近消息）
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        query = self._safe_text(query)

        recent = await self._get_working_memory(user_id, conv_id)
        logger.info(f"获取工作记忆：{recent}")

        # 2. 历史服务经历（跨会话语义检索）
        history = await self._search_episodic(user_id, query or (recent[-1].content if recent else ""))
        logger.info(f"获取历史服务经历：{history}")

        # 3. 用户画像
        profile = await self._get_profile(user_id)
        logger.info(f"获取用户画像：{profile}")

        # 4. 会话服务记录（如果已压缩过）
        service_records = await self._redis.get(self._service_records_key(user_id, conv_id)) or ""
        logger.info(f"获取服务记录：{service_records}")
        
        # 5. 用户承诺记录检索  
        commitments = await self._search_commitments(user_id, query or (recent[-1].content if recent else ""))
        logger.info(f"获取承诺记录：{commitments}")

        # 检查承诺是否超时，若超时则更新状态
        updated_commitments = []  
        for c in commitments: 
            if c.get("status") in ("待受理", "正在受理"):
                ts_str = c.get("ts")
                deadline_str = c.get("deadline")
                if ts_str and deadline_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        match = re.search(r"(\d+)(?:\.\d+)?", deadline_str)
                        if match:
                            num = float(match.group(1))
                            if "天" in deadline_str or "day" in deadline_str.lower():
                                delta = timedelta(days=num)
                            elif "分" in deadline_str or "min" in deadline_str.lower():
                                delta = timedelta(minutes=num)
                            else:
                                delta = timedelta(hours=num) # 默认按小时
                            
                            if datetime.now() > ts + delta:
                                c["status"] = "已过期"
                                # 异步写回，持久化过期状态
                                asyncio.create_task(self.add_commitment(user_id, c))
                    except Exception as e:
                        logger.error(f"解析承诺期限失败: {e}")
            updated_commitments.append(c)
        logger.info(f"获取承诺记录：{updated_commitments}")


        return MemoryContext(
            recent_messages=recent,
            relevant_history=history,
            user_profile=profile,
            service_records=service_records,
            commitments=commitments,
        )

    # ── 压缩（防止 context 爆炸）─────────────────────────────────────────────

    async def _compress(self, user_id: str, conv_id: str) -> None:
        """
        工作记忆压缩：
          1. 用 LLM 对旧消息生成摘要
          2. 摘要存 Redis（覆盖旧摘要）
          3. 旧消息存入情景记忆（ChromaDB）供跨会话检索
          4. 工作记忆只保留最近 5 条
        """
        messages = await self._get_working_memory(user_id, conv_id)
        if len(messages) < self.COMPRESS_AT:
            return

        to_compress = messages[:-5]   # 保留最近 5 条
        keep        = messages[-5:]

        # LLM 提取服务记录
        text = self._safe_text("\n".join(f"{m.role.value}: {m.content}" for m in to_compress))
        prompt = self._safe_text(f"""根据以下对话提取服务记录，必须逐字保留关键具体值：
- issue_types：问题分类（技术/账务/配送/售后等）
- handel_flow：客服与用户共同推进问题解决的关键流程
- submitted_materials：用户明确提供的订单号、截图等辅助材料原文
- extracted_slots：必须逐字填入用户给出的具体值（联系电话、收货地址、订单号、预约时间、产品型号等），
  不允许概括或省略（例如用户说了"电话 13800138000"，就必须把 13800138000 原样写入）
- escalation_events / resolution_status：如实填写

【对话内容】：
{text}""")
        try:
            # pyrefly: ignore [not-async]
            resp = await messages_create(
                self._client,
                model=self._fast_model, max_tokens=1024, temperature=0.0,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
                response_model=ServiceRecords,
            )
            extracted_records = json.dumps(resp.model_dump(), ensure_ascii=False)
            original_len = len(text)
            compressed_len = len(extracted_records)
            ratio = original_len / compressed_len if compressed_len > 0 else 0
            
            # 记录到监控系统或日志
            logger.info(
                f"[Memory Compression] User: {user_id} | "
                f"Original: {original_len} chars -> Compressed: {compressed_len} chars | "
                f"Ratio: {ratio:.2f}x"
            )
        except Exception as e:
            logger.error(f"[Memory Compression] Failed to extract service records: {e}")
            extracted_records = f"对话包含 {len(to_compress)} 条消息（服务记录提取失败）"

        # 存服务记录到 Redis
        skey = self._service_records_key(user_id, conv_id)
        old_records = await self._redis.get(skey) or ""
        new_records = self._safe_text(f"{old_records}\n{extracted_records}").strip()
        await self._redis.setex(skey, 86400, new_records) ## 24小时更新一次服务记录

        # 旧消息存入历史服务经历
        await self._store_episodic(user_id, conv_id, text, extracted_records)

        # 重置工作记忆为最近 5 条
        key = self._wm_key(user_id, conv_id)
        await self._redis.delete(key)
        for m in reversed(keep):
            await self._redis.lpush(key, json.dumps({
                "role": m.role.value, "content": m.content,
                "ts": m.timestamp.isoformat(), "metadata": m.metadata,
            }))
        await self._redis.expire(key, 86400)
        logger.info(f"工作记忆压缩完成: {user_id}/{conv_id}，服务记录 {len(extracted_records)} 字")

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    async def _get_working_memory(self, user_id: str, conv_id: str) -> List[Message]:
        key  = self._wm_key(user_id, conv_id)
        raws = await self._redis.lrange(key, 0, self.WORKING_MAX - 1)
        msgs = []
        for raw in reversed(raws):  # Redis lpush 最新在前，reversed 还原时序
            d = json.loads(raw)
            msgs.append(Message(
                role=MsgRole(d["role"]),
                content=d["content"],
                timestamp=datetime.fromisoformat(d["ts"]),
                metadata=d.get("metadata", {}),
            ))
        return msgs

    async def _search_commitments(self, user_id: str, query: str) -> List[Dict[str, Any]]:
        """语义检索用户承诺记录。"""
        query_text = self._safe_text(query).strip()
        if not query_text:
            return []
        try:
            results = await asyncio.to_thread(
                self._commitments.query,
                query_texts=[query_text],
                n_results=self.HISTORY_TOP_K,
                where={"user_id": self._safe_text(user_id)},
            )
            metadatas = results["metadatas"][0] if results["metadatas"] else []
            return metadatas
        except Exception as ex:
            logger.warning(f"承诺记录检索失败: {ex}")
            return []

    async def _search_episodic(self, user_id: str, query: str) -> List[str]:
        """语义检索历史服务经历。ChromaDB 内置 embedding，不依赖外部 API。"""
        query_text = self._safe_text(query).strip()
        if not query_text:
            return []
        try:
            # 直接传 query_texts，ChromaDB 内置模型自动生成向量做匹配
            results = await asyncio.to_thread(
                self._episodic.query,
                query_texts=[query_text],
                n_results=self.HISTORY_TOP_K,
                where={"user_id": self._safe_text(user_id)},
            )
            docs = results["documents"][0] if results["documents"] else []
            return [self._safe_text(doc) for doc in docs if isinstance(doc, str) and doc.strip()]
        except Exception as ex:
            logger.warning(f"历史服务经历检索失败: {ex}")
            return []

    async def _store_episodic(self, user_id: str, conv_id: str, text: str, service_records: str) -> None:
        """将压缩后的服务经历存入历史服务经历。ChromaDB 内置 embedding，不依赖外部 API。"""
        try:
            user_id = self._safe_text(user_id)
            conv_id = self._safe_text(conv_id)
            text = self._safe_text(text)
            service_records = self._safe_text(service_records)
            doc_id = hashlib.md5(f"{user_id}{conv_id}{time.time()}".encode()).hexdigest()
            # 直接传 documents，ChromaDB 内置模型自动生成 embedding
            await asyncio.to_thread(
                self._episodic.add,
                ids=[doc_id],
                documents=[service_records],
                metadatas=[{"user_id": user_id, "conv_id": conv_id,
                            "ts": datetime.now().isoformat(), "full_text": self._safe_text(text[:500])}],
            )
        except Exception as ex:
            logger.warning(f"存储情景记忆失败: {ex}")

    async def _get_profile(self, user_id: str) -> Dict[str, Any]:
        """获取用户画像（取最新一条）。"""
        try:
            results = await asyncio.to_thread(self._profile.get, where={"user_id": user_id}, limit=1)
            if results["documents"]:
                return json.loads(results["documents"][0])
        except Exception:
            pass
        return {}

    async def close(self) -> None:
        """关闭异步 Redis 连接。"""
        await self._redis.aclose()

    @staticmethod
    def _wm_key(user_id: str, conv_id: str) -> str:
        return f"wm:{user_id}:{conv_id}"

    @staticmethod
    def _service_records_key(user_id: str, conv_id: str) -> str:
        return f"service_records:{user_id}:{conv_id}"

    @staticmethod
    def _safe_text(value: Any) -> str:
        """转成 ChromaDB 可接受的普通 UTF-8 字符串。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @classmethod
    def _safe_metadata_value(cls, value: Any) -> Any:
        """递归清洗 metadata，避免 Redis/ChromaDB 后续读写遇到非法 UTF-8。"""
        if isinstance(value, str):
            return cls._safe_text(value)
        if isinstance(value, dict):
            return {cls._safe_text(k): cls._safe_metadata_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._safe_metadata_value(v) for v in value]
        return value
