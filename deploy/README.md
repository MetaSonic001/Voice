# Deploy: Docker images and AWS ECS (13 Nov 2026)

**The pilot (brain and UI on ECS EC2 / Managed Instances, SageMaker search), with the database DDL, updates and CI/CD:** `deploy/aws-pilot-guide.md`. This file has the AWS steps.

**Decided 19 Nov 2026 (ADR 0028):** the brain runs on **ECS with EC2 capacity** (Amazon ECS Managed Instances, `c7i`, no GPU), the UI on **ECS with EC2 capacity** as well, on its own small `t3.small` servers (ADR 0030). Search runs on a **SageMaker** endpoint (`SEARCH_PROVIDER=sagemaker`), or alternatively on a dedicated GPU (`SEARCH_PROVIDER=http`, below). The brain never loads the search models on AWS; `SEARCH_PROVIDER=local` is only for a laptop with a GPU.

Two images, both built from the repository root.

| Image | What is inside | Port | Runs on |
|---|---|---|---|
| `au-brain` (`brain/Dockerfile`, `--build-arg SEARCH_MODELS=0`) | The modular monolith: text agent, voice engine, call audit, desk API, judge, embedded mock bank. No search models | 8000 | ECS on **EC2** (Managed Instances, `c7i`; 2 vCPU, 3.5 GB per task) |
| `au-search` (`brain/Dockerfile`, default build) | The search server with the two models (bge-m3 embed + rerank) | 8080 / 8200 | **SageMaker** endpoint (or ECS on a GPU EC2, the alternative) |
| `au-ui` (`ui/Dockerfile`) | The test console as static pages, served by nginx (non-root) | 8080 | ECS on **EC2** (own `t3.small` servers, capacity provider `au-ui-ec2`; 0.25 vCPU, 0.5 GB per task) |

**Docker Compose is for the laptop only** (`compose.yaml` in the repository root: Postgres with pgvector, brain and UI;
brain/README "Setup with Docker Desktop"). ECS does not read Compose files (AWS retired the Compose-to-ECS tool in
2023); on AWS, ECS uses the task definitions in `deploy/ecs/`.

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
            ui["Staff screens · au-ui<br/>EC2 t3.small · nginx + static pages<br/>calls, KB, desk"]
            app["Voice agent app · au-brain<br/>ECS on EC2 c7i, several copies later<br/>voice + conversation + call audit + reviews"]
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
| `au-brain` image | Dockerfile written. Built with `--build-arg SEARCH_MODELS=0` (no models) and run on ECS EC2 capacity with `SEARCH_PROVIDER=sagemaker` (task definition updated 19 Nov) | — |
| `au-search` (GPU search server) | **Code built 16 Nov** (`python -m app search`, same image, different command; `deploy/ecs/search-task-definition.json`). Not yet run on AWS | Steps in "Moving search to its own GPU server" below |
| Several brain copies | Not yet: the OTP secret and rate limits are per container. Recordings on S3 and the startup lock are built (16 Nov) | B2.3; PROJECT-GUIDE §26.12 |
| Bedrock LLM | Adapter built (Converse API); set in the task definition. **Model ID to confirm**: gpt-oss in India-only processing isn't confirmed (ADR 0026) | B1.8 quota |
| Sarvam speech | Built; set in the task definition. Speech-to-text sends each sentence after it ends, not as a live stream | B2.6 |
| Cisco call audio | **Not built**: depends on how AU's Cisco hands calls over | B1.2, B2.5 |
| AU bank APIs | **Not built**: the task definition keeps the embedded mock bank until AU shares its APIs | B1.3, B2.8 |

The pilot is one brain task (ECS on EC2) and one UI task (EC2, `t3.small`), plus search on SageMaker. Moving search to a dedicated GPU instead
is a settings change, described below.

## What this deploys today, and its limits

The brain is already one process with everything in it, so it runs as **one ECS task on an EC2 server**. That is
right for a demo or pilot on AWS. It can't yet run as several copies:

| Limit | Why | Fix (pending-work B2) |
|---|---|---|
| **Run exactly 1 brain task** | The OTP secret and the rate limiter are kept in the process's memory (live calls are fine: each call stays on its copy) | B2.3; PROJECT-GUIDE §26.12 |
| A deploy cuts live calls | Calls aren't drained before the task stops | B2.4: set the target group's deregistration delay to about 600 s, so ECS waits for calls to end before stopping the old task; check on the first deploy |

Uploaded documents, call recordings and the database survive restarts (S3 and RDS below). Recordings go to
`<S3_PREFIX>recordings/` in the bucket (default `brain/recordings/`), so the task role's S3 access to `brain/` covers
them. With `FILE_STORE=s3` the brain **refuses to start without `RECORDING_KEY`** (base64 of 32 random bytes, in the
`au/brain` secret). Keep that key safe: without it no recording can be played. Make one with
`python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"`. Retention is done by the app
(`RECORDING_RETENTION_DAYS`), so don't add an S3 lifecycle rule that deletes recordings.
Call records (one JSON per call) go to `<S3_PREFIX>call_records/YYYY/MM/DD/`. Give that prefix **Object Lock with
versioning** (write-once; a chat exported again adds a version) and a lifecycle rule for the bank's retention period, and
set `CALL_ROWS_DAYS` (e.g. 7) so Postgres keeps only recent turn rows plus the call index. Two copies starting at once on
an empty database take turns on migrations and seeding (a Postgres advisory lock).

## Alternative: search on a dedicated GPU server instead of SageMaker (B2.1)

The brain can load the two search models itself (`SEARCH_PROVIDER=local`, the default and what the laptop demo uses) or
call a search server (`SEARCH_PROVIDER=http`). The search server is the same image started with a different command,
`python -m app search --host 0.0.0.0 --port 8200`. Same models and same code, so vectors and scores are identical.

| | `au-search` | `au-brain` |
|---|---|---|
| Image | the default build (models inside) | `--build-arg SEARCH_MODELS=0` (about 4.5 GB smaller; still has PyTorch for voice detection) |
| Task definition | `deploy/ecs/search-task-definition.json` | `brain-task-definition.json` with the changes below |
| Runs on | GPU capacity provider (g6.xlarge, EC2: Fargate has no GPUs), 1 + a spare | EC2 `c7i`, as in the pilot |
| Settings | `SEARCH_TOKEN` (secret) | `SEARCH_PROVIDER=http`, `SEARCH_URL=http://au-search:8200`, `SEARCH_TOKEN` (same secret) |
| Network | private subnets only; security group allows port 8200 **only from the brain's security group** | |

Steps:
1. Add `SEARCH_TOKEN` (a long random value) to the `au/brain` secret.
2. Create the `/ecs/au-search` log group, register the search task definition, and create service `au-search` with
   **ECS Service Connect** (namespace e.g. `au.internal`, port name `search`, client alias `au-search:8200`).
   Health check grace period 300 s (the models load first). No load balancer: only the brain calls it.
3. In the brain task definition replace `SEARCH_PROVIDER=sagemaker` / `SAGEMAKER_ENDPOINT` with the three settings
   above (`SEARCH_TOKEN` under `secrets`), and turn Service Connect on as a client.
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

## Search on SageMaker instead (`SEARCH_PROVIDER=sagemaker`, 18 Nov 2026)

The same search server image runs as a **SageMaker real-time endpoint**. SageMaker starts it as
`docker run <image> serve`; the image's `serve` command runs `python -m app search --sagemaker`: port 8080, `GET /ping`
and `POST /invocations` (`{"op": "info" | "embed" | "rerank", ...}`). Same model code as `local` and `http`, so the
same vectors and scores, and a knowledge base indexed in one mode works in the others without re-indexing.

**Choosing where search runs** (one setting in `brain/.env`; nothing else changes):

| `SEARCH_PROVIDER` | Where the models run | Needs | Good for |
|---|---|---|---|
| `local` (default) | Inside the brain | The models on that machine (`scripts/download_models.py`); a GPU, or ~25 s per product question on CPU | A laptop with a GPU; one GPU server |
| `http` | Our search server (`python -m app search`) on a GPU machine | `SEARCH_URL`, `SEARCH_TOKEN` (same on both sides) | A GPU EC2 / ECS task you run |
| `sagemaker` | A SageMaker endpoint running this image | `SAGEMAKER_ENDPOINT`, `AWS_REGION`, AWS credentials allowed `sagemaker:InvokeEndpoint` | AWS-managed hosting; laptops and CPU servers without models |

**Not run on real AWS yet** (built and tested offline against a fake SageMaker runtime, 18 Nov). Expect small fixes on
the first deploy.

### Steps (Mumbai, ap-south-1)

1. **Build and push the image** (the default build: the two models are inside, about 4.5 GB), from the repository root:
   ```bash
   docker build --platform linux/amd64 -f brain/Dockerfile -t au-search .
   aws ecr create-repository --repository-name au-search --region ap-south-1
   aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.ap-south-1.amazonaws.com
   docker tag au-search <account>.dkr.ecr.ap-south-1.amazonaws.com/au-search:1
   docker push <account>.dkr.ecr.ap-south-1.amazonaws.com/au-search:1
   ```
   Optional check on a machine with an NVIDIA GPU: `docker run --gpus all -p 8080:8080 au-search serve`, then
   `curl -s localhost:8080/ping` (200) once the log says `search server ready`.
2. **An execution role for SageMaker** (`au-search-sagemaker`): trust `sagemaker.amazonaws.com`; allow pulling from
   that ECR repository and writing CloudWatch logs (AWS's `AmazonSageMakerFullAccess` works for a first test; narrow it
   afterwards).
3. **The model**:
   ```bash
   aws sagemaker create-model --region ap-south-1 --model-name au-search-1 \
     --execution-role-arn arn:aws:iam::<account>:role/au-search-sagemaker \
     --primary-container 'Image=<account>.dkr.ecr.ap-south-1.amazonaws.com/au-search:1,Environment={DEVICE=cuda,LOG_CONSOLE=json}'
   ```
4. **The endpoint configuration**: one GPU instance. `ml.g4dn.xlarge` (T4, 16 GB, the cheapest) is enough for the two
   models; `ml.g5.xlarge` is faster. Use the newest GPU machine image: our PyTorch is a CUDA 12.8 build and needs a
   recent NVIDIA driver (`al2023-ami-sagemaker-inference-gpu-4-1`: driver 580). The models load before `/ping` answers
   (1–2 min), so allow 10 minutes for the health check.
   ```bash
   aws sagemaker create-endpoint-config --region ap-south-1 --endpoint-config-name au-search-1 \
     --production-variants 'VariantName=main,ModelName=au-search-1,InstanceType=ml.g4dn.xlarge,InitialInstanceCount=1,InferenceAmiVersion=al2023-ami-sagemaker-inference-gpu-4-1,ContainerStartupHealthCheckTimeoutInSeconds=600'
   aws sagemaker create-endpoint --region ap-south-1 --endpoint-name au-search --endpoint-config-name au-search-1
   aws sagemaker wait endpoint-in-service --region ap-south-1 --endpoint-name au-search
   ```
5. **Check it** (should name both models and `"device": "cuda"`):
   ```bash
   aws sagemaker-runtime invoke-endpoint --region ap-south-1 --endpoint-name au-search \
     --content-type application/json --cli-binary-format raw-in-base64-out --body '{"op": "info"}' info.json && cat info.json
   ```
6. **Point the brain at it**, in `brain/.env`:
   ```
   SEARCH_PROVIDER=sagemaker
   SAGEMAKER_ENDPOINT=au-search
   AWS_REGION=ap-south-1
   ```
   Whoever runs the brain (the EC2 / ECS role, or your AWS login on a laptop) needs `sagemaker:InvokeEndpoint` on
   `arn:aws:sagemaker:ap-south-1:<account>:endpoint/au-search`. No token: SageMaker checks the caller itself.
   On startup the brain checks the endpoint runs the expected models (`remote:cuda` in the log); `GET /health/ready`
   shows `"search": "ok"`.

How it behaves: as with `http` (above). Wrong models stop the brain; an unreachable endpoint does not (bank questions,
card actions and voice keep working; product questions get the "system problem" reply and a hand-over). Timeouts:
`SEARCH_TIMEOUT_S` (3 s) for a caller's question, `SEARCH_INGEST_TIMEOUT_S` (60 s, SageMaker's own limit) per batch
while a document is indexed.

Good to know:
- **Cost**: a real-time endpoint runs (and bills) all the time. Delete it when not in use
  (`aws sagemaker delete-endpoint --endpoint-name au-search`) and create it again from the same configuration.
  SageMaker Serverless Inference has no GPUs, so it is not an option for these models.
- **Private network**: a brain with no internet access reaches SageMaker through a VPC interface endpoint for
  `com.amazonaws.ap-south-1.sagemaker.runtime`.
- **New image version**: push `au-search:2`, create model and endpoint configuration `-2`, then
  `aws sagemaker update-endpoint --endpoint-name au-search --endpoint-config-name au-search-2` (no downtime).
- **The brain machine** needs no models in this mode (skip `scripts/download_models.py`; for a container use
  `--build-arg SEARCH_MODELS=0`). `uv sync` still installs PyTorch, which voice-activity detection uses.

## 1. AWS resources (Mumbai, ap-south-1), once

1. **ECR**: three repositories, `au-brain`, `au-ui` and `au-search`.
2. **Database**: RDS for PostgreSQL 16 or 17 (pgvector is built in), private subnets. The DBA runs
   `deploy/database/01_bootstrap.sql` and `02_schema.sql` (login `au_voice_app`, database `au_voice`; details in
   `deploy/aws-pilot-guide.md`). A pilot can use `db.t4g.medium`.
3. **S3 bucket** for uploaded documents (block public access, SSE-KMS).
4. **Secrets Manager** secret `au/brain` with three keys:
   - `DATABASE_URL`: `postgresql://au_voice_app:PASSWORD@RDS-ENDPOINT:5432/au_voice?sslmode=require`
   - `SARVAM_API_KEY`: the Sarvam key (speech-to-text and text-to-speech)
   - `RECORDING_KEY`: base64 of 32 random bytes, e.g. `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`
5. **IAM roles**:
   - `au-ecs-execution`: the AWS policy `AmazonECSTaskExecutionRolePolicy`, plus `secretsmanager:GetSecretValue` on `au/brain`.
   - `au-brain-task`: `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the bucket,
     `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` on the gpt-oss model (India-only profile, ADR 0026),
     and `sagemaker:InvokeEndpoint` on `endpoint/au-search`.
6. **CloudWatch log groups**: `/ecs/au-brain` and `/ecs/au-ui`.
7. **ECS cluster** `au-voice` (private subnets) with two capacity providers: **Managed Instances** for the brain
   (instance type `c7i.xlarge` for the pilot, `c7i.2xlarge` for production; needs an infrastructure role and an
   instance profile), and a second, small **Managed Instances** capacity provider `au-ui-ec2` for the UI (`t3.small`;
   1 server for the pilot, 2 in production, one per availability zone; ADR 0030). If AU's security team requires its own agents or a hardened image on
   every server, use an EC2 Auto Scaling group capacity provider instead of Managed Instances (ADR 0028). A GPU capacity provider (`g6.xlarge`, ECS GPU-optimized
   AMI, 100 GB root volume) is needed only for the dedicated-GPU search alternative.
   The SageMaker endpoint is created separately ("Search on SageMaker" above).
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

Build on a machine with good internet (an EC2 in Mumbai or CodeBuild is fastest): the brain build downloads ~6 GB of Python
packages; the `au-search` build adds ~4.5 GB of models (about 12 GB in all).

```bash
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com
```

```bash
docker build --platform linux/amd64 --build-arg SEARCH_MODELS=0 -f brain/Dockerfile -t ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/au-brain:v1 .
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
| `au-brain` | `au-brain` | Managed Instances (EC2) | **1** | `au-brain-tg`, container `brain:8000` | **120 s** |
| `au-ui` | `au-ui` | EC2 (`au-ui-ec2`) | 1 (or 2) | `au-ui-tg`, container `ui:8080` | 30 s |

For `au-brain`, set deployment minimum healthy 0% and maximum 100% (only one brain may run, so the old task stops
before the new one starts), and turn on the deployment circuit breaker with rollback.

## 4. After the first start

- Open `https://YOUR-DOMAIN/kb` and upload the knowledge-base documents (the database starts empty, apart from the
  synthetic service-policy document). The mock bank seeds its demo customers on its own.
- Check `https://YOUR-DOMAIN/health/ready`: `"database": "ok"`, embedder and reranker named, voice on.

## Settings worth knowing

All settings are environment variables (full list: `brain/.env.example`). Already set in the image: `LOG_CONSOLE=json`,
`HF_HUB_OFFLINE=1` (no model downloads at run time), and `FORWARDED_ALLOW_IPS=*` (so rate limits see the caller's IP,
not the ALB's). Already set in the brain task definition: `SEARCH_PROVIDER=sagemaker`, `FILE_STORE=s3`. The search
server (SageMaker or GPU) sets `DEVICE=cuda` (no silent fall-back to a slow CPU).
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
