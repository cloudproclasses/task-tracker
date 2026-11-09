#!/usr/bin/env bash
# For Ubuntu 22.04 / 24.04 EC2 instances. Run from the unpacked project directory:
#   sudo ./deploy/setup-ubuntu.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
APP_DIR=/opt/taskapp

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx

id -u taskapp >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin taskapp

mkdir -p "$APP_DIR"
cp -r app.py requirements.txt templates "$APP_DIR"/
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --no-cache-dir -r "$APP_DIR/requirements.txt"

if [ ! -f /etc/taskapp.env ]; then
  cp .env.example /etc/taskapp.env
  echo ">> Set AWS_REGION and DB_SECRET_ID in /etc/taskapp.env (no passwords), then: sudo systemctl restart taskapp"
fi
chown root:taskapp /etc/taskapp.env && chmod 640 /etc/taskapp.env
chown -R taskapp:taskapp "$APP_DIR"

cat > /etc/systemd/system/taskapp.service <<UNIT
[Unit]
Description=Task Tracker (gunicorn)
After=network.target

[Service]
User=taskapp
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/taskapp.env
ExecStart=$APP_DIR/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 --timeout 30 app:app
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/nginx/sites-available/taskapp <<'NGINX'
server {
  listen 80 default_server;
  server_name _;
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
NGINX
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/taskapp /etc/nginx/sites-enabled/taskapp
nginx -t

systemctl daemon-reload
systemctl enable --now taskapp nginx
systemctl restart taskapp nginx
echo "Done. Check: curl -s localhost/health"
