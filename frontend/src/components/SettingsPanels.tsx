import { FormEvent, useEffect, useState } from "react";
import axios from "axios";

const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || "/api", timeout: 30000 });
api.interceptors.request.use(config => {
  const token = localStorage.getItem("admin_token");
  if (token) config.headers.set("X-Admin-Token", token);
  return config;
});

function errorText(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(item => item.msg).join("；");
    return error.message;
  }
  return error instanceof Error ? error.message : "请求失败";
}

type DownloadOptions = {
  concurrent_downloads: number;
  speed_limit_kib: number;
  idle_timeout_seconds: number;
  task_timeout_seconds: number;
  max_retries: number;
  retry_delay_seconds: number;
};

const fields: { key: keyof DownloadOptions; title: string; unit: string; min: number; max: number; hint: string }[] = [
  { key: "concurrent_downloads", title: "并行任务数", unit: "个任务", min: 1, max: 32, hint: "Bot 与频道规则共享并发上限。降低后，正在下载的任务继续，队列等待空位。" },
  { key: "speed_limit_kib", title: "每任务速度上限", unit: "KiB/s", min: 0, max: 1048576, hint: "0 表示不限速。多个任务分别限速，总速度约为各任务之和。" },
  { key: "idle_timeout_seconds", title: "无进度超时", unit: "秒", min: 5, max: 3600, hint: "连续没有新增下载字节时，取消当前尝试并重试。" },
  { key: "task_timeout_seconds", title: "单次下载总超时", unit: "秒", min: 0, max: 604800, hint: "0 表示不限制总时长；无进度超时仍然生效。" },
  { key: "max_retries", title: "失败重试次数", unit: "次", min: 0, max: 10, hint: "0 表示不重试。重试会从文件开头重新下载，耗尽后标记失败并让出队列。" },
  { key: "retry_delay_seconds", title: "重试等待时间", unit: "秒", min: 0, max: 300, hint: "Telegram 要求的限流等待时间优先于此设置。" },
];

export function DownloadSettingsPanel() {
  const [values, setValues] = useState<DownloadOptions | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try { setValues((await api.get("/download-settings")).data); }
    catch (error) { setError(errorText(error)); }
  };
  useEffect(() => { void load(); }, []);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      setValues((await api.put("/download-settings", values)).data);
      setNotice("下载设置已保存。并发上限立即用于队列，速度与超时用于新开始的任务。");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  };
  return <div className="card settings-content-card">
    <div className="settings-section-heading"><span className="settings-eyebrow">DOWNLOADS</span><h2>下载设置</h2><p>为所有下载设置明确的资源上限与恢复策略。</p></div>
    {error && <p className="settings-error" role="alert">{error} <button type="button" onClick={load}>重新加载</button></p>}
    {!values ? <p role="status">正在加载下载设置…</p> : <form onSubmit={save}>
      <div className="settings-field-grid">{fields.map(field => <label key={field.key}>
        <span>{field.title}<span className="settings-unit">{field.unit}</span></span>
        <input type="number" required min={field.min} max={field.max} step="1" value={values[field.key]} onChange={e => setValues({ ...values, [field.key]: e.target.value === "" ? "" : Number(e.target.value) } as DownloadOptions)} />
        <small>{field.hint}</small>
      </label>)}</div>
      <div className="settings-note">建议先使用 3–5 个并行任务和 90 秒无进度超时。卡住的任务会先停止旧下载，再重试，避免重复占用连接。</div>
      <div className="settings-actions"><button className="btn-primary" disabled={busy}>{busy ? "保存中…" : "保存下载设置"}</button></div>
      {notice && <p role="status" className="settings-success">{notice}</p>}
    </form>}
  </div>;
}

type UpdateState = {
  current: string; channel: string;
  settings: { check_enabled: boolean; auto_update: boolean; experience_program: boolean; check_interval_hours: number };
  result: { target?: string; available?: boolean; error?: string; checked_at?: number; release_url?: string };
  job: { status?: string; message?: string; target?: string };
  capability: { supported: boolean; reason?: string };
};

const activeJobs = ["scheduled", "downloading", "restarting"];

export function UpdateSettingsPanel() {
  const [state, setState] = useState<UpdateState | null>(null);
  const [draft, setDraft] = useState<UpdateState["settings"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = async () => {
    setError("");
    try {
      const { data } = await api.get<UpdateState>("/update");
      setState(data); setDraft(data.settings);
    } catch (error) { setError(errorText(error)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  const running = activeJobs.includes(state?.job.status || "");
  useEffect(() => {
    if (!running) return;
    let stopped = false;
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (stopped) return;
      try {
        const { data } = await api.get<UpdateState>("/update");
        if (stopped) return;
        setState(data);
        if (!activeJobs.includes(data.job.status || "")) {
          setNotice(data.job.message || "更新任务已结束");
          return;
        }
      } catch (error) {
        if (stopped) return;
        if (axios.isAxiosError(error) && error.response?.status === 401) {
          setError("服务已重启，面板会话已过期。请重新登录后查看更新结果。");
          return;
        }
      }
      if (Date.now() - started >= 15 * 60 * 1000) { setError("尚未确认更新结果，请刷新状态或查看容器日志。"); return; }
      timer = setTimeout(poll, 2500);
    };
    timer = setTimeout(poll, 2500);
    return () => { stopped = true; clearTimeout(timer); };
  }, [running]);
  const action = async (kind: "check" | "apply" | "save") => {
    setBusy(true); setError(""); setNotice("");
    try {
      if (kind === "save") {
        const { data } = await api.put<UpdateState>("/update/settings", draft);
        setState(data); setDraft(data.settings); setNotice("更新偏好已保存。");
      } else if (kind === "check") {
        const { data } = await api.post("/update/check", {}, { timeout: 60000 });
        setState(current => current ? { ...current, result: data } : current);
        if (!data.error) setNotice(data.available ? "发现可用更新，请查看版本后开始更新。" : "当前已是所选通道的最新版本。");
      } else {
        const { data } = await api.post("/update/apply", {}, { timeout: 60000 });
        if (data.status === "scheduled") setState(current => current ? { ...current, job: data } : current);
        else setNotice("当前已是最新版本。");
      }
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  };
  const dirty = draft && state && JSON.stringify(draft) !== JSON.stringify(state.settings);
  return <div className="card settings-content-card">
    <div className="settings-section-heading"><span className="settings-eyebrow">VERSION & UPDATES</span><h2>版本与更新</h2><p>统一检查前后端版本，下载完整应用包后校验并重启。</p></div>
    {loading && <p role="status">正在读取更新状态…</p>}
    {error && <p className="settings-error" role="alert">{error} {error.includes("重新登录") && <a href="/login">前往登录</a>}</p>}
    {state && draft && <>
      <div className="settings-version-grid"><div><small>运行版本</small><strong>{state.current}</strong><span>{state.current.startsWith("bata.") ? "体验版" : "正式版"}</span></div><div><small>可用版本</small><strong>{state.result.target || "尚未检查"}</strong><span>{state.result.error ? "检查失败" : state.result.available ? "可更新" : state.result.target ? "已是最新" : "点击下方检查"}</span></div></div>
      {state.result.error && <p className="settings-error" role="alert">{state.result.error}</p>}
      {state.result.checked_at && <p className="settings-muted">上次检查：{new Date(state.result.checked_at * 1000).toLocaleString()}</p>}
      <form onSubmit={event => { event.preventDefault(); void action("save"); }}>
        <div className="settings-field-grid">
          <label>更新通道<select value={draft.experience_program ? "bata" : "stable"} onChange={e => setDraft({ ...draft, experience_program: e.target.value === "bata" })}><option value="stable">正式版 · stable</option><option value="bata">体验版 · bata</option></select><small>切换通道后先保存，再检查版本；保存偏好不会立即安装。</small></label>
          <label>检查间隔（小时）<input type="number" required min={1} max={168} value={draft.check_interval_hours} onChange={e => setDraft({ ...draft, check_interval_hours: e.target.value === "" ? "" as unknown as number : Number(e.target.value) })} /></label>
        </div>
        <label className="settings-switch"><input type="checkbox" checked={draft.check_enabled} onChange={e => setDraft({ ...draft, check_enabled: e.target.checked, auto_update: e.target.checked && draft.auto_update })} /><span>自动检查更新<small>按设定间隔检查所选通道。</small></span></label>
        <label className="settings-switch"><input type="checkbox" checked={draft.auto_update} onChange={e => setDraft({ ...draft, auto_update: e.target.checked, check_enabled: e.target.checked || draft.check_enabled })} /><span>自动安装更新<small>发现新版本时自动下载，服务会短暂重启。</small></span></label>
        <div className="settings-actions"><button className="btn-secondary" disabled={busy || running}>保存更新偏好</button><button type="button" className="btn-secondary" disabled={busy || running || !!dirty} onClick={() => void action("check")}>{busy ? "处理中…" : "检查更新"}</button><button type="button" className="btn-primary" disabled={busy || running || !!dirty || !state.capability.supported || !state.result.available || !!state.result.error} onClick={() => void action("apply")}>{running ? "更新进行中…" : "安装更新"}</button></div>
      </form>
      {dirty && <p className="settings-muted">请先保存更新偏好，再检查更新。</p>}
      {!state.capability.supported && <div className="settings-note">{state.capability.reason}<p>Docker 部署可在宿主机运行：</p><code>docker compose pull &amp;&amp; docker compose up -d</code></div>}
      {state.job.message && <p role="status" className="settings-note">{state.job.message}</p>}
    </>}
    {notice && <p className="settings-success" role="status">{notice}</p>}
    <div className="settings-actions"><button type="button" className="btn-secondary" disabled={busy || running} onClick={refresh}>刷新状态</button></div>
  </div>;
}
