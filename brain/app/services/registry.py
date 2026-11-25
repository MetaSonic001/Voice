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


# The rules code enforces, in plain words, for the read-only Guardrails tab on /journeys. Each names the file that
# enforces it; changing a rule is a code change (and a test), never an edit on the page.
GUARDRAILS = [
    ("Who may ask what", "Product questions: anyone. The caller's own accounts, cards and loans: only after the keypad "
     "code sent to their registered mobile.", "services/registry.py, services/auth.py"),
    ("Card actions need a yes", "Blocking, unblocking or changing a card's settings: only for a verified caller, and only "
     "after an explicit yes to a read-back of exactly what will change.", "services/registry.py, services/confirm.py"),
    ("Some things always go to a person", "Address or mobile changes, refunds, limits, closing an FD and payments are "
     "never done by the agent: it offers the right team.", "services/registry.py"),
    ("Secrets stay on the keypad", "OTP and PIN are typed on the keypad only. They never go through speech recognition, the "
     "AI or the logs, and are masked everywhere.", "services/privacy.py, voice/session.py"),
    ("Personal details are masked", "Account numbers, phone numbers and other personal details are masked before anything "
     "is sent to the AI and in every log.", "services/privacy.py"),
    ("Never asks for a secret, never sends a strange link", "No sentence may ask for an OTP, PIN, CVV or password, or give a "
     "web link that isn't in the bank's own documents.", "services/guard.py"),
    ("Figures are never invented", "Every number in an answer must come from the bank's documents or the caller's bank "
     "data; a sentence with an unsupported figure is not said. A fixed sentence can't add a new figure.",
     "services/numbers.py, services/releases.py"),
    ("Fraud first", "Words about fraud, a stolen card or a shared OTP start the fraud journey: protect the account first, "
     "then offer the 24x7 fraud team.", "services/guard.py, services/journeys.py"),
    ("Stays on purpose", "A caller who tries to change the agent's instructions, or asks about something the bank doesn't "
     "do, gets a short polite line and nothing else.", "services/guard.py, services/conversation.py"),
    ("Short answers on a phone", "An answer stops after 6 sentences or 110 words (unless the caller asked for everything).",
     "services/dialogue.py"),
    ("Edits are checked before they go live", "A changed sentence must keep its blanks ({ref}, {dept}...), add no figure, "
     "be sayable by a voice and pass the secret and link checks; prompt changes start on a small share of calls.",
     "services/releases.py"),
]
