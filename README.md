# Task Tracker — Flask on EC2 + Amazon RDS (PostgreSQL), credentials in AWS Secrets Manager

```
Internet ─► EC2 (nginx :80 ─► gunicorn/Flask :8000) ─┬─► Secrets Manager (via instance role)
                                                     └─► RDS PostgreSQL :5432 (private)
```
No database password is stored on the server or in the code. The app reads it at runtime with the EC2 instance role,
caches it for 5 minutes, and re-reads it automatically if a connection fails (e.g. after rotation).

## 1. RDS
- PostgreSQL 16, **Public access: No**, same VPC as EC2, database name `appdb`.
- Security group: inbound **TCP 5432 from the EC2 security group** only.

## 2. Secret
Two options:
- **A (simplest): RDS-managed master secret.** Create the DB with "Manage master credentials in AWS Secrets Manager".
  RDS creates and rotates the secret (`username`, `password` only). Set `DB_SECRET_ID` to its ARN and keep
  `DB_HOST` / `DB_NAME` in `/etc/taskapp.env`.
- **B (recommended): dedicated app user.** Create `appuser` in the DB (SQL below), then store a secret with
  `./deploy/create-secret.sh` (JSON: username, password, host, port, dbname). Enable rotation from the Secrets Manager
  console (RDS PostgreSQL template).
```sql
CREATE ROLE appuser LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE appdb TO appuser;
GRANT USAGE, CREATE ON SCHEMA public TO appuser;
```

## 3. IAM role for EC2
Create an IAM role for EC2 with `deploy/iam-policy.json` (replace REGION / ACCOUNT_ID, or point it at your secret's ARN;
add `kms:Decrypt` if the secret uses a customer-managed KMS key) and attach it to the instance as its instance profile.
Require IMDSv2 on the instance so only the instance itself can obtain the role credentials.

## 4. EC2 + deploy
- Amazon Linux 2023, `t3.micro`; security group: **80** from anywhere, **22 only from your IP**.
```bash
scp -r taskapp ec2-user@<ec2-ip>:~
ssh ec2-user@<ec2-ip>
cd taskapp && sudo ./deploy/setup.sh
sudo nano /etc/taskapp.env        # AWS_REGION, DB_SECRET_ID (+ DB_HOST/DB_NAME if not in the secret)
sudo systemctl restart taskapp
curl -s localhost/health          # {"status":"ok"}
```
Open `http://<ec2-public-ip>/`. If `/health` returns 503, run `sudo journalctl -u taskapp -n 50` (usually a missing IAM
permission, wrong secret ID/region, or the RDS security group).

## Run locally
Needs AWS credentials with access to the secret (`aws configure` or SSO):
```bash
python3 -m venv venv && . venv/bin/activate && pip install -r requirements.txt
set -a; . ./.env; set +a && python app.py
```

## Before real production use
HTTPS (ACM + ALB, or certbot), login + CSRF protection if public, Multi-AZ and automated backups on RDS,
a Secrets Manager VPC endpoint if the instance has no internet route, and `sslmode=verify-full` with the RDS CA bundle.
