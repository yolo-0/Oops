import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Optional

import chromadb

logger = logging.getLogger(__name__)


class SemanticCache:
    """
    基于 ChromaDB 的真实语义缓存。
    复用原有的 ChromaDB，实现真正的近似语义匹配（如“退钱”与“退款”命中），
    从而拦截对后端 LLM（Query Rewrite / Rerank）的重复高成本调用。
    """

    COLLECTION_NAME = "semantic_cache"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        try:
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            logger.info(f"语义缓存 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"语义缓存 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 获取或创建缓存表，强制使用余弦相似度（cosine）度量
        self.collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )

    async def get(self, query: str, threshold: float = 0.1) -> Optional[Any]:
        """
        从 ChromaDB 检索语义缓存。
        :param query: 用户问题
        :param threshold: 容忍的余弦距离，越小要求越精确，一般建议 0.1 左右
        :return: 缓存的数据，未命中或过期则返回 None
        """
        def _search():
            return self.collection.query(query_texts=[query], n_results=1)

        try:
            res = await asyncio.to_thread(_search)
            if res.get("distances") and res["distances"][0]:
                dist = res["distances"][0][0]
                if dist < threshold:
                    meta = res["metadatas"][0][0]
                    # 检查是否过期
                    expire_at = float(meta.get("expire_at", 0))
                    if expire_at > time.time():
                        logger.info(f"语义缓存命中 (dist={dist:.3f})：{query}")
                        data_str = meta.get("data")
                        return json.loads(data_str) if data_str else None
                    else:
                        logger.debug(f"语义缓存已过期：{query}")
                        # 理论上可以触发一条异步删除任务
        except Exception as ex:
            logger.warning(f"读取语义缓存失败: {ex}")
        
        return None

    async def set(self, query: str, data: Any, ttl: float = 3600) -> None:
        """
        将数据存入 ChromaDB 缓存。
        :param query: 用户问题（用于向量化）
        :param data: 需要缓存的结果结构（须可被 json.dumps）
        :param ttl: 过期时间，单位秒
        """
        def _add():
            doc_id = hashlib.md5(query.encode("utf-8")).hexdigest()
            meta = {
                "data": json.dumps(data, ensure_ascii=False),
                "expire_at": time.time() + ttl
            }
            # upsert 会覆盖同一个完全一样的 query 的结果
            self.collection.upsert(
                ids=[doc_id],
                documents=[query],
                metadatas=[meta]
            )

        try:
            await asyncio.to_thread(_add)
            logger.debug(f"语义缓存写入成功：{query}")
        except Exception as ex:
            logger.warning(f"写入语义缓存失败: {ex}")
