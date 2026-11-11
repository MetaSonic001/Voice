"""The task registry (PHASE-4-PLAN §3.1): what each kind of request needs, decided in code.

The LLM only names a kind of request; this table says which handler runs it and who may ask for it:

| access   | meaning                                                                           |
|----------|-----------------------------------------------------------------------------------|
| open     | product information; no identity needed                                           |
| verified | the caller's own data or a workflow on their account; registered mobile + keypad code |
| confirm  | verified, and an explicit "yes" to a read-back before anything changes            |
| people   | the agent never does it (address or mobile change, refunds, limits, closing an FD, payments) |

Being verified does not unlock everything. Adding a task is a row here plus a line in the prompt, not a classifier rewrite.
Handler kinds: kb, bank_read, action, workflow, handoff, lead, fixed.
"""

from dataclasses import dataclass

from app.domain import Request

CARD_ACTIONS = ("block", "unblock", "controls")


@dataclass(frozen=True)
class Task:
    name: str
    handler: str  # kb | bank_read | action | workflow | handoff | lead | fixed
    access: str  # open | verified | confirm | people

    @property
    def needs_verification(self) -> bool:
        return self.access in ("verified", "confirm")


def task_for(r: Request) -> Task:
    kind = r.kind
    if kind == "general":
        return Task("kb.answer", "kb", "open")
    if kind == "new_customer":
        return Task("kb.new_customer", "kb", "open")
    if kind in ("smalltalk", "out_of_scope"):
        return Task(f"fixed.{kind}", "fixed", "open")
    if kind == "human":
        return Task("handoff.person", "handoff", "open")
    if kind == "personal":
        return Task(f"bank.{(r.personal or {}).get('topic') or 'other'}", "bank_read", "verified")
    if kind == "action":
        if (r.action or {}).get("type") in CARD_ACTIONS:
            return Task(f"card.{r.action['type']}", "action", "confirm")
        return Task("people.change", "handoff", "people")  # anything else that changes something goes to a person
    if kind == "issue":
        return Task(f"issue.{r.issue}", "workflow", "verified")
    if kind == "fraud":
        return Task("workflow.fraud", "workflow", "verified")
    return Task("fixed.out_of_scope", "fixed", "open")
