import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";

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
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
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
  const [loginState, setLoginState] = useState<LoginState | null>(null);
  const [panelUsername, setPanelUsername] = useState("");
  const [panelPassword, setPanelPassword] = useState("");

  const proxy = useMemo(
    () =>
      config.proxy ?? {
        type: "socks5",
        host: "",
        port: 0,
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
    setConfig((prev) => ({ ...prev, ...data }));
  };

  const fetchLoginState = async () => {
    try {
      const { data } = await api.get("/auth/status");
      setLoginState(data);
      if (data.is_authorized) {
        setLoginStep("connected");
      }
    } catch (error) {
      console.error("获取登录状态失败:", error);
    }
  };

  useEffect(() => {
    fetchConfig();
    fetchLoginState();
  }, []);

  const formatError = (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const detail = error.response?.data?.detail;
      if (typeof detail === "string") {
        return detail;
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
      await api.post("/config", {
        ...config,
        api_id: Number(config.api_id),
        proxy,
      });
      setMessage({ type: "success", text: "配置已保存" });
    } catch (error) {
      setMessage({ type: "error", text: `保存失败：${formatError(error)}` });
    } finally {
      setLoading(false);
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
    <div className="app" style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2rem" }}>
        <h1>系统设置</h1>
        <Link to="/" style={{ 
          padding: "0.5rem 1rem", 
          backgroundColor: "#2196f3", 
          color: "white", 
          textDecoration: "none", 
          borderRadius: "4px" 
        }}>
          ← 返回主页
        </Link>
      </div>

      <div className="card">
        <h2>基础配置</h2>
        <form onSubmit={saveConfig}>
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
              style={{ borderColor: validationErrors.api_id ? "#f44336" : "#ddd" }}
            />
            {validationErrors.api_id && (
              <small style={{ color: "#f44336", display: "block", marginTop: "4px" }}>
                {validationErrors.api_id}
              </small>
            )}
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
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
              style={{ borderColor: validationErrors.api_hash ? "#f44336" : "#ddd" }}
            />
            {validationErrors.api_hash && (
              <small style={{ color: "#f44336", display: "block", marginTop: "4px" }}>
                {validationErrors.api_hash}
              </small>
            )}
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
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
              style={{ borderColor: validationErrors.phone_number ? "#f44336" : "#ddd" }}
            />
            {validationErrors.phone_number && (
              <small style={{ color: "#f44336", display: "block", marginTop: "4px" }}>
                {validationErrors.phone_number}
              </small>
            )}
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
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
              style={{ borderColor: validationErrors.bot_token ? "#f44336" : "#ddd" }}
            />
            {validationErrors.bot_token && (
              <small style={{ color: "#f44336", display: "block", marginTop: "4px" }}>
                {validationErrors.bot_token}
              </small>
            )}
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
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
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
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
              style={{ borderColor: validationErrors.admin_user_ids ? "#f44336" : "#ddd" }}
            />
            {validationErrors.admin_user_ids && (
              <small style={{ color: "#f44336", display: "block", marginTop: "4px" }}>
                {validationErrors.admin_user_ids}
              </small>
            )}
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
              必须是纯数字ID，多个用逗号分隔。获取ID：搜索 @userinfobot
            </small>
          </label>

          <div>
            <strong>代理设置（可选）</strong>
          </div>

          <label>
            代理类型
            <select
              value={proxy.type || "socks5"}
              onChange={(e) =>
                setConfig({
                  ...config,
                  proxy: { ...proxy, type: e.target.value },
                })
              }
              style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ddd" }}
            >
              <option value="socks5">SOCKS5</option>
              <option value="http">HTTP</option>
            </select>
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
              选择代理协议类型
            </small>
          </label>

          <label>
            Host
            <input
              value={proxy.host}
              onChange={(e) =>
                setConfig({
                  ...config,
                  proxy: { ...proxy, host: e.target.value },
                })
              }
              placeholder="127.0.0.1 或 host.docker.internal"
            />
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
              Docker 容器访问宿主机代理请使用：host.docker.internal
            </small>
          </label>

          <label>
            Port
            <input
              type="number"
              value={proxy.port ?? ""}
              onChange={(e) => {
                setConfig({
                  ...config,
                  proxy: { ...proxy, port: Number(e.target.value) },
                });
                setValidationErrors({ ...validationErrors, proxy_port: "" });
              }}
              placeholder="1080"
              min="1"
              max="65535"
              style={{ borderColor: validationErrors.proxy_port ? "#f44336" : "#ddd" }}
            />
            {validationErrors.proxy_port && (
              <small style={{ color: "#f44336", display: "block", marginTop: "4px" }}>
                {validationErrors.proxy_port}
              </small>
            )}
          </label>

          <label>
            用户名
            <input
              value={proxy.user ?? ""}
              onChange={(e) =>
                setConfig({
                  ...config,
                  proxy: { ...proxy, user: e.target.value },
                })
              }
            />
          </label>

          <label>
            密码
            <input
              value={proxy.password ?? ""}
              onChange={(e) =>
                setConfig({
                  ...config,
                  proxy: { ...proxy, password: e.target.value },
                })
              }
            />
          </label>

          <button type="submit" className="btn-primary" disabled={loading}>
            保存配置
          </button>
        </form>
      </div>

      <div className="card">
        <h2>面板账号</h2>
        <p style={{ color: "#666", fontSize: "0.9rem", marginTop: 0 }}>
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

      <div className="card">
        <h2>登录状态</h2>
        {loginState?.is_authorized ? (
          <div style={{ 
            padding: "1rem", 
            backgroundColor: "#d4edda", 
            border: "1px solid #c3e6cb", 
            borderRadius: "4px",
            marginBottom: "1rem"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "1.5rem" }}>✅</span>
              <strong style={{ color: "#155724" }}>已登录</strong>
            </div>
            <div style={{ color: "#155724", marginTop: "0.5rem" }}>
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
          <div style={{ 
            padding: "1rem", 
            backgroundColor: "#fff3cd", 
            border: "1px solid #ffeaa7", 
            borderRadius: "4px",
            marginBottom: "1rem"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "1.5rem" }}>⚠️</span>
              <strong style={{ color: "#856404" }}>未登录</strong>
            </div>
            <p style={{ color: "#856404", marginTop: "0.5rem", marginBottom: 0 }}>
              请完成登录流程以使用机器人功能。
            </p>
          </div>
        )}
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
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
        {message && (
          <p className={`feedback ${message.type}`} style={{ marginTop: "1rem" }}>
            {message.text}
          </p>
        )}
      </div>

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
    </div>
  );
}

