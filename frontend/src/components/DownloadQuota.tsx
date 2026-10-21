import { quotaDraft, type QuotaDraft, type QuotaUsage } from "../utils/downloadQuota";

type Props = {
  enabled: boolean;
  limits: QuotaDraft[];
  onEnabled: (value: boolean) => void;
  onLimits: (value: QuotaDraft[]) => void;
};

export function DownloadQuotaEditor({ enabled, limits, onEnabled, onLimits }: Props) {
  const update = (index: number, patch: Partial<QuotaDraft>) => onLimits(limits.map((limit, i) => i === index ? { ...limit, ...patch } : limit));
  return <fieldset className="quota-editor">
    <legend>下载限额</legend>
    <label className="quota-toggle"><input type="checkbox" checked={enabled} onChange={event => {
      onEnabled(event.target.checked);
      if (event.target.checked && !limits.length) onLimits([quotaDraft()]);
    }} />启用下载容量限额</label>
    <p className="quota-help">按完整文件大小计算，额度不足时整个文件等待。多个限额同时生效，时区为北京时间（Asia/Shanghai）。</p>
    {enabled && <>
      {limits.map((limit, index) => <div key={limit.id ?? `new-${index}`} className="quota-limit-row">
        <label>周期<select aria-label={`限额 ${index + 1} 周期`} value={limit.period}
          onChange={event => update(index, { period: event.target.value as QuotaDraft["period"] })}>
          <option value="hour">每小时</option><option value="day">每天</option>
          <option value="week">每周</option><option value="month">每月</option><option value="custom">自定义周期</option>
        </select></label>
        {limit.period === "custom" && <label>每隔<div className="quota-input-pair">
          <input aria-label={`限额 ${index + 1} 周期间隔`} type="number" min="1" max="100000" step="1" value={limit.custom_value || ""}
            onChange={event => update(index, { custom_value: event.target.value })} />
          <select aria-label={`限额 ${index + 1} 间隔单位`} value={limit.custom_unit || "hours"}
            onChange={event => update(index, { custom_unit: event.target.value as "hours" | "days" })}>
            <option value="hours">小时</option><option value="days">天</option>
          </select></div></label>}
        <label>下载容量<div className="quota-input-pair">
          <input aria-label={`限额 ${index + 1} 容量`} type="number" min="0" step="any" value={limit.value}
            onChange={event => update(index, { value: event.target.value })} />
          <select aria-label={`限额 ${index + 1} 容量单位`} value={limit.unit}
            onChange={event => update(index, { unit: event.target.value as QuotaDraft["unit"] })}>
            <option>MB</option><option>GB</option><option>TB</option>
          </select></div></label>
        <button type="button" className="quota-remove" aria-label={`移除限额 ${index + 1}`} onClick={() => onLimits(limits.filter((_, i) => i !== index))}>移除</button>
      </div>)}
      <button type="button" className="quota-add" onClick={() => onLimits([...limits, quotaDraft()])}>＋ 添加限额</button>
      <p className="quota-help">自然周期于整点、零点、周一或月初恢复；自定义周期从首次保存起计算。1 GB = 1024 MB。</p>
    </>}
  </fieldset>;
}

function capacity(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
}

export function DownloadQuotaUsage({ usage, timezone = "Asia/Shanghai" }: { usage: QuotaUsage[]; timezone?: string }) {
  return <div className="quota-usage" aria-label="下载限额用量">
    <strong>下载限额 <small>（{timezone}）</small></strong>
    {usage.map(limit => <div key={limit.id} className="quota-usage-row">
      <div><strong>{limit.period_label} {capacity(limit.limit_bytes)}</strong><span>剩余 {capacity(limit.remaining_bytes)}</span></div>
      <progress max={limit.limit_bytes} value={Math.min(limit.limit_bytes, limit.used_bytes + limit.reserved_bytes)} aria-label={`${limit.period_label}额度使用情况`} />
      <div className="quota-help"><span>已用 {capacity(limit.used_bytes)} · 预占 {capacity(limit.reserved_bytes)}</span><span>恢复：{limit.next_reset_at_local}</span></div>
    </div>)}
  </div>;
}

export function DownloadWaitReason({ record }: { record: { status: string; queue_reason_text?: string; error?: string } }) {
  const reason = record.status === "queued" ? record.queue_reason_text : record.status === "failed" ? record.error : undefined;
  return reason ? <div className="quota-wait-reason" role="status">{reason}</div> : null;
}
