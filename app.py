"""Simple monolithic task tracker: Flask + Amazon RDS (PostgreSQL).
The DB password is read from AWS Secrets Manager (plain-text password, or JSON with username/password/host/dbname)."""
import json
import os
import threading
import time
from contextlib import contextmanager

import boto3
import psycopg2
import psycopg2.pool
from flask import Flask, abort, redirect, render_template, request, url_for
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

SECRET_TTL = int(os.getenv("DB_SECRET_TTL", "300"))  # seconds to cache the secret
_lock = threading.Lock()
_pool = None
_secret = {"value": None, "at": 0.0}
_ready = False


def get_secret(force=False):
    """Fetch DB credentials from Secrets Manager (cached). Uses the EC2 instance role."""
    with _lock:
        if force or _secret["value"] is None or time.time() - _secret["at"] > SECRET_TTL:
            sm = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])
            raw = sm.get_secret_value(SecretId=os.environ["DB_SECRET_ID"])["SecretString"]
            try:
                val = json.loads(raw)
            except ValueError:
                val = None
            if not isinstance(val, dict):  # plain-text secret = just the password
                val = {"password": raw}
            _secret["value"], _secret["at"] = val, time.time()
        return _secret["value"]


def _new_pool():
    s = get_secret()
    return psycopg2.pool.ThreadedConnectionPool(
        1, 10,
        host=s.get("host") or os.environ["DB_HOST"],
        port=int(s.get("port") or os.getenv("DB_PORT", "5432")),
        dbname=s.get("dbname") or os.getenv("DB_NAME", "appdb"),
        user=s.get("username") or os.environ["DB_USER"],
        password=s["password"],
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=5,
    )


def pool():
    global _pool
    with _lock:
        existing = _pool
    if existing is None:
        new = _new_pool()
        with _lock:
            if _pool is None:
                _pool = new
            else:
                new.closeall()
            existing = _pool
    return existing


def reset_pool():
    """Drop pooled connections and re-read the secret (handles password rotation)."""
    global _pool
    with _lock:
        old, _pool = _pool, None
    if old:
        old.closeall()
    get_secret(force=True)


@contextmanager
def cur():
    try:
        conn = pool().getconn()
    except psycopg2.OperationalError:
        reset_pool()  # likely a rotated password: retry once with the fresh secret
        conn = pool().getconn()
    p = pool()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            yield c
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            p.putconn(conn)
        except Exception:
            pass


def init_db():
    global _ready
    if _ready:
        return
    with cur() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            done BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    _ready = True


@app.before_request
def ensure_schema():
    if request.endpoint != "health":
        init_db()


@app.get("/")
def index():
    with cur() as c:
        c.execute("SELECT * FROM tasks ORDER BY done, created_at DESC")
        tasks = c.fetchall()
    return render_template("index.html", tasks=tasks)


@app.post("/add")
def add():
    title = request.form.get("title", "").strip()[:200]
    if title:
        with cur() as c:
            c.execute("INSERT INTO tasks(title) VALUES (%s)", (title,))
    return redirect(url_for("index"))


@app.post("/toggle/<int:tid>")
def toggle(tid):
    with cur() as c:
        c.execute("UPDATE tasks SET done = NOT done WHERE id=%s RETURNING id", (tid,))
        if not c.fetchone():
            abort(404)
    return redirect(url_for("index"))


@app.post("/delete/<int:tid>")
def delete(tid):
    with cur() as c:
        c.execute("DELETE FROM tasks WHERE id=%s", (tid,))
    return redirect(url_for("index"))


@app.get("/health")
def health():
    """Checks Secrets Manager access and the RDS connection."""
    try:
        with cur() as c:
            c.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        return {"status": "unavailable"}, 503


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
