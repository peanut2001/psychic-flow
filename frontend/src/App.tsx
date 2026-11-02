import {
  createContext,
  useContext,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArchiveRestore,
  ArrowLeft,
  Ban,
  BriefcaseBusiness,
  Building2,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  ClipboardPaste,
  Download,
  Ellipsis,
  FileText,
  FolderOpen,
  GraduationCap,
  Inbox,
  ListFilter,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import { api, clearModelConfig, loadModelConfig, saveModelConfig, type ModelConfig } from "./api";
import type { Evaluation, Job, PoolPasteDuplicate, PoolResume, Resume, Run, ScoringDimension, ScoringKind, ScoringPreview, TrashItem, TrashType } from "./types";

const reviewLabels: Record<Evaluation["review_status"], string> = {
  pending: "待复核",
  advance: "推进",
  reject: "淘汰",
  hold: "保留",
};

const runLabels: Record<string, string> = {
  queued: "排队中",
  running: "筛选中",
  completed: "已完成",
  partial: "部分失败",
  failed: "失败",
  canceled: "已取消",
};

const evaluationStatusLabels: Record<string, string> = {
  queued: "排队中",
  parsing: "解析中",
  evaluating: "评分中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

const activeRunStatuses = ["queued", "running"];
const activeEvaluationStatuses = ["queued", "parsing", "evaluating"];

const trashLabels: Record<TrashType, string> = {
  job: "岗位",
  resume: "简历档案",
  run: "筛选任务",
  evaluation: "筛选结果",
};

type NoticeKind = "success" | "error" | "info";
type Notify = (message: string, kind?: NoticeKind) => void;
const ToastContext = createContext<Notify>(() => undefined);
const useNotify = () => useContext(ToastContext);

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function screeningDateLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" }).format(new Date(value));
}

function screeningTimeLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function sizeLabel(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function resumeName(resume: Resume) {
  return resume.display_name || resume.original_name;
}

function isUnrecognizedName(name?: string | null) {
  return !name || name === "未识别";
}

function candidateDisplayName(evaluation: Evaluation) {
  const name = evaluation.result?.candidate.name;
  if (!isUnrecognizedName(name)) return name as string;
  return evaluation.resume_name;
}

function BackgroundBadges({ items = [] }: { items?: NonNullable<Evaluation["result"]>["background_highlights"] }) {
  if (!items?.length) return null;
  const visibleItems = items.filter(
    (item, index) => items.findIndex((candidate) => candidate.kind === item.kind) === index,
  );
  return <div className="background-badges">{visibleItems.map((item) => {
    const label = item.kind === "company" ? "名企" : "名校";
    const Icon = item.kind === "company" ? Building2 : GraduationCap;
    const evidence = item.evidence.map((entry) => `${entry.text}（${entry.location}）`).join("；");
    return <span className={`background-badge ${item.kind}`} key={`${item.kind}-${item.name}`} title={`${label}背景${item.bonus_points ? ` +${item.bonus_points}` : ""}${evidence ? `：${evidence}` : ""}`}><Icon />{label} · {item.name}</span>;
  })}{items.length > visibleItems.length && <span className="background-badge more">+{items.length - visibleItems.length}</span>}</div>;
}

function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error-notice" role="alert"><CircleAlert size={17} />{error instanceof Error ? error.message : "操作失败"}</div>;
}

function ConfirmDialog({ title, description, confirmLabel, danger = false, busy = false, onCancel, onConfirm }: {
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return <div className="modal-backdrop" role="presentation"><section className="modal confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
    <div className="confirm-symbol"><Trash2 /></div><h2 id="confirm-title">{title}</h2><p>{description}</p>
    <div className="modal-actions"><button autoFocus className="button quiet" type="button" onClick={onCancel}>取消</button><button className={`button ${danger ? "danger" : "primary"}`} type="button" disabled={busy} onClick={onConfirm}>{busy ? "处理中…" : confirmLabel}</button></div>
  </section></div>;
}

function RowMenu({ label, children }: { label: string; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return <div className="row-menu"><button type="button" className="icon-button compact" aria-label={label} aria-expanded={open} onClick={() => setOpen((value) => !value)}><Ellipsis /></button>{open && <><button className="menu-scrim" aria-label="关闭操作菜单" onClick={() => setOpen(false)} /><div className="action-popover" role="menu">{children(() => setOpen(false))}</div></>}</div>;
}

const scoringKindOptions: { value: ScoringKind; label: string }[] = [
  { value: "core", label: "普通" },
  { value: "knockout", label: "硬性 Must-have" },
  { value: "bonus", label: "加分项" },
];

function scoringKindOf(item: ScoringDimension): ScoringKind {
  return item.kind || (item.knockout ? "knockout" : "core");
}

function toScoringProfile(dimensions: ScoringDimension[]) {
  return dimensions.map((item) => ({ name: item.name, weight: item.weight, kind: scoringKindOf(item) }));
}

function ScoringRubricEditor({
  prompt,
  initialDimensions = [],
  onChange,
}: {
  prompt: string;
  initialDimensions?: ScoringDimension[];
  onChange?: (dimensions: ScoringDimension[]) => void;
}) {
  const [preview, setPreview] = useState<ScoringPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState<ScoringDimension[]>(initialDimensions);
  const kindsRef = useRef<Record<string, ScoringKind>>(
    Object.fromEntries(initialDimensions.map((item) => [item.name, scoringKindOf(item)])),
  );
  useEffect(() => {
    if (!prompt.trim()) {
      setPreview(null);
      setError(null);
      setDimensions([]);
      onChange?.([]);
      return;
    }
    let cancelled = false;
    api.previewScoring(prompt).then((data) => {
      if (cancelled) return;
      const merged = data.dimensions.map((item) => {
        const kind = kindsRef.current[item.name] || scoringKindOf(item);
        kindsRef.current[item.name] = kind;
        return { ...item, kind, knockout: kind === "knockout" };
      });
      setPreview(data);
      setDimensions(merged);
      setError(null);
      onChange?.(merged);
    }).catch((err: Error) => {
      if (cancelled) return;
      setPreview(null);
      setDimensions([]);
      setError(err.message);
      onChange?.([]);
    });
    return () => { cancelled = true; };
  }, [prompt]);
  const setKind = (criterionId: string, kind: ScoringKind) => {
    const next = dimensions.map((item) => {
      if (item.criterion_id !== criterionId) return item;
      kindsRef.current[item.name] = kind;
      return { ...item, kind, knockout: kind === "knockout" };
    });
    setDimensions(next);
    onChange?.(next);
  };
  if (!prompt.trim()) return null;
  return (
    <div className="scoring-preview">
      <div className="scoring-preview-heading">
        <strong>实际计分维度</strong>
        {preview?.source === "default" && <small>未识别到评分标题或权重表，将使用默认 6 维，可在下面改硬性/加分</small>}
        {preview?.source === "prompt" && <small>已从提示词编译。硬性与加分项可直接改，保存后生效</small>}
      </div>
      {error && <p className="scoring-preview-error">{error}</p>}
      {dimensions.length > 0 && (
        <ol>
          {dimensions.map((item) => (
            <li key={item.criterion_id}>
              <span>{item.name}</span>
              <select
                aria-label={`${item.name}的计分性质`}
                value={scoringKindOf(item)}
                onChange={(event) => setKind(item.criterion_id, event.target.value as ScoringKind)}
              >
                {scoringKindOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
              <b>{item.weight} 分</b>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function CreateJobDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (job: Job) => void }) {
  const [name, setName] = useState("");
  const [jd, setJd] = useState("");
  const [prompt, setPrompt] = useState("");
  const [profile, setProfile] = useState<ScoringDimension[]>([]);
  const mutation = useMutation({
    mutationFn: () => api.createJob({ name, jd_text: jd, prompt, scoring_profile: toScoringProfile(profile) }),
    onSuccess: onCreated,
  });
  return <div className="modal-backdrop" role="presentation"><form className="modal dossier-form" role="dialog" aria-modal="true" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
    <div className="modal-heading"><div><span className="eyebrow">NEW DOSSIER / 新案卷</span><h2>建立岗位筛选标准</h2></div><button className="icon-button" type="button" onClick={onClose} aria-label="关闭"><X /></button></div>
    <label>岗位名称<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：高级后端工程师" required /></label>
    <label>岗位 JD<textarea value={jd} onChange={(event) => setJd(event.target.value)} placeholder="粘贴完整岗位描述、职责与基本要求" rows={7} required /></label>
    <label>筛选提示词<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="使用「## 1. 名称：XX分」或带权重列的表格。硬性 Must-have 与加分项请在下方量尺中修改。" rows={6} required /></label>
    <ScoringRubricEditor prompt={prompt} onChange={setProfile} />
    <p className="privacy-note">系统会固定保存本次设置的版本。模型只提供证据与评分，最终决定始终由你完成。</p><ErrorNotice error={mutation.error} />
    <div className="modal-actions"><button type="button" className="button quiet" onClick={onClose}>取消</button><button className="button primary" disabled={mutation.isPending}>{mutation.isPending ? "创建中…" : "建立岗位案卷"}</button></div>
  </form></div>;
}

function RenameResumeDialog({ resume, onClose, onSaved }: { resume: Resume; onClose: () => void; onSaved: () => void }) {
  const notify = useNotify();
  const [name, setName] = useState(resumeName(resume));
  const mutation = useMutation({
    mutationFn: () => api.updateResume(resume.id, { display_name: name }),
    onSuccess: () => { notify("简历显示名称已更新"); onSaved(); onClose(); },
  });
  return <div className="modal-backdrop"><form className="modal compact-modal" role="dialog" aria-modal="true" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><div className="modal-heading"><div><span className="eyebrow">FILE LABEL / 档案名称</span><h2>修改显示名称</h2></div><button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><X /></button></div><label>显示名称<input autoFocus value={name} onChange={(event) => setName(event.target.value)} required /></label><p className="privacy-note">只修改工作台中的名称，不改动原始文件。</p><ErrorNotice error={mutation.error} /><div className="modal-actions"><button type="button" className="button quiet" onClick={onClose}>取消</button><button className="button primary" disabled={mutation.isPending}>保存名称</button></div></form></div>;
}

function ResumeTextDialog({ resume, onClose, onSaved }: { resume: Resume; onClose: () => void; onSaved?: () => void }) {
  const notify = useNotify();
  const textQuery = useQuery({ queryKey: ["resume-text", resume.id], queryFn: () => api.resumeText(resume.id) });
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  useEffect(() => { if (textQuery.data != null) setText(textQuery.data); }, [textQuery.data]);
  const mutation = useMutation({
    mutationFn: () => api.updateResume(resume.id, { parsed_text: text }),
    onSuccess: () => { notify("解析文本已保存，后续评分将使用修正版"); setEditing(false); onSaved?.(); },
  });
  return <div className="modal-backdrop"><section className="modal text-preview-modal" role="dialog" aria-modal="true" aria-labelledby="resume-text-title"><div className="modal-heading"><div><span className="eyebrow">PARSED TEXT / 入库文本</span><h2 id="resume-text-title">{resumeName(resume)}</h2></div><div className="text-preview-actions"><a className="button quiet" href={`/api/resumes/${resume.id}/text?download=true`}><Download size={15} />下载 TXT</a><button className="icon-button" type="button" onClick={onClose} aria-label="关闭文本预览"><X /></button></div></div>
    <div className="text-source-line"><span className={resume.parsed_text_source === "manual" ? "manual" : ""}>{resume.parsed_text_source === "manual" ? "人工修正版" : "系统解析文本"}</span><p>筛选模型会直接使用当前入库版本。</p><button type="button" className="text-action" onClick={() => setEditing((value) => !value)}><Pencil size={14} />{editing ? "取消编辑" : "修正文本"}</button></div>
    {textQuery.isLoading ? <div className="loading-block"><LoaderCircle className="spin" />正在读取文本</div> : textQuery.error ? <ErrorNotice error={textQuery.error} /> : editing ? <textarea className="parsed-text-editor" value={text} onChange={(event) => setText(event.target.value)} aria-label="简历解析文本" /> : <pre className="parsed-text">{text}</pre>}
    {editing && <div className="modal-actions"><button className="button primary" type="button" disabled={!text.trim() || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "保存中…" : "保存修正版"}</button></div>}<ErrorNotice error={mutation.error} />
  </section></div>;
}

type PasteSubmitPayload = { text: string; name?: string; confirm?: boolean };
type PasteOutcome = { created: boolean; duplicates: PoolPasteDuplicate[] };

function PasteResumeDialog({ submit, createdMessage, onClose, onCreated }: {
  submit: (payload: PasteSubmitPayload) => Promise<PasteOutcome>;
  createdMessage: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const notify = useNotify();
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [duplicates, setDuplicates] = useState<PoolPasteDuplicate[] | null>(null);
  const mutation = useMutation({
    mutationFn: (confirm: boolean) => submit({ text, name: name.trim() || undefined, confirm }),
    onSuccess: (result) => {
      if (!result.created) { setDuplicates(result.duplicates); return; }
      notify(createdMessage);
      onCreated();
      onClose();
    },
  });
  return <div className="modal-backdrop" role="presentation"><form className="modal dossier-form" role="dialog" aria-modal="true" aria-labelledby="paste-title" onSubmit={(event) => { event.preventDefault(); mutation.mutate(false); }}>
    <div className="modal-heading"><div><span className="eyebrow">PASTE INTAKE / 粘贴录入</span><h2 id="paste-title">粘贴简历文本</h2></div><button className="icon-button" type="button" onClick={onClose} aria-label="关闭"><X /></button></div>
    <label>简历文本<textarea autoFocus value={text} onChange={(event) => setText(event.target.value)} placeholder="粘贴简历全文；提交前会按姓名、邮箱与手机号在简历池中查重" rows={10} required /></label>
    <label>姓名（选填）<input value={name} onChange={(event) => setName(event.target.value)} placeholder="留空则由系统自动提取" /></label>
    {duplicates && <div className="paste-duplicates" role="alert">
      <div className="cost-strip"><CircleAlert size={16} /><span>简历池中已有 {duplicates.length} 份疑似重复的简历，请确认是否为同一人。</span></div>
      <ul>{duplicates.map((item) => <li key={item.id}><strong>{item.name}</strong><small>{item.source === "email" ? "邮箱收取" : item.source === "paste" ? "粘贴文本" : "手动上传"} · {dateLabel(item.created_at)} · 匹配：{item.matched_fields.join("、")}</small></li>)}</ul>
    </div>}
    <ErrorNotice error={mutation.error} />
    {duplicates ? <div className="modal-actions"><button type="button" className="button quiet" onClick={() => setDuplicates(null)}>取消</button><button type="button" className="button primary" disabled={mutation.isPending} onClick={() => mutation.mutate(true)}>{mutation.isPending ? "录入中…" : "仍然录入"}</button></div>
      : <div className="modal-actions"><button type="button" className="button quiet" onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={!text.trim() || mutation.isPending}>{mutation.isPending ? "检查中…" : "确认录入"}</button></div>}
  </form></div>;
}

// 请求头只能携带 ASCII，且 key、地址、模型名都不应含空格。
const asciiToken = /^[\x21-\x7e]*$/;

function ModelSettingsDialog({ defaults, onClose, onChanged }: {
  defaults: { textModel: string; visionModel: string };
  onClose: () => void;
  onChanged: () => void;
}) {
  const notify = useNotify();
  const saved = loadModelConfig();
  const [apiKey, setApiKey] = useState(saved?.apiKey || "");
  const [baseUrl, setBaseUrl] = useState(saved?.baseUrl || "");
  const [textModel, setTextModel] = useState(saved?.textModel || defaults.textModel);
  const [visionModel, setVisionModel] = useState(saved?.visionModel || defaults.visionModel);
  const [showKey, setShowKey] = useState(false);
  // 模型只能从接口返回的列表中选择；key 或地址变化后列表作废，需要重新获取。
  const [models, setModels] = useState<string[] | null>(null);
  const config: ModelConfig = { apiKey: apiKey.trim(), baseUrl: baseUrl.trim(), textModel, visionModel: visionModel || textModel };
  const invalid = [config.apiKey, config.baseUrl].some((value) => !asciiToken.test(value));
  const ready = Boolean(config.apiKey && models?.includes(textModel) && (!visionModel || models.includes(visionModel))) && !invalid;
  const testMutation = useMutation({ mutationFn: () => api.testModel(config) });
  const modelsMutation = useMutation({
    mutationFn: () => api.listModels(config),
    onSuccess: ({ models: list }) => {
      setModels(list);
      setTextModel((current) => (list.includes(current) ? current : ""));
      setVisionModel((current) => (list.includes(current) && current !== textModel ? current : ""));
    },
  });
  useEffect(() => { if (saved) modelsMutation.mutate(); }, []);
  const editConnection = (setter: (value: string) => void) => (event: { target: { value: string } }) => { setter(event.target.value); setModels(null); modelsMutation.reset(); testMutation.reset(); };
  const pickModel = (setter: (value: string) => void) => (event: { target: { value: string } }) => { setter(event.target.value); testMutation.reset(); };
  const save = () => { saveModelConfig(config); notify("模型配置已保存在本浏览器"); onChanged(); onClose(); };
  const clear = () => { clearModelConfig(); notify("已清除本浏览器的模型配置", "info"); onChanged(); onClose(); };
  return <div className="modal-backdrop" role="presentation"><form className="modal dossier-form" role="dialog" aria-modal="true" aria-labelledby="model-settings-title" onSubmit={(event) => { event.preventDefault(); if (ready) save(); }}>
    <div className="modal-heading"><div><span className="eyebrow">MODEL / 模型设置</span><h2 id="model-settings-title">模型连接</h2></div><button className="icon-button" type="button" onClick={onClose} aria-label="关闭"><X /></button></div>
    <label>API Key<div className="secret-input"><input autoFocus aria-label="API Key" type={showKey ? "text" : "password"} autoComplete="off" spellCheck={false} value={apiKey} onChange={editConnection(setApiKey)} placeholder="sk-..." required /><button type="button" className="text-action" onClick={() => setShowKey((value) => !value)}>{showKey ? "隐藏" : "显示"}</button></div></label>
    <label>接口地址<div className="secret-input"><input aria-label="接口地址" spellCheck={false} value={baseUrl} onChange={editConnection(setBaseUrl)} placeholder="留空使用 OpenAI 官方；网关地址通常以 /v1 结尾" /><button type="button" className="button quiet" disabled={!config.apiKey || invalid || modelsMutation.isPending} onClick={() => modelsMutation.mutate()}>{modelsMutation.isPending ? <><LoaderCircle className="spin" size={14} />获取中…</> : <><RefreshCw size={14} />获取模型</>}</button></div></label>
    <div className="model-fields">
      <label>文本模型<select aria-label="文本模型" value={models ? textModel : ""} disabled={!models} onChange={pickModel(setTextModel)} required><option value="">{models ? "请选择模型" : "先获取模型列表"}</option>{models?.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
      <label>视觉模型<select aria-label="视觉模型" value={models ? visionModel : ""} disabled={!models} onChange={pickModel(setVisionModel)}><option value="">{models ? "与文本模型相同" : "先获取模型列表"}</option>{models?.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
    </div>
    {models && <small className="model-list-note">已获取 {models.length} 个模型</small>}
    <ErrorNotice error={modelsMutation.error} />
    <p className="privacy-note">只保存在本浏览器（localStorage），每次操作时随请求发给本机后端，后端不保存。简历池身份提取、邮箱附件识图等不由页面发起的后台任务拿不到这里的配置。</p>
    {invalid && <ErrorNotice error={new Error("API Key 与接口地址只能填写英文、数字和符号，不能含空格或中文")} />}
    {testMutation.isSuccess && <div className="success-note"><Check size={15} /><span>连接成功：{testMutation.data.checked_models.join("、")}</span></div>}
    <ErrorNotice error={testMutation.error} />
    <div className="modal-actions">{saved && <button type="button" className="button quiet model-clear" onClick={clear}>清除配置</button>}<button type="button" className="button quiet" disabled={!ready || testMutation.isPending} onClick={() => testMutation.mutate()}>{testMutation.isPending ? "测试中…" : "测试连接"}</button><button type="submit" className="button primary" disabled={!ready}>保存</button></div>
  </form></div>;
}

function JobList({ onOpen, onTrash, onPool }: { onOpen: (id: string) => void; onTrash: () => void; onPool: () => void }) {
  const queryClient = useQueryClient();
  const { data: jobs = [], error, isLoading } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const pool = useQuery({ queryKey: ["pool", "pool"], queryFn: () => api.poolResumes() });
  const poolNewCount = (pool.data || []).filter((item) => !item.evaluation).length;
  const [creating, setCreating] = useState(false);
  const [modelSettings, setModelSettings] = useState(false);
  return <main className="landing-shell"><header className="topbar"><div className="brand-mark"><span>PF</span></div><div className="brand-copy"><strong>PsychicFlow</strong><small>招聘筛选工作台</small></div><div className="topbar-actions"><button className="button quiet" type="button" aria-label="打开简历池" onClick={onPool}><Inbox size={15} />简历池{poolNewCount > 0 && <span className="count-badge">{poolNewCount}</span>}</button><button className="button quiet" type="button" aria-label="打开回收站" onClick={onTrash}><Trash2 size={15} />回收站</button><button type="button" className={`health-pill ${health.data?.model_configured ? "ready" : "warn"}`} aria-label="打开模型设置" title={health.data?.model_configured ? `${health.data.model_source === "browser" ? "使用本浏览器配置" : "使用 .env 配置"} · ${health.data.text_model}` : "点击填写模型 API Key 与地址"} onClick={() => setModelSettings(true)}><span />{health.data?.model_configured ? "模型已连接" : "待配置模型"}<Settings2 size={13} /></button></div></header>
    <section className="hero"><div><span className="eyebrow">RECRUITMENT INTELLIGENCE / 01</span><h1>把筛选依据<br />留在<span>案卷</span>里。</h1></div><div className="hero-aside"><p>同一标准审阅每一份简历，保留原文证据、当前版本和你的最终判断。</p><button className="button primary" onClick={() => setCreating(true)}><Plus size={17} />创建岗位</button></div></section>
    <section className="jobs-section"><div className="section-heading"><div><span className="section-index">A</span><h2>岗位案卷</h2></div><span>{jobs.length.toString().padStart(2, "0")} 份</span></div><ErrorNotice error={error} />
      {isLoading ? <div className="loading-block"><LoaderCircle className="spin" />正在调取案卷</div> : jobs.length === 0 ? <div className="empty-state"><FolderOpen /><h3>还没有岗位案卷</h3><p>先写下岗位 JD 和你的筛选标准，再放入候选人简历。</p><button className="button primary" onClick={() => setCreating(true)}>创建第一个岗位</button></div> : <div className="job-grid">{jobs.map((job, index) => <button className="job-card" key={job.id} onClick={() => onOpen(job.id)}><div className="job-number">{String(index + 1).padStart(2, "0")}</div><BriefcaseBusiness className="job-icon" /><div className="job-card-body"><span>{job.resume_count} 份简历 · {job.run_count} 次筛选</span><h3>{job.name}</h3><p>{job.jd_text}</p></div><div className="job-card-footer"><span>更新于 {dateLabel(job.updated_at)}</span><ChevronRight /></div></button>)}</div>}
    </section>{modelSettings && <ModelSettingsDialog defaults={{ textModel: health.data?.text_model || "", visionModel: health.data?.vision_model || "" }} onClose={() => setModelSettings(false)} onChanged={() => queryClient.invalidateQueries({ queryKey: ["health"] })} />}{creating && <CreateJobDialog onClose={() => setCreating(false)} onCreated={(job) => { queryClient.invalidateQueries({ queryKey: ["jobs"] }); setCreating(false); onOpen(job.id); }} />}</main>;
}

export function ResumeDesk({ job, onRunCreated }: { job: Job; onRunCreated: (run: Run) => void }) {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const inputRef = useRef<HTMLInputElement>(null);
  const { data: resumes = [], error } = useQuery({ queryKey: ["resumes", job.id], queryFn: () => api.resumes(job.id), refetchInterval: (query) => (query.state.data || []).some((resume) => ["uploaded", "parsing"].includes(resume.parse_status)) ? 1500 : false });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [uploadReport, setUploadReport] = useState<{ message: string; recoverable?: string } | null>(null);
  const [textPreview, setTextPreview] = useState<Resume | null>(null);
  const [rename, setRename] = useState<Resume | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Resume[] | null>(null);
  const [confirmReparse, setConfirmReparse] = useState<Resume | null>(null);
  const [pasting, setPasting] = useState(false);
  const [search, setSearch] = useState("");
  const [formatFilter, setFormatFilter] = useState("all");
  const [parseFilter, setParseFilter] = useState("all");
  const [sort, setSort] = useState("newest");
  const invalidate = () => { queryClient.invalidateQueries({ queryKey: ["resumes", job.id] }); queryClient.invalidateQueries({ queryKey: ["job", job.id] }); };
  const uploadMutation = useMutation({ mutationFn: (files: File[]) => api.upload(job.id, files), onSuccess: (result) => { invalidate(); setSelected(new Set(result.uploaded.map((item) => item.id))); const firstRecoverable = result.rejected.find((item) => item.recoverable_resume_id); const rejected = result.rejected.map((item) => `${item.filename}：${item.reason}`).join("；"); setUploadReport({ message: rejected || `已接收 ${result.uploaded.length} 份简历`, recoverable: firstRecoverable?.recoverable_resume_id || undefined }); notify(result.rejected.length ? "上传完成，部分文件需要处理" : `已接收 ${result.uploaded.length} 份简历`, result.rejected.length ? "info" : "success"); } });
  const runMutation = useMutation({ mutationFn: (ids: string[]) => api.createRun(job.id, ids), onSuccess: (run) => { notify("筛选任务已启动"); onRunCreated(run); } });
  const deleteMutation = useMutation({ mutationFn: async (items: Resume[]) => { for (const item of items) await api.deleteResume(item.id); }, onSuccess: () => { notify("简历档案已移入回收站"); setConfirmDelete(null); setSelected(new Set()); invalidate(); } });
  const reparseMutation = useMutation({ mutationFn: (item: Resume) => api.reparseResume(item.id), onSuccess: () => { notify("重新解析已启动"); setConfirmReparse(null); invalidate(); } });
  const restoreMutation = useMutation({ mutationFn: (id: string) => api.restoreTrash("resume", id), onSuccess: () => { notify("旧简历档案已恢复"); setUploadReport(null); invalidate(); } });
  const shown = useMemo(() => resumes.filter((resume) => resumeName(resume).toLowerCase().includes(search.trim().toLowerCase())).filter((resume) => formatFilter === "all" || resume.original_name.toLowerCase().endsWith(`.${formatFilter}`)).filter((resume) => parseFilter === "all" || resume.parse_status === parseFilter).sort((a, b) => { if (sort === "oldest") return +new Date(a.created_at) - +new Date(b.created_at); if (sort === "name-asc") return resumeName(a).localeCompare(resumeName(b), "zh-CN"); if (sort === "size-desc") return b.size_bytes - a.size_bytes; if (sort === "pages-desc") return (b.page_count || 0) - (a.page_count || 0); return +new Date(b.created_at) - +new Date(a.created_at); }), [resumes, search, formatFilter, parseFilter, sort]);
  const allSelected = shown.length > 0 && shown.every((resume) => selected.has(resume.id));
  const toggleAllShown = () => setSelected((current) => { const next = new Set(current); if (allSelected) shown.forEach((resume) => next.delete(resume.id)); else shown.forEach((resume) => next.add(resume.id)); return next; });
  const selectedResumes = resumes.filter((item) => selected.has(item.id));
  const totalPages = selectedResumes.reduce((sum, item) => sum + (item.page_count || 0), 0);
  const acceptFiles = (files: File[]) => { if (files.length) uploadMutation.mutate(files); };
  return <section className="workspace-section"><div className="desk-heading"><div><span className="eyebrow">INPUT / 候选人材料</span><h2>简历材料</h2><p className="heading-note">原件、解析文本与筛选入口集中管理</p></div><div className="desk-actions"><input ref={inputRef} hidden type="file" multiple accept=".pdf,.docx" onChange={(event) => { if (event.target.files) acceptFiles([...event.target.files]); event.target.value = ""; }} /><button className="button quiet" onClick={() => inputRef.current?.click()} disabled={uploadMutation.isPending}><Upload size={16} />{uploadMutation.isPending ? "上传中…" : "上传简历"}</button><button className="button quiet" type="button" onClick={() => setPasting(true)}><ClipboardPaste size={16} />粘贴文本</button><button className="button primary" disabled={!selected.size || runMutation.isPending} onClick={() => runMutation.mutate([...selected])}><Sparkles size={16} />筛选已选 {selected.size || ""}</button></div></div>
    {selected.size > 0 && <div className="bulk-toolbar"><strong>已选 {selected.size} 份材料</strong><button type="button" onClick={() => runMutation.mutate([...selected])}><Sparkles />筛选 / 重评</button><button type="button" onClick={() => setConfirmDelete(selectedResumes)}><Trash2 />移入回收站</button><button type="button" onClick={() => setSelected(new Set())}>取消选择</button></div>}
    {selected.size > 0 && <div className="cost-strip"><CircleAlert size={16} /><span>{health.data?.vision_enabled ? `本次将处理 ${selected.size} 份简历，其中 PDF 共 ${totalPages} 页；PDF 每页优先调用视觉模型。` : `本次将处理 ${selected.size} 份简历，其中 PDF 共 ${totalPages} 页；当前将使用本地文本/OCR。`}</span></div>}
    {uploadReport && <div className="success-note"><Check size={15} /><span>{uploadReport.message}</span>{uploadReport.recoverable && <button type="button" onClick={() => restoreMutation.mutate(uploadReport.recoverable!)}>恢复旧档案</button>}<button type="button" aria-label="关闭上传结果" onClick={() => setUploadReport(null)}><X size={14} /></button></div>}<ErrorNotice error={error || uploadMutation.error || runMutation.error || deleteMutation.error || reparseMutation.error} />
    <button className={`upload-dropzone inline ${uploadMutation.isPending ? "busy" : ""}`} onClick={() => inputRef.current?.click()} onDragOver={(event) => { event.preventDefault(); event.currentTarget.classList.add("dragging"); }} onDragLeave={(event) => event.currentTarget.classList.remove("dragging")} onDrop={(event) => { event.preventDefault(); event.currentTarget.classList.remove("dragging"); acceptFiles([...event.dataTransfer.files]); }}><Upload /><strong>拖入 PDF 或 DOCX 简历</strong><span>也可以点击选择文件 · 单文件不超过 20 MB</span></button>
    {resumes.length > 0 && <><div className="retrieval-bar"><div className="retrieval-title"><ListFilter size={16} /><span>材料检索</span><small>{shown.length} / {resumes.length}</small></div><div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索文件名" aria-label="搜索简历" /></div><div className="retrieval-filters"><label><span>格式</span><select aria-label="简历格式" value={formatFilter} onChange={(event) => setFormatFilter(event.target.value)}><option value="all">全部格式</option><option value="pdf">PDF</option><option value="docx">DOCX</option></select></label><label><span>状态</span><select aria-label="解析状态" value={parseFilter} onChange={(event) => setParseFilter(event.target.value)}><option value="all">全部状态</option><option value="ready">文本就绪</option><option value="uploaded">待解析</option><option value="parsing">解析中</option><option value="failed">解析失败</option></select></label><label><span>排序</span><select aria-label="简历排序" value={sort} onChange={(event) => setSort(event.target.value)}><option value="newest">最近上传</option><option value="oldest">最早上传</option><option value="name-asc">文件名 A–Z</option><option value="size-desc">文件从大到小</option><option value="pages-desc">页数从多到少</option></select></label></div></div>
      {shown.length === 0 ? <div className="filtered-empty"><Search /><strong>没有符合条件的简历</strong><span>调整关键词或分类条件后再试。</span></div> : <div className="resume-table"><div className="table-row table-head"><label><input type="checkbox" checked={allSelected} onChange={toggleAllShown} aria-label="选择当前筛选结果" /></label><span>文件</span><span>格式 / 大小</span><span>解析状态</span><span>上传时间</span><span>操作</span></div>{shown.map((resume) => { const name = resumeName(resume); return <div className="table-row" key={resume.id}><label><input type="checkbox" aria-label={`选择${name}`} checked={selected.has(resume.id)} onChange={() => setSelected((current) => { const next = new Set(current); next.has(resume.id) ? next.delete(resume.id) : next.add(resume.id); return next; })} /></label><div className="file-name"><FileText size={17} /><div><strong data-testid="resume-name">{name}</strong>{resume.parsed_text_source === "manual" && <small>人工修正文本</small>}</div></div><span>{resume.original_name.split(".").pop()?.toUpperCase()} · {sizeLabel(resume.size_bytes)}{resume.page_count ? ` · ${resume.page_count} 页` : ""}</span><span className={`status-dot ${resume.parse_status}`} title={resume.parse_error || undefined}>{resume.parse_status === "ready" ? "文本就绪" : resume.parse_status === "failed" ? "解析失败" : resume.parse_status === "parsing" ? "解析中" : "待解析"}</span><span>{dateLabel(resume.created_at)}</span><div className="row-actions">{resume.parse_status === "ready" && <button className="text-action" onClick={() => setTextPreview(resume)} aria-label={`查看${name}文本`}>查看文本</button>}<RowMenu label={`${name}更多操作`}>{(close) => <><a role="menuitem" href={`/api/resumes/${resume.id}/preview`} target="_blank" rel="noreferrer"><FileText />预览原件</a><button role="menuitem" onClick={() => { close(); setRename(resume); }}><Pencil />修改名称</button><button role="menuitem" onClick={() => { close(); setConfirmReparse(resume); }}><RefreshCw />重新解析</button><button role="menuitem" onClick={() => { close(); runMutation.mutate([resume.id]); }}><Sparkles />重新评分</button><button role="menuitem" className="danger-text" onClick={() => { close(); setConfirmDelete([resume]); }}><Trash2 />移入回收站</button></>}</RowMenu></div></div>; })}</div>}
    </>}
    {textPreview && <ResumeTextDialog resume={textPreview} onClose={() => setTextPreview(null)} onSaved={invalidate} />}{rename && <RenameResumeDialog resume={rename} onClose={() => setRename(null)} onSaved={invalidate} />}
    {pasting && <PasteResumeDialog submit={(payload) => api.pasteJobResume(job.id, payload)} createdMessage="简历已录入，评分任务已启动" onClose={() => setPasting(false)} onCreated={() => { invalidate(); queryClient.invalidateQueries({ queryKey: ["runs", job.id] }); }} />}
    {confirmDelete && <ConfirmDialog title={confirmDelete.length > 1 ? `删除 ${confirmDelete.length} 份简历档案？` : `删除“${resumeName(confirmDelete[0])}”？`} description="原件、解析文本和关联筛选结果会一起移入回收站，30 天内可以恢复。" confirmLabel="移入回收站" danger busy={deleteMutation.isPending} onCancel={() => setConfirmDelete(null)} onConfirm={() => deleteMutation.mutate(confirmDelete)} />}
    {confirmReparse && <ConfirmDialog title={`重新解析“${resumeName(confirmReparse)}”？`} description={confirmReparse.parsed_text_source === "manual" ? "这会覆盖当前人工修正文本，覆盖后无法撤销。" : "系统会重新从原始文件提取文本。"} confirmLabel="开始重新解析" busy={reparseMutation.isPending} onCancel={() => setConfirmReparse(null)} onConfirm={() => reparseMutation.mutate(confirmReparse)} />}
  </section>;
}

function scoreBand(score?: number | null) {
  // 与后端 match_level_for_score 保持一致：≥85 高匹配，≥65 中匹配。
  if (score == null) return "pending";
  if (score >= 85) return "high";
  if (score >= 65) return "medium";
  return "low";
}

export function EvaluationDetail({ evaluation, onClose }: { evaluation: Evaluation; onClose: () => void }) {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const [status, setStatus] = useState(evaluation.review_status);
  const [note, setNote] = useState(evaluation.review_note);
  const [analysisWidth, setAnalysisWidth] = useState(54);
  const [panelWidth, setPanelWidth] = useState(() => Math.min(1320, window.innerWidth - 48));
  const detailPanelRef = useRef<HTMLElement>(null);
  const detailContentRef = useRef<HTMLDivElement>(null);
  const mutation = useMutation({ mutationFn: () => api.review(evaluation.id, status, note), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["evaluations"] }); notify("招聘判断已保存"); } });
  const result = evaluation.result;
  const resizeDetail = (event: ReactPointerEvent<HTMLDivElement>) => { event.preventDefault(); const bounds = detailContentRef.current?.getBoundingClientRect(); if (!bounds) return; const onMove = (moveEvent: PointerEvent) => setAnalysisWidth(Math.min(76, Math.max(28, ((moveEvent.clientX - bounds.left) / bounds.width) * 100))); const onEnd = () => { document.body.style.cursor = ""; document.body.style.userSelect = ""; window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerup", onEnd); }; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; window.addEventListener("pointermove", onMove); window.addEventListener("pointerup", onEnd, { once: true }); };
  const resizePanel = (event: ReactPointerEvent<HTMLDivElement>) => { event.preventDefault(); const bounds = detailPanelRef.current?.getBoundingClientRect(); if (!bounds) return; const rightEdge = bounds.right; const onMove = (moveEvent: PointerEvent) => setPanelWidth(Math.min(window.innerWidth, Math.max(Math.min(720, window.innerWidth), rightEdge - moveEvent.clientX))); const onEnd = () => { document.body.style.cursor = ""; document.body.style.userSelect = ""; window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerup", onEnd); }; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; window.addEventListener("pointermove", onMove); window.addEventListener("pointerup", onEnd, { once: true }); };
  return <div className="detail-backdrop"><aside className="detail-panel" ref={detailPanelRef} style={{ width: `${panelWidth}px` }}><div className="panel-resizer" role="separator" aria-label="调整详情面板宽度" aria-orientation="vertical" aria-valuemin={Math.min(720, window.innerWidth)} aria-valuemax={window.innerWidth} aria-valuenow={Math.round(panelWidth)} tabIndex={0} onPointerDown={resizePanel} onKeyDown={(event) => { if (event.key === "ArrowLeft") setPanelWidth((value) => Math.min(window.innerWidth, value + 24)); if (event.key === "ArrowRight") setPanelWidth((value) => Math.max(Math.min(720, window.innerWidth), value - 24)); }}><span /></div><div className="detail-header"><div><span className="eyebrow">CANDIDATE REVIEW</span><h2>{candidateDisplayName(evaluation)}</h2><p>{result?.candidate.current_role || evaluation.resume_name}</p></div><button className="icon-button" onClick={onClose} aria-label="关闭详情"><X /></button></div>
    <div className="detail-content" ref={detailContentRef} style={{ gridTemplateColumns: `${analysisWidth}% 9px minmax(0, 1fr)` }}><div className="assessment-column">{result ? <><div className={`score-card ${result.match_level}`}><div><span>综合匹配</span><strong>{result.overall_score}</strong><small>/ 100</small></div><p>{result.summary}</p></div>{result.criteria.some((item) => item.knockout && item.status === "not_met") ? <p className="knockout-note">硬性项未满足，匹配等级已限制为低</p> : null}<section className="dimension-section"><div className="dimension-heading"><div><span className="section-index">分</span><h3>维度评分</h3></div><small>{result.criteria.filter((item) => item.score != null).length} / {result.criteria.length} 已评分</small></div><div className="dimension-list">{result.criteria.map((criterion, index) => <div className="dimension-item" key={`${criterion.criterion}-${index}`}><div className="dimension-meta"><span>{String(index + 1).padStart(2, "0")}</span><strong>{criterion.criterion}</strong>{criterion.knockout || criterion.kind === "knockout" ? <em className="knockout-tag">硬性</em> : criterion.kind === "bonus" ? <em className="knockout-tag">加分项</em> : null}<div className={`dimension-score ${scoreBand(criterion.score)}`} aria-label={`${criterion.criterion}评分`}>{criterion.score == null ? <em>待重新评分</em> : <><b>{criterion.score}</b><small> / 100</small>{criterion.weight ? <span className="weighted-contribution">贡献 {criterion.weighted_score ?? 0} / {criterion.weight}</span> : null}</>}</div></div><div className="dimension-track"><span className={scoreBand(criterion.score)} style={{ width: `${criterion.score ?? 0}%` }} /></div></div>)}</div></section><section className="evidence-section"><h3>标准与证据</h3>{result.criteria.map((criterion, index) => <article className="criterion" key={`${criterion.criterion}-${index}`}><header><div className="criterion-title"><span className={`criterion-status ${criterion.status}`}>{({ met: "满足", partial: "部分", not_met: "不满足", unknown: "未知" } as const)[criterion.status]}</span><strong>{criterion.criterion}</strong>{criterion.knockout || criterion.kind === "knockout" ? <em className="knockout-tag">硬性</em> : criterion.kind === "bonus" ? <em className="knockout-tag">加分项</em> : null}</div><div className={`criterion-score ${scoreBand(criterion.score)}`}>{criterion.score == null ? "待重新评分" : <><b>{criterion.score}</b><small>/100{criterion.weight ? ` · 贡献 ${criterion.weighted_score ?? 0}/${criterion.weight}` : ""}</small></>}</div></header>{criterion.evidence.map((entry, idx) => <blockquote key={idx}>“{entry.text}”<cite>{entry.location}</cite></blockquote>)}</article>)}</section><div className="insight-grid"><section><h3>优势</h3><ul>{result.strengths.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>缺口</h3><ul>{result.gaps.map((item) => <li key={item}>{item}</li>)}</ul></section></div>{result.risk_flags.length > 0 && <section className="risk-box"><h3>需核实</h3>{result.risk_flags.join("；")}</section>}<section className="questions"><h3>建议面试追问</h3><ol>{result.interview_questions.map((item) => <li key={item}>{item}</li>)}</ol></section></> : <div className="loading-block">{evaluation.error || "评估仍在进行"}</div>}</div><div className="detail-resizer" role="separator" aria-label="调整分析与简历宽度" aria-orientation="vertical" aria-valuemin={28} aria-valuemax={76} aria-valuenow={Math.round(analysisWidth)} tabIndex={0} onPointerDown={resizeDetail} onKeyDown={(event) => { if (event.key === "ArrowLeft") setAnalysisWidth((value) => Math.max(28, value - 2)); if (event.key === "ArrowRight") setAnalysisWidth((value) => Math.min(76, value + 2)); }}><span /></div><div className="document-column"><div className="preview-toolbar"><span>原始简历</span><div><a href={`/api/resumes/${evaluation.resume_id}/preview`} target="_blank" rel="noreferrer">新窗口</a><a href={`/api/resumes/${evaluation.resume_id}/original`}><Download size={15} />下载</a></div></div><iframe title="简历预览" src={`/api/resumes/${evaluation.resume_id}/preview`} /></div></div>
    <footer className="review-bar"><div className="review-buttons">{(["pending", "advance", "hold", "reject"] as const).map((value) => <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>{reviewLabels[value]}</button>)}</div><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="写下招聘备注…" /><button className="button primary" onClick={() => mutation.mutate()} disabled={mutation.isPending}>{mutation.isPending ? "保存中" : "保存判断"}</button></footer></aside></div>;
}

export function ResultsDesk({ jobId, initialRunId: _initialRunId }: { jobId: string; initialRunId?: string }) {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [reviewFilter, setReviewFilter] = useState("all");
  const [matchFilter, setMatchFilter] = useState("all");
  const [runFilter, setRunFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState("screened-desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [detail, setDetail] = useState<Evaluation | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targetJob, setTargetJob] = useState("");
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, enabled: selected.size > 0 });
  const [confirmDelete, setConfirmDelete] = useState<string[] | null>(null);
  useEffect(() => { setPage(1); }, [deferredSearch, reviewFilter, matchFilter, runFilter, dateFrom, dateTo, sort, pageSize]);
  const queryParams = useMemo(() => { const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), search: deferredSearch.trim(), review_status: reviewFilter, match_level: matchFilter, run_status: runFilter, sort }); if (dateFrom) params.set("date_from", dateFrom); if (dateTo) params.set("date_to", dateTo); return params; }, [page, pageSize, deferredSearch, reviewFilter, matchFilter, runFilter, dateFrom, dateTo, sort]);
  const evaluations = useQuery({ queryKey: ["evaluations", jobId, queryParams.toString()], queryFn: () => api.jobEvaluations(jobId, queryParams), refetchInterval: 2000 });
  // 重评中的新评估在完成前不是 current，结果列表看不到它；单独拉取进行中的任务展示进度。
  const runs = useQuery({ queryKey: ["runs", jobId, "active"], queryFn: () => api.runs(jobId, new URLSearchParams({ page: "1", page_size: "20", run_status: "all" })), refetchInterval: (query) => (query.state.data?.items || []).some((run) => activeRunStatuses.includes(run.status)) ? 1500 : false });
  const activeRuns = (runs.data?.items || []).filter((run) => activeRunStatuses.includes(run.status));
  const activeRunIds = activeRuns.map((run) => run.id);
  const activeEvaluations = useQuery({ queryKey: ["run-evaluations", ...activeRunIds], queryFn: async () => (await Promise.all(activeRunIds.map((id) => api.evaluations(id)))).flat(), enabled: activeRunIds.length > 0, refetchInterval: 1500 });
  const pendingByResume = new Map((activeEvaluations.data || []).filter((item) => activeEvaluationStatuses.includes(item.status)).map((item) => [item.resume_id, item]));
  const data = evaluations.data;
  const shown = data?.items || [];
  const selectedIds = [...selected];
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["evaluations", jobId] });
  const reviewMutation = useMutation({ mutationFn: ({ ids, status }: { ids: string[]; status: Evaluation["review_status"] }) => api.batchReview(ids, status), onSuccess: () => { notify("所选候选人的人工状态已更新"); invalidate(); } });
  const rescoreMutation = useMutation({ mutationFn: (ids: string[]) => api.rescore(ids), onSuccess: () => { notify("重新评分任务已启动"); setSelected(new Set()); invalidate(); queryClient.invalidateQueries({ queryKey: ["runs", jobId] }); } });
  const changeJobMutation = useMutation({
    mutationFn: ({ ids, target }: { ids: string[]; target: string }) => api.rescore(ids, target),
    onSuccess: (_run, { target }) => {
      notify(`已启动「${jobs.data?.find((job) => job.id === target)?.name || "目标职位"}」重新评分，请在该职位的筛选结果查看`);
      setSelected(new Set());
      setTargetJob("");
      for (const key of ["evaluations", "runs", "jobs", "job", "pool"]) queryClient.invalidateQueries({ queryKey: [key] });
    },
  });
  const deleteMutation = useMutation({ mutationFn: async (ids: string[]) => { if (ids.length === 1) await api.deleteEvaluation(ids[0]); else await api.batchDeleteEvaluations(ids); }, onSuccess: () => { notify("筛选结果已移入回收站"); setSelected(new Set()); setConfirmDelete(null); setDetail(null); invalidate(); } });
  const exportHref = (format: string) => { const params = new URLSearchParams({ format }); selectedIds.forEach((id) => params.append("evaluation_id", id)); return `/api/jobs/${jobId}/export?${params.toString()}`; };
  const allShownSelected = shown.length > 0 && shown.every((item) => selected.has(item.id));
  const toggleShown = () => setSelected((current) => { const next = new Set(current); if (allShownSelected) shown.forEach((item) => next.delete(item.id)); else shown.forEach((item) => next.add(item.id)); return next; });
  const totalPages = Math.max(1, data?.total_pages || 1);
  const pageNumbers = Array.from({ length: Math.min(5, totalPages) }, (_, index) => Math.max(1, Math.min(totalPages - 4, page - 2)) + index).filter((value) => value <= totalPages);
  return <section className="workspace-section results-desk"><div className="desk-heading"><div><span className="eyebrow">OUTPUT / 评估案卷</span><h2>筛选结果</h2><p className="heading-note">每份简历仅展示当前有效评分</p></div><div className="desk-actions"><div className="export-actions"><span><Download size={15} />{selected.size ? `导出已选 ${selected.size} 人` : "导出全部"}</span><a href={exportHref("xlsx")} aria-label="导出 Excel">XLSX</a><a href={exportHref("csv")} aria-label="导出 CSV">CSV</a><a href={exportHref("json")} aria-label="导出 JSON">JSON</a><a href={exportHref("md")} aria-label="导出 Markdown">MD</a><a href={exportHref("docx")} aria-label="导出 Word">DOCX</a></div></div></div>
    {activeRuns.length > 0 && <section className="active-runs" aria-label="进行中的筛选任务">{activeRuns.map((run) => { const done = run.completed_count + run.failed_count; const progress = run.total_count ? Math.round((done / run.total_count) * 100) : 0; const items = (activeEvaluations.data || []).filter((entry) => entry.run_id === run.id); return <article className="active-run" key={run.id}><header><span className={`run-state ${run.status}`}><LoaderCircle className="spin" />{runLabels[run.status] || run.status}</span><time dateTime={run.created_at}>开始于 {dateLabel(run.created_at)}</time><strong>{done} / {run.total_count}</strong></header><div className="run-progress" role="progressbar" aria-label="筛选进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>{items.length > 0 && <ul>{items.map((entry) => <li key={entry.id}><span>{entry.resume_name}</span><em className={`eval-state ${entry.status}`}>{evaluationStatusLabels[entry.status] || entry.status}</em></li>)}</ul>}</article>; })}<p>评分完成后自动替换下方结果；完成前保留上一次的有效评分。</p></section>}
    {selected.size > 0 && <div className="bulk-toolbar results-bulk"><strong>已选 {selected.size} 人</strong><button aria-label="批量推进" type="button" onClick={() => reviewMutation.mutate({ ids: selectedIds, status: "advance" })}>推进</button><button type="button" onClick={() => reviewMutation.mutate({ ids: selectedIds, status: "hold" })}>保留</button><button type="button" onClick={() => reviewMutation.mutate({ ids: selectedIds, status: "reject" })}>淘汰</button><button type="button" onClick={() => reviewMutation.mutate({ ids: selectedIds, status: "pending" })}>恢复待定</button><button aria-label="批量重新评分" disabled={rescoreMutation.isPending || changeJobMutation.isPending} type="button" onClick={() => rescoreMutation.mutate(selectedIds)}><RefreshCw />重新评分</button><button aria-label="批量移入回收站" type="button" onClick={() => setConfirmDelete(selectedIds)}><Trash2 />删除</button><button type="button" onClick={() => setSelected(new Set())}>取消选择</button></div>}
    {selected.size > 0 && <div className="bulk-toolbar pool-bulk"><label className="pool-job-select"><span>更换职位</span><select aria-label="重评目标职位" value={targetJob} disabled={changeJobMutation.isPending} onChange={(event) => setTargetJob(event.target.value)}><option value="">选择目标职位…</option>{(jobs.data || []).filter((job) => job.id !== jobId).map((job) => <option key={job.id} value={job.id}>{job.name}</option>)}</select></label><button className="button primary" disabled={!targetJob || changeJobMutation.isPending || rescoreMutation.isPending} onClick={() => changeJobMutation.mutate({ ids: selectedIds, target: targetJob })}><RefreshCw size={15} />{changeJobMutation.isPending ? "正在启动…" : "更换职位重新评分"}</button><span>按目标职位标准评分，保留原职位评分；完成后在目标职位查看。</span></div>}
    <div className="retrieval-bar results-retrieval"><div className="retrieval-title"><ListFilter size={16} /><span>结果检索</span><small>{data?.total || 0} 条</small></div><div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索姓名、岗位或文件" aria-label="搜索筛选结果" /></div><div className="retrieval-filters results-filters"><label><span>人工状态</span><select aria-label="人工状态" value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}><option value="all">全部状态</option><option value="pending">待复核</option><option value="advance">推进</option><option value="hold">保留</option><option value="reject">淘汰</option></select></label><label><span>匹配等级</span><select aria-label="匹配等级" value={matchFilter} onChange={(event) => setMatchFilter(event.target.value)}><option value="all">全部等级</option><option value="high">高匹配</option><option value="medium">中匹配</option><option value="low">低匹配</option><option value="unscored">尚未评分</option></select></label><label><span>任务状态</span><select aria-label="任务状态" value={runFilter} onChange={(event) => setRunFilter(event.target.value)}><option value="all">全部任务</option><option value="running">筛选中</option><option value="completed">已完成</option><option value="partial">部分失败</option><option value="canceled">已取消</option></select></label><label><span>开始日期</span><input type="date" aria-label="筛选开始日期" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label><label><span>结束日期</span><input type="date" aria-label="筛选结束日期" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label><label><span>排序</span><select aria-label="结果排序" value={sort} onChange={(event) => setSort(event.target.value)}><option value="screened-desc">最近筛选</option><option value="score-desc">分数从高到低</option><option value="score-asc">分数从低到高</option><option value="name-asc">姓名 A–Z</option></select></label></div></div>
    {evaluations.isLoading ? <div className="loading-block"><LoaderCircle className="spin" />正在汇总筛选结果</div> : shown.length === 0 ? <div className="filtered-empty"><Search /><strong>{data?.total === 0 && !search && reviewFilter === "all" && matchFilter === "all" ? "还没有筛选结果" : "没有符合条件的候选人"}</strong><span>{data?.total === 0 ? "在简历材料页选择候选人并开始筛选。" : "调整检索条件后再试。"}</span></div> : <><div className="candidate-selection-bar"><label><input type="checkbox" aria-label="选择当前候选人结果" checked={allShownSelected} onChange={toggleShown} /><strong>{selected.size ? "跨页选择已开启" : "选择当前页"}</strong></label><span>{selected.size ? "选择可跨页保留" : "勾选后可批量审核、重评、删除与导出"}</span></div><div className="candidate-list">{shown.map((item, index) => { const name = candidateDisplayName(item); const pending = pendingByResume.get(item.resume_id); const rescoring = pending && pending.id !== item.id; return <div className={`candidate-row ${selected.has(item.id) ? "selected" : ""}`} key={item.id}><label className="candidate-check"><input type="checkbox" aria-label={`选择${name}`} checked={selected.has(item.id)} onChange={() => setSelected((current) => { const next = new Set(current); next.has(item.id) ? next.delete(item.id) : next.add(item.id); return next; })} /></label><button className="candidate-open" type="button" onClick={() => setDetail(item)}><span className="rank">{String((page - 1) * pageSize + index + 1).padStart(2, "0")}</span><div className="candidate-name"><strong data-testid="candidate-name">{name}</strong><small>{item.result?.candidate.current_role || evaluationStatusLabels[item.status] || item.status}</small>{rescoring && <span className="rescoring-tag"><LoaderCircle className="spin" />重评中</span>}<BackgroundBadges items={item.result?.background_highlights} /></div><div className="candidate-summary">{item.result?.summary || item.error || "正在生成评估卡…"}</div><div className="screened-date"><span>筛选日期</span><time dateTime={item.screened_at}>{screeningDateLabel(item.screened_at)}</time><small>{screeningTimeLabel(item.screened_at)}</small></div><div className={`review-chip ${item.review_status}`}>{reviewLabels[item.review_status]}</div><div className={`score-ring ${item.result?.match_level || "pending"}`}>{item.result?.overall_score ?? "—"}</div><ChevronRight /></button><RowMenu label={`${name}结果操作`}>{(close) => <><button role="menuitem" onClick={() => { close(); setDetail(item); }}><FileText />查看详情</button><button role="menuitem" onClick={() => { close(); rescoreMutation.mutate([item.id]); }}><RefreshCw />重新评分</button><button role="menuitem" className="danger-text" onClick={() => { close(); setConfirmDelete([item.id]); }}><Trash2 />移入回收站</button></>}</RowMenu></div>; })}</div><div className="pagination-bar"><label>每页<select aria-label="每页数量" value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}><option value={10}>10</option><option value={20}>20</option><option value={50}>50</option></select>条</label><div><button type="button" aria-label="第一页" disabled={page <= 1} onClick={() => setPage(1)}><ChevronLeft /></button>{pageNumbers.map((number) => <button type="button" className={number === page ? "active" : ""} key={number} onClick={() => setPage(number)}>{number}</button>)}<span>第 {page} / {totalPages} 页</span><button type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div></>}
    <ErrorNotice error={changeJobMutation.error || jobs.error || evaluations.error || reviewMutation.error || rescoreMutation.error || deleteMutation.error} />{detail && <EvaluationDetail evaluation={detail} onClose={() => setDetail(null)} />}{confirmDelete && <ConfirmDialog title={`删除 ${confirmDelete.length} 条筛选结果？`} description="只会删除本次评分，原始简历和其他任务记录仍会保留；30 天内可恢复。" confirmLabel="移入回收站" danger busy={deleteMutation.isPending} onCancel={() => setConfirmDelete(null)} onConfirm={() => deleteMutation.mutate(confirmDelete)} />}
  </section>;
}

function TasksDesk({ jobId, onOpenResults }: { jobId: string; onOpenResults: () => void }) {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("all");
  const [confirmDelete, setConfirmDelete] = useState<Run | null>(null);
  const params = useMemo(() => new URLSearchParams({ page: String(page), page_size: "12", run_status: statusFilter }), [page, statusFilter]);
  const query = useQuery({ queryKey: ["runs", jobId, params.toString()], queryFn: () => api.runs(jobId, params), refetchInterval: (current) => (current.state.data?.items || []).some((run) => ["queued", "running"].includes(run.status)) ? 1200 : false });
  const invalidate = () => { queryClient.invalidateQueries({ queryKey: ["runs", jobId] }); queryClient.invalidateQueries({ queryKey: ["evaluations", jobId] }); };
  const cancelMutation = useMutation({ mutationFn: api.cancelRun, onSuccess: () => { notify("筛选任务已取消"); invalidate(); } });
  const retryMutation = useMutation({ mutationFn: api.retryRun, onSuccess: () => { notify("失败项已重新排队"); invalidate(); } });
  const deleteMutation = useMutation({ mutationFn: api.deleteRun, onSuccess: () => { notify("筛选任务及本批结果已移入回收站"); setConfirmDelete(null); invalidate(); } });
  const items = query.data?.items || [];
  return <section className="workspace-section tasks-desk"><div className="desk-heading"><div><span className="eyebrow">OPERATIONS / 运行记录</span><h2>任务中心</h2><p className="heading-note">查看每次筛选进度、模型与失败原因</p></div><label className="standalone-filter"><span>任务状态</span><select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }}><option value="all">全部任务</option><option value="running">筛选中</option><option value="completed">已完成</option><option value="partial">部分失败</option><option value="canceled">已取消</option></select></label></div>
    {query.isLoading ? <div className="loading-block"><LoaderCircle className="spin" />正在读取任务记录</div> : items.length === 0 ? <div className="filtered-empty"><ClipboardList /><strong>暂无筛选任务</strong><span>从简历材料页选择候选人并启动筛选。</span></div> : <div className="run-list">{items.map((run) => { const progress = run.total_count ? Math.round(((run.completed_count + run.failed_count) / run.total_count) * 100) : 0; return <article className="run-card" key={run.id}><header><div><span className={`run-state ${run.status}`}>{runLabels[run.status] || run.status}</span><time>{dateLabel(run.created_at)}</time></div><strong>{run.completed_count + run.failed_count} / {run.total_count}</strong></header><div className="run-progress"><span style={{ width: `${progress}%` }} /></div><dl><div><dt>文本模型</dt><dd>{run.text_model}</dd></div><div><dt>视觉模型</dt><dd>{run.vision_model}</dd></div><div><dt>失败</dt><dd>{run.failed_count}</dd></div></dl><footer><button type="button" onClick={onOpenResults}>查看结果</button>{["queued", "running"].includes(run.status) && <button type="button" onClick={() => cancelMutation.mutate(run.id)}><Ban />取消任务</button>}{run.failed_count > 0 && !["queued", "running"].includes(run.status) && <button type="button" onClick={() => retryMutation.mutate(run.id)}><RotateCcw />重试失败项</button>}{!["queued", "running"].includes(run.status) && <button type="button" className="danger-text" onClick={() => setConfirmDelete(run)}><Trash2 />删除本批</button>}</footer></article>; })}</div>}
    {(query.data?.total_pages || 0) > 1 && <div className="pagination-bar"><span>共 {query.data?.total} 个任务</span><div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>第 {page} / {query.data?.total_pages} 页</span><button disabled={page >= (query.data?.total_pages || 1)} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div>}<ErrorNotice error={query.error || cancelMutation.error || retryMutation.error || deleteMutation.error} />
    {confirmDelete && <ConfirmDialog title="删除这次筛选任务？" description="该任务产生的整批筛选结果会一起进入回收站，简历材料仍然保留。" confirmLabel="移入回收站" danger busy={deleteMutation.isPending} onCancel={() => setConfirmDelete(null)} onConfirm={() => deleteMutation.mutate(confirmDelete.id)} />}
  </section>;
}

function SettingsDesk({ job, onUpdated, onDeleted }: { job: Job; onUpdated: (job: Job) => void; onDeleted: () => void }) {
  const notify = useNotify();
  const [name, setName] = useState(job.name);
  const [jd, setJd] = useState(job.jd_text);
  const [prompt, setPrompt] = useState(job.prompt);
  const [profile, setProfile] = useState<ScoringDimension[]>(job.scoring_dimensions || []);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const mutation = useMutation({
    mutationFn: () => api.updateJob(job.id, { name, jd_text: jd, prompt, scoring_profile: toScoringProfile(profile) }),
    onSuccess: (updated) => { notify("岗位标准已保存"); onUpdated(updated); },
  });
  const deleteMutation = useMutation({ mutationFn: () => api.deleteJob(job.id), onSuccess: () => { notify("岗位案卷已移入回收站"); onDeleted(); } });
  const dirty = name !== job.name || jd !== job.jd_text || prompt !== job.prompt
    || JSON.stringify(toScoringProfile(profile)) !== JSON.stringify(toScoringProfile(job.scoring_dimensions || []));
  return <section className="workspace-section settings-desk"><div className="desk-heading"><div><span className="eyebrow">STANDARD / 岗位标准</span><h2>岗位与提示词</h2><p className="heading-note">修改后只影响新启动的筛选任务</p></div><button className="button primary" onClick={() => mutation.mutate()} disabled={mutation.isPending || !dirty}>{mutation.isPending ? "保存中…" : "保存修改"}</button></div><div className="settings-grid"><label>岗位名称<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>岗位 JD<textarea rows={14} value={jd} onChange={(event) => setJd(event.target.value)} /></label><label>筛选提示词<textarea rows={14} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="使用「## 1. 名称：XX分」或带权重列的表格。硬性 Must-have 与加分项请在下方量尺中修改。" /></label></div><ScoringRubricEditor prompt={prompt} initialDimensions={job.scoring_dimensions || []} onChange={setProfile} /><div className="danger-zone"><div><strong>删除岗位案卷</strong><p>岗位及其简历、任务和筛选结果会进入回收站，30 天内可以恢复。</p></div><button className="button danger" onClick={() => setConfirmDelete(true)}><Trash2 size={16} />移入回收站</button></div><ErrorNotice error={mutation.error || deleteMutation.error} />{confirmDelete && <ConfirmDialog title={`删除岗位“${job.name}”？`} description="整个岗位案卷会进入回收站，包含简历原件、解析文本、筛选任务、评估结果和招聘备注。" confirmLabel="移入回收站" danger busy={deleteMutation.isPending} onCancel={() => setConfirmDelete(false)} onConfirm={() => deleteMutation.mutate()} />}</section>;
}

function TrashDesk({ onBack }: { onBack: () => void }) {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const [typeFilter, setTypeFilter] = useState("all");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [page, setPage] = useState(1);
  const [confirmPurge, setConfirmPurge] = useState<TrashItem | "all" | null>(null);
  const params = useMemo(() => new URLSearchParams({ item_type: typeFilter, search: deferredSearch, page: String(page), page_size: "20" }), [typeFilter, deferredSearch, page]);
  const query = useQuery({ queryKey: ["trash", params.toString()], queryFn: () => api.trash(params) });
  const invalidate = () => { queryClient.invalidateQueries({ queryKey: ["trash"] }); queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.invalidateQueries({ queryKey: ["resumes"] }); queryClient.invalidateQueries({ queryKey: ["evaluations"] }); queryClient.invalidateQueries({ queryKey: ["runs"] }); };
  const restoreMutation = useMutation({ mutationFn: (item: TrashItem) => api.restoreTrash(item.item_type, item.id), onSuccess: () => { notify("档案已恢复"); invalidate(); } });
  const purgeMutation = useMutation({ mutationFn: async (item: TrashItem | "all") => { if (item === "all") await api.emptyTrash(); else await api.purgeTrash(item.item_type, item.id); }, onSuccess: () => { notify("已永久删除，无法恢复", "info"); setConfirmPurge(null); invalidate(); } });
  return <main className="trash-shell"><header className="topbar"><button className="back-link dark" onClick={onBack}><ArrowLeft />返回岗位</button><div className="brand-copy"><strong>PsychicFlow</strong><small>全局回收站</small></div><button className="button danger" disabled={!query.data?.total} onClick={() => setConfirmPurge("all")}><Trash2 />清空回收站</button></header><section className="trash-hero"><div><span className="eyebrow">RECYCLE ARCHIVE / 30 DAYS</span><h1>回收站</h1><p>被删除的岗位、简历档案、筛选任务和单次结果会在这里保留 30 天。</p></div><ArchiveRestore /></section><section className="trash-content"><div className="retrieval-bar"><div className="retrieval-title"><ListFilter /><span>回收检索</span><small>{query.data?.total || 0} 项</small></div><div className="search-box"><Search /><input aria-label="搜索回收站" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="搜索名称或岗位" /></div><div className="retrieval-filters"><label><span>类型</span><select value={typeFilter} aria-label="回收站类型" onChange={(event) => { setTypeFilter(event.target.value); setPage(1); }}><option value="all">全部类型</option><option value="job">岗位</option><option value="resume">简历档案</option><option value="run">筛选任务</option><option value="evaluation">筛选结果</option></select></label></div></div><ErrorNotice error={query.error || restoreMutation.error || purgeMutation.error} />
    {query.isLoading ? <div className="loading-block"><LoaderCircle className="spin" />正在整理回收档案</div> : !query.data?.items.length ? <div className="empty-state"><ArchiveRestore /><h3>回收站是空的</h3><p>删除的内容会在这里保留 30 天。</p></div> : <div className="trash-list">{query.data.items.map((item) => <article key={`${item.item_type}-${item.id}`}><span className="trash-type">{trashLabels[item.item_type]}</span><div><strong>{item.name}</strong><p>{item.job_name}</p></div><div className="trash-date"><span>删除于 {dateLabel(item.deleted_at)}</span><small>将在 {screeningDateLabel(item.purge_at)} 清理</small></div><button className="button quiet" onClick={() => restoreMutation.mutate(item)}><ArchiveRestore />恢复</button><button className="icon-button" aria-label={`永久删除${item.name}`} onClick={() => setConfirmPurge(item)}><Trash2 /></button></article>)}</div>}
    {(query.data?.total_pages || 0) > 1 && <div className="pagination-bar"><span>共 {query.data?.total} 项</span><div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>第 {page} / {query.data?.total_pages} 页</span><button disabled={page >= (query.data?.total_pages || 1)} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div>}</section>
    {confirmPurge && <ConfirmDialog title={confirmPurge === "all" ? "清空整个回收站？" : `永久删除“${confirmPurge.name}”？`} description="该操作会物理删除数据库记录和简历文件，无法恢复。" confirmLabel="永久删除" danger busy={purgeMutation.isPending} onCancel={() => setConfirmPurge(null)} onConfirm={() => purgeMutation.mutate(confirmPurge)} />}
  </main>;
}

const poolGenderLabels: Record<string, string> = { male: "男", female: "女" };

function PoolDesk({ onBack }: { onBack: () => void }) {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const { data: jobs = [] } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });
  const { data: allItems = [], isLoading } = useQuery({
    queryKey: ["pool", "all"],
    queryFn: () => api.poolResumes("all"),
    refetchInterval: (query) => (query.state.data || []).some((item) => ["uploaded", "parsing"].includes(item.parse_status) || ["queued", "parsing", "evaluating"].includes(item.evaluation_status || "")) ? 1500 : false,
  });
  const [tab, setTab] = useState<"new" | "evaluated" | "all">("new");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targetJob, setTargetJob] = useState("");
  const [pasting, setPasting] = useState(false);
  const [textPreview, setTextPreview] = useState<Resume | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<PoolResume[] | null>(null);
  const [evalFilter, setEvalFilter] = useState<"any" | "yes" | "no">("any");
  const [scoreRange, setScoreRange] = useState({ min: "", max: "" });
  const [ageRange, setAgeRange] = useState({ min: "", max: "" });
  const [genderFilter, setGenderFilter] = useState<"any" | "male" | "female" | "unknown">("any");
  const [jobFilter, setJobFilter] = useState<Set<string>>(new Set());
  const [evalJobFilter, setEvalJobFilter] = useState<Set<string>>(new Set());
  const poolItems = allItems.filter((item) => item.source === "email" || !item.job_id);
  const newItems = poolItems.filter((item) => !item.evaluation);
  const evaluatedItems = poolItems.filter((item) => item.evaluation);
  const evaluatedJobOptions = useMemo(() => {
    const seen = new Map<string, string>();
    allItems.forEach((item) => (item.evaluated_jobs || []).forEach((entry) => seen.set(entry.job_id, entry.job_name)));
    return [...seen.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  }, [allItems]);
  const allFiltered = useMemo(() => {
    const minScore = scoreRange.min === "" ? null : Number(scoreRange.min);
    const maxScore = scoreRange.max === "" ? null : Number(scoreRange.max);
    const minAge = ageRange.min === "" ? null : Number(ageRange.min);
    const maxAge = ageRange.max === "" ? null : Number(ageRange.max);
    return allItems.filter((item) => {
      const evaluation = item.evaluation;
      if (evalFilter === "yes" && !evaluation) return false;
      if (evalFilter === "no" && evaluation) return false;
      const score = evaluation?.overall_score;
      if ((minScore !== null || maxScore !== null) && (score === null || score === undefined)) return false;
      if (minScore !== null && (score ?? 0) < minScore) return false;
      if (maxScore !== null && (score ?? 0) > maxScore) return false;
      const age = evaluation?.age;
      if ((minAge !== null || maxAge !== null) && (age === null || age === undefined)) return false;
      if (minAge !== null && (age ?? 0) < minAge) return false;
      if (maxAge !== null && (age ?? 0) > maxAge) return false;
      if (genderFilter === "unknown" && evaluation?.gender) return false;
      if ((genderFilter === "male" || genderFilter === "female") && evaluation?.gender !== genderFilter) return false;
      if (jobFilter.size > 0 && !(item.job_id ? jobFilter.has(item.job_id) : jobFilter.has("none"))) return false;
      if (evalJobFilter.size > 0 && !(item.evaluated_jobs || []).some((entry) => evalJobFilter.has(entry.job_id))) return false;
      return true;
    });
  }, [allItems, evalFilter, scoreRange, ageRange, genderFilter, jobFilter, evalJobFilter]);
  const shown = tab === "new" ? newItems : tab === "evaluated" ? evaluatedItems : allFiltered;
  const invalidate = () => { queryClient.invalidateQueries({ queryKey: ["pool"] }); queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.invalidateQueries({ queryKey: ["job"] }); };
  const switchTab = (next: "new" | "evaluated" | "all") => { setTab(next); setSelected(new Set()); };
  const toggleAll = () => setSelected((current) => { const next = new Set(current); if (shown.length && shown.every((item) => selected.has(item.id))) shown.forEach((item) => next.delete(item.id)); else shown.forEach((item) => next.add(item.id)); return next; });
  const toggleFilter = (current: Set<string>, id: string) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next; };
  const selectedItems = allItems.filter((item) => selected.has(item.id));
  const allParsed = selectedItems.length > 0 && selectedItems.every((item) => item.parse_status === "ready");
  const assignMutation = useMutation({
    mutationFn: (evaluate: boolean) => api.assignPool({ resume_ids: [...selected], job_id: targetJob, evaluate }),
    onSuccess: (result) => { const message = `已分配 ${result.assigned} 份到岗位${result.run ? "，筛选任务已启动" : ""}`; const skipped = result.skipped.length ? `；跳过 ${result.skipped.length} 份` : ""; notify(message + skipped, result.skipped.length ? "info" : "success"); setSelected(new Set()); invalidate(); },
  });
  const deleteMutation = useMutation({
    mutationFn: async (rows: PoolResume[]) => { for (const row of rows) await api.deleteResume(row.id); },
    onSuccess: () => { notify("简历档案已移入回收站"); setConfirmDelete(null); setSelected(new Set()); invalidate(); },
  });
  const busy = assignMutation.isPending || deleteMutation.isPending;
  const emptyTitle = tab === "new" ? "还没有新收取的简历" : tab === "evaluated" ? "还没有已评估的简历" : "没有符合筛选条件的简历";
  const emptyHint = tab === "new" ? "邮箱捕获的 PDF/DOCX 简历会自动出现在这里。" : tab === "evaluated" ? "在「新收取」中选择简历，分配岗位并评估后即可在这里追踪。" : "调整上方的筛选条件，或到岗位案卷中上传简历。";
  return <main className="trash-shell"><header className="topbar"><button className="back-link dark" onClick={onBack}><ArrowLeft />全部岗位</button><div className="brand-copy"><strong>PsychicFlow</strong><small>简历池</small></div><div className="topbar-actions"><button className="button quiet" type="button" onClick={() => setPasting(true)}><ClipboardPaste size={15} />粘贴文本</button></div></header><section className="trash-hero pool-hero"><div><span className="eyebrow">EMAIL INTAKE / INBOX</span><h1>简历池</h1><p>邮箱收取的简历先汇集在这里；选择后分配所属岗位，再统一评估。「所有简历」汇总全库档案，可按评估、分数、岗位、年龄与性别筛选。</p></div><Inbox /></section><section className="trash-content">
    <div className="pool-tabs"><button className={`button ${tab === "new" ? "primary" : "quiet"}`} onClick={() => switchTab("new")}>新收取 {newItems.length}</button><button className={`button ${tab === "evaluated" ? "primary" : "quiet"}`} onClick={() => switchTab("evaluated")}>已评估 {evaluatedItems.length}</button><button className={`button ${tab === "all" ? "primary" : "quiet"}`} onClick={() => switchTab("all")}>所有简历 {allItems.length}</button></div>
    {tab === "all" && <div className="pool-filters">
      <label><span>是否评估</span><select aria-label="是否评估" value={evalFilter} onChange={(event) => setEvalFilter(event.target.value as typeof evalFilter)}><option value="any">全部</option><option value="yes">已评估</option><option value="no">未评估</option></select></label>
      <label><span>评估分数</span><div className="range-pair"><input aria-label="最低分" type="number" min={0} max={100} placeholder="最低" value={scoreRange.min} onChange={(event) => setScoreRange((range) => ({ ...range, min: event.target.value }))} /><em>–</em><input aria-label="最高分" type="number" min={0} max={100} placeholder="最高" value={scoreRange.max} onChange={(event) => setScoreRange((range) => ({ ...range, max: event.target.value }))} /></div></label>
      <label><span>年龄</span><div className="range-pair"><input aria-label="最小年龄" type="number" min={0} max={120} placeholder="最小" value={ageRange.min} onChange={(event) => setAgeRange((range) => ({ ...range, min: event.target.value }))} /><em>–</em><input aria-label="最大年龄" type="number" min={0} max={120} placeholder="最大" value={ageRange.max} onChange={(event) => setAgeRange((range) => ({ ...range, max: event.target.value }))} /></div></label>
      <label><span>性别</span><select aria-label="性别" value={genderFilter} onChange={(event) => setGenderFilter(event.target.value as typeof genderFilter)}><option value="any">全部</option><option value="male">男</option><option value="female">女</option><option value="unknown">未知</option></select></label>
      <div className="chip-filter"><span>所属岗位（可多选）</span><div>{jobs.map((job) => <button key={job.id} type="button" className={`chip ${jobFilter.has(job.id) ? "active" : ""}`} onClick={() => setJobFilter((current) => toggleFilter(current, job.id))}>{job.name}</button>)}<button type="button" className={`chip ${jobFilter.has("none") ? "active" : ""}`} onClick={() => setJobFilter((current) => toggleFilter(current, "none"))}>未分配</button></div></div>
      <div className="chip-filter"><span>已评估岗位（可多选）</span><div>{evaluatedJobOptions.length === 0 ? <small>暂无可选岗位</small> : evaluatedJobOptions.map((option) => <button key={option.id} type="button" className={`chip ${evalJobFilter.has(option.id) ? "active" : ""}`} onClick={() => setEvalJobFilter((current) => toggleFilter(current, option.id))}>{option.name}</button>)}</div></div>
    </div>}
    <ErrorNotice error={assignMutation.error || deleteMutation.error} />
    {selected.size > 0 && <div className="bulk-toolbar pool-bulk"><strong>已选 {selected.size} 份简历</strong>{tab !== "evaluated" && <><label className="pool-job-select"><span>分配到</span><select aria-label="选择目标岗位" value={targetJob} onChange={(event) => setTargetJob(event.target.value)}><option value="">选择岗位…</option>{jobs.map((job) => <option key={job.id} value={job.id}>{job.name}</option>)}</select></label><button className="button primary" disabled={!targetJob || !allParsed || busy} onClick={() => assignMutation.mutate(true)}><Sparkles size={15} />分配并评估</button><button className="button quiet" disabled={!targetJob || busy} onClick={() => assignMutation.mutate(false)}>仅分配</button></>}<button className="button quiet" disabled={busy} onClick={() => setConfirmDelete(selectedItems)}><Trash2 size={15} />移入回收站</button><button className="button quiet" onClick={() => setSelected(new Set())}>取消选择</button></div>}
    {tab !== "evaluated" && selected.size > 0 && !allParsed && <div className="cost-strip"><CircleAlert size={16} /><span>部分简历仍在解析，请等全部变为「文本就绪」后再启动评估。</span></div>}
    {isLoading ? <div className="loading-block"><LoaderCircle className="spin" />正在整理简历池</div> : shown.length === 0 ? <div className="empty-state"><Inbox /><h3>{emptyTitle}</h3><p>{emptyHint}</p></div> : <div className="resume-table"><div className="table-row table-head"><label><input type="checkbox" aria-label="选择当前筛选结果" checked={shown.length > 0 && shown.every((item) => selected.has(item.id))} onChange={toggleAll} /></label><span>文件</span>{tab === "all" ? <><span>性别 / 年龄</span><span>所属岗位</span><span>评估分数</span><span>收取时间</span></> : tab === "new" ? <><span>格式 / 大小</span><span>解析状态</span><span>收取时间</span></> : <><span>格式 / 大小</span><span>所属岗位</span><span>评估结果</span></>}<span>操作</span></div>{shown.map((item) => { const name = item.display_name || item.original_name; return <div className="table-row" key={item.id}><label><input type="checkbox" aria-label={`选择${name}`} checked={selected.has(item.id)} onChange={() => setSelected((current) => { const next = new Set(current); next.has(item.id) ? next.delete(item.id) : next.add(item.id); return next; })} /></label><div className="file-name"><FileText size={17} /><div><strong>{name}</strong><small className="pool-source">{item.source === "email" ? `邮箱收取${item.source_title ? ` · 职位：${item.source_title}` : ""}` : item.source === "paste" ? "粘贴文本" : "手动上传"}{tab === "all" ? ` · ${item.original_name.split(".").pop()?.toUpperCase()} · ${sizeLabel(item.size_bytes)}` : ""}</small></div></div>{tab === "all" ? <><span>{item.evaluation?.gender ? poolGenderLabels[item.evaluation.gender] : "—"} · {item.evaluation?.age ? `${item.evaluation.age} 岁` : "—"}</span><span className="cell-stack">{item.job_name || "未分配"}{(item.evaluated_jobs || []).length > 0 && <small>已评估：{(item.evaluated_jobs || []).map((entry) => entry.job_name).join("、")}</small>}</span><span className={`score-chip ${item.evaluation?.match_level || "pending"}`}>{item.evaluation ? `${item.evaluation.overall_score ?? "—"} 分` : "未评估"}</span><span>{dateLabel(item.created_at)}</span></> : <><span>{item.original_name.split(".").pop()?.toUpperCase()} · {sizeLabel(item.size_bytes)}{item.page_count ? ` · ${item.page_count} 页` : ""}</span>{tab === "new" ? <><span className={`status-dot ${item.parse_status}`} title={item.parse_error || undefined}>{item.parse_status === "ready" ? "文本就绪" : item.parse_status === "failed" ? "解析失败" : item.parse_status === "parsing" ? "解析中" : "待解析"}</span><span>{dateLabel(item.created_at)}</span></> : <><span>{item.job_name || "未分配"}</span><span className={`score-chip ${item.evaluation?.match_level || "pending"}`}>{item.evaluation?.overall_score ?? "—"} 分</span><span>{dateLabel(item.created_at)}</span></>}</>}<div className="row-actions">{item.parse_status === "ready" && <button className="text-action" onClick={() => setTextPreview(item)} aria-label={`查看${name}文本`}>查看文本</button>}<a className="text-action" href={`/api/resumes/${item.id}/preview`} target="_blank" rel="noreferrer">预览原件</a></div></div>; })}</div>}
    {textPreview && <ResumeTextDialog resume={textPreview} onClose={() => setTextPreview(null)} onSaved={invalidate} />}
    {pasting && <PasteResumeDialog submit={api.pastePoolResume} createdMessage="简历已录入简历池，候选人身份将自动提取" onClose={() => setPasting(false)} onCreated={invalidate} />}
    {confirmDelete && <ConfirmDialog title={`删除 ${confirmDelete.length} 份简历档案？`} description="原件、解析文本和关联筛选结果会一起移入回收站，30 天内可以恢复。" confirmLabel="移入回收站" danger busy={deleteMutation.isPending} onCancel={() => setConfirmDelete(null)} onConfirm={() => deleteMutation.mutate(confirmDelete)} />}
  </section></main>;
}

function JobWorkspace({ jobId, onBack, onTrash }: { jobId: string; onBack: () => void; onTrash: () => void }) {
  const queryClient = useQueryClient();
  const { data: job, error, isLoading } = useQuery({ queryKey: ["job", jobId], queryFn: () => api.job(jobId) });
  const [tab, setTab] = useState<"resumes" | "results" | "tasks" | "settings">("resumes");
  const [newRunId, setNewRunId] = useState<string>();
  if (isLoading) return <div className="full-loading"><LoaderCircle className="spin" />正在打开岗位案卷</div>;
  if (!job) return <main className="landing-shell"><ErrorNotice error={error} /><button className="button quiet" onClick={onBack}>返回</button></main>;
  return <main className="workspace-shell"><aside className="workspace-rail"><button className="back-link" onClick={onBack}><ArrowLeft size={16} />全部岗位</button><div className="rail-title"><span className="eyebrow">ACTIVE DOSSIER</span><h1>{job.name}</h1><p>{job.resume_count} 份简历 · {job.run_count} 次筛选</p></div><nav><button className={tab === "resumes" ? "active" : ""} onClick={() => setTab("resumes")}><FileText />简历材料</button><button className={tab === "results" ? "active" : ""} onClick={() => setTab("results")}><Search />筛选结果</button><button className={tab === "tasks" ? "active" : ""} onClick={() => setTab("tasks")}><ClipboardList />任务中心</button><button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}><Settings2 />岗位标准</button><button onClick={onTrash}><Trash2 />回收站</button></nav><div className="rail-footer"><Sparkles /><p>模型提供分析证据<br /><strong>你保留最终判断</strong></p></div></aside><div className="workspace-main">{tab === "resumes" && <ResumeDesk job={job} onRunCreated={(run) => { setNewRunId(run.id); setTab("tasks"); queryClient.invalidateQueries({ queryKey: ["runs", job.id] }); queryClient.invalidateQueries({ queryKey: ["job", job.id] }); }} />}{tab === "results" && <ResultsDesk jobId={job.id} initialRunId={newRunId} />}{tab === "tasks" && <TasksDesk jobId={job.id} onOpenResults={() => setTab("results")} />}{tab === "settings" && <SettingsDesk job={job} onUpdated={(updated) => { queryClient.setQueryData(["job", job.id], updated); queryClient.invalidateQueries({ queryKey: ["jobs"] }); }} onDeleted={() => { queryClient.invalidateQueries({ queryKey: ["jobs"] }); onBack(); }} />}</div></main>;
}

export default function App() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [poolOpen, setPoolOpen] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const [notices, setNotices] = useState<{ id: number; message: string; kind: NoticeKind }[]>([]);
  const notify: Notify = (message, kind = "success") => { const id = Date.now() + Math.random(); setNotices((items) => [...items, { id, message, kind }]); window.setTimeout(() => setNotices((items) => items.filter((item) => item.id !== id)), 3500); };
  return <ToastContext.Provider value={notify}>{poolOpen ? <PoolDesk onBack={() => setPoolOpen(false)} /> : trashOpen ? <TrashDesk onBack={() => setTrashOpen(false)} /> : jobId ? <JobWorkspace jobId={jobId} onBack={() => setJobId(null)} onTrash={() => setTrashOpen(true)} /> : <JobList onOpen={setJobId} onTrash={() => setTrashOpen(true)} onPool={() => setPoolOpen(true)} />}<div className="toast-stack" aria-live="polite">{notices.map((notice) => <div className={`toast ${notice.kind}`} key={notice.id}>{notice.kind === "success" ? <Check /> : <CircleAlert />}<span>{notice.message}</span><button aria-label="关闭通知" onClick={() => setNotices((items) => items.filter((item) => item.id !== notice.id))}><X /></button></div>)}</div></ToastContext.Provider>;
}
