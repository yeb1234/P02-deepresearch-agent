#!/usr/bin/env bash
set -e

# 等待 PostgreSQL 就绪（compose 的 healthcheck 之外再兜一层）
python - <<'PY'
import os, socket, sys, time

host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))

for i in range(90):
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        print("[entrypoint] postgres ready", flush=True)
        break
    except OSError:
        time.sleep(1)
else:
    print("[entrypoint] postgres not reachable, abort", file=sys.stderr, flush=True)
    sys.exit(1)
PY

# 初始化数据库（建表 + 写入初始用户，幂等）
python -m agent.db.init_db || echo "[entrypoint] init_db failed, continue anyway"

exec uvicorn agent.app:app --host 0.0.0.0 --port 2024
