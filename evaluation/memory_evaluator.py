"""
亮点：三级记忆架构底层评测引擎

核心问题：如何验证复杂记忆系统的准确性与演进能力？

评测维度：
  1. 压缩保真度 (Compression Fidelity) —— 工作记忆超限后，LLM 生成的摘要能否精准保留核心槽位（Slot Recall）和业务关键信息。
  2. 检索准确率 (Retrieval Accuracy) —— ChromaDB 情景记忆检索能否在海量历史中准确召回 Top-K 语义高度相关的服务记录。
  3. 记忆演进能力 (Memory Evolution) —— 用户画像能否随着多轮对话逐步丰富（如：从小白变成熟练工）；以及承诺状态机 (Commitment Machine) 的超时预警、状态流转是否可靠。

底层评测与端到端评测互补：
  端到端看的是 Agent 的最终回复，而 Memory Benchmark 通过白盒验证保证记忆组件在压缩、检索、状态追踪全生命周期的稳定性，防止系统产生严重的“记忆扭曲”或“失忆”。
"""
import asyncio
import logging
import uuid
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List

from memory.conversation_memory import MemoryManager, MsgRole, UserProfileUpdate

logger = logging.getLogger(__name__)

class MemoryEvaluator:
    """
    专门针对 Memory 层的底层白盒/灰盒评测工具。
    涵盖大厂级评测标准：Write Accuracy, Retrieval Accuracy, Memory Evolution。
    """

    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager

    async def run_all_tests(self) -> Dict[str, Any]:
        logger.info("开始执行 Memory Benchmark...")
        results = {
            "compression_fidelity": await self.evaluate_compression_fidelity(),
            "retrieval_accuracy": await self.evaluate_retrieval_accuracy(),
            "memory_evolution": await self.evaluate_memory_evolution(),
        }
        logger.info(f"Memory Benchmark 执行完毕: {json.dumps(results, ensure_ascii=False, indent=2)}")
        return results

    async def evaluate_compression_fidelity(self) -> Dict[str, Any]:
        """
        1. 验证工作记忆触发阈值（COMPRESS_AT=10）时的行为。
        2. 验证大模型生成的 ServiceRecords 是否包含前序对话的关键 Slots (Slot Recall)。
        """
        logger.info("--- 测试: 压缩保真度 (Write Accuracy) ---")
        user_id = f"eval_user_compression_{uuid.uuid4().hex[:6]}"
        conv_id = f"eval_conv_{uuid.uuid4().hex[:6]}"

        # 模拟包含明确槽位的 10 轮对话
        # 预设提取目标 slots: 订单号 A12345, 电话 13800138000, 地址 北京朝阳
        target_slots = ["A12345", "13800138000", "北京朝阳"]
        
        messages = [
            (MsgRole.USER, "你好，我要投诉快递。"),
            (MsgRole.ASSISTANT, "请问您的订单号是多少？"),
            (MsgRole.USER, "单号是 A12345"),
            (MsgRole.ASSISTANT, "好的，我看到订单了，需要留一下您的联系电话。"),
            (MsgRole.USER, "电话是 13800138000"),
            (MsgRole.ASSISTANT, "记下了，请问送货地址是哪里？"),
            (MsgRole.USER, "地址是北京朝阳区"),
            (MsgRole.ASSISTANT, "好的，已经为您加急催单。"),
            (MsgRole.USER, "催了也没用，今天必须送到。"),
            (MsgRole.ASSISTANT, "我理解您的心情，会为您跟进。"),
            (MsgRole.USER, "如果有问题随时联系我。"),  
            (MsgRole.ASSISTANT, "好的，我们会尽快处理。"), 
            (MsgRole.USER, "那就好。"),
            (MsgRole.ASSISTANT, "感谢您的支持。"),
            (MsgRole.USER, "拜拜。"),
            (MsgRole.ASSISTANT, "再见，祝您生活愉快！"),
        ]

        for role, content in messages:
            await self.memory.add_message(user_id, conv_id, role, content)
            
        # 稍等片刻确保异步 Redis 和 ChromaDB 操作完成
        await asyncio.sleep(1)

        context = await self.memory.get_context(user_id, conv_id)
        service_records = context.service_records
        
        # 计算 Slot Recall
        retained = 0
        missing = []
        for slot in target_slots:
            if slot in service_records:
                retained += 1
            else:
                missing.append(slot)
                
        slot_recall = retained / len(target_slots) if target_slots else 0.0
        
        result = {
            "passed": slot_recall == 1.0,
            "slot_recall": slot_recall,
            "missing_slots": missing,
            "service_records": service_records,
            "recent_messages_count": len(context.recent_messages),
        }
        logger.info(f"Compression Fidelity Result: {result}")
        return result

    async def evaluate_retrieval_accuracy(self) -> Dict[str, Any]:
        """
        验证 Episodic Memory 的召回准确率 (Hard Negatives 测试)。
        测试 Top-1 命中率。
        """
        logger.info("--- 测试: 检索召回率 (Retrieval Accuracy) ---")
        user_id = f"eval_user_retrieval_{uuid.uuid4().hex[:6]}"
        conv_id = f"eval_conv_{uuid.uuid4().hex[:6]}"

        # 手动注入干扰记录 (Hard Negatives)
        distractors = [
            "用户反映宽带欠费，已指导通过APP缴费恢复。",
            "用户反映电视机顶盒故障，屏幕显示错误码E04。",
            "用户反映路由器频繁掉线，重启后依然不稳定。", # 目标历史
        ]
        
        for i, text in enumerate(distractors):
            await self.memory._store_episodic(user_id, f"{conv_id}_past_{i}", text, text)
            
        # 稍等片刻让 ChromaDB 完成索引
        await asyncio.sleep(2)
        
        # 用户用不同表述查询特定问题
        query = "网络又断了，和昨天情况一样"
        history = await self.memory._search_episodic(user_id, query)
        
        # 验证 Top-1 是否命中路由器掉线记录
        passed = False
        top_1 = history[0] if history else ""
        if "路由器频繁掉线" in top_1:
            passed = True
            
        result = {
            "passed": passed,
            "top_1": top_1,
            "all_retrieved": history
        }
        logger.info(f"Retrieval Accuracy Result: {result}")
        return result

    async def evaluate_memory_evolution(self) -> Dict[str, Any]:
        """
        验证记忆演化 (Memory Evolution)：
        1. 画像的演化 (从低技术水平正确演进为高技术水平，不陷于固有标签)
        2. 承诺状态机的演化 (过期强制转人工 -> 状态完成解除拦截)
        """
        logger.info("--- 测试: 记忆演化与状态机闭环 (Memory Evolution) ---")
        user_id = f"eval_user_evo_{uuid.uuid4().hex[:6]}"
        conv_id = f"eval_conv_{uuid.uuid4().hex[:6]}"
        results = {}

        # 1. 画像演进测试
        # Phase 1: 小白用户
        await self.memory.add_message(user_id, conv_id, MsgRole.USER, "我是个电脑小白，完全不懂那些专业名词，能找个人直接帮我弄吗？")
        await self.memory.update_profile(user_id, conv_id)
        profile_p1 = await self.memory._get_profile(user_id)
        
        # Phase 2: 用户变专业
        await self.memory.add_message(user_id, conv_id, MsgRole.USER, "我最近学了半年Python，已经能自己写脚本处理数据了，你直接把接口文档发给我就行。")
        await self.memory.update_profile(user_id, conv_id)
        profile_p2 = await self.memory._get_profile(user_id)
        
        tech1 = profile_p1.get("tech_proficiency", "")
        tech2 = profile_p2.get("tech_proficiency", "")
        results["profile_evolution"] = {
            "phase1": tech1,
            "phase2": tech2,
            "passed": tech1 != tech2 and ("小白" not in tech2)
        }

        # 2. 承诺状态机闭环测试
        c_id = "test_commitment_1"
        past_time = (datetime.now() - timedelta(hours=2)).isoformat() # 过去2小时创建
        
        # 添加一个1小时超时的承诺
        await self.memory.add_commitment(user_id, {
            "commitment_id": c_id,
            "type": "回电承诺",
            "content": "承诺在1小时内回电解决",
            "deadline": "1 小时",
            "status": "待受理",
            "ts": past_time, 
        })
        
        # 第一次 get_context，验证是否自动转为过期并触发系统指令
        ctx1 = await self.memory.get_context(user_id, conv_id, "")
        prompt1 = ctx1.to_prompt_text()
        has_escalation_alert1 = "主动询问是否需要转接人工客服处理" in prompt1
        status1 = "已过期" if any(c.get("status") == "已过期" for c in ctx1.commitments) else "待受理"

        # 闭环：人工介入并完成
        # 等待后台的过期状态更新 task 执行完毕，防止竞态条件覆盖
        await asyncio.sleep(1)
        await self.memory.add_commitment(user_id, {
            "commitment_id": c_id,
            "type": "回电承诺",
            "content": "承诺在1小时内回电解决",
            "deadline": "1 小时",
            "status": "已解决",
            "ts": past_time, 
        })
        
        # 第二次 get_context，验证系统指令是否消失
        ctx2 = await self.memory.get_context(user_id, conv_id, "")
        prompt2 = ctx2.to_prompt_text()
        has_escalation_alert2 = "主动询问是否需要转接人工客服处理" in prompt2
        
        results["commitment_machine"] = {
            "status_after_timeout": status1,
            "escalation_alert_fired": has_escalation_alert1,
            "escalation_alert_cleared_after_done": not has_escalation_alert2,
            "passed": has_escalation_alert1 and (not has_escalation_alert2) and (status1 == "已过期")
        }

        logger.info(f"Memory Evolution Result: {results}")
        return results
