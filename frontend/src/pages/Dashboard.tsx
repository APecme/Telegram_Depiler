import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";

const api = axios.create({
  // 默认使用相对路径，始终请求当前站点下的 /api，避免端口写死为 8000
  baseURL: import.meta.env.VITE_API_URL || "/api",
});

api.interceptors.request.use((config: any) => {
  const token = localStorage.getItem("admin_token");
  if (token) {
    if (!config.headers) {
      config.headers = {};
    }
    config.headers["X-Admin-Token"] = token;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

// 添加响应拦截器处理401错误
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      // 401错误：清除token并提示重新登录
      localStorage.removeItem("admin_token");
      // 如果不在登录页，跳转到登录页
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

type DownloadRecord = {
  id: number;
  file_name: string;
  origin_file_name?: string;
  status: string;
  progress: number;
  download_speed?: number;
  created_at: string;
  error?: string;
  bot_username?: string;
  source?: string;
  file_size?: number;
  save_dir?: string;
  file_path?: string;
  rule_id?: number;
  rule_name?: string;
};

type GroupRule = {
  id: number;
  chat_id: number;
  chat_title?: string;
  rule_name?: string;
  mode: string;
  include_extensions?: string;
  min_size_bytes?: number;
  max_size_bytes?: number;
  size_range?: string;
  save_dir?: string;
  filename_template?: string;
  match_mode?: string;
  include_keywords?: string;
  exclude_keywords?: string;
  start_time?: string;
  end_time?: string;
  enabled: boolean;
  add_download_suffix?: boolean;
  move_after_complete?: boolean;
  auto_catch_up?: boolean;
  created_at: string;
};

type Dialog = {
  id: number;
  title: string;
  username?: string;
  is_group: boolean;
};

type LogEntry = {
  timestamp: string;
  level: string;
  message: string;
};

export default function Dashboard() {
  const [downloads, setDownloads] = useState<DownloadRecord[]>([]);
  const [groupRules, setGroupRules] = useState<GroupRule[]>([]);
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [showRuleModal, setShowRuleModal] = useState(false);
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isMobile, setIsMobile] = useState(false);
  // 下载记录筛选 & 分页
  const [downloadPage, setDownloadPage] = useState<number>(1);
  const [downloadPageSize, setDownloadPageSize] = useState<number>(20);
  const [downloadTotal, setDownloadTotal] = useState<number>(0);
  const [downloadStatusFilter, setDownloadStatusFilter] = useState<string>("all");
  const [downloadRuleFilter, setDownloadRuleFilter] = useState<number | "all">("all");
  const [downloadPathFilter, setDownloadPathFilter] = useState<string>("");
  const [downloadMinSize, setDownloadMinSize] = useState<string>(""); // MB
  const [downloadMaxSize, setDownloadMaxSize] = useState<string>(""); // MB
  const [downloadStartTime, setDownloadStartTime] = useState<string>(""); // datetime-local
  const [downloadEndTime, setDownloadEndTime] = useState<string>("");
  
  // 规则表单状态
  const [formChatId, setFormChatId] = useState<number | "">("");
  const [formRuleName, setFormRuleName] = useState("");
  const [formMode, setFormMode] = useState<"monitor" | "history">("monitor");
  const [formExtensions, setFormExtensions] = useState("mp4,mp3,jpg");
  const [formSizeRange, setFormSizeRange] = useState("0");
  const [formSaveDir, setFormSaveDir] = useState("");
  const [currentBrowsePath, setCurrentBrowsePath] = useState(""); // 当前浏览的路径（用于导航）
  const [formFilenameTemplate, setFormFilenameTemplate] = useState("{task_id}_{message_id}_{chat_title}");
  const [formMatchMode, setFormMatchMode] = useState<"all" | "include" | "exclude">("all");
  const [formIncludeKeywords, setFormIncludeKeywords] = useState("");
  const [formExcludeKeywords, setFormExcludeKeywords] = useState("");
  const [formAddDownloadSuffix, setFormAddDownloadSuffix] = useState(false);
  const [formMoveAfterComplete, setFormMoveAfterComplete] = useState(false);
  const [formAutoCatchUp, setFormAutoCatchUp] = useState(false);
  const [dirOptions, setDirOptions] = useState<string[]>([]);
  const [dirLoading, setDirLoading] = useState(false);
  const [notification, setNotification] = useState<{message: string; type: "success" | "error" | "info"} | null>(null);
  const [defaultDownloadPath, setDefaultDownloadPath] = useState<string>("");
  const [defaultFilenameTemplate, setDefaultFilenameTemplate] = useState<string>("{task_id}_{file_name}");
  const [showDefaultPathModal, setShowDefaultPathModal] = useState(false);
  const [showFilenameTemplateModal, setShowFilenameTemplateModal] = useState(false);
  const [selectedDefaultPath, setSelectedDefaultPath] = useState<string>("");

  useEffect(() => {
    fetchGroupRules();
    fetchDialogs();
    fetchLogs();
    fetchDefaultDownloadPath();
    fetchDefaultFilenameTemplate();
  }, []);

  useEffect(() => {
    fetchDownloads();
    const interval = setInterval(() => {
      fetchDownloads();
    }, 2000);
    return () => clearInterval(interval);
  }, [downloadPage, downloadPageSize, downloadStatusFilter, downloadRuleFilter, downloadPathFilter, downloadMinSize, downloadMaxSize, downloadStartTime, downloadEndTime]);

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(() => {
      fetchLogs();
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    const update = () => setIsMobile(mq.matches);
    update();
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", update);
      return () => mq.removeEventListener("change", update);
    }
    // @ts-expect-error older Safari
    mq.addListener(update);
    // @ts-expect-error older Safari
    return () => mq.removeListener(update);
  }, []);

  const fetchDefaultDownloadPath = async () => {
    try {
      const { data } = await api.get("/config/default-download-path");
      setDefaultDownloadPath(data.path || "");
    } catch (error) {
      console.error("Failed to fetch default download path:", error);
    }
  };

  const fetchDefaultFilenameTemplate = async () => {
    try {
      const { data } = await api.get("/config/default-filename-template");
      setDefaultFilenameTemplate(data.template || "{task_id}_{file_name}");
    } catch (error) {
      console.error("Failed to fetch default filename template:", error);
    }
  };

  // 显示通知
  const showNotification = (message: string, type: "success" | "error" | "info" = "info") => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 3000);
  };

  const fetchDownloads = async () => {
    try {
      const params: any = {
        page: downloadPage,
        page_size: downloadPageSize,
      };

      if (downloadStatusFilter && downloadStatusFilter !== "all") {
        params.status = downloadStatusFilter;
      }
      if (downloadRuleFilter !== "all") {
        params.rule_id = downloadRuleFilter;
      }
      if (downloadPathFilter.trim()) {
        params.save_dir = downloadPathFilter.trim();
      }
      if (downloadMinSize.trim()) {
        const v = Number(downloadMinSize.trim());
        if (!Number.isNaN(v) && v >= 0) {
          params.min_size_mb = v;
        }
      }
      if (downloadMaxSize.trim()) {
        const v = Number(downloadMaxSize.trim());
        if (!Number.isNaN(v) && v >= 0) {
          params.max_size_mb = v;
        }
      }
      if (downloadStartTime) {
        params.start_time = downloadStartTime.replace("T", " ") + ":00";
      }
      if (downloadEndTime) {
        params.end_time = downloadEndTime.replace("T", " ") + ":59";
      }

      const { data } = await api.get("/downloads", { params });
      setDownloads(data.items || []);
      setDownloadTotal(data.total || 0);
    } catch (error) {
      console.error("Failed to fetch downloads:", error);
    }
  };

  const fetchGroupRules = async () => {
    try {
      const { data } = await api.get("/group-rules");
      setGroupRules(data.items || []);
    } catch (error) {
      console.error("Failed to fetch group rules:", error);
    }
  };

  const fetchDialogs = async () => {
    try {
      const { data } = await api.get("/dialogs");
      setDialogs(data.items || []);
    } catch (error) {
      console.error("Failed to fetch dialogs:", error);
    }
  };

  const fetchLogs = async () => {
    try {
      const { data } = await api.get("/logs?limit=50");
      if (data.logs) {
        setLogs(data.logs);
      }
    } catch (error) {
      // 日志接口可能不存在，静默失败
    }
  };

  const fetchDirectories = async (basePath: string = "") => {
    try {
      setDirLoading(true);
      const { data } = await api.get(`/fs/dirs?base=${encodeURIComponent(basePath)}`);
      const items: string[] = data.items || [];
      setDirOptions(items);
    } catch (error: any) {
      console.error("Failed to fetch directories:", error);
      // 401错误由响应拦截器统一处理，这里只设置空列表
      setDirOptions([]);
    } finally {
      setDirLoading(false);
    }
  };

  const handleCreateDirectory = async () => {
    const name = window.prompt("输入新建文件夹名称：");
    if (!name) return;
    try {
      const parent_path = currentBrowsePath || "";
      const { data } = await api.post("/fs/dirs", { parent_path, name });
      await fetchDirectories(currentBrowsePath);
      if (data.path) {
        setFormSaveDir(data.path);
      }
      showNotification("已创建文件夹", "success");
    } catch (error) {
      console.error("Failed to create directory:", error);
      showNotification("创建文件夹失败", "error");
    }
  };

  const handleRenameDirectory = async () => {
    if (!formSaveDir) {
      showNotification("请选择要重命名的文件夹", "info");
      return;
    }
    const newName = window.prompt("输入新的文件夹名称：", formSaveDir.split("/").pop() || "");
    if (!newName) return;
    try {
      const { data } = await api.put("/fs/dirs/rename", { path: formSaveDir, new_name: newName });
      await fetchDirectories(currentBrowsePath);
      if (data.path) {
        setFormSaveDir(data.path);
      }
      showNotification("已重命名文件夹", "success");
    } catch (error) {
      console.error("Failed to rename directory:", error);
      showNotification("重命名失败", "error");
    }
  };

  useEffect(() => {
    if (showRuleModal) {
      fetchDirectories("");
    }
  }, [showRuleModal]);

  const handlePauseDownload = async (downloadId: number) => {
    try {
      await api.post(`/downloads/${downloadId}/pause`);
      await fetchDownloads();
      showNotification("已暂停下载", "success");
    } catch (error) {
      console.error("Failed to pause download:", error);
      showNotification("暂停失败", "error");
    }
  };

  const handlePriorityDownload = async (downloadId: number) => {
    try {
      await api.post(`/downloads/${downloadId}/priority`);
      await fetchDownloads();
      showNotification("已设置优先级", "success");
    } catch (error) {
      console.error("Failed to set priority:", error);
      showNotification("设置优先级失败", "error");
    }
  };

  const handleResumeDownload = async (downloadId: number) => {
    try {
      await api.post(`/downloads/${downloadId}/resume`);
      await fetchDownloads();
      showNotification("已恢复下载", "success");
    } catch (error) {
      console.error("Failed to resume download:", error);
      showNotification("恢复失败", "error");
    }
  };

  const handleRetryDownload = async (downloadId: number) => {
    try {
      const { data } = await api.post(`/downloads/${downloadId}/retry`);
      await fetchDownloads();
      showNotification(data?.message || "已提交重试", "success");
    } catch (error) {
      console.error("Failed to retry download:", error);
      showNotification("重试失败", "error");
    }
  };

  const handlePauseAll = async () => {
    const activeDownloads = downloads.filter(
      (d: DownloadRecord) => d.status === "downloading" || d.status === "queued"
    );
    if (activeDownloads.length === 0) {
      showNotification("没有可暂停的任务", "info");
      return;
    }
    try {
      let successCount = 0;
      for (const download of activeDownloads) {
        try {
          await api.post(`/downloads/${download.id}/pause`);
          successCount++;
        } catch (error) {
          console.error(`Failed to pause download ${download.id}:`, error);
        }
      }
      await fetchDownloads();
      showNotification(`已暂停 ${successCount} 个任务`, "success");
    } catch (error) {
      console.error("Failed to pause all downloads:", error);
      showNotification("批量暂停失败", "error");
    }
  };

  const handleResumeAll = async () => {
    const pausedDownloads = downloads.filter((d: DownloadRecord) => d.status === "paused");
    if (pausedDownloads.length === 0) {
      showNotification("没有可恢复的任务", "info");
      return;
    }
    try {
      let successCount = 0;
      for (const download of pausedDownloads) {
        try {
          await api.post(`/downloads/${download.id}/resume`);
          successCount++;
        } catch (error) {
          console.error(`Failed to resume download ${download.id}:`, error);
        }
      }
      await fetchDownloads();
      showNotification(`已恢复 ${successCount} 个任务`, "success");
    } catch (error) {
      console.error("Failed to resume all downloads:", error);
      showNotification("批量恢复失败", "error");
    }
  };

  const handleDeleteDownload = async (downloadId: number, deleteFile: boolean) => {
    if (!window.confirm(deleteFile ? "确定删除记录并删除文件吗？" : "确定仅删除记录吗？")) {
      return;
    }
    try {
      await api.delete(`/downloads/${downloadId}?delete_file=${deleteFile ? "true" : "false"}`);
      await fetchDownloads();
      showNotification(deleteFile ? "已删除记录并删除文件" : "已删除记录", "success");
    } catch (error) {
      console.error("Failed to delete download:", error);
      showNotification("删除失败", "error");
    }
  };

  const bulkDelete = async (deleteFile: boolean) => {
    if (selectedIds.length === 0) return;
    if (!window.confirm(
      deleteFile
        ? `确定删除选中的 ${selectedIds.length} 条记录并删除文件吗？`
        : `确定仅删除选中的 ${selectedIds.length} 条记录吗？`
    )) {
      return;
    }
    try {
      for (const id of selectedIds) {
        await api.delete(`/downloads/${id}?delete_file=${deleteFile ? "true" : "false"}`);
      }
      await fetchDownloads();
      setSelectedIds([]);
      showNotification(deleteFile ? "已删除记录并删除文件" : "已删除记录", "success");
    } catch (error) {
      console.error("Bulk delete failed:", error);
      showNotification("批量删除失败", "error");
    }
  };

  const handleCreateRule = () => {
    setEditingRuleId(null);
    setFormChatId("");
    setFormRuleName("");
    setFormMode("monitor");
    setFormExtensions("mp4,mp3,jpg");
    setFormSizeRange("0");
    setFormSaveDir("");
    setCurrentBrowsePath("");
    setFormFilenameTemplate("{task_id}_{message_id}_{chat_title}");
    setFormMatchMode("all");
    setFormIncludeKeywords("");
    setFormExcludeKeywords("");
    setFormMoveAfterComplete(false);
    setFormAutoCatchUp(false);
    setShowRuleModal(true);
  };

  const handleEditRule = (rule: GroupRule) => {
    setEditingRuleId(rule.id);
    setFormChatId(rule.chat_id);
    setFormRuleName(rule.rule_name || rule.chat_title || "");
    setFormMode(rule.mode as "monitor" | "history");
    setFormExtensions(rule.include_extensions || "");
    setFormSizeRange(rule.size_range || "0");
    setFormSaveDir(rule.save_dir || "");
    const parentPath = rule.save_dir ? rule.save_dir.split("/").slice(0, -1).join("/") : "";
    setCurrentBrowsePath(parentPath);
    setFormFilenameTemplate(rule.filename_template || "{task_id}_{message_id}_{chat_title}");
    setFormMatchMode((rule.match_mode as "all" | "include" | "exclude") || "all");
    setFormIncludeKeywords(rule.include_keywords || "");
    setFormExcludeKeywords(rule.exclude_keywords || "");
    setFormAddDownloadSuffix(rule.add_download_suffix || false);
    setFormMoveAfterComplete(rule.move_after_complete || false);
    setFormAutoCatchUp(rule.auto_catch_up || false);
    setShowRuleModal(true);
    // 如果已有保存路径，加载该路径的父目录
    if (rule.save_dir) {
      fetchDirectories(parentPath);
    } else {
      fetchDirectories("");
    }
  };

  const handleSaveRule = async () => {
    if (!formChatId) {
      showNotification("请选择目标群聊", "info");
      return;
    }

    const selectedDialog = dialogs.find((d) => d.id === formChatId);
    const chatTitle = selectedDialog?.title || selectedDialog?.username || null;
    const normalizedRuleName = formRuleName.trim() || chatTitle || null;

    const ruleData = {
      chat_id: formChatId,
      chat_title: chatTitle,
      rule_name: normalizedRuleName,
      mode: formMode,
      include_extensions: formExtensions || null,
      size_range: formSizeRange || "0",
      save_dir: formSaveDir || null,
      filename_template: formFilenameTemplate || null,
      match_mode: formMatchMode,
      include_keywords: formMatchMode === "include" ? formIncludeKeywords : null,
      exclude_keywords: formMatchMode === "exclude" ? formExcludeKeywords : null,
      enabled: true,
      add_download_suffix: formAddDownloadSuffix,
      move_after_complete: formMoveAfterComplete,
      auto_catch_up: formAutoCatchUp,
    };

    try {
      if (editingRuleId) {
        await api.put(`/group-rules/${editingRuleId}`, ruleData);
      } else {
        await api.post("/group-rules", ruleData);
      }
      await fetchGroupRules();
      setShowRuleModal(false);
      showNotification(editingRuleId ? "规则更新成功！" : "规则创建成功！", "success");
    } catch (error) {
      console.error("Failed to save rule:", error);
      showNotification("保存规则失败", "error");
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    if (!window.confirm("确定要删除这条规则吗？")) {
      return;
    }
    try {
      await api.delete(`/group-rules/${ruleId}`);
      await fetchGroupRules();
      showNotification("规则删除成功！", "success");
    } catch (error) {
      console.error("Failed to delete rule:", error);
      showNotification("删除规则失败", "error");
    }
  };

  const handleToggleRule = async (ruleId: number, enabled: boolean) => {
    try {
      await api.put(`/group-rules/${ruleId}`, { enabled: !enabled });
      await fetchGroupRules();
      showNotification(enabled ? "规则已禁用" : "规则已启用", "success");
    } catch (error) {
      console.error("Failed to toggle rule:", error);
      showNotification("操作失败", "error");
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + " " + sizes[i];
  };

  const sourceLabel = (record: DownloadRecord) => {
    if (record.source === "rule") return record.rule_name || `规则 #${record.rule_id ?? "-"}`;
    return "机器人接收";
  };

  const getStatusMeta = (status: string) => {
    switch (status) {
      case "completed":
        return { label: "已完成", emoji: "✅", bg: "#e8f5e9", color: "#2e7d32" };
      case "downloading":
        return { label: "下载中", emoji: "⏳", bg: "#e3f2fd", color: "#1565c0" };
      case "queued":
        return { label: "队列中", emoji: "📋", bg: "#fff3e0", color: "#e65100" };
      case "paused":
        return { label: "已暂停", emoji: "⏸️", bg: "#fce4ec", color: "#880e4f" };
      case "failed":
        return { label: "失败", emoji: "❌", bg: "#ffebee", color: "#c62828" };
      case "pending":
        return { label: "待开始", emoji: "🕓", bg: "#f3f4f6", color: "#4b5563" };
      default:
        return { label: status || "未知", emoji: "•", bg: "#eef2ff", color: "#4338ca" };
    }
  };

  const clearDownloadFilters = () => {
    setDownloadStatusFilter("all");
    setDownloadRuleFilter("all");
    setDownloadPathFilter("");
    setDownloadMinSize("");
    setDownloadMaxSize("");
    setDownloadStartTime("");
    setDownloadEndTime("");
    setDownloadPage(1);
  };

  const hasActiveDownloadFilters =
    downloadStatusFilter !== "all" ||
    downloadRuleFilter !== "all" ||
    downloadPathFilter.trim() !== "" ||
    downloadMinSize.trim() !== "" ||
    downloadMaxSize.trim() !== "" ||
    downloadStartTime !== "" ||
    downloadEndTime !== "";

  const allCurrentPageSelected = downloads.length > 0 && selectedIds.length === downloads.length;

  const renderDownloadRecordsSection = () => (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "flex-start", flexWrap: "wrap", marginBottom: "1rem" }}>
        <div>
          <h2 style={{ margin: "0 0 0.35rem 0" }}>📥 下载记录</h2>
          <p style={{ margin: 0, color: "#64748b", fontSize: "0.92rem" }}>
            用卡片浏览每条任务，筛选、批量处理和状态观察都会更直观。
          </p>
        </div>
        <div className="download-summary-badge">
          当前页 {downloads.length} 条 / 总计 {downloadTotal} 条
        </div>
      </div>

      <div className="download-filter-shell">
        <div className="download-filter-header">
          <div>
            <div className="download-filter-title">筛选下载记录</div>
            <div className="download-filter-subtitle">按状态、规则、路径、体积和时间快速收窄结果。</div>
          </div>
          <button
            onClick={clearDownloadFilters}
            disabled={!hasActiveDownloadFilters}
            style={{
              padding: "0.5rem 0.9rem",
              borderRadius: "999px",
              border: "1px solid #cbd5e1",
              background: hasActiveDownloadFilters ? "#fff" : "#f8fafc",
              color: hasActiveDownloadFilters ? "#334155" : "#94a3b8",
              cursor: hasActiveDownloadFilters ? "pointer" : "not-allowed",
            }}
          >
            清空筛选
          </button>
        </div>

        <div className="download-filter-grid">
          <label className="download-filter-field">
            <span>状态</span>
            <select
              value={downloadStatusFilter}
              onChange={(e) => {
                setDownloadStatusFilter(e.target.value);
                setDownloadPage(1);
              }}
            >
              <option value="all">全部状态</option>
              <option value="downloading">下载中</option>
              <option value="queued">队列中</option>
              <option value="completed">已完成</option>
              <option value="paused">已暂停</option>
              <option value="failed">失败</option>
              <option value="pending">待开始</option>
            </select>
          </label>

          <label className="download-filter-field">
            <span>规则来源</span>
            <select
              value={downloadRuleFilter === "all" ? "all" : String(downloadRuleFilter)}
              onChange={(e) => {
                const v = e.target.value === "all" ? "all" : Number(e.target.value);
                setDownloadRuleFilter(v);
                setDownloadPage(1);
              }}
            >
              <option value="all">全部规则 / Bot</option>
              {groupRules.map((rule) => (
                <option key={rule.id} value={rule.id}>
                  {rule.rule_name || rule.chat_title || `群聊ID:${rule.chat_id}`} (规则ID:{rule.id})
                </option>
              ))}
            </select>
          </label>

          <label className="download-filter-field">
            <span>保存路径包含</span>
            <input
              type="text"
              value={downloadPathFilter}
              onChange={(e) => {
                setDownloadPathFilter(e.target.value);
                setDownloadPage(1);
              }}
              placeholder="例如：/overwatch"
            />
          </label>

          <label className="download-filter-field">
            <span>最小体积 (MB)</span>
            <input
              type="number"
              min={0}
              value={downloadMinSize}
              onChange={(e) => {
                setDownloadMinSize(e.target.value);
                setDownloadPage(1);
              }}
              placeholder="最小"
            />
          </label>

          <label className="download-filter-field">
            <span>最大体积 (MB)</span>
            <input
              type="number"
              min={0}
              value={downloadMaxSize}
              onChange={(e) => {
                setDownloadMaxSize(e.target.value);
                setDownloadPage(1);
              }}
              placeholder="最大"
            />
          </label>

          <label className="download-filter-field">
            <span>开始时间</span>
            <input
              type="datetime-local"
              value={downloadStartTime}
              onChange={(e) => {
                setDownloadStartTime(e.target.value);
                setDownloadPage(1);
              }}
            />
          </label>

          <label className="download-filter-field">
            <span>结束时间</span>
            <input
              type="datetime-local"
              value={downloadEndTime}
              onChange={(e) => {
                setDownloadEndTime(e.target.value);
                setDownloadPage(1);
              }}
            />
          </label>
        </div>
      </div>

      <div className="download-toolbar">
        <div className="download-toolbar-selection">
          <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.92rem", color: "#334155" }}>
            <input
              type="checkbox"
              checked={allCurrentPageSelected}
              onChange={(e) => {
                if (e.target.checked) {
                  setSelectedIds(downloads.map((d) => d.id));
                } else {
                  setSelectedIds([]);
                }
              }}
            />
            全选本页
          </label>
          <span className="download-toolbar-count">已选 {selectedIds.length} 项</span>
        </div>

        <div className="download-toolbar-actions">
          <button
            onClick={handlePauseAll}
            style={{ padding: "0.45rem 0.95rem", borderRadius: "999px", border: "1px solid #ff9800", background: "#fff3e0", color: "#e65100", cursor: "pointer" }}
          >
            ⏸️ 全部暂停
          </button>
          <button
            onClick={handleResumeAll}
            style={{ padding: "0.45rem 0.95rem", borderRadius: "999px", border: "1px solid #4caf50", background: "#e8f5e9", color: "#2e7d32", cursor: "pointer" }}
          >
            ▶️ 全部恢复
          </button>
          <button
            onClick={() => bulkDelete(false)}
            disabled={selectedIds.length === 0}
            style={{ padding: "0.45rem 0.95rem", borderRadius: "999px", border: "1px solid #cbd5e1", background: selectedIds.length ? "#f8fafc" : "#f1f5f9", color: selectedIds.length ? "#334155" : "#94a3b8", cursor: selectedIds.length ? "pointer" : "not-allowed" }}
          >
            🗑️ 删除记录
          </button>
          <button
            onClick={() => bulkDelete(true)}
            disabled={selectedIds.length === 0}
            style={{ padding: "0.45rem 0.95rem", borderRadius: "999px", border: "1px solid #f44336", background: selectedIds.length ? "#ffebee" : "#fef2f2", color: selectedIds.length ? "#c62828" : "#fca5a5", cursor: selectedIds.length ? "pointer" : "not-allowed" }}
          >
            🗑️ 删除记录并删除文件
          </button>
        </div>
      </div>

      <div>
        {downloads.length === 0 ? (
          <p style={{ textAlign: "center", color: "#666", padding: "2rem" }}>
            暂无下载记录
          </p>
        ) : (
          <div
            className="download-record-grid"
            style={{
              gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fit, minmax(340px, 1fr))",
            }}
          >
            {downloads.map((record: DownloadRecord) => {
              const statusMeta = getStatusMeta(record.status);
              return (
                <article key={record.id} className="download-record-card">
                  <div className="download-record-top">
                    <label className="download-record-checkbox">
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(record.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedIds([...selectedIds, record.id]);
                          } else {
                            setSelectedIds(selectedIds.filter((id) => id !== record.id));
                          }
                        }}
                      />
                    </label>
                    <div className="download-record-heading">
                      <div className="download-record-title" title={record.file_name}>
                        {record.file_name}
                      </div>
                      {record.origin_file_name && record.origin_file_name !== record.file_name && (
                        <div className="download-record-origin">
                          源文件名：{record.origin_file_name}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="download-record-tags">
                    <span
                      className="download-record-chip"
                      style={{
                        backgroundColor: record.source === "rule" ? "#ede9fe" : "#e0f2fe",
                        color: record.source === "rule" ? "#6d28d9" : "#0369a1",
                      }}
                    >
                      {sourceLabel(record)}
                    </span>
                    <span
                      className="download-record-chip"
                      style={{
                        backgroundColor: statusMeta.bg,
                        color: statusMeta.color,
                      }}
                    >
                      {statusMeta.emoji} {statusMeta.label}
                    </span>
                  </div>

                  <div className="download-record-meta">
                    <div className="download-record-meta-item">
                      <span className="download-record-meta-label">大小</span>
                      <strong>{record.file_size && record.file_size > 0 ? formatBytes(record.file_size) : "未知"}</strong>
                    </div>
                    <div className="download-record-meta-item">
                      <span className="download-record-meta-label">速度</span>
                      <strong>{record.download_speed && record.download_speed > 0 ? `${formatBytes(record.download_speed)}/s` : "-"}</strong>
                    </div>
                    <div className="download-record-meta-item">
                      <span className="download-record-meta-label">创建时间</span>
                      <strong>{new Date(record.created_at).toLocaleString()}</strong>
                    </div>
                  </div>

                  <div className="download-record-paths">
                    <div className="download-record-path-block">
                      <span className="download-record-meta-label">保存目录</span>
                      <div>{record.save_dir || "-"}</div>
                    </div>
                    {record.file_path && (
                      <div className="download-record-path-block">
                        <span className="download-record-meta-label">文件路径</span>
                        <div>{record.file_path}</div>
                      </div>
                    )}
                  </div>

                  <div className="download-progress-shell">
                    <div className="download-progress-labels">
                      <span>进度</span>
                      <strong>{typeof record.progress === "number" ? `${Math.round(record.progress)}%` : "-"}</strong>
                    </div>
                    <div className="download-progress-track">
                      <div
                        className="download-progress-fill"
                        style={{
                          width: `${Math.min(100, Math.max(0, record.progress || 0))}%`,
                          backgroundColor: record.status === "completed" ? "#4caf50" : "#2196f3",
                        }}
                      />
                    </div>
                  </div>

                  {record.error && record.status === "failed" && (
                    <div className="download-record-error">{record.error}</div>
                  )}

                  <div className="download-record-actions">
                    {record.status === "downloading" && (
                      <button
                        onClick={() => handlePauseDownload(record.id)}
                        style={{ padding: "0.45rem 0.75rem", fontSize: "0.85rem", border: "1px solid #ff9800", backgroundColor: "#fff3e0", color: "#e65100", borderRadius: "8px", cursor: "pointer" }}
                      >
                        ⏸️ 暂停
                      </button>
                    )}
                    {record.status === "paused" && (
                      <button
                        onClick={() => handleResumeDownload(record.id)}
                        style={{ padding: "0.45rem 0.75rem", fontSize: "0.85rem", border: "1px solid #4caf50", backgroundColor: "#e8f5e9", color: "#2e7d32", borderRadius: "8px", cursor: "pointer" }}
                      >
                        ▶️ 开始
                      </button>
                    )}
                    {record.status === "failed" && (
                      <button
                        onClick={() => handleRetryDownload(record.id)}
                        style={{ padding: "0.45rem 0.75rem", fontSize: "0.85rem", border: "1px solid #7c3aed", backgroundColor: "#f3e8ff", color: "#6d28d9", borderRadius: "8px", cursor: "pointer" }}
                      >
                        🔄 重试
                      </button>
                    )}
                    {(record.status === "downloading" || record.status === "pending" || record.status === "queued" || record.status === "paused") && (
                      <button
                        onClick={() => handlePriorityDownload(record.id)}
                        style={{ padding: "0.45rem 0.75rem", fontSize: "0.85rem", border: "1px solid #ffc107", backgroundColor: "#fff8e1", color: "#f57f17", borderRadius: "8px", cursor: "pointer" }}
                      >
                        ⭐ 置顶
                      </button>
                    )}
                    <button
                      onClick={() => handleDeleteDownload(record.id, false)}
                      style={{ padding: "0.45rem 0.75rem", fontSize: "0.85rem", border: "1px solid #9e9e9e", backgroundColor: "#f5f5f5", color: "#424242", borderRadius: "8px", cursor: "pointer" }}
                    >
                      🗑️ 删记录
                    </button>
                    <button
                      onClick={() => handleDeleteDownload(record.id, true)}
                      style={{ padding: "0.45rem 0.75rem", fontSize: "0.85rem", border: "1px solid #f44336", backgroundColor: "#ffebee", color: "#c62828", borderRadius: "8px", cursor: "pointer" }}
                    >
                      🗑️ 记录+文件
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </>
  );

  return (
    <div style={{ padding: isMobile ? "1rem" : "2rem", maxWidth: "1400px", margin: "0 auto", minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <div style={{ marginBottom: "2rem", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <img
            src="/images/logo2.png"
            alt="Telegram Depiler Logo"
            style={{ height: isMobile ? "64px" : "100px", objectFit: "contain" }}
          />
          <span style={{ fontSize: "0.9rem", color: "#6b7280" }}>v{__APP_VERSION__}</span>
        </div>
        <Link to="/settings" style={{ textDecoration: "none" }}>
          <button style={{ padding: "0.5rem 1rem", cursor: "pointer" }}>⚙️ Settings</button>
        </Link>
      </div>

      {/* 群聊下载规则 */}
      <div className="card" style={{ marginBottom: "2rem", padding: "1.5rem", backgroundColor: "white", borderRadius: "8px", boxShadow: "0 2px 4px rgba(0,0,0,0.1)" }}>
        <div style={{ marginBottom: "1rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h2 style={{ margin: "0 0 0.5rem 0" }}>📂 群聊下载规则</h2>
            <p style={{ margin: 0, color: "#666", fontSize: "0.9rem" }}>
              为群聊配置自动下载规则，支持监控新消息和下载历史文件
            </p>
          </div>
          <button
            onClick={handleCreateRule}
            style={{
              padding: "0.6rem 1.2rem",
              backgroundColor: "#2196f3",
              color: "white",
              border: "none",
              borderRadius: "6px",
              cursor: "pointer",
              fontSize: "0.9rem",
              fontWeight: "500",
            }}
          >
            ➕ 新建规则
          </button>
        </div>

        {/* 默认下载路径显示 */}
        {defaultDownloadPath && (
          <div style={{
            marginBottom: "1.5rem",
            padding: "0.75rem 1rem",
            backgroundColor: "#e3f2fd",
            border: "1px solid #2196f3",
            borderRadius: "6px",
            display: "flex",
            alignItems: "center",
            gap: "0.5rem"
          }}>
            <span style={{ fontSize: "1.2rem" }}>📁</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: "600", marginBottom: "0.25rem", color: "#1976d2" }}>
                默认下载路径（不可删除/禁用）
              </div>
              <div style={{ fontSize: "0.9rem", color: "#1565c0" }}>
                {defaultDownloadPath}
              </div>
              <div style={{ fontSize: "0.75rem", color: "#666", marginTop: "0.25rem" }}>
                默认文件名模板：{defaultFilenameTemplate}
              </div>
              <div style={{ fontSize: "0.75rem", color: "#666", marginTop: "0.25rem" }}>
                当规则未指定保存路径时，文件将保存到此路径
              </div>
            </div>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                onClick={() => setShowFilenameTemplateModal(true)}
                style={{
                  padding: "0.4rem 0.8rem",
                  fontSize: "0.8rem",
                  borderRadius: "6px",
                  border: "1px solid #2196f3",
                  backgroundColor: "white",
                  color: "#2196f3",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                ✏️ 编辑模板
              </button>
              <button
                onClick={() => {
                  setSelectedDefaultPath("");
                  setCurrentBrowsePath("");
                  fetchDirectories("");
                  setShowDefaultPathModal(true);
                }}
                style={{
                  padding: "0.4rem 0.8rem",
                  fontSize: "0.8rem",
                  borderRadius: "6px",
                  border: "1px solid #2196f3",
                  backgroundColor: "white",
                  color: "#2196f3",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                📂 选择路径
              </button>
            </div>
          </div>
        )}

        {groupRules.length === 0 ? (
          <div style={{ textAlign: "center", padding: "3rem", color: "#999" }}>
            <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>📭</div>
            <p>暂无下载规则</p>
            <button
              onClick={handleCreateRule}
              style={{
                marginTop: "1rem",
                padding: "0.6rem 1.5rem",
                backgroundColor: "#2196f3",
                color: "white",
                border: "none",
                borderRadius: "6px",
                cursor: "pointer",
                fontSize: "0.95rem",
              }}
            >
              立即创建第一条规则
            </button>
          </div>
        ) : (
          <div className="rule-card-grid" style={{ display: "grid", gap: "1rem" }}>
            {groupRules.map((rule) => (
              <div
                key={rule.id}
                className="rule-card"
                style={{
                  border: "1px solid #e0e0e0",
                  borderRadius: "8px",
                  padding: "1rem",
                  backgroundColor: "#fafafa",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.75rem" }}>
                  <div>
                    <h4 style={{ margin: "0 0 0.25rem 0", fontSize: "1rem", fontWeight: "600" }}>
                      {rule.rule_name || rule.chat_title || `群聊 ID: ${rule.chat_id}`}
                    </h4>
                    <span
                      style={{
                        display: "inline-block",
                        padding: "0.2rem 0.6rem",
                        fontSize: "0.75rem",
                        borderRadius: "12px",
                        backgroundColor: rule.mode === "monitor" ? "#e3f2fd" : "#fff3e0",
                        color: rule.mode === "monitor" ? "#1976d2" : "#f57c00",
                        fontWeight: "500",
                      }}
                    >
                      {rule.mode === "monitor" ? "📡 监控下载" : "📚 历史下载"}
                    </span>
                    {!rule.enabled && (
                      <span
                        style={{
                          display: "inline-block",
                          marginLeft: "0.5rem",
                          padding: "0.2rem 0.6rem",
                          fontSize: "0.75rem",
                          borderRadius: "12px",
                          backgroundColor: "#ffebee",
                          color: "#c62828",
                        }}
                      >
                        已禁用
                      </span>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <button
                      onClick={() => handleToggleRule(rule.id, rule.enabled)}
                      style={{
                        padding: "0.4rem 0.8rem",
                        fontSize: "0.85rem",
                        backgroundColor: rule.enabled ? "#fff3e0" : "#e8f5e9",
                        color: rule.enabled ? "#e65100" : "#2e7d32",
                        border: `1px solid ${rule.enabled ? "#ff9800" : "#4caf50"}`,
                        borderRadius: "6px",
                        cursor: "pointer",
                      }}
                    >
                      {rule.enabled ? "⏸️ 禁用" : "▶️ 启用"}
                    </button>
                    <button
                      onClick={() => handleEditRule(rule)}
                      style={{
                        padding: "0.4rem 0.8rem",
                        fontSize: "0.85rem",
                        backgroundColor: "white",
                        color: "#2196f3",
                        border: "1px solid #2196f3",
                        borderRadius: "6px",
                        cursor: "pointer",
                      }}
                    >
                      ✏️ 编辑
                    </button>
                    <button
                      onClick={() => handleDeleteRule(rule.id)}
                      style={{
                        padding: "0.4rem 0.8rem",
                        fontSize: "0.85rem",
                        backgroundColor: "white",
                        color: "#f44336",
                        border: "1px solid #f44336",
                        borderRadius: "6px",
                        cursor: "pointer",
                      }}
                    >
                      🗑️ 删除
                    </button>
                  </div>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "0.5rem", fontSize: "0.85rem" }}>
                  {rule.include_extensions && (
                    <div>
                      <span style={{ color: "#666" }}>文件类型：</span>
                      <span style={{ fontWeight: "500" }}>{rule.include_extensions}</span>
                    </div>
                  )}
                  {rule.size_range && rule.size_range !== "0" && (
                    <div>
                      <span style={{ color: "#666" }}>体积范围：</span>
                      <span style={{ fontWeight: "500" }}>
                        {(() => {
                          const range = rule.size_range;
                          if (range.includes("-")) {
                            const [min, max] = range.split("-");
                            return `${min || "0"} ~ ${max} MB`;
                          } else {
                            return `≥ ${range} MB`;
                          }
                        })()}
                      </span>
                    </div>
                  )}
                  {rule.save_dir && (
                    <div>
                      <span style={{ color: "#666" }}>保存路径：</span>
                      <span style={{ fontWeight: "500" }}>{rule.save_dir}</span>
                    </div>
                  )}
                  {rule.match_mode && rule.match_mode !== "all" && (
                    <div>
                      <span style={{ color: "#666" }}>关键词模式：</span>
                      <span style={{ fontWeight: "500" }}>
                        {rule.match_mode === "include" ? "包含" : "排除"}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 下载记录 */}
      <div className="card" style={{ padding: "1.5rem", backgroundColor: "white", borderRadius: "8px", boxShadow: "0 2px 4px rgba(0,0,0,0.1)" }}>
        {renderDownloadRecordsSection()}

        {false && (
        <>
        <h2 style={{ margin: "0 0 1rem 0" }}>📥 下载记录</h2>

        {/* 筛选条件 */}
        <div
            style={{
            marginBottom: "0.75rem",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: "0.5rem",
            alignItems: "center",
          }}
        >
          <div>
            <label style={{ fontSize: "0.8rem", color: "#555" }}>状态</label>
            <select
              value={downloadStatusFilter}
              onChange={(e) => {
                setDownloadStatusFilter(e.target.value);
                setDownloadPage(1);
              }}
              style={{ width: "100%", padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
            >
              <option value="all">全部状态</option>
              <option value="downloading">下载中</option>
              <option value="queued">队列中</option>
              <option value="completed">已完成</option>
              <option value="paused">已暂停</option>
              <option value="failed">失败</option>
              <option value="pending">待开始</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: "0.8rem", color: "#555" }}>规则</label>
            <select
              value={downloadRuleFilter === "all" ? "all" : String(downloadRuleFilter)}
              onChange={(e) => {
                const v = e.target.value === "all" ? "all" : Number(e.target.value);
                setDownloadRuleFilter(v);
                setDownloadPage(1);
              }}
              style={{ width: "100%", padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
            >
              <option value="all">全部规则 / Bot</option>
              {groupRules.map((rule) => (
                <option key={rule.id} value={rule.id}>
                  {rule.rule_name || rule.chat_title || `群聊ID:${rule.chat_id}`} (规则ID:{rule.id})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ fontSize: "0.8rem", color: "#555" }}>保存路径包含</label>
            <input
              type="text"
              value={downloadPathFilter}
              onChange={(e) => {
                setDownloadPathFilter(e.target.value);
                setDownloadPage(1);
              }}
              placeholder="例如：/overwach"
              style={{ width: "100%", padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
            />
          </div>
          <div>
            <label style={{ fontSize: "0.8rem", color: "#555" }}>大小区间 (MB)</label>
            <div style={{ display: "flex", gap: "0.25rem" }}>
              <input
                type="number"
                min={0}
                value={downloadMinSize}
                onChange={(e) => {
                  setDownloadMinSize(e.target.value);
                  setDownloadPage(1);
                }}
                placeholder="最小"
                style={{ flex: 1, padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
              />
              <span style={{ alignSelf: "center", fontSize: "0.8rem", color: "#666" }}>~</span>
              <input
                type="number"
                min={0}
                value={downloadMaxSize}
                onChange={(e) => {
                  setDownloadMaxSize(e.target.value);
                  setDownloadPage(1);
                }}
                placeholder="最大"
                style={{ flex: 1, padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
              />
            </div>
          </div>
          <div>
            <label style={{ fontSize: "0.8rem", color: "#555" }}>开始时间</label>
            <input
              type="datetime-local"
              value={downloadStartTime}
              onChange={(e) => {
                setDownloadStartTime(e.target.value);
                setDownloadPage(1);
              }}
              style={{ width: "100%", padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
            />
          </div>
          <div>
            <label style={{ fontSize: "0.8rem", color: "#555" }}>结束时间</label>
            <input
              type="datetime-local"
              value={downloadEndTime}
              onChange={(e) => {
                setDownloadEndTime(e.target.value);
                setDownloadPage(1);
              }}
              style={{ width: "100%", padding: "0.35rem", borderRadius: "4px", border: "1px solid #ddd", fontSize: "0.85rem" }}
            />
          </div>
        </div>

        {/* 操作工具栏 */}
        <div style={{ marginBottom: "0.75rem", display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          <button
            onClick={handlePauseAll}
            style={{ padding: "0.35rem 0.9rem", borderRadius: "6px", border: "1px solid #ff9800", background: "#fff3e0", color: "#e65100", cursor: "pointer" }}
          >
            ⏸️ 全部暂停
          </button>
          <button
            onClick={handleResumeAll}
            style={{ padding: "0.35rem 0.9rem", borderRadius: "6px", border: "1px solid #4caf50", background: "#e8f5e9", color: "#2e7d32", cursor: "pointer" }}
          >
            ▶️ 全部恢复
          </button>
          <div style={{ flex: 1 }} />
          <span style={{ color: "#555", fontSize: "0.9rem" }}>已选 {selectedIds.length} 项</span>
          <button
            onClick={() => bulkDelete(false)}
            disabled={selectedIds.length === 0}
            style={{ padding: "0.35rem 0.9rem", borderRadius: "6px", border: "1px solid #ccc", background: "#f5f5f5", color: "#333", cursor: selectedIds.length ? "pointer" : "not-allowed" }}
          >
            🗑️ 删除记录
          </button>
          <button
            onClick={() => bulkDelete(true)}
            disabled={selectedIds.length === 0}
            style={{ padding: "0.35rem 0.9rem", borderRadius: "6px", border: "1px solid #f44336", background: "#ffebee", color: "#c62828", cursor: selectedIds.length ? "pointer" : "not-allowed" }}
          >
            🗑️ 删除记录并删除文件
          </button>
        </div>
        <div>
          {downloads.length === 0 ? (
            <p style={{ textAlign: "center", color: "#666", padding: "2rem" }}>
              暂无下载记录
            </p>
          ) : (
            isMobile ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.75rem" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem", color: "#374151" }}>
                    <input
                      type="checkbox"
                      checked={selectedIds.length > 0 && selectedIds.length === downloads.length}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedIds(downloads.map((d) => d.id));
                        } else {
                          setSelectedIds([]);
                        }
                      }}
                    />
                    全选本页
                  </label>
                  <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>共 {downloads.length} 条</span>
                </div>
                {downloads.map((record: DownloadRecord) => (
                  <div
                    key={record.id}
                    style={{
                      border: "1px solid #e5e7eb",
                      borderRadius: "10px",
                      background: "#fff",
                      padding: "0.9rem",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.6rem",
                    }}
                  >
                    <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-start" }}>
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(record.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedIds([...selectedIds, record.id]);
                          } else {
                            setSelectedIds(selectedIds.filter((id) => id !== record.id));
                          }
                        }}
                      />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 700, wordBreak: "break-word" }}>{record.file_name}</div>
                        {record.origin_file_name && record.origin_file_name !== record.file_name && (
                          <div style={{ marginTop: "0.25rem", fontSize: "0.85rem", color: "#6b7280", wordBreak: "break-word" }}>
                            源文件名：{record.origin_file_name}
                          </div>
                        )}
                        <div style={{ marginTop: "0.35rem", fontSize: "0.85rem", color: "#374151", wordBreak: "break-word" }}>
                          保存：{record.save_dir || record.file_path || "-"}
                        </div>
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                      <span
                        style={{
                          padding: "0.2rem 0.55rem",
                          borderRadius: "999px",
                          fontSize: "0.8rem",
                          backgroundColor: record.source === "rule" ? "#ede9fe" : "#e0f2fe",
                          color: record.source === "rule" ? "#6d28d9" : "#0369a1",
                          fontWeight: 600,
                        }}
                      >
                        {sourceLabel(record)}
                      </span>
                      <span
                        style={{
                          padding: "0.2rem 0.55rem",
                          borderRadius: "999px",
                          fontSize: "0.8rem",
                          backgroundColor:
                            record.status === "completed"
                              ? "#e8f5e9"
                              : record.status === "failed"
                              ? "#ffebee"
                              : record.status === "queued"
                              ? "#fff3e0"
                              : record.status === "paused"
                              ? "#fce4ec"
                              : "#e3f2fd",
                          color:
                            record.status === "completed"
                              ? "#2e7d32"
                              : record.status === "failed"
                              ? "#c62828"
                              : record.status === "queued"
                              ? "#e65100"
                              : record.status === "paused"
                              ? "#880e4f"
                              : "#1565c0",
                          fontWeight: 600,
                        }}
                      >
                        {record.status === "completed"
                          ? "✅ 完成"
                          : record.status === "downloading"
                          ? "⏳ 下载中"
                          : record.status === "queued"
                          ? "📋 队列中"
                          : record.status === "paused"
                          ? "⏸️ 已暂停"
                          : record.status === "failed"
                          ? "❌ 失败"
                          : record.status}
                      </span>
                      {record.file_size && record.file_size > 0 && (
                        <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>{formatBytes(record.file_size)}</span>
                      )}
                      <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>{new Date(record.created_at).toLocaleString()}</span>
                      {record.download_speed && record.download_speed > 0 && (
                        <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>{formatBytes(record.download_speed)}/s</span>
                      )}
                    </div>

                    {typeof record.progress === "number" && (
                      <div>
                        <div
                          style={{
                            width: "100%",
                            height: "18px",
                            backgroundColor: "#e0e0e0",
                            borderRadius: "10px",
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              width: `${Math.min(100, Math.max(0, record.progress || 0))}%`,
                              height: "100%",
                              backgroundColor: record.status === "completed" ? "#4caf50" : "#2196f3",
                              transition: "width 0.3s ease",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              color: "#fff",
                              fontSize: "0.75em",
                              fontWeight: "bold",
                            }}
                          >
                            {record.progress ? `${Math.round(record.progress)}%` : "0%"}
                          </div>
                        </div>
                      </div>
                    )}

                    <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-start", flexWrap: "wrap" }}>
                      {record.status === "downloading" && (
                        <button
                          onClick={() => handlePauseDownload(record.id)}
                          style={{
                            padding: "0.35rem 0.6rem",
                            fontSize: "0.85rem",
                            border: "1px solid #ff9800",
                            backgroundColor: "#fff3e0",
                            color: "#e65100",
                            borderRadius: "6px",
                            cursor: "pointer",
                          }}
                        >
                          ⏸️ 暂停
                        </button>
                      )}
                      {record.status === "paused" && (
                        <button
                          onClick={() => handleResumeDownload(record.id)}
                          style={{
                            padding: "0.35rem 0.6rem",
                            fontSize: "0.85rem",
                            border: "1px solid #4caf50",
                            backgroundColor: "#e8f5e9",
                            color: "#2e7d32",
                            borderRadius: "6px",
                            cursor: "pointer",
                          }}
                        >
                          ▶️ 开始
                        </button>
                      )}
                      {(record.status === "downloading" || record.status === "pending" || record.status === "queued" || record.status === "paused") && (
                        <button
                          onClick={() => handlePriorityDownload(record.id)}
                          style={{
                            padding: "0.35rem 0.6rem",
                            fontSize: "0.85rem",
                            border: "1px solid #ffc107",
                            backgroundColor: "#fff8e1",
                            color: "#f57f17",
                            borderRadius: "6px",
                            cursor: "pointer",
                          }}
                        >
                          ⭐ 置顶
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteDownload(record.id, false)}
                        style={{
                          padding: "0.35rem 0.6rem",
                          fontSize: "0.85rem",
                          border: "1px solid #9e9e9e",
                          backgroundColor: "#f5f5f5",
                          color: "#424242",
                          borderRadius: "6px",
                          cursor: "pointer",
                        }}
                      >
                        🗑️ 删记录
                      </button>
                      <button
                        onClick={() => handleDeleteDownload(record.id, true)}
                        style={{
                          padding: "0.35rem 0.6rem",
                          fontSize: "0.85rem",
                          border: "1px solid #f44336",
                          backgroundColor: "#ffebee",
                          color: "#c62828",
                          borderRadius: "6px",
                          cursor: "pointer",
                        }}
                      >
                        🗑️ 记录+文件
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "980px" }}>
                  <thead>
                    <tr style={{ borderBottom: "2px solid #e0e0e0" }}>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>
                        <input
                          type="checkbox"
                          checked={selectedIds.length > 0 && selectedIds.length === downloads.length}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedIds(downloads.map((d) => d.id));
                            } else {
                              setSelectedIds([]);
                            }
                          }}
                        />
                      </th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>文件名</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>源文件名</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>大小</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>保存路径</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>状态 / 来源</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>进度</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>速度</th>
                      <th style={{ padding: "0.75rem", textAlign: "left" }}>时间</th>
                      <th style={{ padding: "0.75rem", textAlign: "center" }}>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {downloads.map((record: DownloadRecord) => (
                      <tr key={record.id} style={{ borderBottom: "1px solid #f0f0f0" }}>
                        <td style={{ padding: "0.75rem" }}>
                          <input
                            type="checkbox"
                            checked={selectedIds.includes(record.id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedIds([...selectedIds, record.id]);
                              } else {
                                setSelectedIds(selectedIds.filter((id) => id !== record.id));
                              }
                            }}
                          />
                        </td>
                        <td style={{ padding: "0.75rem", maxWidth: "260px" }}>
                          <div
                            style={{
                              display: "inline-block",
                              maxWidth: "260px",
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              verticalAlign: "top",
                            }}
                            title={record.file_name}
                          >
                            {record.file_name}
                          </div>
                        </td>
                        <td style={{ padding: "0.75rem" }}>{record.origin_file_name || "-"}</td>
                        <td style={{ padding: "0.75rem" }}>
                          {record.file_size && record.file_size > 0 ? (
                            formatBytes(record.file_size)
                          ) : (
                            <span style={{ color: "#999" }}>未知</span>
                          )}
                        </td>
                        <td style={{ padding: "0.75rem", maxWidth: "260px", wordBreak: "break-all", fontSize: "0.8rem", color: "#374151" }}>
                          {record.save_dir || record.file_path || "-"}
                        </td>
                        <td style={{ padding: "0.75rem" }}>
                          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                            <span
                              style={{
                                padding: "0.2rem 0.5rem",
                                borderRadius: "999px",
                                fontSize: "0.8rem",
                                backgroundColor: record.source === "rule" ? "#ede9fe" : "#e0f2fe",
                                color: record.source === "rule" ? "#6d28d9" : "#0369a1",
                                fontWeight: 500,
                                alignSelf: "flex-start",
                              }}
                            >
                              {sourceLabel(record)}
                            </span>
                            <span
                              style={{
                                padding: "0.25rem 0.5rem",
                                borderRadius: "4px",
                                fontSize: "0.85rem",
                                backgroundColor:
                                  record.status === "completed"
                                    ? "#e8f5e9"
                                    : record.status === "failed"
                                    ? "#ffebee"
                                    : record.status === "queued"
                                    ? "#fff3e0"
                                    : record.status === "paused"
                                    ? "#fce4ec"
                                    : "#e3f2fd",
                                color:
                                  record.status === "completed"
                                    ? "#2e7d32"
                                    : record.status === "failed"
                                    ? "#c62828"
                                    : record.status === "queued"
                                    ? "#e65100"
                                    : record.status === "paused"
                                    ? "#880e4f"
                                    : "#1565c0",
                                alignSelf: "flex-start",
                              }}
                            >
                              {record.status === "completed"
                                ? "✅ 完成"
                                : record.status === "downloading"
                                ? "⏳ 下载中"
                                : record.status === "queued"
                                ? "📋 队列中"
                                : record.status === "paused"
                                ? "⏸️ 已暂停"
                                : record.status === "failed"
                                ? "❌ 失败"
                                : record.status}
                            </span>
                          </div>
                        </td>
                        <td style={{ padding: "0.75rem" }}>
                          {typeof record.progress === "number" ? (
                            <div style={{ minWidth: "150px" }}>
                              <div
                                style={{
                                  width: "100%",
                                  height: "20px",
                                  backgroundColor: "#e0e0e0",
                                  borderRadius: "10px",
                                  overflow: "hidden",
                                }}
                              >
                                <div
                                  style={{
                                    width: `${Math.min(100, Math.max(0, record.progress || 0))}%`,
                                    height: "100%",
                                    backgroundColor: record.status === "completed" ? "#4caf50" : "#2196f3",
                                    transition: "width 0.3s ease",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    color: "#fff",
                                    fontSize: "0.75em",
                                    fontWeight: "bold",
                                  }}
                                >
                                  {record.progress ? `${Math.round(record.progress)}%` : "0%"}
                                </div>
                              </div>
                            </div>
                          ) : (
                            <span style={{ color: "#999" }}>-</span>
                          )}
                        </td>
                        <td style={{ padding: "0.75rem" }}>
                          {record.download_speed && record.download_speed > 0 ? (
                            <span style={{ fontSize: "0.9em" }}>{formatBytes(record.download_speed)}/s</span>
                          ) : (
                            <span style={{ color: "#999" }}>-</span>
                          )}
                        </td>
                        <td style={{ padding: "0.75rem", fontSize: "0.85rem", color: "#666" }}>
                          {new Date(record.created_at).toLocaleString()}
                        </td>
                        <td style={{ padding: "0.75rem", textAlign: "center" }}>
                          <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center", flexWrap: "wrap" }}>
                            {record.status === "downloading" && (
                              <button
                                onClick={() => handlePauseDownload(record.id)}
                                style={{
                                  padding: "0.25rem 0.5rem",
                                  fontSize: "0.8rem",
                                  border: "1px solid #ff9800",
                                  backgroundColor: "#fff3e0",
                                  color: "#e65100",
                                  borderRadius: "4px",
                                  cursor: "pointer",
                                }}
                                title="暂停下载"
                              >
                                ⏸️ 暂停
                              </button>
                            )}
                            {record.status === "paused" && (
                              <button
                                onClick={() => handleResumeDownload(record.id)}
                                style={{
                                  padding: "0.25rem 0.5rem",
                                  fontSize: "0.8rem",
                                  border: "1px solid #4caf50",
                                  backgroundColor: "#e8f5e9",
                                  color: "#2e7d32",
                                  borderRadius: "4px",
                                  cursor: "pointer",
                                }}
                                title="继续下载"
                              >
                                ▶️ 开始
                              </button>
                            )}
                            {(record.status === "downloading" || record.status === "pending" || record.status === "queued" || record.status === "paused") && (
                              <button
                                onClick={() => handlePriorityDownload(record.id)}
                                style={{
                                  padding: "0.25rem 0.5rem",
                                  fontSize: "0.8rem",
                                  border: "1px solid #ffc107",
                                  backgroundColor: "#fff8e1",
                                  color: "#f57f17",
                                  borderRadius: "4px",
                                  cursor: "pointer",
                                }}
                                title="设置优先级"
                              >
                                ⭐ 置顶
                              </button>
                            )}
                            <button
                              onClick={() => handleDeleteDownload(record.id, false)}
                              style={{
                                padding: "0.25rem 0.5rem",
                                fontSize: "0.8rem",
                                border: "1px solid #9e9e9e",
                                backgroundColor: "#f5f5f5",
                                color: "#424242",
                                borderRadius: "4px",
                                cursor: "pointer",
                              }}
                              title="仅删除记录"
                            >
                              🗑️ 删记录
                            </button>
                            <button
                              onClick={() => handleDeleteDownload(record.id, true)}
                              style={{
                                padding: "0.25rem 0.5rem",
                                fontSize: "0.8rem",
                                border: "1px solid #f44336",
                                backgroundColor: "#ffebee",
                                color: "#c62828",
                                borderRadius: "4px",
                                cursor: "pointer",
                              }}
                              title="删除记录和文件"
                            >
                              🗑️ 记录+文件
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>
        </>
        )}

        {/* 分页控制 */}
        <div style={{ marginTop: "0.75rem", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "0.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#555" }}>
            共 {downloadTotal} 条记录，每页
            <select
              value={downloadPageSize}
              onChange={(e) => {
                setDownloadPageSize(Number(e.target.value));
                setDownloadPage(1);
              }}
              style={{ margin: "0 0.35rem", padding: "0.1rem 0.3rem", fontSize: "0.85rem" }}
            >
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
            条
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <button
              onClick={() => setDownloadPage((p) => Math.max(1, p - 1))}
              disabled={downloadPage <= 1}
              style={{
                padding: "0.25rem 0.6rem",
                borderRadius: "4px",
                border: "1px solid #ddd",
                backgroundColor: downloadPage <= 1 ? "#f5f5f5" : "white",
                color: "#333",
                cursor: downloadPage <= 1 ? "not-allowed" : "pointer",
                fontSize: "0.85rem",
              }}
            >
              上一页
            </button>
            <span style={{ fontSize: "0.85rem", color: "#555" }}>
              第 {downloadPage} 页
            </span>
            <button
              onClick={() => {
                const maxPage = downloadTotal > 0 ? Math.ceil(downloadTotal / downloadPageSize) : 1;
                setDownloadPage((p) => Math.min(maxPage, p + 1));
              }}
              disabled={downloadTotal <= downloadPage * downloadPageSize}
              style={{
                padding: "0.25rem 0.6rem",
                borderRadius: "4px",
                border: "1px solid #ddd",
                backgroundColor: downloadTotal <= downloadPage * downloadPageSize ? "#f5f5f5" : "white",
                color: "#333",
                cursor: downloadTotal <= downloadPage * downloadPageSize ? "not-allowed" : "pointer",
                fontSize: "0.85rem",
              }}
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      {/* 日志显示 */}
      {logs.length > 0 && (
        <div className="card" style={{ marginTop: "2rem", padding: "1.5rem", backgroundColor: "white", borderRadius: "8px", boxShadow: "0 2px 4px rgba(0,0,0,0.1)" }}>
          <h2 style={{ margin: "0 0 1rem 0" }}>📋 实时日志</h2>
          <div style={{ maxHeight: "400px", overflowY: "auto", backgroundColor: "#f5f5f5", padding: "1rem", borderRadius: "4px", fontFamily: "monospace", fontSize: "0.85rem" }}>
            {logs.map((log: LogEntry, index: number) => (
              <div key={index} style={{ marginBottom: "0.5rem", display: "flex", gap: "0.5rem" }}>
                <span style={{ color: "#666" }}>{log.timestamp}</span>
                <span style={{ color: log.level === "ERROR" ? "#f44336" : log.level === "WARNING" ? "#ff9800" : "#4caf50", fontWeight: "bold" }}>
                  [{log.level}]
                </span>
                <span>{log.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: "2rem", paddingTop: "1rem", borderTop: "1px solid #e5e7eb", textAlign: "center", color: "#6b7280", fontSize: "0.9rem" }}>
        <a
          href="https://github.com/APecme/Telegram_Depiler"
          target="_blank"
          rel="noreferrer"
          style={{ color: "#2563eb", textDecoration: "none" }}
        >
          GitHub: https://github.com/APecme/Telegram_Depiler
        </a>
      </div>

      {/* 规则创建/编辑模态框 */}
      {showRuleModal && (
        <div className="rule-modal-overlay" style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: "rgba(0,0,0,0.5)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}>
          <div className="rule-modal-panel" style={{
            backgroundColor: "white",
            borderRadius: "8px",
            padding: "2rem",
            maxWidth: "600px",
            width: "90%",
            maxHeight: "90vh",
            overflowY: "auto",
          }}>
            <h2 className="rule-modal-title" style={{ margin: "0 0 1.5rem 0" }}>
              {editingRuleId ? "编辑规则" : "新建规则"}
            </h2>

            <div className="rule-modal-form" style={{ display: "grid", gap: "1rem" }}>
              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  目标群聊 *
                </label>
                <select
                  value={formChatId === "" ? "" : String(formChatId)}
                  onChange={(e) => {
                    const value = e.target.value;
                    const nextId = value ? Number(value) : "";
                    setFormChatId(nextId);
                    if (nextId !== "" && !formRuleName.trim() && !editingRuleId) {
                      const d = dialogs.find((x) => x.id === nextId);
                      const t = d?.title || d?.username || "";
                      if (t) setFormRuleName(t);
                    }
                  }}
                  style={{
                    width: "100%",
                    padding: "0.5rem",
                    borderRadius: "4px",
                    border: "1px solid #ddd",
                  }}
                >
                  <option value="">请选择群聊</option>
                  {dialogs
                    .filter((d) => d.is_group)
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.title || d.username || "未知群聊"} (ID: {d.id})
                        {d.username ? ` [@${d.username}]` : ""}
                      </option>
                    ))}
                </select>
                <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                  下拉选择要应用规则的群聊或频道
                </small>
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  规则名（可编辑）
                </label>
                <input
                  type="text"
                  value={formRuleName}
                  onChange={(e) => setFormRuleName(e.target.value)}
                  placeholder="默认使用群聊名称"
                  style={{
                    width: "100%",
                    padding: "0.5rem",
                    borderRadius: "4px",
                    border: "1px solid #ddd",
                    fontSize: "0.9rem"
                  }}
                />
                <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                  用于下载记录和筛选中展示，留空会自动使用群聊名称
                </small>
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  规则类型
                </label>
                <div style={{ display: "flex", gap: "1rem" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <input
                      type="radio"
                      checked={formMode === "monitor"}
                      onChange={() => setFormMode("monitor")}
                    />
                    监控下载（新消息）
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <input
                      type="radio"
                      checked={formMode === "history"}
                      onChange={() => setFormMode("history")}
                    />
                    下载历史文件
                  </label>
                </div>
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  文件类型（可选）
                </label>
                <input
                  type="text"
                  value={formExtensions}
                  onChange={(e) => setFormExtensions(e.target.value)}
                  placeholder="例如：mp4,mp3,jpg,png,zip"
                  style={{
                    width: "100%",
                    padding: "0.5rem",
                    border: "1px solid #ddd",
                    borderRadius: "4px",
                    fontSize: "0.9rem"
                  }}
                />
                <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("mp4")) {
                        setFormExtensions([...exts, "mp4"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + MP4
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("mkv")) {
                        setFormExtensions([...exts, "mkv"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + MKV
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("mp3")) {
                        setFormExtensions([...exts, "mp3"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + MP3
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("jpg") && !exts.includes("jpeg")) {
                        setFormExtensions([...exts, "jpg", "jpeg"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + JPG
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("png")) {
                        setFormExtensions([...exts, "png"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + PNG
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("zip")) {
                        setFormExtensions([...exts, "zip"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + ZIP
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const exts = formExtensions.split(",").filter(x => x.trim());
                      if (!exts.includes("pdf")) {
                        setFormExtensions([...exts, "pdf"].join(","));
                      }
                    }}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #ddd",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      cursor: "pointer"
                    }}
                  >
                    + PDF
                  </button>
                  <button
                    type="button"
                    onClick={() => setFormExtensions("")}
                    style={{
                      padding: "0.3rem 0.6rem",
                      fontSize: "0.8rem",
                      border: "1px solid #f44336",
                      borderRadius: "4px",
                      backgroundColor: "white",
                      color: "#f44336",
                      cursor: "pointer"
                    }}
                  >
                    清空
                  </button>
                </div>
                <p style={{ fontSize: "0.8rem", color: "#666", marginTop: "0.5rem" }}>
                  输入文件扩展名，多个用逗号分隔（例如：mp4,mp3,jpg）。留空则下载所有类型文件。点击上方按钮可快速添加常用扩展名。
                </p>
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  体积范围（MB）
                  <span
                    title="格式说明"
                    style={{
                      display: "inline-block",
                      marginLeft: "0.5rem",
                      width: "18px",
                      height: "18px",
                      lineHeight: "18px",
                      textAlign: "center",
                      borderRadius: "50%",
                      backgroundColor: "#2196f3",
                      color: "white",
                      fontSize: "0.75rem",
                      cursor: "help",
                    }}
                  >
                    ?
                    <div
                      style={{
                        display: "none",
                        position: "absolute",
                        left: "25px",
                        top: "-10px",
                        backgroundColor: "#333",
                        color: "white",
                        padding: "0.75rem",
                        borderRadius: "6px",
                        fontSize: "0.85rem",
                        whiteSpace: "nowrap",
                        zIndex: 1000,
                        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
                      }}
                      className="tooltip-content"
                    >
                      <div style={{ marginBottom: "0.5rem", fontWeight: "600", borderBottom: "1px solid #555", paddingBottom: "0.25rem" }}>
                        格式说明：
                      </div>
                      <div style={{ lineHeight: "1.6" }}>
                        <div>• <strong>0</strong> - 不限制大小</div>
                        <div>• <strong>10</strong> - 大于等于 10MB</div>
                        <div>• <strong>10-100</strong> - 10MB 到 100MB 之间</div>
                        <div>• <strong>0-100</strong> - 小于等于 100MB</div>
                      </div>
                    </div>
                  </span>
                </label>
                <input
                  type="text"
                  value={formSizeRange}
                  onChange={(e) => setFormSizeRange(e.target.value)}
                  placeholder="例如: 0 或 10 或 10-100"
                  style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                />
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  保存路径（可选）
                </label>
                <div style={{ 
                  border: "1px solid #ddd", 
                  borderRadius: "4px", 
                  padding: "0.5rem",
                  maxHeight: "200px",
                  overflowY: "auto",
                  backgroundColor: "#fafafa"
                }}>
                  <div style={{ marginBottom: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                    <button 
                      onClick={() => {
                        setCurrentBrowsePath("");
                        setFormSaveDir("");
                        fetchDirectories("");
                      }}
                      style={{ 
                        padding: "0.4rem 0.8rem",
                        fontSize: "0.85rem",
                        border: "1px solid #2196f3",
                        backgroundColor: "white",
                        color: "#2196f3",
                        borderRadius: "4px",
                        cursor: "pointer"
                      }}
                    >
                      🏠 返回根目录
                    </button>
                    <button onClick={() => fetchDirectories(currentBrowsePath)} style={{ padding: "0.4rem 0.8rem", fontSize: "0.85rem" }}>🔄 刷新</button>
                    <button onClick={handleCreateDirectory} style={{ padding: "0.4rem 0.8rem", fontSize: "0.85rem" }}>➕ 新建文件夹</button>
                    {formSaveDir && (
                      <button onClick={handleRenameDirectory} style={{ padding: "0.4rem 0.8rem", fontSize: "0.85rem" }}>✏️ 重命名</button>
                    )}
                  </div>
                  {dirLoading ? (
                    <div style={{ padding: "1rem", textAlign: "center", color: "#666" }}>加载中...</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                      {/* 面包屑导航 */}
                      {currentBrowsePath && (
                        <div style={{ marginBottom: "0.5rem", padding: "0.5rem", backgroundColor: "#f5f5f5", borderRadius: "4px", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                          <button
                            type="button"
                            onClick={() => {
                              const parentPath = currentBrowsePath.split("/").slice(0, -1).join("/");
                              setCurrentBrowsePath(parentPath);
                              fetchDirectories(parentPath);
                            }}
                            style={{
                              padding: "0.25rem 0.5rem",
                              fontSize: "0.8rem",
                              border: "1px solid #ddd",
                              borderRadius: "4px",
                              backgroundColor: "white",
                              cursor: "pointer"
                            }}
                          >
                            ← 返回
                          </button>
                          <span style={{ fontSize: "0.85rem", color: "#666" }}>
                            当前路径: /{currentBrowsePath || "根目录"}
                          </span>
                        </div>
                      )}
                      {/* 目录列表 */}
                      {dirOptions
                        .filter((p): p is string => typeof p === "string" && p !== "")
                        .map((path) => {
                          const pathParts = path.split("/");
                          const displayName = pathParts[pathParts.length - 1];
                          const isSelected = formSaveDir === path;
                          return (
                            <div key={path} style={{ display: "flex", gap: "0.25rem" }}>
                              <button
                                type="button"
                                onClick={() => {
                                  // 进入目录
                                  setCurrentBrowsePath(path);
                                  fetchDirectories(path);
                                }}
                                style={{
                                  flex: 1,
                                  padding: "0.5rem 0.75rem",
                                  textAlign: "left",
                                  border: "1px solid #e0e0e0",
                                  borderRadius: "4px",
                                  backgroundColor: "white",
                                  color: "#333",
                                  cursor: "pointer",
                                  fontSize: "0.85rem",
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "0.5rem"
                                }}
                              >
                                <span>📁</span>
                                <span style={{ flex: 1 }}>{displayName}</span>
                                <span style={{ color: "#999", fontSize: "0.75rem" }}>→</span>
                              </button>
                              <button
                                type="button"
                                onClick={() => setFormSaveDir(path)}
                                style={{
                                  padding: "0.5rem 0.75rem",
                                  border: `1px solid ${isSelected ? "#2196f3" : "#e0e0e0"}`,
                                  borderRadius: "4px",
                                  backgroundColor: isSelected ? "#2196f3" : "white",
                                  color: isSelected ? "white" : "#2196f3",
                                  cursor: "pointer",
                                  fontSize: "0.85rem",
                                  whiteSpace: "nowrap"
                                }}
                              >
                                {isSelected ? "✓ 已选择" : "选择"}
                              </button>
                            </div>
                          );
                        })}
                      {dirOptions.filter((p): p is string => typeof p === "string" && p !== "").length === 0 && (
                        <div style={{ padding: "1rem", textAlign: "center", color: "#999", fontSize: "0.85rem" }}>
                          {currentBrowsePath ? "此目录下没有子目录" : '暂无目录，点击"新建文件夹"创建'}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                  当前选择: {formSaveDir ? `/${formSaveDir}` : `默认路径: /${defaultDownloadPath}`}
                </small>
                {!formSaveDir && defaultDownloadPath && (
                  <div style={{ marginTop: "0.5rem", padding: "0.5rem", backgroundColor: "#fff3cd", border: "1px solid #ffc107", borderRadius: "4px" }}>
                    <small style={{ color: "#856404", fontSize: "0.8rem" }}>
                      ⚠️ 未指定保存路径，将使用默认下载路径: <strong>/{defaultDownloadPath}</strong>
                    </small>
                  </div>
                )}
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  文件名模板
                </label>
                <input
                  type="text"
                  value={formFilenameTemplate}
                  onChange={(e) => setFormFilenameTemplate(e.target.value)}
                  placeholder="{task_id}_{message_id}_{chat_title}"
                  style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                />
                <div style={{ marginTop: "0.5rem", border: "1px solid #eee", borderRadius: "6px", padding: "0.5rem" }}>
                  <div style={{ fontWeight: 600, marginBottom: "0.5rem" }}>可用变量（点击复制）</div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "0.5rem" }}>
                    {[
                      { key: "{task_id}", desc: "任务ID" },
                      { key: "{message_id}", desc: "消息ID" },
                      { key: "{chat_title}", desc: "群聊名称" },
                      { key: "{timestamp}", desc: "时间戳" },
                      { key: "{file_name}", desc: "原始文件名" },
                      { key: "{year}", desc: "年份" },
                      { key: "{month}", desc: "月份(01-12)" },
                      { key: "{day}", desc: "日期(01-31)" },
                    ].map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => navigator.clipboard?.writeText(item.key)}
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          width: "100%",
                          padding: "0.45rem 0.6rem",
                          borderRadius: "4px",
                          border: "1px solid #ddd",
                          background: "#fafafa",
                          cursor: "pointer",
                        }}
                        title="点击复制变量"
                      >
                        <span style={{ fontFamily: "monospace", fontSize: "0.8rem" }}>{item.key}</span>
                        <span style={{ color: "#666", fontSize: "0.75rem" }}>{item.desc}</span>
                      </button>
                    ))}
                  </div>
                  <small style={{ display: "block", marginTop: "0.5rem", color: "#666", fontSize: "0.8rem" }}>
                    💡 支持文件夹：<code style={{ background: "#f0f0f0", padding: "0.1rem 0.3rem", borderRadius: "3px" }}>{`{chat_title}/{year}-{month}/{file_name}`}</code>
                  </small>
                  <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                    示例：<code style={{ background: "#f0f0f0", padding: "0.1rem 0.3rem", borderRadius: "3px" }}>{`{task_id}_{message_id}_{file_name}`}</code>
                  </small>
                </div>
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  关键词过滤
                </label>
                <div style={{ display: "flex", gap: "1rem", marginBottom: "0.5rem" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <input
                      type="radio"
                      checked={formMatchMode === "all"}
                      onChange={() => setFormMatchMode("all")}
                    />
                    不过滤
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <input
                      type="radio"
                      checked={formMatchMode === "include"}
                      onChange={() => setFormMatchMode("include")}
                    />
                    包含关键词
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <input
                      type="radio"
                      checked={formMatchMode === "exclude"}
                      onChange={() => setFormMatchMode("exclude")}
                    />
                    排除关键词
                  </label>
                </div>
                {formMatchMode === "include" && (
                  <input
                    type="text"
                    value={formIncludeKeywords}
                    onChange={(e) => setFormIncludeKeywords(e.target.value)}
                    placeholder="包含关键词，逗号分隔"
                    style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                  />
                )}
                {formMatchMode === "exclude" && (
                  <input
                    type="text"
                    value={formExcludeKeywords}
                    onChange={(e) => setFormExcludeKeywords(e.target.value)}
                    placeholder="排除关键词，逗号分隔"
                    style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                  />
                )}
              </div>

              <div>
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: "500" }}>
                  <input
                    type="checkbox"
                    checked={formAddDownloadSuffix}
                    onChange={(e) => setFormAddDownloadSuffix(e.target.checked)}
                  />
                  为未完成文件添加 .download 后缀
                </label>
                <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                  下载过程中会在文件名末尾添加 .download 后缀，下载完成后自动移除
                </small>
              </div>

              <div>
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: "500" }}>
                  <input
                    type="checkbox"
                    checked={formMoveAfterComplete}
                    onChange={(e) => setFormMoveAfterComplete(e.target.checked)}
                  />
                  文件完整下载后才移动到目标目录
                </label>
                <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                  开启后会先下载到目标目录下的隐藏临时目录，完成后再移动到最终路径，避免目标目录出现未完成文件
                </small>
              </div>

              <div>
                <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: "500" }}>
                  <input
                    type="checkbox"
                    checked={formAutoCatchUp}
                    onChange={(e) => setFormAutoCatchUp(e.target.checked)}
                  />
                  启动时自动下载遗漏消息
                </label>
                <small style={{ display: "block", marginTop: "0.25rem", color: "#666", fontSize: "0.8rem" }}>
                  开启后程序启动会扫描本群自上次记录以来的新消息，并按本规则匹配下载
                </small>
              </div>
            </div>

            <div style={{ marginTop: "2rem", display: "flex", gap: "1rem", justifyContent: "flex-end" }}>
              <button
                onClick={() => setShowRuleModal(false)}
                style={{
                  padding: "0.6rem 1.5rem",
                  backgroundColor: "#f5f5f5",
                  color: "#333",
                  border: "none",
                  borderRadius: "6px",
                  cursor: "pointer",
                }}
              >
                取消
              </button>
              <button
                onClick={handleSaveRule}
                style={{
                  padding: "0.6rem 1.5rem",
                  backgroundColor: "#2196f3",
                  color: "white",
                  border: "none",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontWeight: "500",
                }}
              >
                {editingRuleId ? "保存修改" : "创建规则"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 默认下载路径选择模态框 */}
      {showDefaultPathModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: "rgba(0,0,0,0.5)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}>
          <div style={{
            backgroundColor: "white",
            borderRadius: "8px",
            padding: "1.5rem",
            maxWidth: "600px",
            width: "90%",
            maxHeight: "80vh",
            overflowY: "auto",
          }}>
            <h2 style={{ marginTop: 0, marginBottom: "1rem" }}>选择默认下载路径</h2>
            <p style={{ marginTop: 0, marginBottom: "0.75rem", fontSize: "0.9rem", color: "#555" }}>
              该路径将作为所有未指定保存路径任务的默认保存位置，建议选择挂载到宿主机的目录（例如 <code>/overwach</code>）。
            </p>
            <div style={{ 
              border: "1px solid #ddd", 
              borderRadius: "4px", 
              padding: "0.5rem",
              maxHeight: "260px",
              overflowY: "auto",
              backgroundColor: "#fafafa"
            }}>
              <div style={{ marginBottom: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                <button 
                  onClick={() => {
                    setCurrentBrowsePath("");
                    fetchDirectories("");
                  }}
                  style={{ 
                    padding: "0.4rem 0.8rem",
                    fontSize: "0.85rem",
                    border: "1px solid #2196f3",
                    backgroundColor: "white",
                    color: "#2196f3",
                    borderRadius: "4px",
                    cursor: "pointer"
                  }}
                >
                  🏠 根目录
                </button>
                <button onClick={() => fetchDirectories(currentBrowsePath)} style={{ padding: "0.4rem 0.8rem", fontSize: "0.85rem" }}>🔄 刷新</button>
              </div>
              {dirLoading ? (
                <div style={{ padding: "1rem", textAlign: "center", color: "#666" }}>加载中...</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                  {/* 面包屑导航 */}
                  {currentBrowsePath && (
                    <div style={{ marginBottom: "0.5rem", padding: "0.5rem", backgroundColor: "#f5f5f5", borderRadius: "4px", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <button
                        type="button"
                        onClick={() => {
                          const parentPath = currentBrowsePath.split("/").slice(0, -1).join("/");
                          setCurrentBrowsePath(parentPath);
                          fetchDirectories(parentPath);
                        }}
                        style={{
                          padding: "0.25rem 0.5rem",
                          fontSize: "0.8rem",
                          border: "1px solid #ddd",
                          borderRadius: "4px",
                          backgroundColor: "white",
                          cursor: "pointer"
                        }}
                      >
                        ← 返回
                      </button>
                      <span style={{ fontSize: "0.85rem", color: "#666" }}>
                        当前路径: /{currentBrowsePath || "根目录"}
                      </span>
                    </div>
                  )}
                  {/* 目录列表 */}
                  {dirOptions
                    .filter((p): p is string => typeof p === "string" && p !== "")
                    .map((path) => {
                      const pathParts = path.split("/");
                      const displayName = pathParts[pathParts.length - 1];
                      const isSelected = selectedDefaultPath === path;
                      return (
                        <div key={path} style={{ display: "flex", gap: "0.25rem" }}>
                          <button
                            type="button"
                            onClick={() => {
                              // 进入目录
                              setCurrentBrowsePath(path);
                              fetchDirectories(path);
                            }}
                            style={{
                              flex: 1,
                              padding: "0.5rem 0.75rem",
                              textAlign: "left",
                              border: "1px solid #e0e0e0",
                              borderRadius: "4px",
                              backgroundColor: "white",
                              color: "#333",
                              cursor: "pointer",
                              fontSize: "0.85rem",
                              display: "flex",
                              alignItems: "center",
                              gap: "0.5rem"
                            }}
                          >
                            <span>📁</span>
                            <span style={{ flex: 1 }}>{displayName}</span>
                            <span style={{ color: "#999", fontSize: "0.75rem" }}>→</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => setSelectedDefaultPath(path)}
                            style={{
                              padding: "0.5rem 0.75rem",
                              border: `1px solid ${isSelected ? "#2196f3" : "#e0e0e0"}`,
                              borderRadius: "4px",
                              backgroundColor: isSelected ? "#2196f3" : "white",
                              color: isSelected ? "white" : "#2196f3",
                              cursor: "pointer",
                              fontSize: "0.85rem",
                              whiteSpace: "nowrap"
                            }}
                          >
                            {isSelected ? "✓ 已选择" : "选择"}
                          </button>
                        </div>
                      );
                    })}
                  {dirOptions.filter((p): p is string => typeof p === "string" && p !== "").length === 0 && (
                    <div style={{ padding: "1rem", textAlign: "center", color: "#999", fontSize: "0.85rem" }}>
                      {currentBrowsePath ? "此目录下没有子目录" : '暂无目录，请在宿主机中创建或挂载目录后再刷新'}
                    </div>
                  )}
                </div>
              )}
            </div>
            <small style={{ display: "block", marginTop: "0.5rem", color: "#666", fontSize: "0.8rem" }}>
              当前选择：{selectedDefaultPath ? `/${selectedDefaultPath}` : defaultDownloadPath ? `保持现有：${defaultDownloadPath}` : "未选择"}
            </small>
            <div style={{ marginTop: "1.5rem", display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                onClick={() => setShowDefaultPathModal(false)}
                style={{
                  padding: "0.5rem 1.2rem",
                  backgroundColor: "#f5f5f5",
                  borderRadius: "6px",
                  border: "none",
                  cursor: "pointer",
                }}
              >
                取消
              </button>
              <button
                onClick={async () => {
                  try {
                    // 如果选择了目录，用 / + 相对路径 作为绝对路径；否则保持原值
                    const targetPath = selectedDefaultPath
                      ? `/${selectedDefaultPath}`
                      : defaultDownloadPath;
                    if (!targetPath) {
                      showNotification("请先选择一个目录", "info");
                      return;
                    }
                    await api.put("/config/default-download-path", { path: targetPath });
                    await fetchDefaultDownloadPath();
                    showNotification("默认下载路径已更新", "success");
                    setShowDefaultPathModal(false);
                  } catch (error) {
                    console.error("Failed to update default download path:", error);
                    showNotification("更新默认下载路径失败", "error");
                  }
                }}
                style={{
                  padding: "0.5rem 1.5rem",
                  backgroundColor: "#2196f3",
                  color: "white",
                  borderRadius: "6px",
                  border: "none",
                  cursor: "pointer",
                  fontWeight: 500,
                }}
              >
                保存为默认路径
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 默认文件名模板编辑模态框 */}
      {showFilenameTemplateModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: "rgba(0,0,0,0.5)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}>
          <div style={{
            backgroundColor: "white",
            borderRadius: "8px",
            padding: "1.5rem",
            maxWidth: "500px",
            width: "90%",
          }}>
            <h2 style={{ marginTop: 0, marginBottom: "1rem" }}>编辑默认文件名模板</h2>
            <p style={{ marginTop: 0, marginBottom: "1rem", fontSize: "0.9rem", color: "#555" }}>
              设置默认的文件名模板，当规则未指定文件名模板时将使用此模板。
            </p>

            <div style={{ marginBottom: "1rem" }}>
              <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: 600 }}>
                文件名模板
              </label>
              <input
                type="text"
                value={defaultFilenameTemplate}
                onChange={(e) => setDefaultFilenameTemplate(e.target.value)}
                placeholder="{task_id}_{file_name}"
                style={{
                  width: "100%",
                  padding: "0.5rem",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  fontSize: "0.9rem",
                }}
              />
            </div>

            <div style={{ marginBottom: "1rem", padding: "0.75rem", backgroundColor: "#f8f9fa", borderRadius: "4px" }}>
              <h4 style={{ margin: "0 0 0.5rem 0", fontSize: "0.9rem", color: "#333" }}>可用变量</h4>
              <div style={{ fontSize: "0.85rem", color: "#666", lineHeight: "1.4" }}>
                <div><code>{`{task_id}`}</code> - 下载任务ID</div>
                <div><code>{`{message_id}`}</code> - 消息ID</div>
                <div><code>{`{chat_title}`}</code> - 群聊标题</div>
                <div><code>{`{timestamp}`}</code> - 时间戳</div>
                <div><code>{`{file_name}`}</code> - 原始文件名</div>
                <div><code>{`{year}`}</code> - 年份 (4位)</div>
                <div><code>{`{month}`}</code> - 月份 (2位)</div>
                <div><code>{`{day}`}</code> - 日期 (2位)</div>
              </div>
            </div>

            <div style={{ marginBottom: "1rem", padding: "0.75rem", backgroundColor: "#e3f2fd", borderRadius: "4px" }}>
              <div style={{ fontSize: "0.85rem", color: "#1565c0" }}>
                <strong>示例：</strong> {defaultFilenameTemplate.replace('{task_id}', '123').replace('{file_name}', 'video.mp4')}
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                onClick={() => setShowFilenameTemplateModal(false)}
                style={{
                  padding: "0.5rem 1.2rem",
                  backgroundColor: "#f5f5f5",
                  borderRadius: "6px",
                  border: "none",
                  cursor: "pointer",
                }}
              >
                取消
              </button>
              <button
                onClick={async () => {
                  try {
                    if (!defaultFilenameTemplate.trim()) {
                      showNotification("文件名模板不能为空", "error");
                      return;
                    }
                    await api.put("/config/default-filename-template", {
                      template: defaultFilenameTemplate.trim()
                    });
                    await fetchDefaultFilenameTemplate();
                    showNotification("默认文件名模板已更新", "success");
                    setShowFilenameTemplateModal(false);
                  } catch (error) {
                    console.error("Failed to update default filename template:", error);
                    showNotification("更新默认文件名模板失败", "error");
                  }
                }}
                style={{
                  padding: "0.5rem 1.5rem",
                  backgroundColor: "#2196f3",
                  color: "white",
                  borderRadius: "6px",
                  border: "none",
                  cursor: "pointer",
                  fontWeight: 500,
                }}
              >
                保存模板
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
