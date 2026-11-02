"""创建 FastAPI 应用并注册工作台 HTTP 接口。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import asyncio
import hashlib
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Integer, String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .config import Settings
from .database import Base, create_database
from .documents import DocumentError, DocumentProcessor
from .names import display_name_from_filename
from .errors import public_failure_message
from .evaluator import Evaluator, OpenAIEvaluator, compile_scoring_dimensions, compile_scoring_rubric
from .identity import (
    IDENTITY_POLL_SECONDS,
    IdentityExtractor,
    OpenAIIdentityExtractor,
    extract_email,
    extract_phone,
    normalize_pasted_text,
    process_pending_identities,
)
from .lifecycle import (
    TRASH_MODELS,
    TrashType,
    is_top_level_trash_item,
    purge_expired,
    purge_item,
    restore_evaluation,
    restore_job,
    restore_resume,
    restore_run,
    trash_evaluation,
    trash_job,
    trash_resume,
    trash_run,
)
from .models import Evaluation, Job, Resume, ScreeningRun
from .mail_ingest import MailIngester
from .model_config import (
    ModelConfig,
    RequestModelConfigMiddleware,
    check_model_connection,
    current_model_config,
    list_available_models,
)
from .runner import ScreeningRunner
from .schemas import (
    BatchActionResult,
    BatchReviewUpdate,
    EmailStatusOut,
    EmailSyncResult,
    EvaluationIds,
    RescoreRequest,
    EvaluationOut,
    EvaluationPage,
    JobCreate,
    JobOut,
    JobPasteResult,
    JobUpdate,
    ModelListOut,
    ModelListRequest,
    ModelTestRequest,
    ModelTestResult,
    PoolAssignRequest,
    PoolAssignResult,
    PoolAssignSkip,
    PoolEvaluatedJob,
    PoolEvaluationSummary,
    PoolPasteDuplicate,
    PoolPasteRequest,
    PoolPasteResult,
    PoolResumeOut,
    ResumeOut,
    ResumeUpdate,
    ReviewUpdate,
    RunCreate,
    RunOut,
    RunPage,
    ScoringPreviewOut,
    ScoringPreviewRequest,
    TrashBatchAction,
    TrashItem,
    TrashPage,
    UploadRejection,
    UploadResult,
)
from .workbench import (
    active_job,
    active_resume,
    active_run,
    create_run_for_resumes,
    export_response,
    require_active_evaluation,
    serialize_evaluation,
    serialize_job,
    serialize_resume,
    serialize_run,
    serialize_trash_item,
    utc_now,
)


def create_app(
    database_url: str | None = None,
    data_dir: Path | None = None,
    settings: Settings | None = None,
    evaluator: Evaluator | None = None,
    identity_extractor: IdentityExtractor | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings()
    if database_url:
        runtime_settings.database_url = database_url
        if data_dir is None and database_url.startswith("sqlite:///"):
            runtime_settings.data_dir = Path(database_url.removeprefix("sqlite:///")).parent / "data"
    if data_dir:
        runtime_settings.data_dir = data_dir
    engine, session_factory = create_database(runtime_settings.database_url)

    document_processor = DocumentProcessor(runtime_settings)
    identity_extractor = identity_extractor or OpenAIIdentityExtractor(runtime_settings)
    screening_runner = ScreeningRunner(
        session_factory,
        document_processor,
        evaluator or OpenAIEvaluator(runtime_settings),
        runtime_settings,
    )

    async def parse_uploaded_resume(resume_id: str) -> None:
        """在上传响应后解析文件，并用独立数据库会话写回文本。"""

        with session_factory() as parse_session:
            resume = parse_session.get(Resume, resume_id)
            if not resume or resume.deleted_at or resume.parsed_text:
                return
            resume.parse_status = "parsing"
            resume.parse_error = None
            storage_path = resume.storage_path
            parse_session.commit()
        try:
            parsed_text = await document_processor.extract_text(storage_path)
        except Exception as exc:
            with session_factory() as parse_session:
                resume = parse_session.get(Resume, resume_id)
                if resume:
                    resume.parse_status = "failed"
                    resume.parse_error = public_failure_message(exc, "简历解析失败，请重试或改用人工修正")
                    parse_session.commit()
            return
        with session_factory() as parse_session:
            resume = parse_session.get(Resume, resume_id)
            if resume:
                resume.parsed_text = parsed_text
                resume.parsed_text_source = "extracted"
                resume.parsed_text_updated_at = utc_now()
                resume.parse_status = "ready"
                resume.parse_error = None
                parse_session.commit()

    def schedule_resume_parse(resume_id: str) -> None:
        """将邮箱导入的简历挂到解析队列（回调内部跳过已有解析文本的简历）。"""

        asyncio.create_task(parse_uploaded_resume(resume_id))

    mail_ingester = MailIngester(
        runtime_settings,
        session_factory,
        document_processor,
        parse_callback=schedule_resume_parse,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_settings.ensure_directories()
        Base.metadata.create_all(engine)
        screening_runner.recover_interrupted()
        with session_factory() as recovery_session:
            purge_expired(recovery_session, runtime_settings)
            pending_resume_ids = list(
                recovery_session.scalars(
                    select(Resume.id)
                    .outerjoin(Resume.job)
                    .where(
                        Resume.deleted_at.is_(None),
                        or_(Job.deleted_at.is_(None), Resume.job_id.is_(None)),
                        Resume.parsed_text.is_(None),
                        Resume.parse_status.in_(["uploaded", "parsing"]),
                    )
                ).all()
            )
        recovery_tasks = [asyncio.create_task(parse_uploaded_resume(resume_id)) for resume_id in pending_resume_ids]

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(24 * 60 * 60)
                with session_factory() as cleanup_session:
                    purge_expired(cleanup_session, runtime_settings)

        cleanup_task = asyncio.create_task(cleanup_loop())

        email_task: asyncio.Task | None = None
        if runtime_settings.imap_enabled and runtime_settings.imap_poll_seconds > 0:

            async def email_loop() -> None:
                while True:
                    try:
                        await mail_ingester.sync()
                    except Exception:
                        pass  # sync 内部已记录计数与日志，轮询继续
                    await asyncio.sleep(runtime_settings.imap_poll_seconds)

            email_task = asyncio.create_task(email_loop())

        async def identity_loop() -> None:
            while True:
                await asyncio.sleep(IDENTITY_POLL_SECONDS)
                try:
                    await process_pending_identities(session_factory, identity_extractor)
                except Exception:
                    pass  # 单轮失败不影响后续轮询

        identity_task = asyncio.create_task(identity_loop())

        try:
            yield
        finally:
            cleanup_task.cancel()
            identity_task.cancel()
            if email_task:
                email_task.cancel()
                await asyncio.gather(email_task, return_exceptions=True)
            await asyncio.gather(cleanup_task, identity_task, return_exceptions=True)
            for task in recovery_tasks:
                if not task.done():
                    task.cancel()
            if recovery_tasks:
                await asyncio.gather(*recovery_tasks, return_exceptions=True)
            await screening_runner.shutdown()
            engine.dispose()

    app = FastAPI(title="HireFlow", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 最后添加 = 最外层：保证 CORS、路由、BackgroundTasks 与路由内创建的任务都能读到请求配置。
    app.add_middleware(RequestModelConfigMiddleware, settings=runtime_settings)
    app.state.settings = runtime_settings
    app.state.session_factory = session_factory
    app.state.screening_runner = screening_runner
    app.state.document_processor = document_processor
    app.state.mail_ingester = mail_ingester
    app.state.identity_extractor = identity_extractor

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def sync_result_of(summary) -> EmailSyncResult:
        return EmailSyncResult(
            messages=summary.messages,
            imported=summary.imported,
            duplicates=summary.duplicates,
            skipped=summary.skipped,
            failed=summary.failed,
            error=summary.error,
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        model = current_model_config(runtime_settings)
        return {
            "status": "ok",
            "database": "configured",
            "text_model": model.text_model,
            "vision_model": model.vision_model,
            "vision_enabled": runtime_settings.vision_enabled,
            "model_configured": bool(model.api_key),
            "model_source": "browser" if model.from_request else "env",
        }

    @app.post("/api/model-config/models", response_model=ModelListOut)
    async def list_model_options(payload: ModelListRequest) -> ModelListOut:
        """用页面表单里尚未保存的 key 与地址读取可选模型；不保存任何内容。"""

        config = ModelConfig(
            api_key=payload.api_key.strip(),
            base_url=(payload.base_url or "").strip() or None,
            text_model="",
            vision_model="",
            from_request=True,
        )
        try:
            models = await list_available_models(config, runtime_settings)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ModelListOut(models=models)

    @app.post("/api/model-config/test", response_model=ModelTestResult)
    async def test_model_config(payload: ModelTestRequest) -> ModelTestResult:
        """用页面表单里尚未保存的配置实际调用一次模型；不保存任何内容。"""

        config = ModelConfig(
            api_key=payload.api_key.strip(),
            base_url=(payload.base_url or "").strip() or None,
            text_model=payload.text_model.strip(),
            vision_model=payload.vision_model.strip(),
            from_request=True,
        )
        try:
            checked = await check_model_connection(config, runtime_settings)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ModelTestResult(ok=True, checked_models=checked)

    @app.get("/api/email/status", response_model=EmailStatusOut)
    def email_status() -> EmailStatusOut:
        last = mail_ingester.last_summary
        return EmailStatusOut(
            configured=runtime_settings.imap_enabled,
            mailbox=runtime_settings.imap_user if runtime_settings.imap_enabled else None,
            poll_seconds=runtime_settings.imap_poll_seconds,
            last_run_at=last.finished_at if last else None,
            last_result=sync_result_of(last) if last else None,
        )

    @app.post("/api/email/sync", response_model=EmailSyncResult)
    async def trigger_email_sync() -> EmailSyncResult:
        if not runtime_settings.imap_enabled:
            raise HTTPException(status_code=400, detail="未配置邮箱捕获（IMAP_HOST / IMAP_USER / IMAP_PASSWORD）")
        summary = await mail_ingester.sync()
        return sync_result_of(summary)

    def serialize_pool_resume(
        resume: Resume,
        *,
        evaluation: PoolEvaluationSummary | None = None,
        evaluation_status: str | None = None,
        evaluated_jobs: list[PoolEvaluatedJob] | None = None,
    ) -> PoolResumeOut:
        return PoolResumeOut(
            id=resume.id,
            job_id=resume.job_id,
            job_name=resume.job.name if resume.job else None,
            original_name=resume.original_name,
            display_name=resume.display_name or resume.original_name,
            source_title=resume.source_title,
            mime_type=resume.mime_type,
            size_bytes=resume.size_bytes,
            page_count=resume.page_count,
            parse_status=resume.parse_status,
            parse_error=resume.parse_error,
            source=resume.source if resume.source in {"upload", "email", "paste"} else "upload",
            created_at=resume.created_at,
            evaluation=evaluation,
            evaluation_status=evaluation_status,
            evaluated_jobs=evaluated_jobs or [],
        )

    @app.get("/api/pool/resumes", response_model=list[PoolResumeOut])
    def list_pool_resumes(
        scope: Literal["pool", "all"] = Query("pool"),
        session: Session = Depends(get_session),
    ) -> list[PoolResumeOut]:
        """简历池：默认返回邮箱收取（含已分配）与未分配岗位的简历；scope=all 返回全库简历。"""

        statement = select(Resume).where(Resume.deleted_at.is_(None))
        if scope == "pool":
            statement = statement.where(or_(Resume.source == "email", Resume.job_id.is_(None)))
        resumes = session.scalars(statement.order_by(Resume.created_at.desc())).all()

        resume_ids = [resume.id for resume in resumes]
        current_evaluations: dict[str, Evaluation] = {}
        evaluated_jobs_by_resume: dict[str, dict[str, str]] = {}
        latest_status: dict[str, str] = {}
        if resume_ids:
            current_rows = session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .where(
                    Evaluation.resume_id.in_(resume_ids),
                    Evaluation.deleted_at.is_(None),
                    ScreeningRun.deleted_at.is_(None),
                )
                .order_by(ScreeningRun.created_at.desc(), Evaluation.created_at.desc())
            ).all()
            for row in current_rows:
                latest_status.setdefault(row.resume_id, row.status)
                if row.status in {"queued", "parsing", "evaluating"}:
                    latest_status[row.resume_id] = row.status
                if row.status == "completed" and row.is_current:
                    current_evaluations.setdefault(row.resume_id, row)
            job_rows = session.execute(
                select(Evaluation.resume_id, Job.id, Job.name)
                .join(ScreeningRun, Evaluation.run_id == ScreeningRun.id)
                .join(Job, ScreeningRun.job_id == Job.id)
                .where(
                    Evaluation.resume_id.in_(resume_ids),
                    Evaluation.status == "completed",
                    Evaluation.deleted_at.is_(None),
                )
                .distinct()
            ).all()
            for resume_id, job_id, job_name in job_rows:
                evaluated_jobs_by_resume.setdefault(resume_id, {})[job_id] = job_name

        items: list[PoolResumeOut] = []
        for resume in resumes:
            evaluation = current_evaluations.get(resume.id)
            summary = None
            if evaluation:
                result = evaluation.result_json or {}
                candidate = result.get("candidate") or {}
                summary = PoolEvaluationSummary(
                    overall_score=result.get("overall_score"),
                    match_level=result.get("match_level"),
                    review_status=evaluation.review_status,
                    gender=candidate.get("gender"),
                    age=candidate.get("age"),
                )
            items.append(
                serialize_pool_resume(
                    resume,
                    evaluation=summary,
                    evaluation_status=latest_status.get(resume.id),
                    evaluated_jobs=[
                        PoolEvaluatedJob(job_id=job_id, job_name=job_name)
                        for job_id, job_name in sorted(evaluated_jobs_by_resume.get(resume.id, {}).items(), key=lambda pair: pair[1])
                    ],
                )
            )
        return items

    def find_paste_duplicates(session: Session, *, text: str, name: str | None, job_id: str | None) -> list[PoolPasteDuplicate]:
        """粘贴文本按身份信息查重：identity 列相等优先，parsed_text 包含兜底。

        job_id 为空时在简历池范围内查重，否则只查该岗位内未删除的简历。
        """

        email = extract_email(text)
        phone = extract_phone(text)
        if not email and not phone and not name:
            return []
        scope = or_(Resume.job_id.is_(None), Resume.source == "email") if job_id is None else Resume.job_id == job_id
        candidates = session.scalars(
            select(Resume).where(Resume.deleted_at.is_(None), scope)
        ).all()
        duplicates: list[PoolPasteDuplicate] = []
        for candidate in candidates:
            matched: list[str] = []
            if name and (candidate.identity_name == name or (candidate.parsed_text and name in candidate.parsed_text)):
                matched.append("姓名")
            if email and (candidate.identity_email == email or (candidate.parsed_text and email in candidate.parsed_text)):
                matched.append("邮箱")
            if phone and (candidate.identity_phone == phone or (candidate.parsed_text and phone in candidate.parsed_text)):
                matched.append("手机号")
            if matched:
                duplicates.append(
                    PoolPasteDuplicate(
                        id=candidate.id,
                        name=candidate.display_name or candidate.original_name,
                        source=candidate.source,
                        created_at=candidate.created_at,
                        matched_fields=matched,
                    )
                )
        return duplicates

    def create_pasted_resume(session: Session, *, job_id: str | None, name: str | None, text: str) -> Resume:
        """粘贴文本落盘为 .txt 并建行（只 flush，由调用方提交）；job_id 为空表示进简历池。"""

        content = text.encode("utf-8")
        resume_id = str(uuid4())
        upload_dir = runtime_settings.uploads_dir / (job_id or "pool")
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_path = upload_dir / f"{resume_id}.txt"
        storage_path.write_bytes(content)
        original_name = f"{name}.txt" if name else f"粘贴简历-{utc_now().strftime('%Y%m%d-%H%M%S')}.txt"
        resume = Resume(
            id=resume_id,
            job_id=job_id,
            original_name=original_name,
            display_name=name or original_name,
            mime_type="text/plain",
            storage_path=str(storage_path),
            preview_path=None,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            page_count=None,
            parse_status="ready",
            parsed_text=text,
            parsed_text_source="extracted",
            parsed_text_updated_at=utc_now(),
            source="paste",
            source_title=None,
            # 仅入池简历做身份提取，岗位直传不提取
            identity_status="pending" if job_id is None else "none",
        )
        session.add(resume)
        session.flush()
        return resume

    @app.post("/api/pool/resumes/paste", response_model=PoolPasteResult)
    def paste_pool_resume(payload: PoolPasteRequest, session: Session = Depends(get_session)) -> PoolPasteResult:
        """把粘贴的简历文本录入简历池；未 confirm 时命中疑似重复则只返回候选列表。"""

        text = normalize_pasted_text(payload.text)
        if not text:
            raise HTTPException(status_code=400, detail="粘贴内容为空，请粘贴简历文本")
        name = (payload.name or "").strip() or None
        if not payload.confirm:
            duplicates = find_paste_duplicates(session, text=text, name=name, job_id=None)
            if duplicates:
                return PoolPasteResult(created=False, duplicates=duplicates)
        resume = create_pasted_resume(session, job_id=None, name=name, text=text)
        session.commit()
        return PoolPasteResult(created=True, resume=serialize_pool_resume(resume))

    @app.post("/api/jobs/{job_id}/resumes/paste", response_model=JobPasteResult)
    async def paste_job_resume(job_id: str, payload: PoolPasteRequest, session: Session = Depends(get_session)) -> JobPasteResult:
        """在岗位内粘贴简历文本：查重后建行，并立即启动只含该简历的评分任务。"""

        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        text = normalize_pasted_text(payload.text)
        if not text:
            raise HTTPException(status_code=400, detail="粘贴内容为空，请粘贴简历文本")
        name = (payload.name or "").strip() or None
        existing_count = session.scalar(
            select(func.count()).select_from(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None))
        ) or 0
        if existing_count >= runtime_settings.max_resumes_per_job:
            raise HTTPException(status_code=400, detail="岗位简历数量已达到上限")
        # 内容完全相同的快速判重：唯一索引覆盖回收站中的行，confirm 也不能绕过
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        duplicate = session.scalar(select(Resume).where(Resume.job_id == job_id, Resume.sha256 == digest))
        if duplicate:
            reason = "相同简历仍在回收站，请先恢复旧档案" if duplicate.deleted_at else "该简历内容与本岗位已有简历完全相同，未重复录入"
            raise HTTPException(status_code=409, detail=reason)
        if not payload.confirm:
            duplicates = find_paste_duplicates(session, text=text, name=name, job_id=job_id)
            if duplicates:
                return JobPasteResult(created=False, duplicates=duplicates)
        try:
            compile_scoring_dimensions(job.prompt, job.scoring_profile)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resume = create_pasted_resume(session, job_id=job.id, name=name, text=text)
        run = create_run_for_resumes(session, job, [resume], runtime_settings)
        session.commit()
        screening_runner.start(run.id)
        return JobPasteResult(created=True, resume=serialize_resume(resume), run=serialize_run(run))

    @app.post("/api/pool/assign", response_model=PoolAssignResult)
    async def assign_pool_resumes(payload: PoolAssignRequest, session: Session = Depends(get_session)) -> PoolAssignResult:
        """把简历池中的简历分配到岗位；evaluate=true 时立即为该批启动筛选任务。"""

        job = active_job(session, payload.job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        if payload.evaluate:
            try:
                compile_scoring_dimensions(job.prompt, job.scoring_profile)
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        assigned: list[Resume] = []
        skipped: list[PoolAssignSkip] = []
        to_evaluate: list[Resume] = []
        for resume_id in dict.fromkeys(payload.resume_ids):
            resume = session.scalar(select(Resume).where(Resume.id == resume_id, Resume.deleted_at.is_(None)))
            if not resume:
                skipped.append(PoolAssignSkip(resume_id=resume_id, filename=resume_id, reason="简历不存在或已删除"))
                continue
            if resume.job_id == job.id and payload.evaluate:
                to_evaluate.append(resume)
                continue
            if resume.job_id == job.id:
                skipped.append(PoolAssignSkip(resume_id=resume_id, filename=resume.original_name, reason="已在该岗位中"))
                continue
            duplicate = session.scalar(
                select(Resume).where(
                    Resume.job_id == job.id,
                    Resume.sha256 == resume.sha256,
                    Resume.id != resume.id,
                )
            )
            if duplicate:
                skipped.append(
                    PoolAssignSkip(resume_id=resume_id, filename=resume.original_name, reason="相同简历仍在回收站，请先恢复旧档案" if duplicate.deleted_at else "目标岗位已有相同内容的简历")
                )
                continue
            resume.job_id = job.id
            assigned.append(resume)
            to_evaluate.append(resume)

        run = None
        if to_evaluate and payload.evaluate:
            run = create_run_for_resumes(session, job, to_evaluate, runtime_settings)
        session.commit()
        if run:
            screening_runner.start(run.id)
        return PoolAssignResult(
            assigned=len(assigned),
            skipped=skipped,
            run=serialize_run(run) if run else None,
        )


    @app.post("/api/scoring-preview", response_model=ScoringPreviewOut)
    def preview_scoring(payload: ScoringPreviewRequest) -> ScoringPreviewOut:
        try:
            rubric = compile_scoring_rubric(payload.prompt)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ScoringPreviewOut(
            source=rubric.source,
            dimensions=[
                {
                    "criterion_id": item.criterion_id,
                    "name": item.name,
                    "weight": item.weight,
                    "kind": item.kind,
                    "knockout": item.knockout,
                }
                for item in rubric.dimensions
            ],
        )

    @app.post("/api/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
    def create_job(payload: JobCreate, session: Session = Depends(get_session)) -> JobOut:
        try:
            compile_scoring_dimensions(payload.prompt, payload.scoring_profile)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job = Job(**payload.model_dump())
        session.add(job)
        session.commit()
        return serialize_job(session, job)

    @app.get("/api/jobs", response_model=list[JobOut])
    def list_jobs(session: Session = Depends(get_session)) -> list[JobOut]:
        jobs = session.scalars(select(Job).where(Job.deleted_at.is_(None)).order_by(Job.updated_at.desc())).all()
        return [serialize_job(session, job) for job in jobs]

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, session: Session = Depends(get_session)) -> JobOut:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        return serialize_job(session, job)

    @app.patch("/api/jobs/{job_id}", response_model=JobOut)
    def update_job(job_id: str, payload: JobUpdate, session: Session = Depends(get_session)) -> JobOut:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        changes = payload.model_dump(exclude_none=True)
        if "prompt" in changes or "scoring_profile" in changes:
            prompt = changes.get("prompt", job.prompt)
            profile = changes.get("scoring_profile", job.scoring_profile)
            try:
                compile_scoring_dimensions(prompt, profile)
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "scoring_profile" in changes:
            changes["scoring_profile"] = [item.model_dump() for item in payload.scoring_profile or []] or None
        for key, value in changes.items():
            setattr(job, key, value)
        session.commit()
        return serialize_job(session, job)

    @app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(job_id: str, session: Session = Depends(get_session)) -> None:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        for run in job.runs:
            if run.status in {"queued", "running"}:
                screening_runner.cancel(run.id)
        trash_job(job)
        session.commit()

    @app.post("/api/jobs/{job_id}/resumes", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
    async def upload_resumes(
        job_id: str,
        background_tasks: BackgroundTasks,
        files: list[UploadFile] = File(...),
        session: Session = Depends(get_session),
    ) -> UploadResult:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        existing_count = session.scalar(
            select(func.count()).select_from(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None))
        ) or 0
        uploaded: list[ResumeOut] = []
        rejected: list[UploadRejection] = []

        for index, upload in enumerate(files):
            filename = upload.filename or f"未命名文件-{index + 1}"
            if existing_count + len(uploaded) >= runtime_settings.max_resumes_per_job:
                rejected.append(UploadRejection(filename=filename, reason="岗位简历数量已达到上限"))
                continue
            content = await upload.read(runtime_settings.max_file_mb * 1024 * 1024 + 1)
            resume_id = str(uuid4())
            try:
                ingested = document_processor.ingest(
                    job_id=job_id,
                    resume_id=resume_id,
                    filename=filename,
                    mime_type=upload.content_type or "application/octet-stream",
                    content=content,
                )
                duplicate = session.scalar(
                    select(Resume).where(Resume.job_id == job_id, Resume.sha256 == ingested.sha256)
                )
                if duplicate:
                    Path(ingested.storage_path).unlink(missing_ok=True)
                    if ingested.preview_path:
                        Path(ingested.preview_path).unlink(missing_ok=True)
                    reason = (
                        "相同简历仍在回收站，请先恢复旧档案"
                        if duplicate.deleted_at
                        else "该简历内容重复，未再次上传"
                    )
                    rejected.append(
                        UploadRejection(
                            filename=filename,
                            reason=reason,
                            recoverable_resume_id=duplicate.id if duplicate.deleted_at else None,
                        )
                    )
                    continue
                resume = Resume(
                    id=resume_id,
                    job_id=job_id,
                    display_name=display_name_from_filename(ingested.original_name) or ingested.original_name,
                    parsed_text_source="extracted",
                    parsed_text_updated_at=utc_now() if ingested.parsed_text else None,
                    **ingested.__dict__,
                )
                session.add(resume)
                session.commit()
                uploaded.append(serialize_resume(resume))
                if not resume.parsed_text:
                    background_tasks.add_task(parse_uploaded_resume, resume.id)
            except DocumentError as exc:
                session.rollback()
                rejected.append(UploadRejection(filename=filename, reason=str(exc)))
            except Exception:
                session.rollback()
                rejected.append(UploadRejection(filename=filename, reason="文件处理失败"))
        return UploadResult(uploaded=uploaded, rejected=rejected)

    @app.get("/api/jobs/{job_id}/resumes", response_model=list[ResumeOut])
    def list_resumes(job_id: str, session: Session = Depends(get_session)) -> list[ResumeOut]:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        items = session.scalars(
            select(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None)).order_by(Resume.created_at.desc())
        ).all()
        return [serialize_resume(item) for item in items]

    @app.patch("/api/resumes/{resume_id}", response_model=ResumeOut)
    def update_resume(
        resume_id: str,
        payload: ResumeUpdate,
        session: Session = Depends(get_session),
    ) -> ResumeOut:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        changes = payload.model_dump(exclude_none=True)
        if "display_name" in changes:
            resume.display_name = changes["display_name"].strip()
        if "parsed_text" in changes:
            if resume.parse_status == "parsing":
                raise HTTPException(status_code=409, detail="简历正在解析，请稍后再编辑")
            resume.parsed_text = changes["parsed_text"].strip()
            resume.parsed_text_source = "manual"
            resume.parsed_text_updated_at = utc_now()
            resume.parse_status = "ready"
            resume.parse_error = None
        session.commit()
        return serialize_resume(resume)

    @app.post("/api/resumes/{resume_id}/reparse", response_model=ResumeOut, status_code=status.HTTP_202_ACCEPTED)
    async def reparse_resume(
        resume_id: str,
        background_tasks: BackgroundTasks,
        session: Session = Depends(get_session),
    ) -> ResumeOut:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        if resume.parse_status == "parsing":
            raise HTTPException(status_code=409, detail="简历已经在解析中")
        resume.parsed_text = None
        resume.parsed_text_source = "extracted"
        resume.parsed_text_updated_at = None
        resume.parse_status = "uploaded"
        resume.parse_error = None
        session.commit()
        background_tasks.add_task(parse_uploaded_resume, resume.id)
        return serialize_resume(resume)

    @app.delete("/api/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_resume(resume_id: str, session: Session = Depends(get_session)) -> None:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        active_evaluations = [
            item for item in resume.evaluations if item.status in {"queued", "parsing", "evaluating"} and not item.deleted_at
        ]
        if active_evaluations:
            raise HTTPException(status_code=409, detail="简历正在筛选，请先取消所属任务")
        trash_resume(resume)
        session.commit()

    @app.get("/api/resumes/{resume_id}/original")
    def original_resume(resume_id: str, session: Session = Depends(get_session)) -> FileResponse:
        resume = active_resume(session, resume_id)
        if not resume or not Path(resume.storage_path).is_file():
            raise HTTPException(status_code=404, detail="简历原件不存在")
        return FileResponse(resume.storage_path, media_type=resume.mime_type, filename=resume.original_name)

    @app.get("/api/resumes/{resume_id}/preview")
    def preview_resume(resume_id: str, session: Session = Depends(get_session)):
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        if resume.original_name.lower().endswith(".pdf"):
            page_count = resume.page_count or 0
            pages = "".join(
                f'<figure><img src="/api/resumes/{resume.id}/preview/pages/{page_number}" '
                f'alt="简历第 {page_number} 页" loading="lazy"><figcaption>第 {page_number} 页</figcaption></figure>'
                for page_number in range(1, page_count + 1)
            )
            html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
*{{box-sizing:border-box}}body{{margin:0;padding:22px;background:#e7e5df;color:#52605a;font:12px system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto}}figure{{margin:0 0 22px}}img{{display:block;width:100%;height:auto;background:white;box-shadow:0 3px 18px rgba(27,38,33,.16)}}
figcaption{{padding:8px 0;text-align:center}}.empty{{padding:60px 20px;text-align:center}}
</style></head><body><main>{pages or '<p class="empty">该 PDF 没有可预览的页面</p>'}</main></body></html>"""
            return HTMLResponse(
                html,
                headers={
                    "Cache-Control": "private, no-store",
                    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
                },
            )
        if resume.preview_path and Path(resume.preview_path).is_file():
            return HTMLResponse(
                Path(resume.preview_path).read_text(encoding="utf-8"),
                headers={"Cache-Control": "private, no-store"},
            )
        raise HTTPException(status_code=404, detail="简历预览不存在")

    @app.get("/api/resumes/{resume_id}/preview/pages/{page_number}")
    def preview_pdf_page(
        resume_id: str,
        page_number: int,
        session: Session = Depends(get_session),
    ) -> Response:
        resume = active_resume(session, resume_id)
        if not resume or not resume.original_name.lower().endswith(".pdf"):
            raise HTTPException(status_code=404, detail="PDF 简历不存在")
        try:
            image = document_processor.render_pdf_page(resume.storage_path, page_number)
        except DocumentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            image,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/api/resumes/{resume_id}/text", response_class=PlainTextResponse)
    def resume_text(
        resume_id: str,
        download: bool = False,
        session: Session = Depends(get_session),
    ) -> PlainTextResponse:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        if resume.parse_status != "ready" or not resume.parsed_text:
            raise HTTPException(status_code=409, detail="简历文本尚未解析完成")
        headers = {"Cache-Control": "private, no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="resume-{resume.id}.txt"'
        return PlainTextResponse(resume.parsed_text, headers=headers)

    @app.post("/api/jobs/{job_id}/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
    async def create_run(job_id: str, payload: RunCreate, session: Session = Depends(get_session)) -> RunOut:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        try:
            compile_scoring_dimensions(job.prompt, job.scoring_profile)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        query = select(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None))
        if payload.resume_ids is not None:
            if not payload.resume_ids:
                raise HTTPException(status_code=400, detail="至少选择一份简历")
            query = query.where(Resume.id.in_(payload.resume_ids))
        resumes = list(session.scalars(query).all())
        if not resumes:
            raise HTTPException(status_code=400, detail="岗位下没有可筛选的简历")
        if payload.resume_ids is not None and len(resumes) != len(set(payload.resume_ids)):
            raise HTTPException(status_code=400, detail="部分简历不属于当前岗位")
        run = create_run_for_resumes(session, job, resumes, runtime_settings)
        session.commit()
        screening_runner.start(run.id)
        return serialize_run(run)

    @app.get("/api/jobs/{job_id}/runs", response_model=RunPage)
    def list_runs(
        job_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        run_status: str = Query(default="all", max_length=30),
        session: Session = Depends(get_session),
    ) -> RunPage:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        query = select(ScreeningRun).where(
            ScreeningRun.job_id == job_id,
            ScreeningRun.deleted_at.is_(None),
        )
        if run_status != "all":
            query = query.where(ScreeningRun.status == run_status)
        total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        items = list(
            session.scalars(
                query.options(selectinload(ScreeningRun.evaluations))
                .order_by(ScreeningRun.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        # runner 只在整批结束时回写计数，列表需按评估状态实时统计进度。
        return RunPage(
            items=[serialize_run(item, refresh_counts=True) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        )

    @app.get("/api/runs/{run_id}", response_model=RunOut)
    def get_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        return serialize_run(run, refresh_counts=True)

    @app.get("/api/runs/{run_id}/evaluations", response_model=list[EvaluationOut])
    def list_evaluations(run_id: str, session: Session = Depends(get_session)) -> list[EvaluationOut]:
        if not active_run(session, run_id):
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        items = session.scalars(
            select(Evaluation)
            .where(
                Evaluation.run_id == run_id,
                Evaluation.deleted_at.is_(None),
            )
            .order_by(Evaluation.updated_at.desc())
        ).all()
        return [serialize_evaluation(item) for item in items]

    @app.get("/api/jobs/{job_id}/evaluations", response_model=EvaluationPage)
    def list_job_evaluations(
        job_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=200),
        review_status: Literal["all", "pending", "advance", "reject", "hold"] = "all",
        match_level: Literal["all", "high", "medium", "low", "unscored"] = "all",
        run_status: Literal["all", "queued", "running", "completed", "partial", "failed", "canceled"] = "all",
        date_from: date | None = None,
        date_to: date | None = None,
        sort: Literal["score-desc", "score-asc", "name-asc", "screened-desc"] = "screened-desc",
        session: Session = Depends(get_session),
    ) -> EvaluationPage:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        query = (
            select(Evaluation)
            .join(Evaluation.run)
            .join(Evaluation.resume)
            .where(
                ScreeningRun.job_id == job_id,
                ScreeningRun.deleted_at.is_(None),
                Resume.deleted_at.is_(None),
                Evaluation.deleted_at.is_(None),
                Evaluation.is_current.is_(True),
            )
        )
        if search.strip():
            pattern = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Resume.original_name.ilike(pattern),
                    Resume.display_name.ilike(pattern),
                    cast(Evaluation.result_json, String).ilike(pattern),
                )
            )
        if review_status != "all":
            query = query.where(Evaluation.review_status == review_status)
        if run_status != "all":
            query = query.where(ScreeningRun.status == run_status)
        if date_from:
            query = query.where(ScreeningRun.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
        if date_to:
            query = query.where(
                ScreeningRun.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
            )
        match_expression = Evaluation.result_json["match_level"].as_string()
        if match_level == "unscored":
            query = query.where(Evaluation.result_json.is_(None))
        elif match_level != "all":
            query = query.where(match_expression == match_level)

        total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        score_expression = cast(Evaluation.result_json["overall_score"].as_string(), Integer)
        name_expression = func.coalesce(
            Evaluation.result_json["candidate"]["name"].as_string(),
            Resume.display_name,
            Resume.original_name,
        )
        if sort == "score-desc":
            query = query.order_by(score_expression.desc().nullslast(), ScreeningRun.created_at.desc())
        elif sort == "score-asc":
            query = query.order_by(score_expression.asc().nullslast(), ScreeningRun.created_at.desc())
        elif sort == "name-asc":
            query = query.order_by(name_expression.asc(), ScreeningRun.created_at.desc())
        else:
            query = query.order_by(ScreeningRun.created_at.desc(), Evaluation.created_at.desc())
        items = list(session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all())
        return EvaluationPage(
            items=[serialize_evaluation(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        )

    @app.patch("/api/evaluations/{evaluation_id}/review", response_model=EvaluationOut)
    def update_review(evaluation_id: str, payload: ReviewUpdate, session: Session = Depends(get_session)) -> EvaluationOut:
        evaluation = require_active_evaluation(session, evaluation_id)
        evaluation.review_status = payload.status
        evaluation.review_note = payload.note
        session.commit()
        return serialize_evaluation(evaluation)

    @app.patch("/api/evaluations/review", response_model=BatchActionResult)
    def batch_update_review(
        payload: BatchReviewUpdate,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        requested = list(dict.fromkeys(payload.evaluation_ids))
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .join(Evaluation.resume)
                .join(ScreeningRun.job)
                .where(
                    Evaluation.id.in_(requested),
                    Evaluation.deleted_at.is_(None),
                    Evaluation.is_current.is_(True),
                    ScreeningRun.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                    Job.deleted_at.is_(None),
                )
            ).all()
        )
        if len(items) != len(requested):
            raise HTTPException(status_code=422, detail="部分筛选结果不存在或不可操作")
        for item in items:
            item.review_status = payload.status
        session.commit()
        return BatchActionResult(updated_count=len(items))

    @app.post("/api/evaluations/rescore", response_model=RunOut, status_code=status.HTTP_201_CREATED)
    async def rescore_evaluations(
        payload: RescoreRequest,
        session: Session = Depends(get_session),
    ) -> RunOut:
        requested = list(dict.fromkeys(payload.evaluation_ids))
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .join(Evaluation.resume)
                .join(ScreeningRun.job)
                .where(
                    Evaluation.id.in_(requested),
                    Evaluation.deleted_at.is_(None),
                    Evaluation.is_current.is_(True),
                    ScreeningRun.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                    Job.deleted_at.is_(None),
                )
            ).all()
        )
        if len(items) != len(requested):
            raise HTTPException(status_code=422, detail="部分筛选结果不存在或已被替换")
        job_ids = {item.run.job_id for item in items}
        if len(job_ids) != 1 and payload.job_id is None:
            raise HTTPException(status_code=422, detail="只能批量重评同一岗位的候选人")
        job = active_job(session, payload.job_id or job_ids.pop())
        if job is None:
            raise HTTPException(status_code=404, detail="岗位不存在")
        try:
            compile_scoring_dimensions(job.prompt, job.scoring_profile)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        run = create_run_for_resumes(session, job, list({item.resume_id: item.resume for item in items}.values()), runtime_settings)
        session.commit()
        screening_runner.start(run.id)
        return serialize_run(run)

    @app.delete("/api/evaluations/{evaluation_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_evaluation(evaluation_id: str, session: Session = Depends(get_session)) -> None:
        evaluation = session.get(Evaluation, evaluation_id)
        if not evaluation or evaluation.deleted_at:
            raise HTTPException(status_code=404, detail="筛选结果不存在")
        if evaluation.run.deleted_at or evaluation.resume.deleted_at or evaluation.run.job.deleted_at:
            raise HTTPException(status_code=404, detail="筛选结果不存在")
        if evaluation.status in {"queued", "parsing", "evaluating"}:
            raise HTTPException(status_code=409, detail="筛选仍在进行，请先取消所属任务")
        trash_evaluation(evaluation)
        session.commit()

    @app.post("/api/evaluations/delete", response_model=BatchActionResult)
    def batch_delete_evaluations(
        payload: EvaluationIds,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        requested = list(dict.fromkeys(payload.evaluation_ids))
        items = list(
            session.scalars(
                select(Evaluation).where(
                    Evaluation.id.in_(requested),
                    Evaluation.deleted_at.is_(None),
                )
            ).all()
        )
        if len(items) != len(requested):
            raise HTTPException(status_code=422, detail="部分筛选结果不存在")
        if any(item.status in {"queued", "parsing", "evaluating"} for item in items):
            raise HTTPException(status_code=409, detail="所选结果中仍有任务正在运行")
        for item in items:
            trash_evaluation(item)
        session.commit()
        return BatchActionResult(updated_count=len(items))

    @app.post("/api/runs/{run_id}/cancel", response_model=RunOut)
    async def cancel_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        if run.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务已结束，无法取消")
        if not screening_runner.cancel(run_id):
            session.refresh(run)
            if run.status not in {"canceled"}:
                raise HTTPException(status_code=409, detail="无法取消该任务")
        session.expire_all()
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        return serialize_run(run, refresh_counts=True)

    @app.delete("/api/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_run(run_id: str, session: Session = Depends(get_session)) -> None:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选任务不存在")
        if run.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务仍在运行，请先取消后再删除")
        trash_run(run)
        session.commit()

    @app.get("/api/runs/{run_id}/export")
    def export_run(
        run_id: str,
        format: Literal["xlsx", "csv", "json", "md", "docx"] = "xlsx",
        evaluation_id: list[str] | None = Query(default=None),
        session: Session = Depends(get_session),
    ) -> Response:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.resume)
                .where(
                    Evaluation.run_id == run_id,
                    Evaluation.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                )
            ).all()
        )
        requested_ids = list(dict.fromkeys(evaluation_id or []))
        if requested_ids:
            items_by_id = {item.id: item for item in items}
            invalid_ids = [item_id for item_id in requested_ids if item_id not in items_by_id]
            if invalid_ids:
                raise HTTPException(status_code=422, detail="所选候选人不属于当前筛选批次")
            items = [items_by_id[item_id] for item_id in requested_ids]
        return export_response(items, format, f"hireflow-{run_id}")

    @app.get("/api/jobs/{job_id}/export")
    def export_job(
        job_id: str,
        format: Literal["xlsx", "csv", "json", "md", "docx"] = "xlsx",
        evaluation_id: list[str] | None = Query(default=None),
        session: Session = Depends(get_session),
    ) -> Response:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .join(Evaluation.resume)
                .where(
                    ScreeningRun.job_id == job_id,
                    ScreeningRun.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                    Evaluation.deleted_at.is_(None),
                    Evaluation.is_current.is_(True),
                )
                .order_by(ScreeningRun.created_at.desc(), Evaluation.created_at.desc())
            ).all()
        )
        requested_ids = list(dict.fromkeys(evaluation_id or []))
        if requested_ids:
            items_by_id = {item.id: item for item in items}
            if any(item_id not in items_by_id for item_id in requested_ids):
                raise HTTPException(status_code=422, detail="所选候选人不属于当前岗位")
            items = [items_by_id[item_id] for item_id in requested_ids]
        return export_response(items, format, f"hireflow-job-{job_id}")

    @app.post("/api/runs/{run_id}/retry-failed", response_model=RunOut)
    async def retry_failed(run_id: str, session: Session = Depends(get_session)) -> RunOut:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        failed = [item for item in run.evaluations if item.status == "failed" and not item.deleted_at]
        if not failed:
            raise HTTPException(status_code=400, detail="没有可重试的失败任务")
        for item in failed:
            item.status = "queued"
            item.error = None
        run.status = "queued"
        run.completed_at = None
        session.commit()
        screening_runner.start(run_id)
        return serialize_run(run)

    @app.get("/api/trash", response_model=TrashPage)
    def list_trash(
        item_type: Literal["all", "job", "resume", "run", "evaluation"] = "all",
        job_id: str | None = None,
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(get_session),
    ) -> TrashPage:
        kinds: list[TrashType] = ["job", "resume", "run", "evaluation"] if item_type == "all" else [item_type]
        items: list[TrashItem] = []
        for kind in kinds:
            model = TRASH_MODELS[kind]
            deleted = session.scalars(select(model).where(model.deleted_at.is_not(None))).all()
            for item in deleted:
                if not is_top_level_trash_item(kind, item):
                    continue
                items.append(serialize_trash_item(kind, item))
        if job_id:
            items = [item for item in items if item.job_id == job_id]
        if search.strip():
            needle = search.strip().casefold()
            items = [item for item in items if needle in item.name.casefold() or needle in item.job_name.casefold()]
        items.sort(key=lambda item: item.deleted_at, reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return TrashPage(
            items=items[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size if total else 0,
        )

    def get_trashed_item(
        session: Session,
        item_type: TrashType,
        item_id: str,
    ) -> Job | Resume | ScreeningRun | Evaluation:
        model = TRASH_MODELS[item_type]
        item = session.get(model, item_id)
        if not item or item.deleted_at is None:
            raise HTTPException(status_code=404, detail="回收站项目不存在")
        if not is_top_level_trash_item(item_type, item):
            raise HTTPException(status_code=404, detail="回收站项目不存在")
        return item

    def restore_trash_resource(
        session: Session,
        item_type: TrashType,
        item: Job | Resume | ScreeningRun | Evaluation,
    ) -> None:
        if item_type == "job":
            assert isinstance(item, Job)
            restore_job(item)
        elif item_type == "resume":
            assert isinstance(item, Resume)
            restore_resume(session, item)
        elif item_type == "run":
            assert isinstance(item, ScreeningRun)
            restore_run(item)
        else:
            assert isinstance(item, Evaluation)
            restore_evaluation(session, item)

    @app.post("/api/trash/{item_type}/{item_id}/restore")
    def restore_trash_item(
        item_type: TrashType,
        item_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, bool]:
        item = get_trashed_item(session, item_type, item_id)
        restore_trash_resource(session, item_type, item)
        session.commit()
        return {"restored": True}

    @app.delete("/api/trash/{item_type}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def permanently_delete_trash_item(
        item_type: TrashType,
        item_id: str,
        session: Session = Depends(get_session),
    ) -> None:
        item = get_trashed_item(session, item_type, item_id)
        purge_item(session, runtime_settings, item_type, item)
        session.commit()

    @app.post("/api/trash/restore", response_model=BatchActionResult)
    def batch_restore_trash(
        payload: TrashBatchAction,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        restored = 0
        for reference in payload.items:
            item = get_trashed_item(session, reference.item_type, reference.id)
            restore_trash_resource(session, reference.item_type, item)
            restored += 1
        session.commit()
        return BatchActionResult(updated_count=restored)

    @app.post("/api/trash/purge", response_model=BatchActionResult)
    def batch_purge_trash(
        payload: TrashBatchAction,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        purged = 0
        for reference in payload.items:
            item = get_trashed_item(session, reference.item_type, reference.id)
            purge_item(session, runtime_settings, reference.item_type, item)
            purged += 1
        session.commit()
        return BatchActionResult(updated_count=purged)

    @app.delete("/api/trash", response_model=BatchActionResult)
    def empty_trash(session: Session = Depends(get_session)) -> BatchActionResult:
        purged = 0
        for item_type in ("job", "resume", "run", "evaluation"):
            model = TRASH_MODELS[item_type]
            items = list(session.scalars(select(model).where(model.deleted_at.is_not(None))).all())
            for item in items:
                if session.get(model, item.id) is None:
                    continue
                if not is_top_level_trash_item(item_type, item):
                    continue
                purge_item(session, runtime_settings, item_type, item)
                purged += 1
            session.flush()
        session.commit()
        return BatchActionResult(updated_count=purged)

    frontend_dist = Path.cwd() / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
