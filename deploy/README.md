# Deploy: Docker images and AWS ECS (13 Nov 2026)

Two images, both built from the repository root.

| Image | What is inside | Port | Runs on |
|---|---|---|---|
| `au-brain` (`brain/Dockerfile`) | The modular monolith: text agent, voice engine, call audit, desk API, judge, embedded mock bank, plus the two search models (bge-m3 embed + rerank) | 8000 | ECS on **EC2 with a GPU** (g6.xlarge) |
| `au-ui` (`ui/Dockerfile`) | The test console as static pages, served by nginx (non-root) | 8080 | ECS on **Fargate** (0.25 vCPU, 0.5 GB) |

**No Docker Compose.** ECS does not read Compose files (AWS retired the Compose-to-ECS tool in 2023). ECS uses the
task definitions in `deploy/ecs/`. On the laptop the demo runs without Docker (`python app.py`, `bun run dev`).

**Not tested yet.** The images have not been built or run (13 Nov). Expect to fix small things on the first build.

## Diagram: production on AWS (Mumbai)

The containers only run on AWS, so the AWS settings apply: **Bedrock** for the LLM, **Sarvam** for speech, calls arrive
through **AU's Cisco**, and the search models run in their **own GPU containers**. The brain containers need no GPU.

```mermaid
flowchart LR
    caller["Caller<br/>any phone"]
    cisco["AU contact centre<br/>Cisco"]
    staff["AU staff<br/>browser, HTTPS"]

    subgraph aws["AWS Mumbai ap-south-1 · one VPC · 2 availability zones"]
        alb["Load balancer ALB + WAF<br/>front door"]
        subgraph ecs["ECS cluster"]
            ui["Staff screens · au-ui<br/>Fargate · nginx + static pages<br/>calls, KB, desk"]
            app["Voice agent app · au-brain<br/>EC2 CPU servers, several copies<br/>voice + conversation + call audit + reviews"]
            gpu["Search servers · au-search<br/>EC2 GPU g6.xlarge × 2, 1 + spare<br/>embed + rerank models"]
        end
        db[("Aurora PostgreSQL<br/>+ pgvector")]
        nat["NAT gateway<br/>outbound only"]
        vpce["VPC endpoints<br/>private path"]
    end

    aws_svc["Bedrock Mumbai · LLM gpt-oss-120b<br/>S3 · KMS · Secrets Manager<br/>CloudWatch · GuardDuty · ECR"]
    sarvam["Sarvam · India<br/>speech-to-text, text-to-speech"]
    bank["AU bank APIs<br/>provided by AU"]

    caller --> cisco
    cisco -->|"call audio"| alb
    staff -->|"pages and API"| alb
    alb -->|"pages"| ui
    alb -->|"/v1/* API, call audio"| app
    app -->|"search"| gpu
    app --> db
    app --> vpce --> aws_svc
    app --> nat -->|"internet"| sarvam
    app -->|"per-customer token"| bank
```

### What exists today vs what the diagram needs

| Piece in the diagram | Status (13 Nov) | Pending work |
|---|---|---|
| `au-ui` image (staff screens) | Dockerfile written | — |
| `au-brain` image | Dockerfile written. By default the search models are inside it (one GPU server, one copy). Built with `--build-arg SEARCH_MODELS=0` and run with `SEARCH_PROVIDER=http`, it needs no GPU | Switch when `au-search` is up |
| `au-search` (GPU search server) | **Code built 16 Nov** (`python -m app search`, same image, different command; `deploy/ecs/search-task-definition.json`). Not yet run on AWS | Steps in "Moving search to its own GPU server" below |
| Several brain copies | Not yet: the OTP secret and rate limits are per container. Recordings on S3 and the startup lock are built (16 Nov) | B2.3; PROJECT-GUIDE §26.12 |
| Bedrock LLM | Adapter built (Converse API); set in the task definition. **Model ID to confirm**: gpt-oss in India-only processing isn't confirmed (ADR 0026) | B1.8 quota |
| Sarvam speech | Built; set in the task definition. Speech-to-text sends each sentence after it ends, not as a live stream | B2.6 |
| Cisco call audio | **Not built**: depends on how AU's Cisco hands calls over | B1.2, B2.5 |
| AU bank APIs | **Not built**: the task definition keeps the embedded mock bank until AU shares its APIs | B1.3, B2.8 |

The simplest deployment is still one brain container on a GPU server (search inside it), plus the UI. Moving search out
is a settings change, described below.

## What this deploys today, and its limits

The brain is already one process with everything in it, so it runs as **one ECS task on one GPU server**. That is
right for a demo or pilot on AWS. It can't yet run as several copies:

| Limit | Why | Fix (pending-work B2) |
|---|---|---|
| **Run exactly 1 brain task** | The OTP secret and the rate limiter are kept in the process's memory (live calls are fine: each call stays on its copy) | B2.3; PROJECT-GUIDE §26.12 |
| Every brain copy needs a GPU (default) | Search models load inside the app | Built (B2.1): run `au-search` and set `SEARCH_PROVIDER=http` |
| A deploy cuts live calls | Calls aren't drained before the task stops | B2.4: set the target group's deregistration delay to about 600 s, so ECS waits for calls to end before stopping the old task; check on the first deploy |

Uploaded documents, call recordings and the database survive restarts (S3 and RDS below). Recordings go to
`<S3_PREFIX>recordings/` in the bucket (default `brain/recordings/`), so the task role's S3 access to `brain/` covers
them. With `FILE_STORE=s3` the brain **refuses to start without `RECORDING_KEY`** (base64 of 32 random bytes, in the
`au/brain` secret). Keep that key safe: without it no recording can be played. Make one with
`python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"`. Retention is done by the app
(`RECORDING_RETENTION_DAYS`), so don't add an S3 lifecycle rule that deletes recordings. Two copies starting at once on
an empty database take turns on migrations and seeding (a Postgres advisory lock).

## Moving search to its own GPU server (B2.1)

The brain can load the two search models itself (`SEARCH_PROVIDER=local`, the default and what the laptop demo uses) or
call a search server (`SEARCH_PROVIDER=http`). The search server is the same image started with a different command,
`python -m app search --host 0.0.0.0 --port 8200`. Same models and same code, so vectors and scores are identical.

| | `au-search` | `au-brain` |
|---|---|---|
| Image | the default build (models inside) | `--build-arg SEARCH_MODELS=0` (about 4.5 GB smaller; still has PyTorch for voice detection) |
| Task definition | `deploy/ecs/search-task-definition.json` | `brain-task-definition.json` with the changes below |
| Runs on | GPU capacity provider (g6.xlarge), 1 + a spare | CPU servers; no `resourceRequirements` GPU line, no `DEVICE` |
| Settings | `SEARCH_TOKEN` (secret) | `SEARCH_PROVIDER=http`, `SEARCH_URL=http://au-search:8200`, `SEARCH_TOKEN` (same secret) |
| Network | private subnets only; security group allows port 8200 **only from the brain's security group** | |

Steps:
1. Add `SEARCH_TOKEN` (a long random value) to the `au/brain` secret.
2. Create the `/ecs/au-search` log group, register the search task definition, and create service `au-search` with
   **ECS Service Connect** (namespace e.g. `au.internal`, port name `search`, client alias `au-search:8200`).
   Health check grace period 300 s (the models load first). No load balancer: only the brain calls it.
3. In the brain task definition add the three settings above (`SEARCH_TOKEN` under `secrets`), turn Service Connect
   on as a client, and remove the GPU line and `DEVICE`. Point it at a CPU capacity provider.
4. Check `GET /health/ready` on the brain: `"search": "ok"`.

How it behaves:
- **Wrong models** on the search server (name or vector size differ from `EMBED_MODEL` / `EMBED_DIM`): the brain refuses
  to start. Stored vectors belong to one model; a mismatch would quietly break search.
- **Search server down**: the brain still starts and stays in the load balancer. Bank questions, card actions and voice keep
  working; product questions get the normal "system problem" reply and a hand-over. `/health/ready` shows the search
  error but stays 200 on purpose (failing it would take every call down with it).
- **Token**: every `/v1` route needs `Authorization: Bearer SEARCH_TOKEN`; the server refuses to start on `0.0.0.0`
  without one. It never logs the questions or passages it receives.
- **Desired count of `au-brain` stays 1** until live calls are shared across copies (see the limits above). Moving
  search out does not change that.

## 1. AWS resources (Mumbai, ap-south-1), once

1. **ECR**: two repositories, `au-brain` and `au-ui`.
2. **Database**: RDS for PostgreSQL 16 or 17 (pgvector is built in), private subnets, initial database `brain`.
   The brain creates the `vector` extension and its tables on first start. A pilot can use `db.t4g.medium`.
3. **S3 bucket** for uploaded documents (block public access, SSE-KMS).
4. **Secrets Manager** secret `au/brain` with three keys:
   - `DATABASE_URL`: `postgresql://USER:PASSWORD@RDS-ENDPOINT:5432/brain?sslmode=require`
   - `SARVAM_API_KEY`: the Sarvam key (speech-to-text and text-to-speech)
   - `RECORDING_KEY`: base64 of 32 random bytes, e.g. `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`
5. **IAM roles**:
   - `au-ecs-execution`: the AWS policy `AmazonECSTaskExecutionRolePolicy`, plus `secretsmanager:GetSecretValue` on `au/brain`.
   - `au-brain-task`: `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the bucket
     and `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` on the gpt-oss model (India-only profile, ADR 0026).
6. **CloudWatch log groups**: `/ecs/au-brain` and `/ecs/au-ui`.
7. **ECS cluster** with a GPU capacity provider:
   - Auto Scaling group of `g6.xlarge`, using the latest **ECS GPU-optimized Amazon Linux 2023 AMI**
     (it has the NVIDIA driver and container runtime);
   - **root volume 100 GB** (the brain image is about 12 GB; the AMI default is too small);
   - private subnets.
8. **Network**: a NAT gateway for Sarvam (the only internet service). VPC endpoints for Bedrock runtime, S3, Secrets
   Manager, CloudWatch Logs and ECR, so AWS traffic stays private.
9. **Load balancer (ALB)**, internet-facing, with an **HTTPS listener and an ACM certificate**. The microphone only
   works on HTTPS pages.
   - Target group `au-brain-tg`: type IP, port 8000, health check `/health/ready`.
   - Target group `au-ui-tg`: type IP, port 8080, health check `/healthz`.
   - Listener rule: path `/v1/*`, `/health`, `/health/ready`, `/docs`, `/openapi.json` → `au-brain-tg`.
   - Default action → `au-ui-tg`.
   - Idle timeout: 300 s (voice calls are WebSockets; ALB supports them as they are).
   - **Allow only your office or VPN IPs** in the ALB security group (or WAF). The demo has no staff login yet.

Because the UI and the API share one address, the UI image works on any domain without a rebuild, and no CORS is needed.

## 2. Build and push the images

Build on a machine with good internet (an EC2 in Mumbai or CodeBuild is fastest): the brain build downloads ~4.5 GB of
models and ~6 GB of Python packages, and pushes a ~12 GB image.

```bash
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com
```

```bash
docker build --platform linux/amd64 -f brain/Dockerfile -t ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/au-brain:v1 .
```

```bash
docker build --platform linux/amd64 -f ui/Dockerfile -t ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/au-ui:v1 .
```

```bash
docker push ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/au-brain:v1
```

```bash
docker push ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/au-ui:v1
```

## 3. Register the task definitions and start the services

In `deploy/ecs/*.json` replace `ACCOUNT_ID`, `IMAGE_TAG` (e.g. `v1`), `BUCKET_NAME`, and `BEDROCK_MODEL_ID` /
`BEDROCK_JUDGE_MODEL_ID` (India-only model IDs from the Bedrock console, ADR 0026), then:

```bash
aws ecs register-task-definition --cli-input-json file://deploy/ecs/brain-task-definition.json
```

```bash
aws ecs register-task-definition --cli-input-json file://deploy/ecs/ui-task-definition.json
```

Create two services in the console or CLI:

| Service | Task definition | Capacity | Desired count | Load balancer | Health check grace period |
|---|---|---|---|---|---|
| `au-brain` | `au-brain` | the GPU capacity provider | **1** | `au-brain-tg`, container `brain:8000` | **300 s** (models load for 1-2 min) |
| `au-ui` | `au-ui` | Fargate | 1 (or 2) | `au-ui-tg`, container `ui:8080` | 30 s |

For `au-brain`, set deployment minimum healthy 0% and maximum 100% (there is one GPU server, so the old task stops
before the new one starts).

## 4. After the first start

- Open `https://YOUR-DOMAIN/kb` and upload the knowledge-base documents (the database starts empty, apart from the
  synthetic service-policy document). The mock bank seeds its demo customers on its own.
- Check `https://YOUR-DOMAIN/health/ready`: `"database": "ok"`, embedder and reranker named, voice on.

## Settings worth knowing

All settings are environment variables (full list: `brain/.env.example`). Already set in the image: `LOG_CONSOLE=json`,
`HF_HUB_OFFLINE=1` (models are inside the image), and `FORWARDED_ALLOW_IPS=*` (so rate limits see the caller's IP, not the
ALB's). Already set in the task definition: `DEVICE=cuda` (no silent fall-back to a slow CPU), `FILE_STORE=s3`.
Demo-only defaults to switch off for anything real: `OTP_DELIVERY=simulated`, `DEMO_CALLERS=true`.

## Running the images on a laptop (optional)

Needs Docker Desktop with an NVIDIA GPU (WSL 2). Without `DATABASE_URL` the brain starts its own Postgres inside the container.

```bash
docker run --gpus all --shm-size=1g -p 8000:8000 --env-file brain/.env -e CORS_ORIGINS=http://localhost:3000 -v au-brain-data:/app/data au-brain
```

```bash
docker build -f ui/Dockerfile --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 -t au-ui-local .
```

```bash
docker run -p 3000:8080 au-ui-local
```
