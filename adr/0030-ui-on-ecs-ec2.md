# ADR-0030: UI on ECS with EC2 capacity (small servers of its own), not Fargate

- **Status:** Accepted (replaces the UI part of [0028](0028-ecs-on-ec2-for-the-brain.md); the rest of 0028 stands)
- **Date:** 2026-11-23
- **Deciders:** project owner

## Context

ADR 0028 put the UI (`au-ui`: static pages served by nginx, 0.25 vCPU and 0.5 GB per task) on Fargate. The owner wants
all our containers on EC2: one way of running containers to explain, secure and review, and one kind of server.
The brain's servers can't take the UI in production: 4 brain containers × 2 vCPU fill a `c7i.2xlarge` (8 vCPU).

## Decision

The UI runs on ECS with EC2 capacity: a second, small Managed Instances capacity provider `au-ui-ec2` with `t3.small`
servers (2 vCPU, 2 GB, x86). One server for the pilot; two in production, one per availability zone. Same image, same
load balancer rule, same port (8080); only the task definition's `requiresCompatibilities` changes
(`deploy/ecs/ui-task-definition.json`).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Own small EC2 servers (`t3.small`)** | One way of running containers; servers we choose; the UI never takes room from calls | Two more servers to account for; about ₹3,400 a month for two (vs ₹1,800 on Fargate) |
| Fargate (ADR 0028) | No servers at all; cheapest for two tiny tasks | A second way of running containers to secure and explain |
| On the brain's `c7i` servers | No extra servers | In production they are full; the UI would compete with calls, or need an extra brain server |
| S3 + CloudFront | No container | A public edge service in front of internal staff screens; a second route for the API |

## Consequences

- **Easier:** one capacity type (EC2, Managed Instances) for everything except the SageMaker search endpoint.
- **Harder / costs:** about ₹1,600 a month more than Fargate (2 × `t3.small` at $0.0224/h + 20 GB disks).
- **Revisit when:** the bank prefers Fargate for small services, or the brain servers get spare room (then the UI
  could move onto them).

## Links

`deploy/ecs/ui-task-definition.json`, `deploy/aws-pilot-guide.md`, `deploy/README.md`, ADR 0028.
