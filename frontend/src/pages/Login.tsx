import { useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import ThemeToggle from "../components/ThemeToggle";
import ThemeLogo from "../components/ThemeLogo";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
});

export default function Login() {
  const [username, setUsername] = useState(
    localStorage.getItem("last_username") || "admin"
  );
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.post("/admin/login", {
        username,
        password,
      });
      if (data?.token) {
        localStorage.setItem("admin_token", data.token);
        localStorage.setItem("last_username", username);
        setPassword("");
        navigate("/", { replace: true });
      } else {
        setError("登录失败，请稍后重试");
      }
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        setError(typeof detail === "string" ? detail : "登录失败");
      } else {
        setError("登录失败");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="login-page"
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
      }}
    >
      <div className="login-theme-control">
        <ThemeToggle />
      </div>
      <div
        className="login-card"
        style={{
          width: "100%",
          maxWidth: "400px",
          borderRadius: "0.75rem",
          boxShadow: "0 10px 25px var(--theme-shadow)",
          padding: "2rem",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
          <ThemeLogo height="72px" width="215px" />
          <h2 className="login-title" style={{ margin: 0, fontSize: "1.25rem" }}>Telegram Depiler 控制台</h2>
          <p className="login-subtitle" style={{ margin: "0.5rem 0 0", fontSize: "0.9rem" }}>
            请输入面板账号密码登录（默认账号/密码：admin / admin）
          </p>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "grid", gap: "0.75rem" }} autoComplete="off">
          <label style={{ display: "grid", gap: "0.25rem", fontSize: "0.9rem" }}>
            <span>用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              name="login-username"
              style={{
                padding: "0.6rem 0.75rem",
                borderRadius: "0.5rem",
                border: "1px solid var(--theme-border)",
                outline: "none",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "0.25rem", fontSize: "0.9rem" }}>
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              name="login-password"
              style={{
                padding: "0.6rem 0.75rem",
                borderRadius: "0.5rem",
                border: "1px solid var(--theme-border)",
                outline: "none",
              }}
            />
          </label>

          {error && (
            <div
              style={{
                marginTop: "0.25rem",
                padding: "0.5rem 0.75rem",
                borderRadius: "0.5rem",
                backgroundColor: "var(--color-danger-surface)",
                color: "var(--color-danger-strong)",
                fontSize: "0.85rem",
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              marginTop: "0.5rem",
              padding: "0.6rem 0.75rem",
              borderRadius: "0.5rem",
              border: "none",
              backgroundColor: loading ? "var(--theme-primary-muted)" : "var(--theme-primary)",
              color: "var(--theme-on-primary)",
              fontWeight: 500,
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}


