import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000/api",
});

type DownloadRecord = {
  id: number;
  file_name: string;
  status: string;
  progress: number;
  download_speed?: number;
  created_at: string;
  error?: string;
};

type GroupRule = {
  id: number;
  chat_id: number;
  chat_title?: string;
  mode: string;
  include_extensions?: string;
  min_size_bytes?: number;
  save_dir?: string;
  filename_template?: string;
  match_mode?: string;
  include_keywords?: string;
  exclude_keywords?: string;
  start_time?: string;
  end_time?: string;
  enabled: boolean;
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
  const [downloadTab, setDownloadTab] = useState<"downloading" | "completed">("downloading");
  const [showRuleModal, setShowRuleModal] = useState(false);
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  
  // 规则表单状态
  const [formChatId, setFormChatId] = useState<number | "">("");
  const [dialogPage, setDialogPage] = useState(0);
  const [dialogSearch, setDialogSearch] = useState("");
  const [formMode, setFormMode] = useState<"monitor" | "history">("monitor");
  const [formExtensions, setFormExtensions] = useState("mp4,mp3,jpg");
  const [formMinSizeMb, setFormMinSizeMb] = useState("0");
  const [formSaveDir, setFormSaveDir] = useState("");
  const [formFilenameTemplate, setFormFilenameTemplate] = useState("{task_id}_{message_id}_{chat_title}");
  const [formMatchMode, setFormMatchMode] = useState<"all" | "include" | "exclude">("all");
  const [formIncludeKeywords, setFormIncludeKeywords] = useState("");
  const [formExcludeKeywords, setFormExcludeKeywords] = useState("");

  useEffect(() => {
    fetchDownloads();
    fetchGroupRules();
    fetchDialogs();
    fetchLogs();
    const interval = setInterval(() => {
      fetchDownloads();
      fetchLogs();
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const fetchDownloads = async () => {
    try {
      const { data } = await api.get("/downloads");
      setDownloads(data.items || []);
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

  const handlePauseDownload = async (downloadId: number) => {
    try {
      await api.post(`/downloads/${downloadId}/pause`);
      await fetchDownloads();
      alert("已暂停下载");
    } catch (error) {
      console.error("Failed to pause download:", error);
      alert("暂停失败");
    }
  };

  const handlePriorityDownload = async (downloadId: number) => {
    try {
      await api.post(`/downloads/${downloadId}/priority`);
      await fetchDownloads();
      alert("已设置优先级");
    } catch (error) {
      console.error("Failed to set priority:", error);
      alert("设置优先级失败");
    }
  };

  const handleDeleteDownload = async (downloadId: number) => {
    if (!confirm("确定要删除此下载任务吗？")) {
      return;
    }
    try {
      await api.delete(`/downloads/${downloadId}`);
      await fetchDownloads();
      alert("已删除下载任务");
    } catch (error) {
      console.error("Failed to delete download:", error);
      alert("删除失败");
    }
  };

  const handleCreateRule = () => {
    setEditingRuleId(null);
    setFormChatId("");
    setFormMode("monitor");
    setFormExtensions("mp4,mp3,jpg");
    setFormMinSizeMb("0");
    setFormSaveDir("");
    setFormFilenameTemplate("{task_id}_{message_id}_{chat_title}");
    setFormMatchMode("all");
    setFormIncludeKeywords("");
    setFormExcludeKeywords("");
    setShowRuleModal(true);
  };

  const handleEditRule = (rule: GroupRule) => {
    setEditingRuleId(rule.id);
    setFormChatId(rule.chat_id);
    setFormMode(rule.mode as "monitor" | "history");
    setFormExtensions(rule.include_extensions || "");
    setFormMinSizeMb(rule.min_size_bytes ? (rule.min_size_bytes / (1024 * 1024)).toFixed(1) : "0");
    setFormSaveDir(rule.save_dir || "");
    setFormFilenameTemplate(rule.filename_template || "{task_id}_{message_id}_{chat_title}");
    setFormMatchMode((rule.match_mode as "all" | "include" | "exclude") || "all");
    setFormIncludeKeywords(rule.include_keywords || "");
    setFormExcludeKeywords(rule.exclude_keywords || "");
    setShowRuleModal(true);
  };

  const handleSaveRule = async () => {
    if (!formChatId) {
      alert("请选择目标群聊");
      return;
    }

    const ruleData = {
      chat_id: formChatId,
      mode: formMode,
      include_extensions: formExtensions || null,
      min_size_bytes: formMinSizeMb ? Math.round(parseFloat(formMinSizeMb) * 1024 * 1024) : 0,
      save_dir: formSaveDir || null,
      filename_template: formFilenameTemplate || null,
      match_mode: formMatchMode,
      include_keywords: formMatchMode === "include" ? formIncludeKeywords : null,
      exclude_keywords: formMatchMode === "exclude" ? formExcludeKeywords : null,
      enabled: true,
    };

    try {
      if (editingRuleId) {
        await api.put(`/group-rules/${editingRuleId}`, ruleData);
      } else {
        await api.post("/group-rules", ruleData);
      }
      await fetchGroupRules();
      setShowRuleModal(false);
      alert(editingRuleId ? "规则更新成功！" : "规则创建成功！");
    } catch (error) {
      console.error("Failed to save rule:", error);
      alert("保存规则失败");
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    if (!confirm("确定要删除这条规则吗？")) {
      return;
    }
    try {
      await api.delete(`/group-rules/${ruleId}`);
      await fetchGroupRules();
      alert("规则删除成功！");
    } catch (error) {
      console.error("Failed to delete rule:", error);
      alert("删除规则失败");
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + " " + sizes[i];
  };

  return (
    <div style={{ padding: "2rem", maxWidth: "1400px", margin: "0 auto", minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <div style={{ marginBottom: "2rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <img
            src="/images/logo2.png"
            alt="Telegram Depiler Logo"
            style={{ height: "40px", objectFit: "contain" }}
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
          <div style={{ display: "grid", gap: "1rem" }}>
            {groupRules.map((rule) => (
              <div
                key={rule.id}
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
                      {rule.chat_title || `群聊 ID: ${rule.chat_id}`}
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
                  {rule.min_size_bytes && rule.min_size_bytes > 0 && (
                    <div>
                      <span style={{ color: "#666" }}>最小体积：</span>
                      <span style={{ fontWeight: "500" }}>
                        {(rule.min_size_bytes / (1024 * 1024)).toFixed(1)} MB
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
        <h2 style={{ margin: "0 0 1rem 0" }}>📥 下载记录</h2>
        <div style={{ marginBottom: "0.75rem", display: "flex", gap: "0.5rem" }}>
          <button
            onClick={() => setDownloadTab("downloading")}
            style={{
              padding: "0.25rem 0.75rem",
              borderRadius: "999px",
              border: "1px solid",
              borderColor: downloadTab === "downloading" ? "#2196f3" : "#ccc",
              backgroundColor: downloadTab === "downloading" ? "#e3f2fd" : "#f5f5f5",
              cursor: "pointer",
              fontSize: "0.9rem",
            }}
          >
            ⏳ 下载中
          </button>
          <button
            onClick={() => setDownloadTab("completed")}
            style={{
              padding: "0.25rem 0.75rem",
              borderRadius: "999px",
              border: "1px solid",
              borderColor: downloadTab === "completed" ? "#4caf50" : "#ccc",
              backgroundColor: downloadTab === "completed" ? "#e8f5e9" : "#f5f5f5",
              cursor: "pointer",
              fontSize: "0.9rem",
            }}
          >
            ✅ 已完成
          </button>
        </div>
        <div style={{ maxHeight: "400px", overflowY: "auto" }}>
          {downloads.length === 0 ? (
            <p style={{ textAlign: "center", color: "#666", padding: "2rem" }}>
              暂无下载记录
            </p>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid #e0e0e0" }}>
                  <th style={{ padding: "0.75rem", textAlign: "left" }}>文件名</th>
                  <th style={{ padding: "0.75rem", textAlign: "left" }}>状态</th>
                  <th style={{ padding: "0.75rem", textAlign: "left" }}>进度</th>
                  <th style={{ padding: "0.75rem", textAlign: "left" }}>速度</th>
                  <th style={{ padding: "0.75rem", textAlign: "left" }}>时间</th>
                  <th style={{ padding: "0.75rem", textAlign: "center" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {downloads
                  .filter((record) =>
                    downloadTab === "downloading"
                      ? record.status !== "completed"
                      : record.status === "completed"
                  )
                  .map((record) => (
                    <tr key={record.id} style={{ borderBottom: "1px solid #f0f0f0" }}>
                      <td style={{ padding: "0.75rem" }}>{record.file_name}</td>
                      <td style={{ padding: "0.75rem" }}>
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
                                : "#e3f2fd",
                            color:
                              record.status === "completed"
                                ? "#2e7d32"
                                : record.status === "failed"
                                ? "#c62828"
                                : "#1565c0",
                          }}
                        >
                          {record.status === "completed"
                            ? "✅ 完成"
                            : record.status === "downloading"
                            ? "⏳ 下载中"
                            : record.status === "failed"
                            ? "❌ 失败"
                            : record.status}
                        </span>
                      </td>
                      <td style={{ padding: "0.75rem" }}>
                        {record.status === "downloading" || record.status === "completed" ? (
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
                                  backgroundColor:
                                    record.status === "completed" ? "#4caf50" : "#2196f3",
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
                          <span style={{ fontSize: "0.9em" }}>
                            {formatBytes(record.download_speed)}/s
                          </span>
                        ) : (
                          <span style={{ color: "#999" }}>-</span>
                        )}
                      </td>
                      <td style={{ padding: "0.75rem", fontSize: "0.85rem", color: "#666" }}>
                        {new Date(record.created_at).toLocaleString()}
                      </td>
                      <td style={{ padding: "0.75rem", textAlign: "center" }}>
                        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center" }}>
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
                          {(record.status === "downloading" || record.status === "pending") && (
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
                            onClick={() => handleDeleteDownload(record.id)}
                            style={{
                              padding: "0.25rem 0.5rem",
                              fontSize: "0.8rem",
                              border: "1px solid #f44336",
                              backgroundColor: "#ffebee",
                              color: "#c62828",
                              borderRadius: "4px",
                              cursor: "pointer",
                            }}
                            title="删除任务"
                          >
                            🗑️ 删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* 日志显示 */}
      {logs.length > 0 && (
        <div className="card" style={{ marginTop: "2rem", padding: "1.5rem", backgroundColor: "white", borderRadius: "8px", boxShadow: "0 2px 4px rgba(0,0,0,0.1)" }}>
          <h2 style={{ margin: "0 0 1rem 0" }}>📋 实时日志</h2>
          <div style={{ maxHeight: "400px", overflowY: "auto", backgroundColor: "#f5f5f5", padding: "1rem", borderRadius: "4px", fontFamily: "monospace", fontSize: "0.85rem" }}>
            {logs.map((log, index) => (
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
            padding: "2rem",
            maxWidth: "600px",
            width: "90%",
            maxHeight: "90vh",
            overflowY: "auto",
          }}>
            <h2 style={{ margin: "0 0 1.5rem 0" }}>
              {editingRuleId ? "编辑规则" : "新建规则"}
            </h2>

            <div style={{ display: "grid", gap: "1rem" }}>
              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  目标群聊 * {formChatId && `(已选择: ${dialogs.find(d => d.id === formChatId)?.title || formChatId})`}
                </label>
                
                {/* 搜索框 */}
                <input
                  type="text"
                  placeholder="搜索群聊名称..."
                  value={dialogSearch}
                  onChange={(e) => {
                    setDialogSearch(e.target.value);
                    setDialogPage(0);
                  }}
                  style={{ 
                    width: "100%", 
                    padding: "0.5rem", 
                    marginBottom: "0.5rem",
                    borderRadius: "4px", 
                    border: "1px solid #ddd" 
                  }}
                />
                
                {/* 群聊列表 */}
                <div style={{ 
                  maxHeight: "300px", 
                  overflowY: "auto", 
                  border: "1px solid #ddd", 
                  borderRadius: "4px",
                  backgroundColor: "#fafafa"
                }}>
                  {(() => {
                    const groupDialogs = dialogs.filter(d => 
                      d.is_group && 
                      (dialogSearch === "" || 
                       (d.title && d.title.toLowerCase().includes(dialogSearch.toLowerCase())) ||
                       (d.username && d.username.toLowerCase().includes(dialogSearch.toLowerCase()))
                      )
                    );
                    const pageSize = 10;
                    const totalPages = Math.ceil(groupDialogs.length / pageSize);
                    const currentPageDialogs = groupDialogs.slice(dialogPage * pageSize, (dialogPage + 1) * pageSize);
                    
                    return (
                      <>
                        {groupDialogs.length === 0 ? (
                          <div style={{ padding: "2rem", textAlign: "center", color: "#999" }}>
                            {dialogSearch ? "未找到匹配的群聊" : "暂无群聊"}
                          </div>
                        ) : (
                          <>
                            {currentPageDialogs.map((d) => (
                              <div
                                key={d.id}
                                onClick={() => setFormChatId(d.id)}
                                style={{
                                  padding: "0.75rem",
                                  cursor: "pointer",
                                  backgroundColor: formChatId === d.id ? "#e3f2fd" : "transparent",
                                  borderBottom: "1px solid #e0e0e0",
                                  transition: "background-color 0.2s",
                                }}
                                onMouseEnter={(e) => {
                                  if (formChatId !== d.id) {
                                    e.currentTarget.style.backgroundColor = "#f5f5f5";
                                  }
                                }}
                                onMouseLeave={(e) => {
                                  if (formChatId !== d.id) {
                                    e.currentTarget.style.backgroundColor = "transparent";
                                  }
                                }}
                              >
                                <div style={{ fontWeight: formChatId === d.id ? "600" : "normal" }}>
                                  {formChatId === d.id && "✓ "}
                                  {d.title || d.username || `ID:${d.id}`}
                                </div>
                                {d.username && (
                                  <div style={{ fontSize: "0.85rem", color: "#666", marginTop: "0.25rem" }}>
                                    @{d.username}
                                  </div>
                                )}
                              </div>
                            ))}
                            
                            {/* 分页控制 */}
                            {totalPages > 1 && (
                              <div style={{ 
                                padding: "0.75rem", 
                                display: "flex", 
                                justifyContent: "space-between", 
                                alignItems: "center",
                                backgroundColor: "#fff",
                                borderTop: "2px solid #e0e0e0"
                              }}>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setDialogPage(Math.max(0, dialogPage - 1));
                                  }}
                                  disabled={dialogPage === 0}
                                  style={{
                                    padding: "0.25rem 0.75rem",
                                    border: "1px solid #ddd",
                                    borderRadius: "4px",
                                    backgroundColor: dialogPage === 0 ? "#f5f5f5" : "white",
                                    cursor: dialogPage === 0 ? "not-allowed" : "pointer",
                                    opacity: dialogPage === 0 ? 0.5 : 1
                                  }}
                                >
                                  ← 上一页
                                </button>
                                <span style={{ fontSize: "0.9rem", color: "#666" }}>
                                  第 {dialogPage + 1} / {totalPages} 页 (共 {groupDialogs.length} 个群聊)
                                </span>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setDialogPage(Math.min(totalPages - 1, dialogPage + 1));
                                  }}
                                  disabled={dialogPage >= totalPages - 1}
                                  style={{
                                    padding: "0.25rem 0.75rem",
                                    border: "1px solid #ddd",
                                    borderRadius: "4px",
                                    backgroundColor: dialogPage >= totalPages - 1 ? "#f5f5f5" : "white",
                                    cursor: dialogPage >= totalPages - 1 ? "not-allowed" : "pointer",
                                    opacity: dialogPage >= totalPages - 1 ? 0.5 : 1
                                  }}
                                >
                                  下一页 →
                                </button>
                              </div>
                            )}
                          </>
                        )}
                      </>
                    );
                  })()}
                </div>
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
                  文件类型
                </label>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: "0.5rem" }}>
                  {/* 视频 */}
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("mp4")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "mp4"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "mp4").join(","));
                        }
                      }}
                    />
                    <span>📹 MP4</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("mkv")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "mkv"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "mkv").join(","));
                        }
                      }}
                    />
                    <span>📹 MKV</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("avi")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "avi"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "avi").join(","));
                        }
                      }}
                    />
                    <span>📹 AVI</span>
                  </label>
                  
                  {/* 图片 */}
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("jpg")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "jpg", "jpeg"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "jpg" && x !== "jpeg").join(","));
                        }
                      }}
                    />
                    <span>🖼️ JPG</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("png")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "png"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "png").join(","));
                        }
                      }}
                    />
                    <span>🖼️ PNG</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("gif")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "gif"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "gif").join(","));
                        }
                      }}
                    />
                    <span>🖼️ GIF</span>
                  </label>
                  
                  {/* 音频 */}
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("mp3")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "mp3"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "mp3").join(","));
                        }
                      }}
                    />
                    <span>🎵 MP3</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("flac")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "flac"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "flac").join(","));
                        }
                      }}
                    />
                    <span>🎵 FLAC</span>
                  </label>
                  
                  {/* 文档 */}
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("pdf")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "pdf"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "pdf").join(","));
                        }
                      }}
                    />
                    <span>📄 PDF</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", border: "1px solid #ddd", borderRadius: "4px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={formExtensions.includes("zip")}
                      onChange={(e) => {
                        const exts = formExtensions.split(",").filter(x => x);
                        if (e.target.checked) {
                          setFormExtensions([...exts, "zip"].join(","));
                        } else {
                          setFormExtensions(exts.filter(x => x !== "zip").join(","));
                        }
                      }}
                    />
                    <span>📦 ZIP</span>
                  </label>
                </div>
                <p style={{ fontSize: "0.8rem", color: "# 666", marginTop: "0.5rem" }}>
                  选择要下载的文件类型
                </p>
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  最小体积（MB）
                </label>
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={formMinSizeMb}
                  onChange={(e) => setFormMinSizeMb(e.target.value)}
                  style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                />
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  保存路径（可选）
                </label>
                <input
                  type="text"
                  value={formSaveDir}
                  onChange={(e) => setFormSaveDir(e.target.value)}
                  placeholder="留空使用默认 downloads 目录"
                  style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                />
              </div>

              <div>
                <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: "500" }}>
                  文件名模板
                  <span
                    title="可用变量说明"
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
                      position: "relative",
                    }}
                    onMouseEnter={(e) => {
                      const tooltip = e.currentTarget.querySelector('.tooltip-content') as HTMLElement;
                      if (tooltip) tooltip.style.display = 'block';
                    }}
                    onMouseLeave={(e) => {
                      const tooltip = e.currentTarget.querySelector('.tooltip-content') as HTMLElement;
                      if (tooltip) tooltip.style.display = 'none';
                    }}
                  >
                    ?
                    <div
                      className="tooltip-content"
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
                    >
                      <div style={{ marginBottom: "0.5rem", fontWeight: "600", borderBottom: "1px solid #555", paddingBottom: "0.25rem" }}>
                        可用变量：
                      </div>
                      <div style={{ lineHeight: "1.6" }}>
                        <div><strong>{"{task_id}"}</strong> - 任务ID（数据库自增ID）</div>
                        <div><strong>{"{message_id}"}</strong> - 消息ID（Telegram消息ID）</div>
                        <div><strong>{"{chat_title}"}</strong> - 群聊名称</div>
                        <div><strong>{"{timestamp}"}</strong> - 时间戳（Unix时间戳）</div>
                        <div><strong>{"{file_name}"}</strong> - 原始文件名</div>
                      </div>
                    </div>
                  </span>
                </label>
                <input
                  type="text"
                  value={formFilenameTemplate}
                  onChange={(e) => setFormFilenameTemplate(e.target.value)}
                  placeholder="{task_id}_{message_id}_{chat_title}"
                  style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
                />
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
    </div>
  );
}
