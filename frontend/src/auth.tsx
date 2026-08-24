import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ensureCsrf, type User } from "./api";

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

function isLoggedIn(data: { authenticated?: boolean; user: User | null }) {
  return Boolean(data.authenticated && data.user);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    await ensureCsrf();
    const data = await api.me();
    setUser(isLoggedIn(data) ? data.user : null);
  };

  useEffect(() => {
    refresh()
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login: async (username, password) => {
        const next = await api.login(username, password);
        setUser(next);
        return next;
      },
      logout: async () => {
        try {
          await api.logout();
        } catch {
          // Still end the local session if the API call fails (stale CSRF, etc.).
        } finally {
          setUser(null);
        }
      },
      refresh,
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
