# AWS study guide for this project

*Note (13 Nov 2026): this describes the first AWS design (EKS, Valkey/Redis, SQS, split services). The 12 Nov proposals simplify it: ECS, one app image + a GPU search server, Aurora, no Valkey/SQS at launch (`docs/deployment-options-and-costs.md`, `context/pending-work.md`). The concepts below still apply.*


**What this is:** everything you should understand about AWS before deploying the voice agent. Each concept is explained from scratch, then tied to where it appears in our plan (the circled numbers refer to diagram 2 in [architecture-diagrams.md](architecture-diagrams.md)).

**How to use it:** read Parts 1–3 first. They are the foundations everything else sits on. Then read the rest in any order. The self-check questions at the end tell you whether it has sunk in.

**Contents**
1. The mental model
2. Where things run: regions and availability zones
3. Accounts and identity (IAM)
4. Networking (VPC and everything around it)
5. Load balancers and the front door
6. Compute: servers, containers, Kubernetes
7. Data services
8. Security services
9. AI services (Bedrock and friends)
10. Monitoring and logs
11. How you deploy: Infrastructure as Code and CI/CD
12. Reliability and disaster recovery
13. Cost
14. Limits (quotas)
15. Compliance in India
16. Our diagram, box by box
17. The order you'd actually set it up
18. Glossary
19. Self-check questions

---

## 1. The mental model

**AWS is a set of data centres you rent by the second, controlled by software.** You don't buy servers. You ask AWS (through a web console, a command line or code) for "two servers of this size in Mumbai", and seconds later they exist. You pay while they exist. When you delete them, you stop paying.

Three ideas to hold on to from the start:

| Idea | What it means | Why it matters to us |
|---|---|---|
| **Everything is an API** | Every action (create a server, open a firewall port, read a file) is an API call. The web console just makes those calls for you. | We can write the whole setup as code (Part 11), review it like any code change, and rebuild it identically in Hyderabad for disaster recovery. |
| **Managed services** | AWS runs many things for you: databases, queues, Kubernetes control planes. You configure them; AWS patches, backs up and repairs them. | We don't run Postgres ourselves; we use Aurora. Fewer things for us to break, and the bank's auditors can lean on AWS's certifications. |
| **Shared responsibility** | AWS secures the **physical building, hardware and its own services**. **You** secure what you put on top: who can log in, which ports are open, whether data is encrypted, your own code. | Most cloud breaches are customer misconfiguration (a public storage bucket, a leaked key), not AWS being hacked. The security parts of this guide are about our half. |

---

## 2. Where things run: regions and availability zones

### Region
A **region** is a geographic area with several data centres. Each region is separate: data you put in Mumbai stays in Mumbai unless you copy it elsewhere.

- **ap-south-1 = Mumbai**: our main region.
- **ap-south-2 = Hyderabad**: our disaster-recovery region (box ⑪).

**Why it matters:** data residency. RBI and AU's policy expect customer data to stay in India, and choosing Indian regions is how you guarantee it. A **Service Control Policy** (Part 3) can block every non-Indian region, so nobody can accidentally create something in Singapore.

Not every AWS service or AI model exists in every region. Always check availability in Mumbai before designing around a service.

### Availability Zone (AZ)
Each region contains several **availability zones**. Each AZ is one or more physically separate data centres with their own power, cooling and network, a few kilometres apart and linked by very fast private fibre.

- Mumbai has three AZs (named like `ap-south-1a`, `ap-south-1b`, `ap-south-1c`).
- If one AZ loses power or floods, the others keep running.

**Why it matters:** "Multi-AZ" means running copies in at least two AZs. If a whole data centre fails, the service continues. This is the main reliability tool, and the plan uses it everywhere: the load balancers, the Kubernetes nodes, Aurora, Valkey.

**Rule:** a single AZ is a single point of failure. Production never runs in one AZ.

### Edge locations
AWS also has smaller sites in many cities for its content network (CloudFront) and DNS (Route 53). We barely need them: our callers come in through Cisco, not the internet.

---

## 3. Accounts and identity (IAM)

### AWS account
An **account** is a container for resources and the bill. Everything you create belongs to exactly one account. Accounts are fully walled off from each other by default.

**The root user:** the email address that created the account. It can do anything, including close the account. Protect it with hardware MFA, lock the credentials away, and never use it for daily work.

### AWS Organizations and multiple accounts
Real setups use **several accounts**, grouped in an **Organization**:

| Account | Holds | Why separate |
|---|---|---|
| Management | Billing, the Organization itself | Nobody runs workloads here |
| Security / log archive | CloudTrail logs, GuardDuty findings, Config history from every account | Even an admin of the production account can't delete the audit trail |
| Production | The live voice agent | Smallest set of people with access |
| Staging / test | A copy for testing | Mistakes in testing can't touch production |
| Shared services | Container registry, CI/CD | Used by all environments |

**AWS Control Tower** sets up this multi-account structure (a "landing zone") with sensible defaults. AU's cloud team may already have one; we'd deploy into accounts they give us.

**Service Control Policies (SCPs):** rules on the Organization that limit what **any** user in an account can do, even an admin. Examples for us:
- deny all regions except Mumbai and Hyderabad;
- deny turning off CloudTrail;
- deny making S3 buckets public.

### IAM (Identity and Access Management)
IAM decides **who can do what to which resource**.

| Concept | Meaning |
|---|---|
| **User** | A person or program with permanent credentials. Avoid them for people (use SSO instead) and for programs (use roles instead). |
| **Group** | A set of users sharing permissions |
| **Role** | An identity that is **assumed temporarily**: a person, a server or a pod "becomes" the role and gets short-lived credentials (usually valid for an hour or less). **This is the normal way everything authenticates.** |
| **Policy** | A JSON document listing allowed or denied actions on resources, e.g. "allow `s3:PutObject` on bucket `au-voice-recordings`". |
| **STS** | The Security Token Service that hands out those temporary credentials |
| **IAM Identity Center (SSO)** | Staff sign in through AU's identity provider (with MFA), then pick an account and a role. No AWS passwords to manage. |

**How policies are evaluated:**
- Everything is **denied by default**.
- An explicit `Allow` grants access.
- An explicit `Deny` always wins, even over an `Allow`.

**Least privilege:** give each identity only what it needs. In our plan:
- the brain's pods may call Bedrock and read one secret;
- only the voice workers may write to the recordings bucket;
- nobody's laptop has permanent AWS keys.

**EKS Pod Identity (or IRSA):** the way a Kubernetes pod (Part 6) gets an IAM role. Each service's pods get their own role, so a compromised staff-UI pod can't read recordings.

---

## 4. Networking

This is the part most people find hardest, and the part that matters most for a bank.

### VPC (Virtual Private Cloud)
A **VPC** is your own private network inside AWS. Nothing can reach it from outside, and nothing inside can reach outside, unless you build a path.

When you create a VPC you give it an **IP address range**, written in **CIDR** notation:
- `10.20.0.0/16` means "all addresses from 10.20.0.0 to 10.20.255.255" (about 65,000 addresses);
- the `/16` says how many leading bits are fixed (16 of 32). A bigger number after the slash means a smaller range: `/24` is 256 addresses.

**Plan IP ranges with AU's network team.** Our VPC connects to AU's data centre (Direct Connect), so its range must **not overlap** AU's internal ranges, or routing breaks.

### Subnets
A VPC is divided into **subnets**. Each subnet lives in exactly one AZ.

| Type | Meaning | What goes there in our plan |
|---|---|---|
| **Public subnet** | Has a route to the internet through an Internet Gateway | Almost nothing. NAT gateways at most. |
| **Private subnet** | No direct route from the internet | Everything: EKS nodes, Aurora, Valkey |
| **Isolated / data subnet** | No route to the internet at all, even outbound | The databases (Aurora, Valkey) |

A typical layout: three tiers (public, private-app, private-data) × two or three AZs, so six or nine subnets.

### Route tables
Each subnet has a **route table**: rules saying "traffic to these addresses goes here". For example:
- "10.20.0.0/16 → local" (stay in the VPC);
- "AU's ranges → Direct Connect";
- "0.0.0.0/0 (everything else) → NAT gateway".

### Internet Gateway and NAT Gateway
- **Internet Gateway (IGW):** the VPC's door to the internet, used by public subnets.
- **NAT Gateway:** lets servers in private subnets make **outbound** connections (e.g. to Sarvam) while staying unreachable from outside. It sits in a public subnet.
  - Run one per AZ, so losing one AZ doesn't cut everyone's outbound access.
  - It charges per GB processed, which adds up with audio traffic.

### Security groups vs network ACLs
Both are firewalls, at different levels:

| | Security group | Network ACL |
|---|---|---|
| Attached to | Each server / network interface / load balancer | A whole subnet |
| Rules | Allow only | Allow and deny |
| Stateful? | **Yes**: if a request is allowed out, the reply is allowed back in automatically | **No**: you must allow both directions |
| Typical use | The main tool: "Aurora accepts port 5432 only from the brain's security group" | A coarse extra layer, often left at default |

**Key trick:** security-group rules can name **other security groups** instead of IP addresses. "The database accepts connections from anything in the `brain-pods` group" keeps working however many pods there are.

### Getting out to the internet safely: egress control
By default, anything with a NAT route can reach any internet address. For a bank, add an **egress allow-list** so the voice workers can reach **only** Sarvam. Use **AWS Network Firewall** or a proxy. Then a compromised pod can't send data anywhere else.

### VPC endpoints (PrivateLink)
Services like S3, Bedrock and Secrets Manager have public internet addresses by default. A **VPC endpoint** gives them a **private address inside your VPC**, so traffic never leaves AWS's network and needs no NAT. Two kinds:
- **Gateway endpoints** (S3, DynamoDB): a route table entry. Free.
- **Interface endpoints** (Bedrock, Secrets Manager, ECR, CloudWatch, STS…): a private network interface in your subnets. Charged per hour and per GB.

In our plan, the brain reaches Bedrock and S3 through endpoints (box ③).

### Connecting to AU's data centre
| Option | What it is | Pros | Cons |
|---|---|---|---|
| **Site-to-Site VPN** | An encrypted tunnel over the public internet, between AU's firewall and an AWS **Virtual Private Gateway** or **Transit Gateway** | Set up in hours; cheap | Speed and latency vary with the internet |
| **Direct Connect (DX)** | A private fibre link from AU's data centre (or a telecom partner's site) into AWS | Steady low latency, high bandwidth, never on the public internet | Weeks to provision; port and partner fees |
| **Both** | DX for normal traffic, VPN as automatic backup | Resilient | More to configure |

Recommendation: VPN for the pilot; DX plus a VPN backup for production. Over this link travel the Cisco SIP and RTP, the bank API calls and the SMS gateway requests.

**Transit Gateway:** a central router connecting many VPCs and on-premises links. Useful when there are several VPCs (production, staging, shared) that all need AU's network.

### DNS: Route 53
**Route 53** is AWS's DNS service: it turns names like `voice.internal.aubank` into IP addresses.
- **Private hosted zones** work only inside your VPCs.
- **Health-checked failover records** can point to Hyderabad automatically when Mumbai is down.

### Fixed IP addresses: Elastic IPs
Normally AWS addresses can change. An **Elastic IP** is a fixed public IPv4 address you own. AU's Cisco and firewall teams need fixed addresses to allow-list, which is why the NLB and media gateways get fixed IPs. Over a private link we'd use fixed **private** IPs instead.

### How a phone call's packets travel
```
Caller → telecom → AU Cisco CUBE ──(Direct Connect)──► NLB (fixed IP, AZ a or b)
   → media-gateway pod (private subnet) → voice-worker pod → brain pod
   → Bedrock via VPC endpoint · Sarvam via NAT + egress firewall · Aurora in data subnet
```

---

## 5. Load balancers and the front door

A **load balancer** spreads incoming connections across several servers, checks which servers are healthy, and stops sending traffic to broken ones. AWS calls the family **Elastic Load Balancing (ELB)**.

| | **ALB** (Application Load Balancer) | **NLB** (Network Load Balancer) |
|---|---|---|
| Layer | 7, HTTP: understands URLs, headers, cookies | 4, TCP/UDP: just forwards connections and packets |
| Use in our plan | Staff web UI (`/calls`, `/kb`), APIs, WebSockets | Phone traffic: SIP signalling (TCP/UDP) and RTP audio (UDP) |
| Routing | By path or host ("`/kb/*` → staff-ui") | By port only |
| Fixed IPs | No (DNS name only) | Yes, one per AZ |
| Firewall (WAF) | Can attach AWS WAF | Can't attach WAF |
| TLS | Terminates HTTPS with certificates from **ACM** (AWS Certificate Manager, free certificates) | Can pass TLS through or terminate it |

**Parts of a load balancer:**
- **Listener:** "accept connections on port 443".
- **Target group:** the set of servers or pods to send them to.
- **Health check:** e.g. call `/health/ready` every 10 s; stop sending traffic to a pod after 2 failures. Our brain already has `/health` and `/health/ready`.

**Internet-facing vs internal:** an internal load balancer has only private addresses. **Our ALB should be internal**: staff reach it over AU's network, not the public internet.

**WebSockets:** the ALB supports them, but its **idle timeout** (60 s by default) must be raised for long-lived connections.

**A caveat on phone audio:** RTP audio is a stream of UDP packets that must keep reaching the **same** media gateway for a whole call. Many voice setups have Cisco send audio straight to the gateways' fixed IPs and use the NLB only for SIP. Decide this with AU's Cisco team.

### AWS WAF and Shield
- **WAF (Web Application Firewall):** rules on the ALB that block common attacks (SQL injection, bad bots) and rate-limit floods. AWS provides managed rule sets.
- **Shield Standard:** free, automatic protection against common network floods (DDoS). **Shield Advanced** is a paid tier with a response team. It's probably unnecessary here, because callers don't come over the internet.

---

## 6. Compute: servers, containers, Kubernetes

### EC2 (Elastic Compute Cloud)
**EC2** gives you virtual servers ("instances"). You pick:

| Choice | Meaning | For us |
|---|---|---|
| **Instance type** | CPU, memory, GPU. Families: `m` general, `c` compute-heavy, `r` memory-heavy, `g` GPU. Size suffix: `large`, `xlarge`, `2xlarge`… | `m7i`/`c7i` for brain and voice pods; **`g6` (NVIDIA L4 GPU)** for the embedder and reranker |
| **AMI** (machine image) | The starting disk image (OS + software) | EKS-optimised AMIs, chosen for us by Karpenter |
| **EBS** | Network-attached disk volumes. They survive reboots and can be snapshotted. | Node disks; nothing important lives there |
| **Auto Scaling group** | Keeps N instances running and replaces dead ones | Replaced by Karpenter in EKS |

**Graviton** (ARM processors, `m7g` etc.) is cheaper per unit of work, but our Python audio libraries and PyTorch would need checking on ARM. Start with x86.

### Containers and Docker
A **container image** packages one service with everything it needs: code, Python, libraries, models. It's built from a **Dockerfile** and runs the same on a laptop, in testing and in production.
- **Image:** the packaged recipe (read-only).
- **Container:** a running copy of an image. Many can run from one image.
- **ECR (Elastic Container Registry):** AWS's private store for images. It can **scan images for known vulnerabilities**; bank security teams will ask for this.

Our plan has seven images: media-gateway, voice-worker, brain-api, embed-rerank, ingest-worker, review-worker, staff-ui.

### Running containers: ECS vs EKS vs Lambda
| Option | What it is | Fit for us |
|---|---|---|
| **ECS** | AWS's own simpler container scheduler. With **Fargate** you don't even manage servers. | Simpler to run, but **Fargate has no GPUs**, and we need GPUs for embeddings |
| **EKS** | Managed **Kubernetes**: AWS runs the control plane, you run the worker nodes | **Chosen**: GPUs, scaling on custom metrics (active calls), graceful drain, and it's the bank's production standard |
| **Lambda** | Run a function on demand, pay per millisecond, max 15 minutes | Only small background jobs. Calls last minutes, models take a minute to load. |

### Kubernetes: the concepts you'll meet
Kubernetes (K8s) runs containers across a fleet of machines and keeps them in the state you declare.

| Concept | Meaning | In our plan |
|---|---|---|
| **Cluster** | Control plane (the brain of Kubernetes, run by AWS in EKS) + worker **nodes** (EC2 instances) | One production cluster in Mumbai, nodes in 2–3 AZs |
| **Pod** | The smallest unit: one or more containers that run together | One brain-api pod = one copy of the brain |
| **Deployment** | "Keep N identical pods running; roll out new versions gradually" | brain-api, staff-ui, review-worker |
| **StatefulSet** | Like a Deployment, for pods that need a stable identity | Possibly media gateways (fixed addresses) |
| **Service** | A stable internal address that load-balances across pods | `brain-api.voice.svc` used by voice workers |
| **Ingress / AWS Load Balancer Controller** | Creates ALBs and NLBs from Kubernetes settings | The staff ALB, the SIP NLB |
| **Namespace** | A folder for separating workloads and permissions | `voice`, `monitoring` |
| **ConfigMap / Secret** | Settings and secrets given to pods | Secrets come from Secrets Manager through the **External Secrets** add-on |
| **Requests / limits** | How much CPU and memory a pod reserves and may use | Sized after load tests |
| **Probes** | **Liveness**: restart if dead. **Readiness**: don't send traffic until ready (models loaded). | Brain readiness waits for the GPU warm-up |
| **HPA** (Horizontal Pod Autoscaler) | Adds or removes pods based on CPU or other metrics | brain-api on CPU |
| **KEDA** | Autoscaling on custom metrics (queue length, active calls) | voice-workers on **active calls**, review-workers on queue depth |
| **PodDisruptionBudget** | "Never take down more than X pods at once" during maintenance | Protects live calls |
| **terminationGracePeriod + preStop** | Time a pod gets to finish before it's killed | Voice workers: stop taking new calls, finish current ones (longer than the longest call) |
| **Node pools / Karpenter** | Karpenter watches for pods that can't fit and starts the right EC2 instance (CPU or GPU) in seconds; removes empty nodes | CPU pool + GPU pool |
| **Helm** | A package format for Kubernetes settings (templates + values per environment) | One chart per service, or one for the whole app |
| **Device plugin** | Lets pods ask for a GPU (`nvidia.com/gpu: 1`) | embed-rerank pods |

---

## 7. Data services

### RDS and Aurora (relational databases)
**RDS** runs standard databases (PostgreSQL, MySQL…) for you: it installs, patches and backs them up. **Aurora PostgreSQL** is AWS's re-engineered version of PostgreSQL. It speaks the same protocol (our code and `psycopg` work unchanged) but stores data in a distributed layer kept in six copies across three AZs.

| Feature | Meaning | For us |
|---|---|---|
| **Writer + readers** | One instance takes writes; read replicas share the storage | Writer for calls; a reader for `/calls` dashboards and the judge |
| **Multi-AZ failover** | If the writer fails, a reader is promoted, typically within about a minute | Brief retry in our connection pool |
| **Backups / point-in-time restore** | Continuous backup; restore to any second within the retention window | Recover from a bad data change |
| **Snapshots** | Manual full copies, can be copied to another region | DR, before big migrations |
| **Parameter groups** | Database settings (memory, timeouts) | Defaults, then tuning |
| **Extensions** | `pgvector` is supported | Our vectors work as today |
| **RDS Proxy** | Pools database connections between many pods and the database | Many pods × 10 connections would otherwise overload Postgres |
| **Encryption** | Encrypted at rest with a KMS key (set at creation) | Always on |
| **Global Database** | Cross-region replica, with promotion if a region fails | The Hyderabad copy (box ⑪) |
| **Serverless v2** | Capacity that scales automatically within limits you set | Good for staging; production probably fixed sizes |

### ElastiCache (Valkey / Redis)
An **in-memory key-value store**, extremely fast (sub-millisecond) but not the system of record. **Valkey** is the open-source continuation of Redis that AWS now offers.
- **Multi-AZ with automatic failover:** a replica in another AZ takes over.
- **Use for short-lived shared data** (ADR 0021): which call is on which pod, OTP and SMS counters with expiry, rate limits, live updates to the desk.
- **Don't** put anything there you can't afford to lose.

### S3 (Simple Storage Service)
**Object storage**: files ("objects") in **buckets**, practically unlimited, extremely durable.

| Feature | Meaning | For us |
|---|---|---|
| **Bucket policy + Block Public Access** | Who may read and write. Block Public Access stops accidental public buckets. | Always on; an SCP enforces it |
| **SSE-KMS** | Encrypt every object with a KMS key | Recordings and documents |
| **Versioning** | Keeps old versions when an object is overwritten or deleted | Documents bucket |
| **Object Lock** | **WORM** (write once, read many): objects can't be changed or deleted until a date. **Governance mode** can be overridden with special permission; **compliance mode** can't be overridden by anyone, even the root user. | Recordings: tamper-proof evidence for disputes. Pick the mode and period with AU compliance. |
| **Lifecycle rules** | Move objects to cheaper storage classes over time, delete after N days | Recordings → Glacier after 90 days, delete after the retention period |
| **Storage classes** | Standard → Infrequent Access → Glacier (cheap, slower to retrieve) | |

**Macie** can scan buckets for personal data that shouldn't be there.

### SQS (Simple Queue Service)
A **message queue**: one service drops a job in, another takes it out later. It decouples work from the call.
- **Standard queue:** very high throughput; a message may occasionally arrive twice, so jobs must be safe to repeat (idempotent).
- **FIFO queue:** strict order and exactly-once processing, with lower throughput.
- **Visibility timeout:** while a worker processes a message it's hidden; if the worker dies, it reappears for another worker.
- **Dead-letter queue (DLQ):** messages that fail repeatedly are moved aside for a human to check, instead of looping forever.

For us: after a call ends, a message triggers the post-call review, the recording upload and the CRM sync. These run in the background, so a slow judge never slows a caller.

### Others you'll hear about
- **EventBridge:** routes events ("a call ended") to several consumers, and runs scheduled jobs (nightly evals, retention purge).
- **DynamoDB:** a NoSQL key-value database. Not needed: Aurora covers us (ADR 0003).
- **EFS:** a shared network file system. Not needed.

---

## 8. Security services

| Service | What it does | For us |
|---|---|---|
| **KMS** (Key Management Service) | Creates and guards encryption keys; keys never leave KMS. Services use **envelope encryption**: KMS encrypts a small data key, and the data key encrypts the data. Every use of a key is logged in CloudTrail. **Key policies** say who may use each key. | One key per purpose: Aurora, S3 recordings, secrets, signing. Customer-managed keys so AU controls rotation and access. |
| **Secrets Manager** | Stores secrets (DB passwords, API keys), hands them to apps at runtime, can rotate them automatically | Sarvam key, bank service token, OTP HMAC secret; replaces our per-process random secrets (briefing §2.3) |
| **ACM** (Certificate Manager) | Free TLS certificates for load balancers, renewed automatically | HTTPS on the ALB |
| **CloudTrail** | Records **every API call** in the account: who, what, when, from where | Sent to the security account; covers who changed a firewall or read a secret |
| **Config** | Records the configuration history of resources and checks rules ("all S3 buckets encrypted?") | Continuous compliance checks |
| **GuardDuty** | Threat detection from logs and network traffic (odd logins, crypto-mining, data leaving) | On in every account; findings go to AU's SOC |
| **Security Hub** | One dashboard collecting findings and checking against standards (CIS, AWS Foundational Best Practices) | The security team's daily view |
| **Inspector** | Scans EC2 instances and container images for known vulnerabilities | ECR scans before deploy |
| **Network Firewall** | A managed firewall for VPC traffic (domain allow-lists, intrusion detection) | The egress allow-list (only Sarvam) |
| **WAF / Shield** | Part 5 | |
| **IAM Access Analyzer** | Finds resources shared outside the account, and unused permissions | Least privilege over time |
| **Artifact** | Download AWS's compliance reports (ISO, SOC, PCI…) | For AU's audit and vendor files |

---

## 9. AI services

### Amazon Bedrock
Bedrock gives you **foundation models** (Anthropic Claude, OpenAI open-weight and GPT models, Meta Llama, Amazon Nova, Cohere and others) through one AWS API, inside AWS's security boundary. **You never manage a GPU for them.**

| Concept | Meaning | For us |
|---|---|---|
| **Model access** | Some models must be enabled per account and region before use | Enable the chosen router, answer and judge models in Mumbai |
| **InvokeModel / Converse / ConverseStream** | The APIs. Converse is one format for all models, with streaming and tool definitions. | Our `adapters/bedrock.py` uses Converse (ADR 0026) |
| **Inference profiles** | How requests are routed across regions. **In-region**: only that region. **Geographic** (e.g. India): only regions in that geography. **Global**: anywhere. | **India geo profile only**; never `apac.` or global for customer data |
| **Quotas** | Limits on tokens per minute and requests per minute, per model per region | The biggest scaling limit (Part 14). Request increases early. |
| **On-demand / provisioned throughput / batch** | Pay per token / reserve guaranteed capacity per hour / cheaper delayed processing | On-demand to start; provisioned if quotas can't meet peak; batch for offline evals |
| **Prompt caching** | Repeated prompt beginnings are billed much cheaper and run faster | Our router's fixed 3,000 tokens |
| **Guardrails** | Optional managed content filters and grounding checks | Not needed (briefing §4); our code guards cover it |
| **Invocation logging** | Log prompts and responses to S3 or CloudWatch; off by default | On, **without text** (ADR 0026) |
| **Data use** | AWS states Bedrock doesn't use your prompts to train models or share them with model providers | Confirm in the contract and AWS's data-protection page |
| **VPC endpoint** | Reach Bedrock privately | Box ③ |

### Other AI services you may meet
- **Amazon Transcribe:** speech-to-text. A possible **fallback** if Sarvam is down; check Indian language support.
- **Amazon Polly:** text-to-speech. A possible fallback voice, at least for "please hold, connecting you".
- **SageMaker:** for training or self-hosting models. Relevant only if Bedrock quotas or cost push us to self-host an LLM (vLLM) at very high volume.
- **Amazon Connect:** AWS's own contact centre. Not used, because AU has Cisco.

---

## 10. Monitoring and logs

**You can't run a call centre you can't see.** These are the tools:

| Tool | What it is | For us |
|---|---|---|
| **CloudWatch Metrics** | Numbers over time (CPU, request count, our custom metrics) | Active calls, first-audio latency, refusals, transfers |
| **CloudWatch Logs** | Central log storage and search (**Logs Insights** query language) | Our JSON logs (already masked) from every pod. Set a **retention period**; log storage costs add up. |
| **CloudWatch Alarms** | Notify or act when a metric crosses a line | "p95 first audio > 4 s", "LLM fallback in use", "Sarvam errors > 1%" |
| **Container Insights** | Pod, node and cluster metrics for EKS | Capacity planning |
| **Amazon Managed Prometheus + Managed Grafana** | Hosted Prometheus (metrics) and Grafana (dashboards) | Our brain already exposes `/metrics` in Prometheus format |
| **OpenTelemetry (ADOT) + X-Ray** | **Distributed tracing**: follow one call across services with timings | One trace per call: audio → STT → router → bank → answer → TTS |
| **CloudWatch Synthetics** | Scripted robot checks every few minutes | A synthetic test call every few minutes to catch outages before customers do |
| **SNS / PagerDuty / Opsgenie** | Deliver alerts to people | On-call rota |

**Golden signals** to watch for each service:
- **latency**;
- **traffic**;
- **errors**;
- **saturation** (how full).

For a voice agent, add: first-audio time, containment rate (calls fully handled by the AI), transfer rate, refusal rate, verification failures and LLM tokens per call.

---

## 11. How you deploy: Infrastructure as Code and CI/CD

### Infrastructure as Code (IaC)
Instead of clicking in the console, you **describe the infrastructure in code files**, keep them in git and apply them with a tool.
- **Why:** reviewable changes, identical environments (staging = production), rebuild DR in another region, an audit trail.
- **Terraform:** the most common multi-cloud tool, large community.
- **CloudFormation / CDK:** AWS's own (CDK lets you write it in Python or TypeScript).

**Pick what AU's cloud team already uses.**

**Rule: nobody changes production by hand in the console.** Changes go through code review and the pipeline.

### CI/CD (Continuous Integration / Continuous Delivery)
The pipeline that turns a code change into a running release:
1. **Build:** run the 316 offline tests, build container images.
2. **Scan:** image vulnerability scan (Inspector/ECR), dependency check.
3. **Eval gate:** the smoke set (and golden set for prompt or model changes) must not get worse.
4. **Push** images to ECR, tagged with the git commit.
5. **Deploy to staging**, then run synthetic calls.
6. **Canary in production:** 1–5% of calls on the new version; watch errors and latency; promote or roll back automatically.

Tools: GitHub Actions / GitLab CI / AWS CodePipeline for the pipeline; **Argo CD** for "GitOps" (the cluster pulls its desired state from git); **Argo Rollouts** or Flagger for canaries.

**Pipeline credentials:** the CI system assumes an IAM role through **OIDC** (short-lived tokens, no stored AWS keys).

---

## 12. Reliability and disaster recovery

| Term | Meaning |
|---|---|
| **High availability (HA)** | Survive the loss of a server or an AZ **without** noticeable downtime: Multi-AZ everything |
| **Disaster recovery (DR)** | Recover from losing a **whole region** |
| **RTO** (Recovery Time Objective) | How long you can be down. Example target: 1 hour. |
| **RPO** (Recovery Point Objective) | How much data you can lose. Example target: under 1 minute (Aurora Global DB replicates in about a second). |

**The four DR strategies, cheapest and slowest first:**

| Strategy | Meaning | RTO |
|---|---|---|
| Backup and restore | Backups copied to the other region; rebuild everything after a disaster | Hours to a day |
| Pilot light | Database replicated; everything else off, created on failover | Tens of minutes to hours |
| **Warm standby** (our plan) | Database replicated; a small copy of the stack running, scaled up on failover | Minutes to an hour |
| Active-active | Both regions serve traffic all the time | Near zero, at the highest cost and complexity |

**Our extra safety net:** during any outage, Cisco can route calls straight to human agents. Callers are still answered even while DR runs.

**AWS Backup** centralises backup schedules and cross-region copies. **Game days** are rehearsed failures (kill an AZ, cut Bedrock) to prove the runbooks work.

**AWS Well-Architected Framework:** AWS's checklist for good designs, in six pillars:
- operational excellence;
- security;
- reliability;
- performance efficiency;
- cost optimisation;
- sustainability.

The **Well-Architected Tool** walks you through a review, and banks often run one before go-live.

---

## 13. Cost

### How AWS charges
| Model | Meaning | For us |
|---|---|---|
| **On-demand** | Pay per second or hour, no commitment | Start here |
| **Savings Plans / Reserved Instances** | Commit to 1 or 3 years of usage for a large discount | Once steady usage is known (CPU and GPU nodes, Aurora) |
| **Spot** | Spare capacity at a big discount, but AWS can take it back with 2 minutes' notice | **Never for live calls.** Fine for batch evals or ingestion. |
| Per request / per GB / per token | Many services | Bedrock tokens, S3, SQS, NAT, logs |

### The big cost drivers here
1. **Bedrock tokens:** scale with calls; reduce with prompt caching and model choice.
2. **GPU instances** for embed and rerank.
3. **EKS nodes** for voice and brain pods.
4. **Aurora** instances and storage.
5. **Data transfer:**
   - NAT gateway processing (audio to Sarvam);
   - traffic **between AZs** (charged in both directions);
   - Direct Connect port hours and data out.
6. **CloudWatch logs** ingestion and storage: set retention, don't log audio.
7. Interface VPC endpoints (per hour per AZ).

### Cost tools
- **Tags** on every resource (`project=voice-agent`, `env=prod`, `service=brain`) so the bill can be split.
- **Cost Explorer:** where money goes.
- **AWS Budgets:** alerts when spend passes a threshold.
- **Compute Optimizer:** right-sizing suggestions.
- **Pricing Calculator:** estimates before building.

---

## 14. Limits (Service Quotas)

Every account has **quotas**: maximum numbers of instances, IPs, tokens per minute and so on. Many start low for new accounts and must be **raised by request**, which can take days.

**Request these early for this project:**
- **Bedrock tokens and requests per minute** for each chosen model in the India profile (about 20M input tokens a minute at 1,000 calls at once, estimate).
- **EC2 vCPU quotas for G instances** (GPUs). New accounts often start with a very low or zero limit.
- Elastic IPs, NAT gateways, load balancers per region.
- EKS pods per node (depends on instance type and networking mode).

Check them in the **Service Quotas** console. Add CloudWatch alarms at about 80% of each quota.

---

## 15. Compliance in India

- **Regions:** Mumbai and Hyderabad keep data in India. Enforce it with SCPs (no other regions) and with Bedrock's India profile.
- **Shared responsibility:** AWS's certifications (ISO 27001, SOC 2, PCI DSS…) cover AWS's half. Download them from **AWS Artifact** for AU's vendor file. Our half (configuration, access, code) is AU's and ours to evidence.
- **RBI outsourcing and IT rules:** the bank stays accountable. Expect contract terms on audit rights, data location and exit plans for AWS, Sarvam and the integrator.
- **CERT-In:** report incidents within 6 hours; keep logs (in India) for 180 days. Set CloudWatch and S3 log retention to match.
- **DPDP Act:** retention, erasure, breach notice (briefing §7.5). AWS services must be configured to support them, e.g. S3 lifecycle deletion aligned to the retention schedule.

---

## 16. Our diagram, box by box

| Box | AWS pieces | Concepts from this guide |
|---|---|---|
| ② Direct Connect | DX + Site-to-Site VPN backup, Virtual Private / Transit Gateway | Part 4 |
| ③ Entry | NLB (SIP), internal ALB + WAF + ACM (staff), VPC interface endpoints | Parts 4–5 |
| ④ EKS call path | EKS, Karpenter, EC2 (`c7i`/`m7i`, `g6`), ECR, KEDA, PodDisruptionBudgets, Pod Identity | Parts 3, 6 |
| ⑤ Sarvam | Outside AWS: NAT gateway + Network Firewall allow-list | Part 4 |
| ⑥ Bedrock | Converse API, India geo profile, quotas, prompt caching, VPC endpoint, invocation logging | Part 9 |
| ⑦ AU systems | Reached over DX; not AWS | Part 4 |
| ⑧ Data | Aurora PostgreSQL + RDS Proxy, ElastiCache Valkey, S3 (Object Lock, lifecycle, SSE-KMS), SQS + DLQ | Part 7 |
| ⑨ Background pods | EKS, SQS-driven scaling (KEDA), internal ALB for staff UI | Parts 6–7 |
| ⑩ Security and ops | KMS, Secrets Manager, IAM, CloudTrail, Config, GuardDuty, Security Hub, Inspector, CloudWatch, Prometheus/Grafana, OpenTelemetry, CI/CD | Parts 8, 10, 11 |
| ⑪ DR | Aurora Global Database, ECR replication, IaC to rebuild, Route 53 failover | Part 12 |

---

## 17. The order you'd actually set it up

1. **Accounts:** the Organization or landing zone from AU's cloud team; SSO; SCPs (India regions only, CloudTrail can't be disabled, no public S3).
2. **Network:** VPC IP plan agreed with AU; subnets in 2–3 AZs; route tables; NAT gateways; VPN first, Direct Connect ordered (it takes weeks).
3. **Security basics:** KMS keys, CloudTrail to the log account, GuardDuty, Security Hub, Config.
4. **Quotas:** request Bedrock, GPU and network quotas now.
5. **IaC repo and pipeline:** Terraform/CDK, CI with OIDC, ECR.
6. **Data:** Aurora (with pgvector), RDS Proxy, Valkey, S3 buckets with policies, SQS with DLQs.
7. **EKS:** cluster, Karpenter, load balancer controller, External Secrets, KEDA, monitoring agents.
8. **Deploy services** to staging; Bedrock and Sarvam adapters; synthetic calls.
9. **Observability:** dashboards, alarms, tracing.
10. **Cisco lab, then load test, DR drill, security review (VAPT), then the rollout phases** (go-live doc §B9).

---

## 18. Glossary

- **ACM:** Certificate Manager, for TLS certificates.
- **ALB / NLB:** Application (HTTP) / Network (TCP/UDP) load balancer.
- **AMI:** machine image for EC2.
- **AZ:** availability zone, an isolated data centre group within a region.
- **CIDR:** IP range notation, e.g. `10.20.0.0/16`.
- **DLQ:** dead-letter queue, for messages that keep failing.
- **DX:** Direct Connect, a private link to AWS.
- **EBS:** disk volumes for EC2.
- **ECR:** container image registry.
- **EKS / ECS:** managed Kubernetes / AWS's own container scheduler.
- **Egress:** outbound traffic.
- **HPA / KEDA:** Kubernetes autoscalers (built-in / custom metrics).
- **IaC:** Infrastructure as Code.
- **IAM:** identities and permissions.
- **IGW / NAT:** internet gateway / outbound-only internet for private subnets.
- **KMS:** encryption key service.
- **Multi-AZ:** copies in at least two AZs.
- **OIDC:** a standard for short-lived logins, used by CI to get AWS roles.
- **PrivateLink / VPC endpoint:** private access to AWS services.
- **RPO / RTO:** acceptable data loss / downtime.
- **SCP:** organization-wide permission guardrail.
- **SG:** security group, a stateful firewall per resource.
- **SSE-KMS:** server-side encryption with KMS keys.
- **STS:** temporary credential service.
- **VPC:** your private network in AWS.
- **WAF:** web application firewall.
- **WORM / Object Lock:** write-once storage that can't be altered.

---

## 19. Self-check questions

Try answering these without looking back. If you can, you're ready for design conversations with AU's cloud team.

1. A pod in a private subnet must call Sarvam on the internet. Which two components make that possible, and which one stops it calling anything else?
2. Why does the phone traffic go through an NLB but the staff UI through an ALB? What would break if you swapped them?
3. The database is in a data subnet with no internet route. How do brain pods reach it, and what one security-group rule allows that?
4. The Mumbai AZ `ap-south-1a` loses power. Walk through what happens to: a live call on a voice-worker in 1a; Aurora's writer in 1a; the NLB.
5. What's the difference between a security group and an SCP? Give one example of each from our plan.
6. Why are pods given IAM roles instead of access keys in environment variables?
7. Recordings must be kept 7 years and never altered. Which S3 features do you use, and which Object Lock mode would compliance likely demand?
8. Bedrock returns "throttled" during a peak. Name two things to do before go-live so this doesn't happen, and one thing the code already does when it does.
9. Explain RTO and RPO for our Hyderabad warm standby in one sentence each.
10. Why must our VPC IP range be agreed with AU's network team before anything is built?
