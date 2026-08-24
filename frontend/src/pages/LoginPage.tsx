import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ThemeToggle } from "../theme";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const user = await login(username, password);
      navigate(user.is_staff ? "/admin-panel" : "/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-toolbar">
        <ThemeToggle />
      </div>
      <form className="auth-card" onSubmit={onSubmit}>
        <span className="orb" aria-hidden="true" />
        <h1>Welcome back</h1>
        <p>Sign in to the BahriaAI policy console.</p>
        {error ? <div className="error">{error}</div> : null}
        <label htmlFor="username">Email or username</label>
        <input
          id="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          placeholder="arshadkhan@gmail.com"
          required
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
        />
        <button className="btn btn-navy" style={{ width: "100%", marginTop: 18 }} disabled={busy}>
          {busy ? "Signing in…" : "Log in"}
        </button>
        <p style={{ marginTop: 16 }}>
          <Link to="/">Back to the policy bot</Link>
        </p>
      </form>
    </div>
  );
}
