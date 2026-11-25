# Deploy: UAT on AWS EKS (Mumbai, ap-south-1)

The UAT setup agreed with AU's infrastructure team (25 Nov 2026; why: ADR 0031). One server, no GPU, no SageMaker.
Docker Compose (`compose.yaml` in the repo root) is for laptops only.

| Piece | What | Files |
|---|---|---|
| ECR | One repository, `au-voice`, two image tags: `brain-<version>` (Python, `python:3.12-slim-bookworm`) and `ui-<version>` (nginx, `nginxinc/nginx-unprivileged:1.28-alpine`; Node 22 only builds it) | `brain/Dockerfile`, `ui/Dockerfile` |
| EKS | One cluster, one node `m7i.xlarge` (4 vCPU, 16 GB, 100 GB disk), On-Demand, always on. Pods: 1 brain, 1 UI | `deploy/eks/*.yaml` |
| Load balancer + domain | One internal ALB (AWS Load Balancer Controller). `https://<domain>/api/*` → brain :8000 (WebSockets, idle timeout 300 s); everything else → UI :8080. Health: `/health/ready` (brain), `/ui-health` (UI) | `deploy/eks/ingress.yaml` |
| RDS | PostgreSQL 16 or 17 with `pgvector`, `db.t4g.medium`, 20 GB gp3, Single-AZ | `deploy/database/*.sql` |
| S3 | One private bucket, encrypted, versioning on, prefix `brain/` | |
| Secrets Manager | Secret `au/brain`: `DATABASE_URL`, `SARVAM_API_KEY`, `RECORDING_KEY` | `deploy/eks/secrets.yaml` |
| Bedrock | GPT-5.6 Luna, India profile `in.openai.gpt-5.6-luna` (understanding, answers and the judge) | ConfigMap in `deploy/eks/brain.yaml` |
| CloudWatch | Container stdout (JSON lines) → CloudWatch Logs via the Amazon CloudWatch Observability add-on | |

Cost: about ₹32,000 a month before GST, ₹37,800 with it (Google Sheet "AU voice agent - AWS and Sarvam cost estimate", tab UAT).

## 1. What the infrastructure team sets up

- EKS add-ons: **AWS Load Balancer Controller**, **Amazon CloudWatch Observability**, **EKS Pod Identity Agent**,
  **Secrets Store CSI Driver + AWS provider** (with `syncSecret.enabled=true`). Their standard equivalents are fine.
- Namespace `au-voice`. Network: brain pods → RDS 5432; outbound HTTPS from the brain to `api.sarvam.ai`.
- S3 bucket:
  - block all public access, versioning on;
  - default encryption SSE-S3 or SSE-KMS. The app asks for SSE-S3 (`AES256`) on each upload, so don't add a bucket policy that demands `aws:kms`;
  - no lifecycle rule that deletes recordings: the app deletes them after `RECORDING_RETENTION_DAYS`.
- **One IAM role** for the brain (linked to service account `au-voice/au-brain` with Pod Identity):

| Permission | Resource | Why |
|---|---|---|
| `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` | `arn:aws:s3:::<bucket>/brain/*` | KB files, encrypted recordings, call records |
| `s3:ListBucket` (prefix `brain/`) | `arn:aws:s3:::<bucket>` | Delete by prefix (retention, deleting a call) |
| `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | the inference profile `in.openai.gpt-5.6-luna` and the Luna foundation model behind it (Mumbai and Hyderabad) | The LLM |
| `secretsmanager:GetSecretValue` | `au/brain` | Secrets at pod start (CSI driver, as the pod) |
| `kms:Decrypt`, `kms:GenerateDataKey` | the bucket's / secret's key | Only if they use a customer-managed KMS key |

  The cluster and node roles that EKS itself needs are separate (ECR pull is on the node role). The UI needs no role.

## 2. Database (once)

Run as the RDS master user from a machine inside the VPC. In `01_bootstrap.sql`, first replace `CHANGE_ME` with a long
random password.

```
psql "host=<RDS endpoint> dbname=postgres user=<master> sslmode=require" -v ON_ERROR_STOP=1 -f deploy/database/01_bootstrap.sql
psql "host=<RDS endpoint> dbname=au_voice user=<master> sslmode=require" -v ON_ERROR_STOP=1 -f deploy/database/02_schema.sql
```

- `01_bootstrap.sql` creates the login `au_voice_app` (not a superuser), the database `au_voice` and the `vector` extension.
- `02_schema.sql` creates every table, owned by `au_voice_app`, and records migrations 001–013 as done.
  - Optional: the brain also applies any missing migration itself on start.
- Put `DATABASE_URL=postgresql://au_voice_app:<password>@<RDS endpoint>:5432/au_voice?sslmode=require` in `au/brain`.
- The mock bank (schema `corebank`) seeds itself on the first start and again each new day.
- KB documents are uploaded on the `/kb` page.

**Migrations rule:** a database change is always a new file in `brain/app/migrations/`, and it only adds (tables,
columns with defaults). Then the previous image still works against the new database, so a rollback is safe.

## 3. Build and push the images (from the repo root)

```
docker build --platform linux/amd64 --build-arg SEARCH_MODELS=embed -f brain/Dockerfile -t <account>.dkr.ecr.ap-south-1.amazonaws.com/au-voice:brain-<version> .
docker build --platform linux/amd64 -f ui/Dockerfile -t <account>.dkr.ecr.ap-south-1.amazonaws.com/au-voice:ui-<version> .
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.ap-south-1.amazonaws.com
docker push <account>.dkr.ecr.ap-south-1.amazonaws.com/au-voice:brain-<version>
docker push <account>.dkr.ecr.ap-south-1.amazonaws.com/au-voice:ui-<version>
```

- `SEARCH_MODELS=embed` puts only bge-m3 in the image. On a CPU the reranker is off (`RERANK=auto`), so its 2.2 GB stays out.
- The UI is built with `NEXT_PUBLIC_API_URL=/` (the default), so its pages call `/api` on their own address. It works for any domain with no rebuild.
- Use a new version tag for every build. Never reuse `latest`: the node caches images by tag.

## 4. Apply (first time)

1. Replace the placeholders in `deploy/eks/*.yaml`: `ACCOUNT_ID`, `VERSION`, `BUCKET_NAME`, `DOMAIN`, `CERT_ARN`.
2. Apply the files:
   ```
   kubectl create namespace au-voice
   kubectl apply -f deploy/eks/secrets.yaml -f deploy/eks/brain.yaml -f deploy/eks/ui.yaml -f deploy/eks/ingress.yaml
   ```
3. Watch the brain start: `kubectl -n au-voice get pods -w`. The first start pulls the ~7 GB image (minutes); later restarts use the node's copy. The models then load (1–3 minutes on a CPU), and the pod turns Ready.
4. Check the brain: `kubectl -n au-voice port-forward deploy/au-brain 8000`, then open `http://127.0.0.1:8000/health/ready`.
   - Health paths are not behind the `/api` rule, so `https://<domain>/health` would reach the UI.
5. Open `https://<domain>/`, go to `/kb` and upload the documents.

## 5. Updates

- **New version:** build and push new tags, then run
  `kubectl -n au-voice set image deploy/au-brain brain=<repo>:brain-<new>` (and `deploy/au-ui ui=<repo>:ui-<new>`).
  - The brain is replaced, not rolled (one copy only, pending-work B2.3): about 1–2 minutes without the API.
  - Live calls get up to 5 minutes to finish.
- **A setting:** edit the ConfigMap in `brain.yaml`, apply it, then `kubectl -n au-voice rollout restart deploy/au-brain`.
- **Rollback:** `kubectl -n au-voice rollout undo deploy/au-brain`.

## 6. Logs and alerts

- Every brain log line is one JSON object on stdout (`LOG_CONSOLE=json`). The CloudWatch Observability add-on ships the logs to the log group `/aws/containerinsights/<cluster>/application`.
- Alerts are lines with `"msg": "alert"`. A CloudWatch metric filter on them plus an alarm (SNS email) is AWS setup only; no code is needed.
- Turn off Container Insights' enhanced metrics if only logs are wanted (it costs extra).

## 7. Later (not for UAT)

| When | Change |
|---|---|
| A GPU is available | The reranker comes back on its own (`RERANK=auto`). Either a GPU node for the brain with `SEARCH_MODELS=1`, or the search server on a GPU (`python -m app search`, `SEARCH_PROVIDER=http`). No code change |
| More than one brain copy | First move the keypad-code secret and rate limits out of memory (pending-work B2.3). Then raise `replicas`, switch to `RollingUpdate` and add a second node |
| Production | Multi-AZ RDS, two nodes in two zones, WAF, private endpoints for Bedrock, S3, Secrets Manager and logs (PROJECT-GUIDE §26) |
