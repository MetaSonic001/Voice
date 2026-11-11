# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

The project's decisions were spread across chat conversations, five planning documents (some now out of date) and `PROJECT-GUIDE.md`. Work now continues across separate Claude Code chats, and the agent is meant to become a production POC reviewed by AU's architecture and security teams. Both need the "why" behind each choice in one place.

## Decision

Keep ADRs in `docs/adr/`, one decision per file, in the format described in [README.md](README.md). Record past decisions retroactively (0002–0021) and write a new ADR for every future decision that is expensive to reverse or spans several parts of the system.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **ADRs in the repo** | Versioned with the code; one file per "why"; industry-standard format reviewers recognise | Must be kept up to date |
| Only `PROJECT-GUIDE.md` | One document | Explains *how*, not the options and trade-offs; long |
| Wiki / Confluence | Easy to browse | Drifts from the code; not available to coding agents |

## Consequences

- **Easier:** onboarding, security and architecture reviews, and new chats that need the reasoning without re-deriving it.
- **Harder:** one more thing to update. `CLAUDE.md` says to add an ADR when a big decision is made.
- **Revisit when:** never; this is the meta-decision.
