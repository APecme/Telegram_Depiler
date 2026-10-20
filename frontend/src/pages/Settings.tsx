import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import ThemeToggle from "../components/ThemeToggle";
import { DownloadSettingsPanel, UpdateSettingsPanel } from "../components/SettingsPanels";

type ProxySettings = {
  type?: string;
  host: string;
  port: number;
  user?: string;
  password?: string;
};

type ConfigState = {
  api_id: string;
  api_hash: string;
  phone_number: string;
  bot_token?: string;
  bot_username: string;
  admin_user_ids?: string;
  proxy?: ProxySettings;
};

type LoginState = {
  is_authorized: boolean;
  account_type?: string;
  user_id?: number;
  username?: string;
  first_name?: string;
  last_name?: string;
  phone_number?: string;
  last_login?: string;
};

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || "/api",
});

api.interceptors.request.use((config: any) => {
  const token = localStorage.getItem("admin_token");
  if (token) {
    if (!config.headers) {
      config.headers = {};
    }
    (config.headers as Record<string, string>)["X-Admin-Token"] = token;
  }
  return config;
});

type LoginStep = "idle" | "verify_code" | "submit_password" | "connected";

export default function Settings() {
  const [config, setConfig] = useState<ConfigState>({
    api_id: "",
    api_hash: "",
    phone_number: "",
    bot_token: "",
    bot_username: "",
    admin_user_ids: "",
  });
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [loginStep, setLoginStep] = useState<LoginStep>("idle");
  const [passwordHint, setPasswordHint] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [loginState, setLoginState] = useState<LoginState | null>(null);
  const [loginStatusLoading, setLoginStatusLoading] = useState(true);
  const [loginStatusError, setLoginStatusError] = useState("");
  const [panelUsername, setPanelUsername] = useState("");
  const [panelPassword, setPanelPassword] = useState("");
  const [activeTab, setActiveTab] = useState("connection");
  const tabs = [
    { id: "connection", title: "基础连接", detail: "API 与代理" },
    { id: "login", title: "Telegram 登录", detail: "会话与验证" },
    { id: "downloads", title: "下载设置", detail: "并发与恢复" },
    { id: "updates", title: "版本更新", detail: "通道与安装" },
    { id: "account", title: "面板账号", detail: "登录与安全" },
  ];

  const proxy = useMemo(
    () =>
      config.proxy ?? {
        type: "socks5",
        host: "",
        port: 7890,
        user: "",
        password: "",
      },
    [config.proxy]
  );

  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  const validateConfig = () => {
    const errors: Record<string, string> = {};

    // API ID 验证
    if (!config.api_id || !/^\d+$/.test(config.api_id)) {
      errors.api_id = "API ID 必须是纯数字";
    }

    // API Hash 验证
    if (!config.api_hash || config.api_hash.length !== 32) {
      errors.api_hash = "API Hash 必须是32位字符串";
    }

    // 手机号验证
    if (config.phone_number && !/^\+\d{10,15}$/.test(config.phone_number)) {
      errors.phone_number = "手机号格式错误，应包含国家码，如：+8612345678901";
    }

    // Bot Token 验证
    if (config.bot_token && !/^\d+:[A-Za-z0-9_-]+$/.test(config.bot_token)) {
      errors.bot_token = "Bot Token 格式错误，应为：123456789:ABCdefGHI...";
    }

    // Admin User IDs 验证
    if (config.admin_user_ids && !/^\d+(,\d+)*$/.test(config.admin_user_ids.replace(/\s/g, ''))) {
      errors.admin_user_ids = "管理员ID必须是数字，多个用逗号分隔";
    }

    // 代理端口验证
    if (proxy.port && (proxy.port < 1 || proxy.port > 65535)) {
      errors.proxy_port = "端口号必须在 1-65535 之间";
    }

    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const fetchConfig = async () => {
    const { data } = await api.get("/config");
    setConfig((prev) => ({
      ...prev,
      ...data,
      api_id: data.api_id == null ? "" : String(data.api_id),
      api_hash: data.api_hash ?? "",
      phone_number: data.phone_number ?? "",
      bot_token: data.bot_token ?? "",
      bot_username: data.bot_username ?? "",
      admin_user_ids: data.admin_user_ids ?? "",
      proxy: {
        type: data.proxy?.type ?? "socks5",
        host: data.proxy?.host ?? "",
        port: data.proxy?.port ? Number(data.proxy.port) : 7890,
        user: data.proxy?.user ?? "",
        password: data.proxy?.password ?? "",
      },
    }));
  };

  const fetchLoginState = async () => {
    setLoginStatusLoading(true);
    setLoginStatusError("");
    try {
      const { data } = await api.get("/auth/status", { timeout: 20000 });
      setLoginState(data);
      setLoginStep((current) => data.is_authorized ? "connected" : current === "connected" ? "idle" : current);
    } catch (error) {
      setLoginState(null);
      setLoginStatusError(`无法确认登录状态：${formatError(error)}`);
    } finally {
      setLoginStatusLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig().catch(error => setMessage({ type: "error", text: `配置加载失败：${formatError(error)}` }));
    void fetchLoginState();
  }, []);

  const formatError = (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const detail = error.response?.data?.detail;
      if (typeof detail === "string") {
        return detail;
      }
      if (Array.isArray(detail)) {
        return detail
          .map((item) => {
            const location = Array.isArray(item?.loc) ? item.loc.slice(1).join(".") : "";
            return location ? `${location}: ${item?.msg ?? "参数无效"}` : item?.msg ?? "参数无效";
          })
          .join("；");
      }
      if (typeof error.response?.data?.message === "string") {
        return error.response?.data?.message;
      }
    }
    if (error instanceof Error) {
      return error.message;
    }
    return "未知错误，请稍后重试";
  };

  const saveConfig = async (event: FormEvent) => {
    event.preventDefault();
    
    if (!validateConfig()) {
      setMessage({ type: "error", text: "请检查表单中的错误" });
      return;
    }

    setLoading(true);
    setMessage(null);
    try {
      const proxyPayload = proxy.host.trim() && proxy.port
        ? { ...proxy, host: proxy.host.trim() }
        : null;
      await api.post("/config", {
        ...config,
        api_id: Number(config.api_id),
        proxy: proxyPayload,
      });
      setMessage({ type: "success", text: "配置已保存" });
    } catch (error) {
      setMessage({ type: "error", text: `保存失败：${formatError(error)}` });
    } finally {
      setLoading(false);
    }
  };

  const testProxy = async () => {
    const host = proxy.host.trim();
    if (!host || !proxy.port) {
      setProxyTestResult({ type: "error", text: "请先填写代理主机和端口" });
      return;
    }
    if (proxy.port < 1 || proxy.port > 65535) {
      setProxyTestResult({ type: "error", text: "端口号必须在 1-65535 之间" });
      return;
    }

    setProxyTesting(true);
    setProxyTestResult(null);
    try {
      const { data } = await api.post("/config/test-proxy", {
        proxy: { ...proxy, host },
      });
      setProxyTestResult({
        type: "success",
        text: `连接成功，耗时 ${data.latency_ms} ms`,
      });
    } catch (error) {
      setProxyTestResult({ type: "error", text: formatError(error) });
    } finally {
      setProxyTesting(false);
    }
  };

  const sendCode = async () => {
    setLoading(true);
    setMessage(null);
    try {
      const { data } = await api.post("/auth/send-code", {
        phone_number: config.phone_number,
      });
      if (data?.next_step === "verify_code") {
        setLoginStep("verify_code");
        setPasswordHint(null);
        setPassword("");
        setCode("");
      }
      setMessage({ type: "success", text: "验证码已发送" });
    } catch (error) {
      setMessage({ type: "error", text: `发送失败：${formatError(error)}` });
    } finally {
      setLoading(false);
    }
  };

  const restartClient = async () => {
    setLoading(true);
    setMessage(null);
    try {
      await api.post("/auth/restart", {
        reset_session: true,
      });
      setLoginStep("idle");
      setPassword("");
      setPasswordHint(null);
      setCode("");
      setMessage({ type: "success", text: "客户端已重启" });
    } catch (error) {
      setMessage({ type: "error", text: `重启失败：${formatError(error)}` });
    } finally {
      setLoading(false);
    }
  };

  const verifyCode = async () => {
    setLoading(true);
    setMessage(null);
    try {
      const payload: Record<string, unknown> = {
        phone_number: config.phone_number,
      };
      if (loginStep === "submit_password") {
        payload.step = "password";
        payload.password = password;
      } else {
        payload.step = "code";
        payload.code = code;
      }
      const { data } = await api.post("/auth/verify", payload);
      if (data?.status === "password_required") {
        setLoginStep("submit_password");
        setPasswordHint(data.password_hint ?? null);
        setMessage({ type: "success", text: "需要输入二步验证密码" });
        return;
      }
      if (data?.status === "connected") {
        setLoginStep("connected");
        setPassword("");
        setPasswordHint(null);
        setCode("");
        setMessage({ type: "success", text: "✅ 登录成功！您现在可以开始使用机器人了。" });
        fetchLoginState();
        return;
      }
      setMessage({ type: "success", text: "操作完成" });
    } catch (error) {
      setMessage({ type: "error", text: `验证失败：${formatError(error)}` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app settings-page settings-redesign">
      <header className="settings-header">
        <div><span className="settings-eyebrow">TELEGRAM DEPILER</span><h1>系统设置</h1><p>管理连接、下载策略与应用更新。</p></div>
        <div className="settings-header-actions"><ThemeToggle /><Link className="settings-back" to="/">← 返回主页</Link></div>
      </header>
      <nav className="settings-tabs" role="tablist" aria-label="设置分类">
        {tabs.map((tab, index) => <button key={tab.id} id={`tab-${tab.id}`} role="tab" type="button" aria-selected={activeTab === tab.id} aria-controls={`panel-${tab.id}`} tabIndex={activeTab === tab.id ? 0 : -1}
          onClick={() => { setActiveTab(tab.id); setMessage(null); }}
          onKeyDown={event => {
            let next = index;
            if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
            else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = tabs.length - 1;
            else return;
            event.preventDefault(); setActiveTab(tabs[next].id);
            document.getElementById(`tab-${tabs[next].id}`)?.focus();
          }}><span>{tab.title}</span><small>{tab.detail}</small></button>)}
      </nav>
      {message && <p className={`settings-${message.type === "error" ? "error" : "success"}`} role={message.type === "error" ? "alert" : "status"}>{message.text}</p>}
      <section role="tabpanel" id="panel-connection" aria-labelledby="tab-connection" hidden={activeTab !== "connection"}>
      <div className="card">
        <div className="settings-section-heading"><span className="settings-eyebrow">CONNECTION</span><h2>基础连接</h2><p>配置 Telegram API、机器人和网络代理。</p></div>
        <form className="settings-connection-form" onSubmit={saveConfig}>
          <label>
            API ID *
            <input
              value={config.api_id}
              onChange={(e) => {
                setConfig({ ...config, api_id: e.target.value });
                setValidationErrors({ ...validationErrors, api_id: "" });
              }}
              required
              placeholder="12345678"
              style={{ borderColor: validationErrors.api_id ? "var(--color-danger)" : "var(--theme-border)" }}
            />
            {validationErrors.api_id && (
              <small style={{ color: "var(--color-danger)", display: "block", marginTop: "4px" }}>
                {validationErrors.api_id}
              </small>
            )}
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              从 https://my.telegram.org 获取，必须是纯数字
            </small>
          </label>
          <label>
            API Hash *
            <input
              value={config.api_hash}
              onChange={(e) => {
                setConfig({ ...config, api_hash: e.target.value });
                setValidationErrors({ ...validationErrors, api_hash: "" });
              }}
              required
              placeholder="1234567890abcdef1234567890abcdef"
              style={{ borderColor: validationErrors.api_hash ? "var(--color-danger)" : "var(--theme-border)" }}
            />
            {validationErrors.api_hash && (
              <small style={{ color: "var(--color-danger)", display: "block", marginTop: "4px" }}>
                {validationErrors.api_hash}
              </small>
            )}
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              从 https://my.telegram.org 获取，必须是32位字符串
            </small>
          </label>
          <label>
            手机号（含国家码）
            <input
              value={config.phone_number}
              onChange={(e) => {
                setConfig({ ...config, phone_number: e.target.value });
                setValidationErrors({ ...validationErrors, phone_number: "" });
              }}
              placeholder="+8612345678901"
              style={{ borderColor: validationErrors.phone_number ? "var(--color-danger)" : "var(--theme-border)" }}
            />
            {validationErrors.phone_number && (
              <small style={{ color: "var(--color-danger)", display: "block", marginTop: "4px" }}>
                {validationErrors.phone_number}
              </small>
            )}
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              格式：+国家码+手机号，如 +8612345678901
            </small>
          </label>
          <label>
            Bot Token（可选，使用bot账户时填写）
            <input
              type="password"
              value={config.bot_token || ""}
              onChange={(e) => {
                setConfig({ ...config, bot_token: e.target.value });
                setValidationErrors({ ...validationErrors, bot_token: "" });
              }}
              placeholder="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
              style={{ borderColor: validationErrors.bot_token ? "var(--color-danger)" : "var(--theme-border)" }}
            />
            {validationErrors.bot_token && (
              <small style={{ color: "var(--color-danger)", display: "block", marginTop: "4px" }}>
                {validationErrors.bot_token}
              </small>
            )}
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              从 @BotFather 获取，格式：123456789:ABCdef...
            </small>
          </label>
          <label>
            目标 Bot 用户名
            <input
              value={config.bot_username}
              onChange={(e) =>
                setConfig({ ...config, bot_username: e.target.value })
              }
              required
            />
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              当使用用户账户时，只接收来自此Bot的消息；当使用Bot账户时，接收所有发送给Bot的消息
            </small>
          </label>
          <label>
            管理员用户ID（可选，多个用逗号分隔）
            <input
              value={config.admin_user_ids || ""}
              onChange={(e) => {
                setConfig({ ...config, admin_user_ids: e.target.value });
                setValidationErrors({ ...validationErrors, admin_user_ids: "" });
              }}
              placeholder="123456789,987654321"
              style={{ borderColor: validationErrors.admin_user_ids ? "var(--color-danger)" : "var(--theme-border)" }}
            />
            {validationErrors.admin_user_ids && (
              <small style={{ color: "var(--color-danger)", display: "block", marginTop: "4px" }}>
                {validationErrors.admin_user_ids}
              </small>
            )}
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              必须是纯数字ID，多个用逗号分隔。获取ID：搜索 @userinfobot
            </small>
          </label>

          <div className="settings-form-divider"><strong>网络代理</strong><small>版本检查和应用包下载也使用这里保存的代理。</small></div>

          <label>
            代理类型
            <select
              value={proxy.type || "socks5"}
              onChange={(e) => {
                setConfig({
                  ...config,
                  proxy: { ...proxy, type: e.target.value },
                });
                setProxyTestResult(null);
              }}
              style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid var(--theme-border)" }}
            >
              <option value="socks5">SOCKS5</option>
              <option value="http">HTTP</option>
            </select>
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              选择代理协议类型
            </small>
          </label>

          <label>
            Host
            <input
              value={proxy.host ?? ""}
              onChange={(e) => {
                setConfig({
                  ...config,
                  proxy: { ...proxy, host: e.target.value },
                });
                setProxyTestResult(null);
              }}
              placeholder="127.0.0.1 或 host.docker.internal"
            />
            <small style={{ display: "block", color: "var(--theme-muted-text)", marginTop: "4px" }}>
              Docker 容器访问宿主机代理请使用：host.docker.internal
            </small>
          </label>

          <label>
            Port
            <input
              type="number"
              value={proxy.port || 7890}
              onChange={(e) => {
                setConfig({
                  ...config,
                  proxy: { ...proxy, port: Number(e.target.value) || 7890 },
                });
                setValidationErrors({ ...validationErrors, proxy_port: "" });
                setProxyTestResult(null);
              }}
              placeholder="1080"
              min="1"
              max="65535"
              style={{ borderColor: validationErrors.proxy_port ? "var(--color-danger)" : "var(--theme-border)" }}
            />
            {validationErrors.proxy_port && (
              <small style={{ color: "var(--color-danger)", display: "block", marginTop: "4px" }}>
                {validationErrors.proxy_port}
              </small>
            )}
          </label>

          <label>
            用户名
            <input
              value={proxy.user ?? ""}
              onChange={(e) => {
                setConfig({
                  ...config,
                  proxy: { ...proxy, user: e.target.value },
                });
                setProxyTestResult(null);
              }}
            />
          </label>

          <label>
            密码
            <input
              value={proxy.password ?? ""}
              onChange={(e) => {
                setConfig({
                  ...config,
                  proxy: { ...proxy, password: e.target.value },
                });
                setProxyTestResult(null);
              }}
            />
          </label>

          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={testProxy}
              disabled={proxyTesting || !proxy.host.trim() || !proxy.port}
            >
              {proxyTesting ? "正在测试..." : "测试连通性"}
            </button>
            {proxyTestResult && (
              <span
                style={{
                  color: proxyTestResult.type === "success" ? "var(--color-success-strong)" : "var(--color-danger-strong)",
                  fontSize: "0.85rem",
                }}
              >
                {proxyTestResult.text}
              </span>
            )}
          </div>

          <button type="submit" className="btn-primary" disabled={loading}>
            保存配置
          </button>
        </form>
      </div>

      </section>
      <section role="tabpanel" id="panel-account" aria-labelledby="tab-account" hidden={activeTab !== "account"}>
      <div className="card">
        <div className="settings-section-heading"><span className="settings-eyebrow">PANEL ACCOUNT</span><h2>面板账号</h2><p>设置控制台登录凭据。</p></div>
        <p style={{ color: "var(--theme-muted-text)", fontSize: "0.9rem", marginTop: 0 }}>
          默认账号密码为 <code>admin / admin</code>，建议登录后尽快修改。
        </p>
        <div style={{ display: "grid", gap: "0.75rem", maxWidth: "400px" }}>
          <label>
            新用户名（留空则不修改）
            <input
              value={panelUsername}
              onChange={(e) => setPanelUsername(e.target.value)}
              placeholder="例如：admin123"
            />
          </label>
          <label>
            新密码（留空则不修改）
            <input
              type="password"
              value={panelPassword}
              onChange={(e) => setPanelPassword(e.target.value)}
              placeholder="例如：更复杂的密码"
            />
          </label>
          <button
            className="btn-secondary"
            disabled={loading}
            onClick={async () => {
              if (!panelUsername && !panelPassword) {
                setMessage({ type: "error", text: "请至少填写用户名或密码其中一项" });
                return;
              }
              setLoading(true);
              setMessage(null);
              try {
                await api.post("/admin/credentials", {
                  username: panelUsername || undefined,
                  password: panelPassword || undefined,
                });
                setPanelUsername("");
                setPanelPassword("");
                localStorage.removeItem("admin_token");
                setMessage({
                  type: "success",
                  text: "面板账号已更新，请使用新账号密码重新登录",
                });
              } catch (error) {
                setMessage({
                  type: "error",
                  text: `更新失败：${formatError(error)}`,
                });
              } finally {
                setLoading(false);
              }
            }}
          >
            更新面板账号
          </button>
          <button
            className="btn-secondary"
            onClick={() => {
              localStorage.removeItem("admin_token");
              window.location.href = "/login";
            }}
          >
            退出登录
          </button>
        </div>
      </div>

      </section>
      <section role="tabpanel" id="panel-login" aria-labelledby="tab-login" hidden={activeTab !== "login"}>
      <div className="card">
        <div className="settings-section-heading"><span className="settings-eyebrow">TELEGRAM SESSION</span><h2>Telegram 登录</h2><p>保存基础连接配置后，再发送验证码完成登录。</p></div>
        {loginStatusLoading ? <p role="status">正在检查 Telegram 登录状态…</p> : loginStatusError ? (
          <p role="alert">{loginStatusError}</p>
        ) : loginState?.is_authorized ? (
          <div className="auth-status auth-status-connected" style={{
            padding: "1rem", 
            backgroundColor: "var(--color-success-surface)",
            border: "1px solid var(--color-success-border)",
            borderRadius: "4px",
            marginBottom: "1rem"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "1.5rem" }}>✅</span>
              <strong style={{ color: "var(--color-success-strong)" }}>已登录</strong>
            </div>
            <div style={{ color: "var(--color-success-strong)", marginTop: "0.5rem" }}>
              <p style={{ margin: "0.25rem 0" }}>
                <strong>账户类型：</strong>{loginState.account_type === "bot" ? "Bot账户" : "用户账户"}
              </p>
              {loginState.username && (
                <p style={{ margin: "0.25rem 0" }}>
                  <strong>用户名：</strong>@{loginState.username}
                </p>
              )}
              {(loginState.first_name || loginState.last_name) && (
                <p style={{ margin: "0.25rem 0" }}>
                  <strong>姓名：</strong>{loginState.first_name || ""} {loginState.last_name || ""}
                </p>
              )}
              {loginState.phone_number && (
                <p style={{ margin: "0.25rem 0" }}>
                  <strong>手机号：</strong>{loginState.phone_number}
                </p>
              )}
              {loginState.user_id && (
                <p style={{ margin: "0.25rem 0" }}>
                  <strong>用户ID：</strong>{loginState.user_id}
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="auth-status auth-status-disconnected" style={{
            padding: "1rem", 
            backgroundColor: "var(--color-warning-surface)",
            border: "1px solid var(--color-warning-border)",
            borderRadius: "4px",
            marginBottom: "1rem"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "1.5rem" }}>⚠️</span>
              <strong style={{ color: "var(--color-warning-strong)" }}>未登录</strong>
            </div>
            <p style={{ color: "var(--color-warning-strong)", marginTop: "0.5rem", marginBottom: 0 }}>
              请完成登录流程以使用机器人功能。
            </p>
          </div>
        )}
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          <button onClick={() => void fetchLoginState()} className="btn-secondary" disabled={loginStatusLoading}>
            刷新登录状态
          </button>
          <button onClick={sendCode} className="btn-secondary" disabled={loading || loginStep === "connected"}>
            发送验证码
          </button>
          <button onClick={restartClient} className="btn-secondary" disabled={loading}>
            重启客户端
          </button>
          <input
            placeholder="验证码"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            disabled={loginStep === "connected"}
            style={{ flex: "1", minWidth: "120px" }}
          />
          {loginStep === "submit_password" && (
            <input
              type="password"
              placeholder={passwordHint ? `二步密码（提示：${passwordHint}）` : "二步密码"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ flex: "1", minWidth: "120px" }}
            />
          )}
          <button 
            onClick={verifyCode} 
            className="btn-primary" 
            disabled={loading || loginStep === "connected"}
          >
            {loginStep === "submit_password" ? "提交二步密码" : "验证并登录"}
          </button>
        </div>
        {loginStep === "submit_password" && (
          <p className="info">
            已检测到二步验证，请输入 Telegram 账户的二步密码。该密码仅用于完成登录，不会被保存。
          </p>
        )}
      </div>

      </section>
      <section role="tabpanel" id="panel-downloads" aria-labelledby="tab-downloads" hidden={activeTab !== "downloads"}><DownloadSettingsPanel /></section>
      <section role="tabpanel" id="panel-updates" aria-labelledby="tab-updates" hidden={activeTab !== "updates"}><UpdateSettingsPanel /></section>
      <footer className="settings-footer"><span>Telegram Depiler</span><a href="https://github.com/APecme/Telegram_Depiler" target="_blank" rel="noreferrer">GitHub ↗</a></footer>
    </div>
  );
}
