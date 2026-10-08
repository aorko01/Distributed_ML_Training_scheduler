import React, { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login, isAuthenticated, isBlockedError } from "../services/auth";
import { ArrowRight, Ban, Loader2 } from "lucide-react";
import AuthLayout from "../components/AuthLayout";

const BLOCKED_MESSAGE =
  "Your account has been blocked. Contact an administrator.";

const Login: React.FC = () => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    if (isAuthenticated()) {
      navigate("/");
      return;
    }
    // Arriving here via the auth layer means the account was disabled
    // mid-session (token revoked server-side).
    if (searchParams.get("blocked") === "1") {
      setBlocked(true);
      setError(BLOCKED_MESSAGE);
    }
  }, [navigate, searchParams]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setBlocked(false);

    try {
      await login(username, password);
      navigate("/");
    } catch (err) {
      if (
        isBlockedError(err) ||
        (err instanceof Error && /blocked|disabled/i.test(err.message))
      ) {
        setBlocked(true);
        setError(BLOCKED_MESSAGE);
      } else {
        setError(
          err instanceof Error ? err.message : "Invalid username or password.",
        );
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout>
      {error && (
        <div className="error-notice" role="alert">
          {blocked && <Ban size={16} />}
          <span>{error}</span>
        </div>
      )}
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label className="form-label" htmlFor="login-username">
            Username
          </label>
          <input
            id="login-username"
            type="text"
            className="form-input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Your username"
            autoComplete="username"
            required
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="login-password">
            Password
          </label>
          <input
            id="login-password"
            type="password"
            className="form-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Your password"
            autoComplete="current-password"
            required
          />
        </div>
        <button
          type="submit"
          className="btn btn-primary auth-submit"
          disabled={loading}
        >
          <span>{loading ? "Signing in…" : "Enter your workspace"}</span>
          {loading ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <ArrowRight size={16} />
          )}
        </button>
      </form>
    </AuthLayout>
  );
};

export default Login;
