"""定义 HTTP 接口与模型评估的权威数据结构。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScoringDimensionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    weight: int = Field(ge=1, le=100)
    kind: Literal["core", "knockout", "bonus"] = "core"


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1, max_length=100_000)
    prompt: str = Field(min_length=1, max_length=50_000)
    scoring_profile: list[ScoringDimensionIn] | None = None


class JobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    jd_text: str | None = Field(default=None, min_length=1, max_length=100_000)
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)
    scoring_profile: list[ScoringDimensionIn] | None = None


class ScoringDimensionOut(BaseModel):
    criterion_id: str
    name: str
    weight: int
    kind: Literal["core", "knockout", "bonus"] = "core"
    knockout: bool = False


class ScoringPreviewRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=50_000)


class ScoringPreviewOut(BaseModel):
    source: Literal["prompt", "default"]
    dimensions: list[ScoringDimensionOut]


class ModelListRequest(BaseModel):
    """页面「获取模型」提交的未保存连接信息；只用于本次请求，不保存。"""

    api_key: str = Field(min_length=1, max_length=500)
    base_url: str | None = Field(default=None, max_length=500)


class ModelListOut(BaseModel):
    models: list[str]


class ModelTestRequest(ModelListRequest):
    """页面「测试连接」提交的未保存配置；只用于本次测试，不保存。"""

    text_model: str = Field(min_length=1, max_length=200)
    vision_model: str = Field(min_length=1, max_length=200)


class ModelTestResult(BaseModel):
    ok: bool
    checked_models: list[str]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    jd_text: str
    prompt: str
    resume_count: int = 0
    run_count: int = 0
    scoring_dimensions: list[ScoringDimensionOut] = Field(default_factory=list)
    scoring_source: Literal["prompt", "default", "profile", "invalid"] = "default"
    scoring_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ResumeUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    parsed_text: str | None = Field(default=None, min_length=1, max_length=500_000)


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    original_name: str
    display_name: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    parse_status: str
    parse_error: str | None
    source: Literal["upload", "email", "paste"] = "upload"
    parsed_text_source: Literal["extracted", "manual"]
    parsed_text_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UploadRejection(BaseModel):
    filename: str
    reason: str
    recoverable_resume_id: str | None = None


class UploadResult(BaseModel):
    uploaded: list[ResumeOut]
    rejected: list[UploadRejection]


class EmailSyncResult(BaseModel):
    """一次邮箱同步的结果计数。"""

    messages: int = 0
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None


class EmailStatusOut(BaseModel):
    """邮箱捕获配置与最近一次同步状态。"""

    configured: bool
    mailbox: str | None = None
    poll_seconds: int = 0
    last_run_at: datetime | None = None
    last_result: EmailSyncResult | None = None


class PoolEvaluationSummary(BaseModel):
    """简历池条目附带的当前评估摘要。"""

    overall_score: float | None = None
    match_level: str | None = None
    review_status: str = "pending"
    gender: str | None = None
    age: int | None = None


class PoolEvaluatedJob(BaseModel):
    """简历曾经被评估过的岗位引用。"""

    job_id: str
    job_name: str


class PoolResumeOut(BaseModel):
    """简历池列表条目：邮箱收取且尚未分配，或已分配但保留在池中追踪。"""

    id: str
    job_id: str | None = None
    job_name: str | None = None
    original_name: str
    display_name: str
    source_title: str | None = None
    mime_type: str
    size_bytes: int
    page_count: int | None = None
    parse_status: str
    parse_error: str | None = None
    source: Literal["upload", "email", "paste"] = "upload"
    created_at: datetime
    evaluation: PoolEvaluationSummary | None = None
    evaluation_status: str | None = None
    evaluated_jobs: list[PoolEvaluatedJob] = Field(default_factory=list)


class PoolPasteRequest(BaseModel):
    """粘贴文本录入简历池的入参；confirm=true 表示用户已确认疑似重复仍要录入。"""

    text: str = Field(min_length=1, max_length=200_000)
    name: str | None = Field(default=None, max_length=200)
    confirm: bool = False


class PoolPasteDuplicate(BaseModel):
    """粘贴查重命中的疑似重复池简历。"""

    id: str
    name: str
    source: str
    created_at: datetime
    matched_fields: list[str] = Field(default_factory=list)


class PoolPasteResult(BaseModel):
    """粘贴录入结果：命中疑似重复时不创建，仅返回候选列表。"""

    created: bool
    resume: PoolResumeOut | None = None
    duplicates: list[PoolPasteDuplicate] = Field(default_factory=list)


class PoolAssignRequest(BaseModel):
    resume_ids: list[str] = Field(min_length=1)
    job_id: str
    evaluate: bool = True


class PoolAssignSkip(BaseModel):
    resume_id: str
    filename: str
    reason: str


class PoolAssignResult(BaseModel):
    assigned: int
    skipped: list[PoolAssignSkip]
    run: RunOut | None = None


class Evidence(BaseModel):
    text: str
    location: str


class BackgroundHighlight(BaseModel):
    kind: Literal["company", "school"]
    name: str = Field(min_length=1, max_length=200)
    bonus_points: int = Field(default=0, ge=0, le=3)
    evidence: list[Evidence] = Field(default_factory=list)


class Criterion(BaseModel):
    criterion_id: str = ""
    criterion: str
    weight: int = Field(default=0, ge=0, le=100)
    score: int | None = Field(default=None, ge=0, le=100)
    weighted_score: float = Field(default=0, ge=0, le=100)
    status: Literal["met", "partial", "not_met", "unknown"]
    knockout: bool = False
    kind: Literal["core", "knockout", "bonus"] = "core"
    evidence: list[Evidence] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    name: str = "未识别"
    email: str | None = None
    phone: str | None = None
    gender: Literal["male", "female"] | None = None
    age: int | None = Field(default=None, ge=0, le=120)
    current_role: str | None = None
    years_experience: float | None = None


class DraftCriterion(BaseModel):
    criterion_id: str = Field(pattern=r"^D\d{2}$")
    score: int = Field(ge=0, le=100)
    status: Literal["met", "partial", "not_met", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)


class EvaluationDraft(BaseModel):
    candidate: CandidateProfile = Field(default_factory=CandidateProfile)
    confidence: float = Field(ge=0, le=1)
    summary: str
    criteria: list[DraftCriterion]
    background_highlights: list[BackgroundHighlight] = Field(default_factory=list, max_length=4)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)


class EvaluationCard(BaseModel):
    scoring_version: str = "1"
    candidate: CandidateProfile = Field(default_factory=CandidateProfile)
    overall_score: int = Field(ge=0, le=100)
    match_level: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    criteria: list[Criterion] = Field(default_factory=list)
    background_highlights: list[BackgroundHighlight] = Field(default_factory=list, max_length=4)
    background_bonus: int = Field(default=0, ge=0, le=5)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)


class RunCreate(BaseModel):
    resume_ids: list[str] | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    status: str
    total_count: int
    completed_count: int
    failed_count: int
    text_model: str
    vision_model: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunPage(BaseModel):
    items: list[RunOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class JobPasteResult(BaseModel):
    """岗位内粘贴录入结果：创建成功时附带回写的简历与已启动的评分任务。"""

    created: bool
    resume: ResumeOut | None = None
    duplicates: list[PoolPasteDuplicate] = Field(default_factory=list)
    run: RunOut | None = None


class EvaluationOut(BaseModel):
    id: str
    run_id: str
    resume_id: str
    resume_name: str
    status: str
    result: EvaluationCard | None
    error: str | None
    attempts: int
    review_status: str
    review_note: str
    screened_at: datetime
    updated_at: datetime


class EvaluationIds(BaseModel):
    evaluation_ids: list[str] = Field(min_length=1, max_length=200)


class RescoreRequest(EvaluationIds):
    job_id: str | None = Field(default=None, min_length=1)


class BatchReviewUpdate(EvaluationIds):
    status: Literal["pending", "advance", "reject", "hold"]


class BatchActionResult(BaseModel):
    updated_count: int


class EvaluationPage(BaseModel):
    items: list[EvaluationOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class ReviewUpdate(BaseModel):
    status: Literal["pending", "advance", "reject", "hold"]
    note: str = Field(default="", max_length=10_000)


class TrashItem(BaseModel):
    item_type: Literal["job", "resume", "run", "evaluation"]
    id: str
    name: str
    job_id: str | None
    job_name: str
    deleted_at: datetime
    purge_at: datetime


class TrashPage(BaseModel):
    items: list[TrashItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class TrashReference(BaseModel):
    item_type: Literal["job", "resume", "run", "evaluation"]
    id: str


class TrashBatchAction(BaseModel):
    items: list[TrashReference] = Field(min_length=1, max_length=200)
