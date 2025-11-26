import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";

type ProxySettings = {
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

  const proxy = useMemo(
    () =>
      config.proxy ?? {
        host: "",
        port: 0,
        user: "",
        password: "",
      },
    [config.proxy]
  );

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
    <div className="app">
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
            API ID
            <input
              value={config.api_id}
              onChange={(e) => setConfig({ ...config, api_id: e.target.value })}
              required
            />
          </label>
          <label>
            API Hash
            <input
              value={config.api_hash}
              onChange={(e) =>
                setConfig({ ...config, api_hash: e.target.value })
              }
              required
            />
          </label>
          <label>
            手机号（含国家码）
            <input
              value={config.phone_number}
              onChange={(e) =>
                setConfig({ ...config, phone_number: e.target.value })
              }
            />
          </label>
          <label>
            Bot Token（可选，使用bot账户时填写）
            <input
              type="password"
              value={config.bot_token || ""}
              onChange={(e) =>
                setConfig({ ...config, bot_token: e.target.value })
              }
              placeholder="从 @BotFather 获取的 Bot Token"
            />
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
              onChange={(e) =>
                setConfig({ ...config, admin_user_ids: e.target.value })
              }
              placeholder="例如: 123456789,987654321"
            />
            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
              设置后，机器人只接受处理来自这些管理员ID的消息
            </small>
          </label>

          <div>
            <strong>代理设置（可选）</strong>
          </div>

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
            />
          </label>

          <label>
            Port
            <input
              type="number"
              value={proxy.port ?? ""}
              onChange={(e) =>
                setConfig({
                  ...config,
                  proxy: { ...proxy, port: Number(e.target.value) },
                })
              }
            />
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
    </div>
  );
}

