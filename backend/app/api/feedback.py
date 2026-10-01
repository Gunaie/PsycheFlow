"""用户反馈 API：收集对话质量评分（1-5 分），用于 A/B 测试效果对比。

POST /api/feedback — 提交某轮对话的评分
GET  /api/feedback/stats — 查询 A/B 测试各组评分统计（管理员）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_account, get_db_session
from app.core.abtest import track_feedback
from app.models import User

logger = logging.getLogger("psycheflow.feedback")
router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    turn_index: int = Field(..., ge=0)  # 第几轮对话（从 0 开始）
    score: int = Field(..., ge=1, le=5)  # 1-5 分
    comment: str = Field("", max_length=500)  # 可选文字反馈
    ab_experiment: str = ""  # 实验名称（从 chat 响应 ab_test.experiment 透传）
    ab_variant: str = ""  # 实验组（从 chat 响应 ab_test.variant 透传）


class FeedbackResponse(BaseModel):
    ok: bool


class FeedbackStatsResponse(BaseModel):
    experiment: str
    control_avg: float
    control_count: int
    treatment_avg: float
    treatment_count: int
    total_count: int


@router.post("", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
) -> FeedbackResponse:
    """提交对话评分。

    匿名用户也可提交（account_id 记为 None，session_id 关联）。
    评分存入 Prometheus 指标，按实验/变体聚合。
    """
    # 埋点：A/B 测试评分
    if req.ab_experiment and req.ab_variant:
        track_feedback(req.ab_experiment, req.ab_variant, req.score)

    # 可选：写入 DB 持久化（供后续分析）
    # 当前版本仅 Prometheus 指标，不新增 DB 表（保持轻量）

    logger.info(
        "feedback: session=%s turn=%d score=%d ab=%s/%s account=%s",
        req.session_id, req.turn_index, req.score,
        req.ab_experiment, req.ab_variant,
        account.id if account else "anonymous",
    )
    return FeedbackResponse(ok=True)


@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats(
    experiment: str = "intervention_prompt_v2",
    account: User = Depends(get_current_account),
) -> FeedbackStatsResponse:
    """查询 A/B 测试评分统计（仅教师/管理员）。"""
    if account.role != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可查看 A/B 测试统计")

    # 从 Prometheus 指标读取（简化版：返回当前内存中的计数）
    # 生产环境应查询 Prometheus API 或时序数据库
    from app.core.abtest import AB_FEEDBACK, AB_FEEDBACK_AVG

    control_count = 0
    control_sum = 0.0
    treatment_count = 0
    treatment_sum = 0.0

    # 遍历 Counter 指标（仅作示例，实际应查询 Prometheus）
    for metric in AB_FEEDBACK.collect():
        for sample in metric.samples:
            if sample.labels.get("experiment") == experiment:
                score = int(sample.labels.get("score", "0"))
                count = int(sample.value)
                variant = sample.labels.get("variant", "")
                if variant == "control":
                    control_count += count
                    control_sum += score * count
                elif variant == "treatment":
                    treatment_count += count
                    treatment_sum += score * count

    control_avg = control_sum / control_count if control_count else 0.0
    treatment_avg = treatment_sum / treatment_count if treatment_count else 0.0

    return FeedbackStatsResponse(
        experiment=experiment,
        control_avg=round(control_avg, 2),
        control_count=control_count,
        treatment_avg=round(treatment_avg, 2),
        treatment_count=treatment_count,
        total_count=control_count + treatment_count,
    )
