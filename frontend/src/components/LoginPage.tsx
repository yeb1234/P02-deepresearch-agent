import { useState, FormEvent } from "react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";

const REMEMBERED_USERNAME_KEY = "zb02:remembered_username";

function readRememberedUsername(): string {
  try {
    return localStorage.getItem(REMEMBERED_USERNAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState(readRememberedUsername);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(() => Boolean(readRememberedUsername()));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;

    setError(null);
    setLoading(true);

    try {
      await login(username.trim(), password, remember);
      try {
        if (remember) {
          localStorage.setItem(REMEMBERED_USERNAME_KEY, username.trim());
        } else {
          localStorage.removeItem(REMEMBERED_USERNAME_KEY);
        }
      } catch {
        // 本地存储不可用时不影响登录
      }
    } catch (err: any) {
      setError(err.message || "登录失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen flex items-center justify-center bg-neutral-800">
      <div className="w-full max-w-sm p-8 bg-neutral-700 rounded-2xl shadow-lg">
        <h2 className="text-2xl font-semibold text-neutral-100 text-center mb-6">
          用户登录
        </h2>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="block text-sm text-neutral-300 mb-1">用户名</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 bg-neutral-600 border border-neutral-500 rounded-lg
                         text-neutral-100 placeholder-neutral-400
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              placeholder="请输入用户名"
              autoComplete="username"
              disabled={loading}
            />
          </div>

          <div>
            <label className="block text-sm text-neutral-300 mb-1">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 bg-neutral-600 border border-neutral-500 rounded-lg
                         text-neutral-100 placeholder-neutral-400
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              placeholder="请输入密码"
              autoComplete="current-password"
              disabled={loading}
            />
          </div>

          <label className="flex cursor-pointer select-none items-center justify-between gap-3 rounded-lg border border-neutral-600/70 bg-neutral-800/40 px-3 py-2 text-sm text-neutral-300 transition-colors hover:border-neutral-500">
            <span className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                disabled={loading}
                className="h-4 w-4 rounded border-neutral-500 bg-neutral-600 accent-blue-500"
              />
              记住我
            </span>
            <span className="text-xs text-neutral-500">30 天内免登录</span>
          </label>

          {error && (
            <p className="text-red-400 text-sm text-center">{error}</p>
          )}

          <Button
            type="submit"
            disabled={loading || !username.trim() || !password}
            className="w-full mt-2"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                登录中...
              </>
            ) : (
              "登录"
            )}
          </Button>
        </form>
      </div>
    </div>
  );
}
