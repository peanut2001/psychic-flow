import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import App, { EvaluationDetail, ResultsDesk, ResumeDesk } from "./App";
import type { Evaluation, Job } from "./types";

test("展示岗位案卷并允许创建第一个岗位", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/health")) {
        return new Response(JSON.stringify({ status: "ok", model_configured: true }));
      }
      return new Response(JSON.stringify([]));
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("还没有岗位案卷")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建第一个岗位" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "打开回收站" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "打开简历池" })).toBeInTheDocument();
});

test("首页可打开简历池并看到空状态", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/pool/resumes")) {
        return new Response(JSON.stringify([]));
      }
      return new Response(JSON.stringify([]));
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "打开简历池" }));
  expect(await screen.findByText("还没有新收取的简历")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "已评估 0" })).toBeInTheDocument();
});

test("候选人详情展示每个评估维度的独立分数", () => {
  const evaluation: Evaluation = {
    id: "evaluation-1",
    run_id: "run-1",
    resume_id: "resume-1",
    resume_name: "候选人.pdf",
    status: "completed",
    result: {
      candidate: { name: "候选人", current_role: "后端工程师" },
      overall_score: 86,
      match_level: "high",
      confidence: 0.9,
      summary: "工程经历与岗位高度匹配。",
      criteria: [
        {
          criterion_id: "D01",
          criterion: "Python 工程能力",
          weight: 25,
          score: 92,
          weighted_score: 23,
          status: "met",
          evidence: [{ text: "五年 Python 后端经验", location: "第 1 页" }],
        },
      ],
      strengths: ["经验扎实"],
      gaps: [],
      risk_flags: [],
      interview_questions: ["如何设计任务系统？"],
    },
    error: null,
    attempts: 1,
    review_status: "pending",
    review_note: "",
    screened_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
  };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <EvaluationDetail evaluation={evaluation} onClose={() => undefined} />
    </QueryClientProvider>,
  );

  expect(screen.getByRole("heading", { name: "维度评分" })).toBeInTheDocument();
  expect(screen.getByLabelText("Python 工程能力评分")).toHaveTextContent("92 / 100");
  expect(screen.getByLabelText("Python 工程能力评分")).toHaveTextContent("贡献 23 / 25");
  expect(screen.getByRole("separator", { name: "调整详情面板宽度" })).toBeInTheDocument();
  expect(screen.getByRole("separator", { name: "调整分析与简历宽度" })).toBeInTheDocument();
  expect(screen.getByTitle("简历预览")).toHaveAttribute("src", "/api/resumes/resume-1/preview");
});

test("简历材料可按格式分类并按名称排序", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok", model_configured: true, vision_enabled: false }));
    }
    return new Response(JSON.stringify([
      { id: "resume-b", job_id: "job-1", original_name: "周明.pdf", mime_type: "application/pdf", size_bytes: 2000, page_count: 3, parse_status: "uploaded", parse_error: null, created_at: "2026-08-10T02:00:00Z" },
      { id: "resume-a", job_id: "job-1", original_name: "安然.docx", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size_bytes: 1000, page_count: 1, parse_status: "ready", parse_error: null, created_at: "2026-08-10T01:00:00Z" },
    ]));
  }));
  const job: Job = { id: "job-1", name: "后端工程师", jd_text: "JD", prompt: "标准", resume_count: 2, run_count: 0, created_at: "2026-08-10T00:00:00Z", updated_at: "2026-08-10T00:00:00Z" };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResumeDesk job={job} onRunCreated={() => undefined} /></QueryClientProvider>);
  expect(await screen.findByText("周明.pdf")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("简历格式"), { target: { value: "docx" } });
  expect(screen.queryByText("周明.pdf")).not.toBeInTheDocument();
  expect(screen.getByText("安然.docx")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看安然.docx文本" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "安然.docx更多操作" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("简历格式"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("简历排序"), { target: { value: "name-asc" } });
  expect(screen.getAllByTestId("resume-name").map((item) => item.textContent)).toEqual(["安然.docx", "周明.pdf"]);
});

test("筛选结果可按人工状态分类并按姓名排序", async () => {
  const result = (name: string, score: number, withBackground = false): Evaluation["result"] => ({
    candidate: { name, current_role: "工程师" }, overall_score: score, match_level: score >= 80 ? "high" : "medium", confidence: 0.9,
    summary: "候选人摘要", criteria: [], strengths: [], gaps: [], risk_flags: [], interview_questions: [],
    background_highlights: withBackground ? [
      { kind: "company", name: "字节跳动", bonus_points: 3, evidence: [{ text: "任职字节跳动", location: "第 1 页" }] },
      { kind: "school", name: "浙江大学", bonus_points: 2, evidence: [{ text: "毕业于浙江大学", location: "第 1 页" }] },
    ] : [],
  });
  const evaluations: Evaluation[] = [
    { id: "evaluation-b", run_id: "run-2", resume_id: "resume-b", resume_name: "周明.pdf", status: "completed", result: result("周明", 90, true), error: null, attempts: 1, review_status: "advance", review_note: "", screened_at: "2026-08-10T02:00:00Z", updated_at: "2026-08-10T02:00:00Z" },
    { id: "evaluation-a", run_id: "run-1", resume_id: "resume-a", resume_name: "安然.pdf", status: "completed", result: result("安然", 70), error: null, attempts: 1, review_status: "pending", review_note: "", screened_at: "2026-08-09T01:00:00Z", updated_at: "2026-08-10T01:00:00Z" },
  ];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/jobs/job-1/evaluations")) {
      const params = new URL(url, "http://localhost").searchParams;
      let items = evaluations.filter((item) => params.get("review_status") === "all" || item.review_status === params.get("review_status"));
      if (params.get("sort") === "name-asc") items = [...items].sort((a, b) => (a.result?.candidate.name || "").localeCompare(b.result?.candidate.name || "", "zh-CN"));
      return new Response(JSON.stringify({ items, page: 1, page_size: 10, total: params.get("review_status") === "all" ? 12 : items.length, total_pages: params.get("review_status") === "all" ? 2 : 1 }));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResultsDesk jobId="job-1" initialRunId="run-1" /></QueryClientProvider>);
  expect(await screen.findByText("周明")).toBeInTheDocument();
  expect(screen.getByText("名企 · 字节跳动")).toBeInTheDocument();
  expect(screen.getByText("名校 · 浙江大学")).toBeInTheDocument();
  expect(screen.queryByLabelText("筛选批次")).not.toBeInTheDocument();
  expect(screen.getByText("2026年8月10日")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "导出 Excel" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=xlsx");
  expect(screen.getByRole("link", { name: "导出 CSV" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=csv");
  expect(screen.getByRole("link", { name: "导出 JSON" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=json");
  expect(screen.getByRole("link", { name: "导出 Markdown" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=md");
  expect(screen.getByRole("link", { name: "导出 Word" })).toHaveAttribute("href", "/api/jobs/job-1/export?format=docx");
  expect(screen.getByText("第 1 / 2 页")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("checkbox", { name: "选择周明" }));
  expect(screen.getByText("已选 1 人")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批量推进" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批量重新评分" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批量移入回收站" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "导出 Excel" })).toHaveAttribute(
    "href",
    "/api/jobs/job-1/export?format=xlsx&evaluation_id=evaluation-b",
  );
  expect(screen.getByRole("link", { name: "导出 Word" })).toHaveAttribute(
    "href",
    "/api/jobs/job-1/export?format=docx&evaluation_id=evaluation-b",
  );

  fireEvent.change(screen.getByLabelText("人工状态"), { target: { value: "advance" } });
  await waitFor(() => expect(screen.queryByText("安然")).not.toBeInTheDocument());
  expect(await screen.findByText("周明")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("人工状态"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("结果排序"), { target: { value: "name-asc" } });
  await waitFor(() => expect(screen.getAllByTestId("candidate-name").map((item) => item.textContent)).toEqual(["安然", "周明"]));
});

test("未识别姓名回退到简历文件名，评估失败不显示为解析失败", async () => {
  const evaluations: Evaluation[] = [
    {
      id: "evaluation-unknown",
      run_id: "run-1",
      resume_id: "resume-1",
      resume_name: "江贺琨_15年.pdf",
      status: "completed",
      result: {
        candidate: { name: "未识别", current_role: "测试工程师" },
        overall_score: 60,
        match_level: "low",
        confidence: 0.4,
        summary: "证据不足",
        criteria: [],
        strengths: [],
        gaps: [],
        risk_flags: [],
        interview_questions: [],
      },
      error: null,
      attempts: 1,
      review_status: "pending",
      review_note: "",
      screened_at: "2026-08-28T05:11:08Z",
      updated_at: "2026-08-28T05:11:08Z",
    },
    {
      id: "evaluation-failed",
      run_id: "run-2",
      resume_id: "resume-2",
      resume_name: "侯东鹏_11年.pdf",
      status: "failed",
      result: null,
      error: "评估模型未返回结果，不是简历文件解析失败，请重试",
      attempts: 2,
      review_status: "pending",
      review_note: "",
      screened_at: "2026-08-28T06:30:42Z",
      updated_at: "2026-08-28T06:30:42Z",
    },
  ];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/jobs/job-1/evaluations")) {
      return new Response(JSON.stringify({ items: evaluations, page: 1, page_size: 10, total: 2, total_pages: 1 }));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResultsDesk jobId="job-1" /></QueryClientProvider>);
  expect(await screen.findByText("江贺琨_15年.pdf")).toBeInTheDocument();
  expect(screen.getByText("侯东鹏_11年.pdf")).toBeInTheDocument();
  expect(screen.getByText("评估模型未返回结果，不是简历文件解析失败，请重试")).toBeInTheDocument();
  expect(screen.queryByText("解析失败")).not.toBeInTheDocument();
});

test("筛选结果页展示进行中的筛选进度，并标出正在重评的旧结果", async () => {
  const evaluation = (id: string, runId: string, resumeId: string, name: string, status: string, score?: number): Evaluation => ({
    id, run_id: runId, resume_id: resumeId, resume_name: `${name}.pdf`, status,
    result: score == null ? null : { candidate: { name, current_role: "工程师" }, overall_score: score, match_level: "medium", confidence: 0.9, summary: "上一次的评分摘要", criteria: [], strengths: [], gaps: [], risk_flags: [], interview_questions: [] },
    error: null, attempts: 1, review_status: "pending", review_note: "", screened_at: "2026-11-02T09:47:00Z", updated_at: "2026-11-02T09:47:00Z",
  });
  const run = (id: string, status: string, completed: number) => ({ id, job_id: "job-1", status, total_count: 2, completed_count: completed, failed_count: 0, text_model: "gpt-6.1-sol", vision_model: "gpt-6.1-sol", created_at: "2026-11-02T09:50:56Z", started_at: "2026-11-02T09:50:56Z", completed_at: null });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/jobs/job-1/evaluations")) {
      return new Response(JSON.stringify({ items: [evaluation("evaluation-old", "run-old", "resume-a", "周明", "completed", 70)], page: 1, page_size: 10, total: 1, total_pages: 1 }));
    }
    if (url.includes("/api/jobs/job-1/runs")) {
      return new Response(JSON.stringify({ items: [run("run-new", "running", 1), run("run-old", "completed", 2)], page: 1, page_size: 20, total: 2, total_pages: 1 }));
    }
    if (url === "/api/runs/run-new/evaluations") {
      return new Response(JSON.stringify([evaluation("evaluation-new-a", "run-new", "resume-a", "周明", "evaluating"), evaluation("evaluation-new-b", "run-new", "resume-b", "安然", "completed", 88)]));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}><ResultsDesk jobId="job-1" /></QueryClientProvider>);
  const panel = await screen.findByRole("region", { name: "进行中的筛选任务" });
  expect(within(panel).getByText("1 / 2")).toBeInTheDocument();
  expect(within(panel).getByRole("progressbar", { name: "筛选进度" })).toHaveAttribute("aria-valuenow", "50");
  expect(await within(panel).findByText("周明.pdf")).toBeInTheDocument();
  expect(within(panel).getByText("评分中")).toBeInTheDocument();
  expect(within(panel).getByText("安然.pdf")).toBeInTheDocument();
  expect(within(panel).getByText("已完成")).toBeInTheDocument();
  expect(screen.getByTestId("candidate-name")).toHaveTextContent("周明");
  expect(screen.getByText("重评中")).toBeInTheDocument();
});

test("创建岗位时展示编译后的计分维度", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(JSON.stringify({ status: "ok", model_configured: true }));
      }
      if (url.endsWith("/api/scoring-preview")) {
        expect(init?.method).toBe("POST");
        return new Response(
          JSON.stringify({
            source: "prompt",
            dimensions: [
              { criterion_id: "D01", name: "CDC 同步引擎能力", weight: 30, kind: "knockout", knockout: true },
              { criterion_id: "D02", name: "工程落地", weight: 70, kind: "core", knockout: false },
            ],
          }),
        );
      }
      if (url.endsWith("/api/jobs") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            id: "job-new",
            name: "资深数据架构师",
            jd_text: "实时数据引擎",
            prompt: "标准",
            resume_count: 0,
            run_count: 0,
            created_at: "2026-08-29T00:00:00Z",
            updated_at: "2026-08-29T00:00:00Z",
          }),
          { status: 201 },
        );
      }
      return new Response(JSON.stringify([]));
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "创建第一个岗位" }));
  fireEvent.change(screen.getByPlaceholderText(/硬性/), {
    target: { value: "### 1. CDC 同步引擎能力（硬性）：30分\n### 2. 工程落地：70分" },
  });

  expect(await screen.findByText("实际计分维度")).toBeInTheDocument();
  expect(screen.getByText("CDC 同步引擎能力")).toBeInTheDocument();
  expect(screen.getByLabelText("CDC 同步引擎能力的计分性质")).toHaveValue("knockout");
  expect(screen.getByText("30 分")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("CDC 同步引擎能力的计分性质"), { target: { value: "bonus" } });
  fireEvent.change(screen.getByLabelText("工程落地的计分性质"), { target: { value: "knockout" } });
  fireEvent.change(screen.getByPlaceholderText("例如：高级后端工程师"), { target: { value: "资深数据架构师" } });
  fireEvent.change(screen.getByPlaceholderText("粘贴完整岗位描述、职责与基本要求"), { target: { value: "实时数据引擎" } });
  fireEvent.click(screen.getByRole("button", { name: "建立岗位案卷" }));

  await waitFor(() => {
    const createCall = vi.mocked(fetch).mock.calls.find(
      (entry) => String(entry[0]).endsWith("/api/jobs") && entry[1]?.method === "POST",
    );
    expect(createCall).toBeTruthy();
    const body = JSON.parse(String(createCall?.[1]?.body));
    expect(body.scoring_profile).toEqual([
      { name: "CDC 同步引擎能力", weight: 30, kind: "bonus" },
      { name: "工程落地", weight: 70, kind: "knockout" },
    ]);
  });
});

test("候选人详情标注未满足的硬性维度", () => {
  const evaluation: Evaluation = {
    id: "evaluation-1",
    run_id: "run-1",
    resume_id: "resume-1",
    resume_name: "候选人.pdf",
    status: "completed",
    result: {
      candidate: { name: "候选人", current_role: "数据工程师" },
      overall_score: 82,
      match_level: "low",
      confidence: 0.8,
      summary: "硬性项不满足。",
      criteria: [
        {
          criterion_id: "D01",
          criterion: "CDC 引擎",
          weight: 20,
          score: 10,
          weighted_score: 2,
          status: "not_met",
          knockout: true,
          evidence: [{ text: "仅使用过同步产品", location: "第 1 页" }],
        },
      ],
      strengths: [],
      gaps: ["缺少引擎开发"],
      risk_flags: [],
      interview_questions: [],
    },
    error: null,
    attempts: 1,
    review_status: "pending",
    review_note: "",
    screened_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
  };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <EvaluationDetail evaluation={evaluation} onClose={() => undefined} />
    </QueryClientProvider>,
  );

  expect(screen.getAllByText("硬性").length).toBeGreaterThan(0);
  expect(screen.getByText("硬性项未满足，匹配等级已限制为低")).toBeInTheDocument();
});

test("简历池「所有简历」汇总全库并可按条件筛选", async () => {
  const job = { id: "job-1", name: "资深数据架构师", jd_text: "JD", prompt: "标准", resume_count: 0, run_count: 0, created_at: "2026-08-10T00:00:00Z", updated_at: "2026-08-10T00:00:00Z" };
  const emailItem = {
    id: "pool-email", job_id: "job-1", job_name: "资深数据架构师", original_name: "邮箱女.docx", display_name: "邮箱女.docx",
    source_title: "资深JAVA工程师-实时数据引擎", mime_type: "docx", size_bytes: 1000, page_count: 1, parse_status: "ready", parse_error: null,
    source: "email", created_at: "2026-08-10T01:00:00Z",
    evaluation: { overall_score: 86, match_level: "high", review_status: "pending", gender: "female", age: 28 },
    evaluated_jobs: [{ job_id: "job-1", job_name: "资深数据架构师" }],
  };
  const uploadItem = {
    id: "pool-upload", job_id: null, job_name: null, original_name: "上传男.pdf", display_name: "上传男.pdf",
    source_title: null, mime_type: "pdf", size_bytes: 2000, page_count: 2, parse_status: "ready", parse_error: null,
    source: "upload", created_at: "2026-08-10T02:00:00Z",
    evaluation: null,
    evaluated_jobs: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok", model_configured: true }));
    }
    if (url.includes("/api/pool/resumes")) {
      return new Response(JSON.stringify([emailItem, uploadItem]));
    }
    if (url.endsWith("/api/jobs")) {
      return new Response(JSON.stringify([job]));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "打开简历池" }));
  await screen.findByRole("button", { name: "所有简历 2" });
  fireEvent.click(screen.getByRole("button", { name: "所有简历 2" }));
  expect(await screen.findByText("女 · 28 岁")).toBeInTheDocument();
  expect(screen.getByText("上传男.pdf")).toBeInTheDocument();
  expect(screen.getByText("所属岗位（可多选）")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("性别"), { target: { value: "female" } });
  expect(screen.queryByText("上传男.pdf")).not.toBeInTheDocument();
  expect(screen.getByText("女 · 28 岁")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("性别"), { target: { value: "any" } });
  fireEvent.change(screen.getByLabelText("是否评估"), { target: { value: "no" } });
  expect(screen.queryByText("邮箱女.docx")).not.toBeInTheDocument();
  expect(screen.getByText("上传男.pdf")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("是否评估"), { target: { value: "yes" } });
  fireEvent.change(screen.getByLabelText("最低分"), { target: { value: "90" } });
  expect(await screen.findByText("没有符合筛选条件的简历")).toBeInTheDocument();
});

test("简历池区分失败与完成评估，并在文本就绪后继续刷新评分", async () => {
  let complete = false;
  let pending = false;
  const row = {
    id: "synthetic-pool", job_id: "job-test", job_name: "测试岗位", original_name: "synthetic.docx", display_name: "合成材料",
    mime_type: "docx", size_bytes: 100, page_count: 1, parse_status: "ready", source: "email", created_at: "2026-09-08T00:00:00Z",
    evaluation: null, evaluation_status: "failed", evaluated_jobs: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/pool/resumes")) return new Response(JSON.stringify([{
      ...row,
      evaluation_status: complete ? "completed" : pending ? "evaluating" : "failed",
      evaluation: complete ? { overall_score: 86, match_level: "high", review_status: "pending" } : null,
    }]));
    if (url.endsWith("/api/health")) return new Response(JSON.stringify({ model_configured: true }));
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><App /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "打开简历池" }));
  expect(await screen.findByRole("button", { name: "新收取 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "已评估 0" })).toBeInTheDocument();
  pending = true;
  await queryClient.invalidateQueries({ queryKey: ["pool"] });
  complete = true;
  await waitFor(() => expect(screen.getByRole("button", { name: "已评估 1" })).toBeInTheDocument(), { timeout: 4000 });
  fireEvent.click(screen.getByRole("button", { name: "已评估 1" }));
  expect(screen.getByText("86 分")).toBeInTheDocument();
  queryClient.clear();
});

test("评分结果支持跨页选中后更换职位重评", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/jobs") return new Response(JSON.stringify([{ id: "job-1", name: "原岗位" }, { id: "job-2", name: "质量架构师" }]));
    if (url === "/api/evaluations/rescore") return new Response(JSON.stringify({ id: "new-run", job_id: "job-2" }));
    const page = new URL(url, "http://localhost").searchParams.get("page") || "1";
    return new Response(JSON.stringify({ items: [{ id: `evaluation-${page}`, resume_id: `resume-${page}`, resume_name: `测试候选人${page}`, status: "completed", review_status: "pending", screened_at: "2026-09-10T00:00:00Z" }], page: Number(page), total: 2, total_pages: 2 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ResultsDesk jobId="job-1" /></QueryClientProvider>);
  fireEvent.click(await screen.findByLabelText("选择测试候选人1"));
  expect(screen.getByRole("button", { name: "更换职位重新评分" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByLabelText("选择测试候选人2"));
  fireEvent.change(screen.getByLabelText("重评目标职位"), { target: { value: "job-2" } });
  fireEvent.click(screen.getByRole("button", { name: "更换职位重新评分" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/evaluations/rescore", expect.objectContaining({ body: JSON.stringify({ evaluation_ids: ["evaluation-1", "evaluation-2"], job_id: "job-2" }) })));
  await waitFor(() => expect(screen.queryByText("已选 2 人")).not.toBeInTheDocument());
});

test("简历池粘贴文本录入，疑似重复时确认后才创建", async () => {
  let confirmSeen = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/pool/resumes/paste")) {
      const payload = JSON.parse(String(init?.body || "{}"));
      if (!payload.confirm) {
        return new Response(JSON.stringify({
          created: false,
          resume: null,
          duplicates: [{ id: "r-dup", name: "张三.txt", source: "paste", created_at: "2026-09-15T01:00:00Z", matched_fields: ["邮箱"] }],
        }));
      }
      confirmSeen = true;
      return new Response(JSON.stringify({
        created: true,
        resume: {
          id: "r-new", job_id: null, job_name: null, original_name: "张三.txt", display_name: "张三",
          mime_type: "text/plain", size_bytes: 100, page_count: null, parse_status: "ready", parse_error: null,
          source: "paste", created_at: "2026-09-15T02:00:00Z", evaluation: null, evaluated_jobs: [],
        },
        duplicates: [],
      }));
    }
    return new Response(JSON.stringify([]));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "打开简历池" }));
  fireEvent.click(await screen.findByRole("button", { name: "粘贴文本" }));
  fireEvent.change(screen.getByPlaceholderText(/粘贴简历全文/), { target: { value: "邮箱 zs@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "确认录入" }));
  expect(await screen.findByText(/疑似重复/)).toBeInTheDocument();
  expect(screen.getByText("张三.txt")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "仍然录入" }));
  await waitFor(() => expect(confirmSeen).toBe(true));
});

test("职位详情页粘贴文本录入后立即启动评分", async () => {
  let pasteSeen = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ status: "ok", model_configured: true, vision_enabled: false }));
    }
    if (url.endsWith("/api/jobs/job-1/resumes/paste")) {
      pasteSeen = true;
      const payload = JSON.parse(String(init?.body || "{}"));
      expect(payload.text).toContain("五年后端经验");
      return new Response(JSON.stringify({
        created: true,
        resume: { id: "r-pasted", job_id: "job-1", original_name: "粘贴简历.txt", display_name: "粘贴简历.txt", mime_type: "text/plain", size_bytes: 50, page_count: null, parse_status: "ready", parse_error: null, source: "paste", parsed_text_source: "extracted", parsed_text_updated_at: null, created_at: "2026-09-16T01:00:00Z" },
        duplicates: [],
        run: { id: "run-1", job_id: "job-1", status: "queued", total_count: 1, completed_count: 0, failed_count: 0, text_model: "m", vision_model: "v", created_at: "2026-09-16T01:00:00Z", started_at: null, completed_at: null },
      }));
    }
    return new Response(JSON.stringify([]));
  }));
  const job: Job = { id: "job-1", name: "后端工程师", jd_text: "JD", prompt: "标准", resume_count: 0, run_count: 0, created_at: "2026-09-16T00:00:00Z", updated_at: "2026-09-16T00:00:00Z" };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <ResumeDesk job={job} onRunCreated={() => undefined} />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "粘贴文本" }));
  fireEvent.change(screen.getByPlaceholderText(/粘贴简历全文/), { target: { value: "五年后端经验" } });
  fireEvent.click(screen.getByRole("button", { name: "确认录入" }));
  await waitFor(() => expect(pasteSeen).toBe(true));
  // 录入成功后弹窗关闭（created 分支），列表随之刷新
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
