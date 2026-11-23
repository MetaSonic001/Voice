# ADR-0028: Brain on ECS with EC2 capacity (Managed Instances), UI on Fargate, search on SageMaker

- **Status:** Accepted. The UI part is superseded by [0030](0030-ui-on-ecs-ec2.md) (UI on EC2, 23 Nov 2026)
- **Date:** 2026-11-19
- **Deciders:** project owner

## Context

We need to decide where each part runs on AWS (Mumbai). The facts that shape the choice:

**The brain**
- One Python process per container. It carries about **9 live calls**, limited by one CPU core (voice detection on every 32 ms of audio, cost sheet "Servers" tab).
- Memory per call is small (a few MB). It needs **no GPU** once search runs elsewhere.
- The image is **several GB** even without the search models, mostly PyTorch (CUDA build) for voice detection.
- **Only one brain task may run for now.** The keypad-code secret and the rate limits are held in memory (PROJECT-GUIDE §26.12).

**Calls**
- A call always connects to a task that is **already running**. No task is started per call.
- Task start time matters only on a deploy, when replacing a crashed task, and when adding tasks during a burst.

**Search**
- The search models (bge-m3 embed + rerank) need a GPU to answer in about 0.1 s; on a CPU a rerank takes 20–37 s.
- The code already supports three places for them: `local` (inside the brain), `http` (our search server on a GPU server) and `sagemaker`.

**Fargate**
- No GPUs. It pulls the whole image every time a task starts; AWS: start time grows with image size, and lazy loading (SOCI) helps for images over 250 MB.
- You can't choose the CPU type.
- It costs more per vCPU than EC2.

**Amazon ECS Managed Instances** (launched October 2025)
- ECS runs tasks on EC2 servers that **AWS** patches and replaces (about every 14 days, inside a maintenance window).
- You still choose the instance type, and images stay cached on the server.
- Price: the EC2 price **plus a management fee** per instance. The Mumbai fee isn't confirmed; one third-party example (m5.large, US East) is about 12 % of the server price.

**Cost sheet**
- Plans for 100 calls at once: 16 brain containers (2 vCPU each) on 4 × `c7i.2xlarge`, sized on a known CPU.

## Decision

1. **The brain runs on ECS with EC2 capacity**, using **ECS Managed Instances** with `c7i` servers.
   - Pilot: 1 task on one `c7i.xlarge` or `c7i.2xlarge`.
   - Production: 4 × `c7i.2xlarge`, 4 tasks each.
   - No GPU.
   - `deploy/ecs/brain-task-definition.json` lists `EC2` and `MANAGED_INSTANCES`: 2 vCPU, 3.5 GB.
2. **If AU's security team requires its own agents** (antivirus or EDR) **or a bank-hardened image on every server**, use our own EC2 servers instead: an ECS capacity provider with an Auto Scaling group. Same task definition.
3. **The UI runs on ECS Fargate.** It's tiny and starts in seconds, so Fargate's drawbacks don't apply.
4. **Search runs on a SageMaker real-time endpoint** (`SEARCH_PROVIDER=sagemaker`, `ml.g4dn.xlarge`).
   - The alternative is our own GPU server: the same `au-search` image as an ECS service on a GPU EC2 capacity provider (`SEARCH_PROVIDER=http`). Switching is a settings change with no re-indexing.
   - On AWS the brain never loads the models itself. `local` is only for a laptop with a GPU.
5. **Deploys and secrets.**
   - Deploys go through ECS (`update-service`, with the circuit breaker and rollback on).
   - The brain service runs with minimum healthy 0 % / maximum 100 %, because only one copy may run.
   - Settings are in the task definition, and ECS reads the secrets from Secrets Manager.

## Options considered

**For the brain**

| Option | Pros | Cons |
|---|---|---|
| **ECS on EC2, Managed Instances (chosen)** | Image cached on the server, so a restart or deploy takes seconds. We choose the CPU the sizing assumes. Matches the cost sheet. AWS patches and replaces the servers; no server image or server group to look after. | A management fee (Mumbai rate to confirm; about +₹12k a month for 4 servers if it is about 12 %). No login to the server, no custom image or extra agents. Each server is replaced about every 14 days: with one brain task, a short break inside the maintenance window. |
| ECS on our own EC2 servers | Same speed and CPU choice. No management fee. Any image, any agents (fits a bank that requires its own). | We patch the operating system, keep the server image current, size and replace the servers, and drain them before maintenance. |
| ECS on Fargate (chosen briefly, then dropped) | No servers at all; pay per task. | Pulls the several-GB image at every start, so a deploy or replacement takes minutes. No CPU choice. About ₹15–35k a month more for 16 containers (the cost sheet's own Fargate rate). |
| Everything on one GPU EC2 server (brain + search inside, `SEARCH_PROVIDER=local`) | The simplest pilot: one server. No network hop for search. One `g6.xlarge` costs about ₹67k a month, about the same as a small app server plus a SageMaker endpoint. | Every brain copy would need a GPU. A `g6.xlarge` has 4 vCPUs, which is 1–2 brain containers (9–18 calls). At 100 calls that means many GPU servers instead of 4 CPU servers plus 1–2 GPUs, which costs much more. The image is about 12 GB. One machine is a single point of failure. Reasonable for a small pilot only; we chose one layout that also scales. |

**For search**

| Option | Pros | Cons |
|---|---|---|
| **SageMaker endpoint (chosen)** | AWS runs and heals the GPU server. Blue/green updates with no downtime. IAM instead of a shared token. | The price per hour is a little above plain EC2, and the Mumbai price is to confirm. |
| Our GPU server via ECS (`http`) | Cheapest per hour. Same image and code. | We look after the GPU servers, the GPU driver image and the GPU quota. |
| Inside the brain (`local`) | No extra service. | Puts a GPU under every brain copy (see above). |
| Bedrock rerank | No servers. | Not offered in Mumbai, so questions would leave India. Costs more at our volume (cost sheet "Choices" §7). |

**Claims we checked (19 Nov)**

| Claim | Holds for us? |
|---|---|
| "EC2 has near-zero cold starts; calls can't wait for a Fargate task" | Partly. Calls never wait for a task start, because they go to a running task. But on a deploy or replacement, Fargate pulls our large image (minutes), while EC2 has it cached (seconds). So this is a real reason. |
| "EC2 enhanced networking gives lower latency and jitter" | Not material. Fargate also runs on AWS's Nitro network. One call's audio is tens of kbit/s. Most delay is waiting for Sarvam, Bedrock and Cisco. The real EC2 gain is choosing the CPU. |
| "EC2 packs many calls into large, high-memory instances with a shared cache" | Doesn't apply. Our limit is one core per process (9 calls), not memory. A bigger server only holds more containers, which the sizing already uses. Each container keeps its own phrase cache. |

## Consequences

- **Easier:**
  - fast deploys and replacements;
  - the cost sheet's app line stays valid;
  - no server patching (Managed Instances);
  - search can move between SageMaker and our own GPU without code changes.
- **Harder / costs:**
  - the Managed Instances fee;
  - a capacity provider plus an infrastructure role and instance profile to set up;
  - a short brain break when a server is replaced (until more than one brain copy can run);
  - not yet confirmed on AWS that `ulimits` and `initProcessEnabled` are accepted on Managed Instances.
- **Revisit when:**
  - AU's security team requires host agents or a hardened image → use our own EC2 servers;
  - the brain can run several copies (PROJECT-GUIDE §26.12) → rolling deploys with no break;
  - the image gets much smaller (for example a CPU-only PyTorch build) → Fargate becomes reasonable;
  - the SageMaker Mumbai price turns out well above our own GPU server → use `http`.

## Links

- `deploy/aws-pilot-guide.md` §3–5, `deploy/README.md`, `deploy/ecs/*.json`
- Diagrams: `docs/diagrams/drawio/02-aws-deployment.drawio`, `05-cicd-pipeline.drawio`
- Cost sheet "AU voice agent - AWS and Sarvam cost estimate" (tabs Servers, Cost, Prices, Choices)
- Related: ADR 0021 (no Redis yet), ADR 0026 (India-only LLM), PROJECT-GUIDE §26.12 (one brain copy)
- AWS sources:
  - [ECS Managed Instances](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ManagedInstances.html)
  - [Task definition parameters for Managed Instances](https://docs.aws.amazon.com/en_us/AmazonECS/latest/developerguide/task_definition_parameters-managed-instances.html)
  - [Managed Instances pricing](https://aws.amazon.com/ecs/managed-instances/pricing)
  - [Fargate image pull behaviour](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-pull-behavior.html)
  - [SOCI on Fargate](https://aws.amazon.com/blogs/containers/under-the-hood-lazy-loading-container-images-with-seekable-oci-and-aws-fargate)
