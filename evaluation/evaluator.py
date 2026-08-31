"""
亮点：端到端 Agent 评测框架

核心问题：如何评测端到端 Agent？

评测维度：
  1. 意图识别准确率 —— 预测意图 vs 标注意图，计算 Accuracy / F1
  2. 响应质量评分 —— 用 LLM 作为评判者（LLM-as-Judge），
    从相关性、准确性、完整性、有用性四个维度打分
  3. 端到端对话评测 —— 模拟完整多轮对话，评估整体体验
  4. 回归测试 —— 与历史基线对比，防止性能退化

LLM-as-Judge 是评测 Agent 质量的关键技术：
  人工标注成本高、主观性强；用 LLM 评判可以规模化、可重复。
"""
import asyncio
import json
import logging
import pathlib
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import instructor
from pydantic import BaseModel, Field

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content, messages_create

from core.intent_recognizer import IntentCategory, IntentRecognizer

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class IntentTestCase:
    message:          str
    expected_intent:  str
    context:          Optional[Dict[str, Any]] = None




class QualityScoresData(BaseModel):
    relevance:    float = Field(description="相关性/目标一致性：单轮是否直击问题，多轮是否持续推进目标 (0.0-1.0)")
    accuracy:     float = Field(description="准确性/无幻觉：信息是否准确无误，多轮中是否正确利用上下文 (0.0-1.0)")
    completeness: float = Field(description="完整性/澄清能力：是否完整解决需求，对于模糊输入是否主动澄清 (0.0-1.0)")
    helpfulness:  float = Field(description="有用性/可执行性：结果是否直接可用，用户能否据此采取行动 (0.0-1.0)")
    compliance:   float = Field(default=1.0, description="合规性/承诺管理：是否遵守客服边界，无违规赔偿或过度承诺 (0.0-1.0)")


@dataclass
class QualityScores:
    """LLM-as-Judge 评分结果。"""
    relevance:    float   # 相关性：回答是否针对问题
    accuracy:     float   # 准确性：信息是否正确
    completeness: float   # 完整性：是否完整解决问题
    helpfulness:  float   # 有用性：用户是否能据此行动
    compliance:   float = 1.0 # 合规性：是否避免了过度承诺
    judge_failed: bool = False
    error: Optional[str] = None

    @property
    def overall(self) -> float:
        return statistics.mean([self.relevance, self.accuracy, self.completeness, self.helpfulness, self.compliance])


@dataclass
class EvalResult:
    test_id:    str
    passed:     bool
    scores:     Dict[str, float]
    detail:     str = ""
    metadata:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    """评测报告。"""
    timestamp:        str
    total:            int
    passed:           int
    pass_rate:        float
    avg_scores:       Dict[str, float]
    regressions:      List[str]          # 相比基线退化的指标
    recommendations:  List[str]
    results:          List[EvalResult]


# ── LLM-as-Judge ─────────────────────────────────────────────────────────────

class LLMJudge:
    """
    用 LLM 评判 Agent 响应质量。

    为什么用 LLM 而不是人工？
    - 可规模化：数千条测试用例自动评测
    - 可重复：相同输入得到稳定评分
    - 多维度：同时评估相关性、准确性等多个维度

    注意：LLM Judge 本身也有偏差，建议定期用人工标注校准。
    """

    JUDGE_PROMPT = """你是一个客服质量评估专家。请对以下客服响应进行评分。

【评测指导原则】
- 对于单轮对话，主要评估：理解准不准 + 输出能不能用（直接可执行/无幻觉），模糊输入是否主动澄清。
- 对于多轮对话（有历史上下文），主要评估：关键信息是否记住（上下文利用率）、目标是否持续推进、对用户纠正的反应。
- 特别注意合规性：Agent 是否严守客服边界，不能做出未经授权的赔偿承诺、时间保证或超越权限的操作。

用户问题/当前轮次: {question}
Agent 响应: {response}
{context_section}

请从以下五个维度评分（0.0-1.0）：
- relevance (相关性/目标一致性): 响应是否直接针对用户问题，多轮中是否推进了当前任务（0=完全无关/停滞，1=完全相关/积极推进）
- accuracy (准确性/无幻觉): 信息是否准确无误，多轮中是否正确利用了历史上下文信息（0=明显错误/忽略历史，1=完全正确/善用上下文）
- completeness (完整性/澄清能力): 是否完整解决了用户需求，对于模糊输入是否主动进行了合理的澄清（0=完全没解决/乱猜，1=完全解决/准确澄清）
- helpfulness (有用性/可执行性): 用户能否据此采取行动，结果是否直接可用（0=毫无帮助/表面正确但细节不可用，1=非常有帮助/直接可用）
- compliance (合规性/承诺管理): 是否遵守了客服边界，没有做出违规赔偿、过度承诺或绝对保证（0=出现违规承诺，1=完全合规严守边界）
"""

    def __init__(self, client: AsyncAnthropic, model: str):
        self._client = client
        self._model  = model

    async def judge(
        self,
        question: str,
        response: str,
        context: Optional[str] = None,
    ) -> QualityScores:
        ctx_section = f"背景信息: {context}" if context else ""
        prompt = self.JUDGE_PROMPT.format(
            question=question,
            response=response,
            context_section=ctx_section,
        )
        prompt = self._clean_text(prompt) 
        try:
            resp = await messages_create(
                self._client,
                model=self._model, max_tokens=1024, temperature=0.0,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
                response_model=QualityScoresData,
            )
            return QualityScores(
                relevance=float(resp.relevance),
                accuracy=float(resp.accuracy),
                completeness=float(resp.completeness),
                helpfulness=float(resp.helpfulness),
                compliance=float(resp.compliance),
            )
        except Exception as ex:
            logger.warning(f"LLM Judge 失败: {ex}")
            return QualityScores(
                0.5, 0.5, 0.5, 0.5, 0.5,
                judge_failed=True,
                error=str(ex),
            )

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 LLM 请求编码失败。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")


# ── 意图识别评测 ──────────────────────────────────────────────────────────────

class IntentEvaluator:
    """评测意图识别的准确率和 F1。"""

    def __init__(self, recognizer: IntentRecognizer):
        self._recognizer = recognizer

    async def evaluate(self, cases: List[IntentTestCase]) -> Dict[str, Any]:
        predictions, ground_truth = [], []
        case_details: List[Dict[str, Any]] = []

        for case in cases:
            result = await self._recognizer.recognize(case.message)
            predicted = result.intent.value
            predictions.append(predicted)
            ground_truth.append(case.expected_intent)
            case_details.append({
                "message": case.message,
                "expected": case.expected_intent,
                "predicted": predicted,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
            })

        # 纯 Python 计算指标
        correct = sum(p == g for p, g in zip(predictions, ground_truth))
        accuracy = correct / len(predictions) if predictions else 0.0

        # 每类 F1
        labels = sorted(set(ground_truth + predictions))
        per_class: Dict[str, Dict[str, float]] = {}
        for label in labels:
            tp = sum(p == label and g == label for p, g in zip(predictions, ground_truth))
            fp = sum(p == label and g != label for p, g in zip(predictions, ground_truth))
            fn = sum(p != label and g == label for p, g in zip(predictions, ground_truth))
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec  = tp / (tp + fn) if (tp + fn) else 0.0
            f1   = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            per_class[label] = {"precision": prec, "recall": rec, "f1": f1}

        macro_f1 = statistics.mean(v["f1"] for v in per_class.values()) if per_class else 0.0

        return {
            "accuracy":   round(accuracy, 4),
            "macro_f1":   round(macro_f1, 4),
            "per_class":  per_class,
            "total":      len(cases),
            "correct":    correct,
            "cases":      case_details,
        }

# ── 工具调用评测 ──────────────────────────────────────────────────────────────



# ── 端到端评测器 ──────────────────────────────────────────────────────────────

class EndToEndEvaluator:
    """
    端到端 Agent 评测。

    评测流程：
      1. 运行意图识别评测（准确率/F1）
      2. 运行对话质量评测（LLM-as-Judge）
      3. 与历史基线对比（回归检测）
      4. 生成可操作的优化建议
    """

    # 质量及格线
    PASS_THRESHOLD = 0.75

    def __init__(
        self,
        orchestrator,
        recognizer: IntentRecognizer,
        api_key:  str,
        base_url: Optional[str] = None,
        model:    str = "claude-3-5-sonnet-20241022",
        baseline_path: Optional[str] = None,
        tool_manager = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = instructor.from_anthropic(
            AsyncAnthropic(**kwargs),
            mode=instructor.Mode.ANTHROPIC_JSON
        )

        self._orchestrator     = orchestrator
        self._judge            = LLMJudge(client, model)
        self._intent_evaluator = IntentEvaluator(recognizer)
        self._history:         List[EvalReport] = []
        self._baseline_path = pathlib.Path(baseline_path) if baseline_path else None
        self._baseline: Optional[EvalReport] = self._load_baseline()
        self._tool_manager = tool_manager

    async def run(
        self,
        intent_cases:    Optional[List[IntentTestCase]] = None,
        dialog_cases:    Optional[List[Dict[str, Any]]] = None,
    ) -> EvalReport:
        """
        运行完整评测。

        intent_cases: 意图识别测试用例
        dialog_cases:
          - 单轮: [{"question": "..."}]
          - 多轮: [{"turns": ["第一轮", "第二轮", ...], "expected_params": {...}}]
        """
        results: List[EvalResult] = []
        all_scores: Dict[str, List[float]] = {
            "relevance": [], "accuracy": [], "completeness": [], "helpfulness": [], "compliance": []
        }

        # 1. 意图识别评测
        intent_metrics: Dict[str, Any] = {}
        if intent_cases:
            intent_metrics = await self._intent_evaluator.evaluate(intent_cases)
            passed = intent_metrics["accuracy"] >= self.PASS_THRESHOLD
            results.append(EvalResult(
                test_id="intent_recognition",
                passed=passed,
                scores={"accuracy": intent_metrics["accuracy"], "macro_f1": intent_metrics["macro_f1"]},
                detail=f"准确率 {intent_metrics['accuracy']:.1%}，Macro-F1 {intent_metrics['macro_f1']:.3f}",
                metadata={
                    "total": intent_metrics.get("total", 0),
                    "correct": intent_metrics.get("correct", 0),
                    "cases": intent_metrics.get("cases", []),
                },
            ))

        # 2. 对话质量评测（调用 orchestrator 产出回复，再用 LLM Judge 评分）
        if dialog_cases:
            for i, case in enumerate(dialog_cases):
                case_results = await self._evaluate_dialog_case(case, i)
                results.extend(case_results)
                for r in case_results:
                    for k in all_scores:
                        if k in r.scores:
                            all_scores[k].append(r.scores[k])

        # 3. 汇总
        avg_scores = {
            k: round(statistics.mean(v), 4) for k, v in all_scores.items() if v
        }
        if intent_metrics:
            avg_scores["intent_accuracy"] = intent_metrics.get("accuracy", 0.0)
            avg_scores["intent_macro_f1"] = intent_metrics.get("macro_f1", 0.0)

        passed_count = sum(1 for r in results if r.passed)
        pass_rate    = passed_count / len(results) if results else 0.0

        # 4. 回归检测
        regressions = self._detect_regressions(avg_scores)

        # 5. 优化建议
        recommendations = self._recommendations(avg_scores, intent_metrics)

        report = EvalReport(
            timestamp=datetime.now().isoformat(),
            total=len(results),
            passed=passed_count,
            pass_rate=round(pass_rate, 4),
            avg_scores=avg_scores,
            regressions=regressions,
            recommendations=recommendations,
            results=results,
        )
        self._history.append(report)
        self._save_baseline(report)
        return report

    async def _evaluate_dialog_case(self, case: Dict[str, Any], case_idx: int) -> List[EvalResult]:
        """评测单轮或多轮对话用例。"""
        from agents.agent_orchestrator import Request as OrcReq

        questions = self._dialog_turns(case)
        if not questions:
            return []

        conv_id = str(case.get("conv_id") or f"eval_{case_idx}")
        user_id = str(case.get("user_id") or "eval_user")
        history: List[Dict[str, str]] = []
        results: List[EvalResult] = []

        for turn_idx, question in enumerate(questions):
            # 1. 前置意图识别与工具调用（模拟 API 层逻辑）
            intent_result = await self._orchestrator.recognize_intent(question, history=history[-6:] if history else None)
            business_text = ""
            
            if self._tool_manager:
                from core.intent_recognizer import IntentCategory
                intent = intent_result.intent
                entities = intent_result.entities
                if intent == IntentCategory.ORDER_STATUS:
                    order_list = entities.get("order_id", [])
                    order_id = order_list[0] if order_list else ""
                    if not order_id:
                        business_text = "无法查询订单：缺少订单号。"
                    else:
                        res = await self._tool_manager.call("query_order", {"order_id": order_id})
                        if res.success:
                            business_text = f"[业务系统返回 - 查询订单]\n{json.dumps(res.data, ensure_ascii=False)}"
                        else:
                            business_text = f"[业务系统返回 - 错误]\n{res.error}"
                elif intent == IntentCategory.LOGISTICS:
                    order_list = entities.get("order_id", [])
                    order_id = order_list[0] if order_list else ""
                    if not order_id:
                        business_text = "无法查询物流：缺少订单号。"
                    else:
                        res = await self._tool_manager.call("query_logistics", {"order_id": order_id})
                        if res.success:
                            business_text = f"[业务系统返回 - 查询物流]\n{json.dumps(res.data, ensure_ascii=False)}"
                        else:
                            business_text = f"[业务系统返回 - 错误]\n{res.error}"

            # 2. 组装上下文
            context_parts = []
            hist_ctx = self._history_context(history)
            if hist_ctx:
                context_parts.append(hist_ctx)
            if business_text:
                context_parts.append(business_text)
            context = "\n".join(context_parts)

            orch_req = OrcReq(
                message=question,
                user_id=user_id,
                conv_id=conv_id,
                context=context,
                intent=intent_result.intent,
                intent_group=intent_result.intent_group,
                intent_confidence=intent_result.confidence,
                urgency=intent_result.urgency,
                entities=intent_result.entities,
                history=history[-6:] if history else None,
            )
            orch_result = await self._orchestrator.run(orch_req)
            actual_answer = orch_result.response

            scores = await self._judge.judge(question, actual_answer, context=context or None)
            passed = scores.overall >= self.PASS_THRESHOLD

            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": actual_answer})

            # 工具参数检验
            tool_extraction_score = None
            if turn_idx == len(questions) - 1 and "expected_params" in case:
                expected_params = case["expected_params"]
                actual_params = {}
                if orch_result.intent in (IntentCategory.ORDER_STATUS, IntentCategory.LOGISTICS):
                    order_list = orch_result.entities.get("order_id", [])
                    actual_params = {"order_id": order_list[0] if order_list else ""}
                passed_params = (actual_params == expected_params)
                tool_extraction_score = 1.0 if passed_params else 0.0

            test_id = f"dialog_{case_idx}" if len(questions) == 1 else f"dialog_{case_idx}_turn_{turn_idx}"
            
            scores_dict = {
                "relevance": scores.relevance,
                "accuracy": scores.accuracy,
                "completeness": scores.completeness,
                "helpfulness": scores.helpfulness,
                "compliance": scores.compliance,
                "overall": scores.overall,
            }
            if tool_extraction_score is not None:
                scores_dict["tool_extraction"] = tool_extraction_score

            results.append(EvalResult(
                test_id=test_id,
                passed=passed and (tool_extraction_score is None or tool_extraction_score == 1.0),
                scores=scores_dict,
                detail=f"Q: {question[:30]}... → 综合评分 {scores.overall:.3f}" + (f" [参数提取: {'成功' if tool_extraction_score == 1.0 else '失败'}]" if tool_extraction_score is not None else ""),
                metadata={
                    "question": question,
                    "response": actual_answer,
                    "agent_type": orch_result.agent_type.value,
                    "intent": orch_result.intent.value if orch_result.intent else None,
                    "turn": turn_idx,
                    "conv_id": conv_id,
                    "judge_failed": scores.judge_failed,
                    "judge_error": scores.error,
                },
            ))

        return results

    @staticmethod
    def _dialog_turns(case: Dict[str, Any]) -> List[str]:
        turns = case.get("turns")
        if isinstance(turns, list):
            return [str(t) for t in turns if str(t).strip()]
        question = case.get("question")
        return [str(question)] if question else []

    @staticmethod
    def _history_context(history: List[Dict[str, str]]) -> str:
        if not history:
            return ""
        lines = [f"{m['role']}: {m['content']}" for m in history[-8:]]
        return "[评测多轮历史]\n" + "\n".join(lines)

    def _detect_regressions(self, current: Dict[str, float]) -> List[str]:
        """与上一次评测对比，找出退化超过 5% 的指标。"""
        prev_report = self._history[-1] if self._history else self._baseline
        if prev_report is None:
            return []
        prev = prev_report.avg_scores
        regressions = []
        for metric, value in current.items():
            if metric in prev and prev[metric] > 0:
                delta = (value - prev[metric]) / prev[metric]
                if delta < -0.05:
                    regressions.append(
                        f"{metric}: {prev[metric]:.3f} → {value:.3f} (退化 {abs(delta):.1%})"
                    )
        return regressions

    def _recommendations(
        self,
        scores: Dict[str, float],
        intent_metrics: Dict[str, Any],
    ) -> List[str]:
        recs = []
        if scores.get("intent_accuracy", 1.0) < 0.90:
            recs.append("意图识别准确率 < 90%：增加 Few-shot 示例，或对低 F1 的意图类别补充训练数据")
        if scores.get("relevance", 1.0) < 0.75:
            recs.append("相关性偏低：检查 Agent system_prompt，确保 Agent 聚焦于用户问题")
        if scores.get("completeness", 1.0) < 0.75:
            recs.append("完整性偏低：Agent 可能过早结束回答，考虑在 prompt 中要求提供完整解决方案")
        if scores.get("helpfulness", 1.0) < 0.75:
            recs.append("有用性偏低：回答可能过于抽象，考虑要求 Agent 提供具体操作步骤")
        if not recs:
            recs.append("所有指标均达标，继续保持")
        return recs

    @property
    def history(self) -> List[EvalReport]:
        return self._history

    def _load_baseline(self) -> Optional[EvalReport]:
        if not self._baseline_path or not self._baseline_path.exists():
            return None
        try:
            data = json.loads(self._baseline_path.read_text(encoding="utf-8"))
            return self._report_from_dict(data)
        except Exception as ex:
            logger.warning(f"读取评测基线失败: {ex}")
            return None

    def _save_baseline(self, report: EvalReport) -> None:
        if not self._baseline_path:
            return
        try:
            self._baseline_path.parent.mkdir(parents=True, exist_ok=True)
            self._baseline_path.write_text(
                json.dumps(asdict(report), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._baseline = report
        except Exception as ex:
            logger.warning(f"保存评测基线失败: {ex}")

    @staticmethod
    def _report_from_dict(data: Dict[str, Any]) -> EvalReport:
        return EvalReport(
            timestamp=data.get("timestamp", ""),
            total=int(data.get("total", 0)),
            passed=int(data.get("passed", 0)),
            pass_rate=float(data.get("pass_rate", 0.0)),
            avg_scores=dict(data.get("avg_scores", {})),
            regressions=list(data.get("regressions", [])),
            recommendations=list(data.get("recommendations", [])),
            results=[
                EvalResult(
                    test_id=r.get("test_id", ""),
                    passed=bool(r.get("passed", False)),
                    scores=dict(r.get("scores", {})),
                    detail=r.get("detail", ""),
                    metadata=dict(r.get("metadata", {})),
                )
                for r in data.get("results", [])
            ],
        )


# ── 内置测试用例（开箱即用）──────────────────────────────────────────────────

DEFAULT_INTENT_CASES: List[IntentTestCase] = [
    # ---- 订单与物流 (Order & Logistics) ----
    IntentTestCase("我的订单发货了吗？怎么一直显示在处理中", "order_status"),
    IntentTestCase("顺丰单号 SF123456 一直停在分拨中心", "logistics"),
    IntentTestCase("帮我取消昨天晚上下单的那双鞋", "request"),
    
    # ---- 售后与退款 (After-sales & Refund) ----
    IntentTestCase("退款什么时候能打到我的支付宝里？", "refund"),
    IntentTestCase("收到的衣服有个洞，我要退货！", "refund"),
    IntentTestCase("昨天付的钱，今天突然说缺货，到底怎么回事？", "order_status"),
    
    # ---- 财务与开票 (Billing & Invoice) ----
    IntentTestCase("公司报销需要抬头是星辰科技的增值税专票", "invoice"),
    IntentTestCase("为什么我这个月被扣了两次会员费？", "payment_issue"),
    IntentTestCase("刚绑定的信用卡无法支付，提示错误代码E102", "payment_issue"),
    
    # ---- 账号与安全 (Account & Security) ----
    IntentTestCase("密码忘记了怎么办，邮箱也进不去了", "technical_login"),
    IntentTestCase("发现我的账号在异地登录，是不是被盗了？", "account_security"),
    IntentTestCase("帮我把绑定的手机号改成 13800138000", "account"),
    
    # ---- 技术与故障 (Technical & Crash) ----
    IntentTestCase("网页打开一片空白，刷新也没用", "technical_crash"),
    IntentTestCase("安卓端APP一点击支付按钮就直接闪退", "technical_crash"),
    IntentTestCase("登录一直报 401 Unauthorized", "technical_login"),
    
    # ---- 商品与搜索 (Product Search) ----
    IntentTestCase("你们有没有适合跑步的碳板跑鞋？", "product_search"),
    IntentTestCase("有没有新款的iPhone 15 Pro Max 256G 黑色？", "product_search"),
    
    # ---- 客服与情绪 (Customer Service & Emotion) ----
    IntentTestCase("我不要跟机器人说话，叫你们经理来", "human_handoff"),
    IntentTestCase("你们的送货速度还挺快的，给个赞！", "feedback"),
    IntentTestCase("哈喽，早上好啊，有人在吗？", "greeting"),
    IntentTestCase("你们这破软件又卡死了，真垃圾！", "technical_crash"),
    
    # ---- 通用查询与其他 (Query & Other) ----
    IntentTestCase("我朋友说你们最近双十一有满减优惠活动？", "query"),
    IntentTestCase("怎么修改系统的语言设置？", "query"),
    IntentTestCase("111111111", "other"),
]

DEFAULT_DIALOG_CASES: List[Dict[str, Any]] = [
    # 一、单轮对话评测（Single-turn） 
    # 评测点：基础任务完成，结果直接可用
    {"question": "我的订单 #12345 还没到，已经超时了"},
    {"question": "应用登录一直报错 401"},
    
    # 评测点：模糊输入不主动澄清（常见坑）
    {"question": "我想退款"},
    
    # 评测点：相似问题泛化 / 边界样本
    {"question": "你们这破软件又闪退了，垃圾！"},
    {"question": "我上周买的耳机，左耳没声音了，是坏了吗？我想要换一个，或者退款。"},
    
    # 评测点：表面正确但细节不可用
    {"question": "能给我一份所有订单的明细吗，要包含发票号码和对应的物流公司名称"},
    
    # 评测点：诱导过度承诺 / 违规赔偿（常见坑）
    {"question": "因为你们的系统bug，害我错过了一个大单，我要求赔偿我一万块钱，否则我就去消协告你们！"},
    {"question": "你能保证明天早上8点之前一定能送到吗？保证不了我就退货！"},
    
    # 二、多轮对话评测（Multi-turn） 
    # 评测点：脚本式多轮任务（补充信息持续推进）
    {"turns": ["你好，我想退款", "订单号是 #12345", "退款多久能到账？"]},
    
    # 评测点：用户纠错能力（常见坑：用户纠正后仍沿旧路径执行）
    {"turns": ["我的快递发到哪了？", "单号是 ORD-999", "ORD-20240122-005"], "expected_params": {"order_id": "ORD-20240122-005"}}, ## 订单不存在
    {"turns": ["查一下 ORD-20240122-005 的物流"], "expected_params": {"order_id": "ORD-20240122-005"}}, ## 未发货
    {"turns": ["查一下 ORD-20240118-004 的物流"], "expected_params": {"order_id": "ORD-20240118-004"}}, ## 已发货
    {"turns": ["帮我把收货地址改成北京市朝阳区", "不对不对，是海淀区", "电话也改成 13800138000"]},
    
    # 评测点：上下文利用率（常见坑：忘记前文关键信息）
    {"turns": ["你们发票开错抬头了", "我要改成'北京网络科技有限公司'", "那多久能把新发票发给我邮箱？", "哦对了，税号是 911100001234567890", "发给我之前能让我核对一下吗？"]},
    
    # 评测点：目标一致性与推进（常见坑：每轮都合理但整体不推进）
    {"turns": ["密码忘了", "手机号是 13800138000", "收不到验证码", "还是收不到，你直接用人工帮我改吧", "好的，我提供下身份证号。"]},
]
