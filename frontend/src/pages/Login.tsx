import { useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
});

export default function Login() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
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
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "#f3f4f6",
        padding: "1rem",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "400px",
          backgroundColor: "#ffffff",
          borderRadius: "0.75rem",
          boxShadow: "0 10px 25px rgba(15,23,42,0.08)",
          padding: "2rem",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
          <img
            src="/images/logo2.png"
            alt="Telegram Depiler Logo"
            style={{ height: "72px", objectFit: "contain", marginBottom: "0.75rem" }}
          />
          <h2 style={{ margin: 0, fontSize: "1.25rem" }}>Telegram Depiler 控制台</h2>
          <p style={{ margin: "0.5rem 0 0", color: "#6b7280", fontSize: "0.9rem" }}>
            请输入面板账号密码登录（默认 admin / admin）
          </p>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "grid", gap: "0.75rem" }}>
          <label style={{ display: "grid", gap: "0.25rem", fontSize: "0.9rem" }}>
            <span>用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              style={{
                padding: "0.6rem 0.75rem",
                borderRadius: "0.5rem",
                border: "1px solid #d1d5db",
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
              autoComplete="current-password"
              style={{
                padding: "0.6rem 0.75rem",
                borderRadius: "0.5rem",
                border: "1px solid #d1d5db",
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
                backgroundColor: "#fef2f2",
                color: "#b91c1c",
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
              backgroundColor: loading ? "#93c5fd" : "#2563eb",
              color: "#ffffff",
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


