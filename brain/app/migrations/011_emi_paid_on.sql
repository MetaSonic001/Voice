-- The day an EMI was actually taken from the account (NULL while unpaid or bounced). The due date is not the debit
-- date: "when was my last EMI deducted?" is answered from this, never from the due date (PM review, 17 Nov 2026).
ALTER TABLE corebank.loan_emis ADD COLUMN IF NOT EXISTS paid_on date;
