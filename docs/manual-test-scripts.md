# Manual test scripts (voice or text)

Five long calls to run yourself on the test console (`/`), by voice or by typing. Each mixes languages and touches
several bank systems. Start each call from the number shown (the demo customers live in the mock bank; their stories
are in `brain/app/adapters/mock_corebank.py`). When the agent asks for a code, type the 6 digits shown in the simulated
SMS on the keypad - never say it aloud.

After each call, open it on `/calls` and check:

- **Turn N** labels; the judge's findings link to them.
- Per turn, **Where the answer came from** (knowledge-base sources, sentence by sentence) and **Bank systems called**
  (which department's system, what was read or changed, status and time).
- **Quality review** at the top: scores, findings, the most important fix. Problems also land on `/fixes`.

Say the lines in your own words too - the point is to test understanding, not the exact sentences.

---

## 1. Ravi - salary, payments, loans, card (English -> Hinglish -> Hindi)

Number: **9000000101**. Two accounts (4521 salary, 4512 inactive), home and personal loan (one EMI bounced), two credit
cards (6712), a UPI payment still processing.

| # | Say | Expect | Systems |
|---|---|---|---|
| 1 | "Hi, what's my balance in both accounts?" | Asks to verify; after the code: both balances; the hold on 4521 explained; no "nothing on hold" for 4512 | CIF, core banking |
| 2 | "Show me the last five transactions on my salary account." | Exactly 5 (3 read out, "want the other 2?") with dates and amounts | core banking |
| 3 | "Haan, baaki bhi batao." | The other 2 | - |
| 4 | "Suresh Patel wala UPI payment ho gaya kya?" | Still processing, not "paid" | core banking |
| 5 | "Mera personal loan ka EMI kyun bounce hua?" | The EMI that failed, why (low balance at presentation), the bounce charge | Loans |
| 6 | "Home loan ki agli EMI kab hai?" | Amount and date of the next EMI | Loans |
| 7 | "मेरे क्रेडिट कार्ड का कुल बकाया और मिनिमम ड्यू क्या है?" | Total due, minimum due, due date, in Hindi | Credit cards |
| 8 | "How many lounge visits do I have left this quarter?" | Visits used / left on the right card | Credit cards |
| 9 | "Block my credit card ending 6712 temporarily." | Read-back + one clear yes needed | - |
| 10 | "Yes." | Done, with a reference number | Credit cards (write) |
| 11 | "Thanks, bye." | One short goodbye, no LLM needed | - |

## 2. Meera - senior citizen FDs (Hindi first, then English)

Number: **9000000102**. Senior citizen, three FDs (7824 maturing next month, auto-renew on).

| # | Say | Expect | Systems |
|---|---|---|---|
| 1 | "नमस्ते, मैं वरिष्ठ नागरिक हूँ। मेरी कौन सी FD सबसे पहले मैच्योर होगी?" | Verify; then the FD ending 7824 and its date, in Hindi | CIF, Deposits |
| 2 | "उस पर मुझे कितने पैसे मिलेंगे?" | Maturity amount, without repeating the date twice | Deposits |
| 3 | "Will it renew automatically?" | Auto-renew status of 7824 | Deposits |
| 4 | "If I break it today, how much would I get?" | Premature value (from bank data), penalty explained | Deposits |
| 5 | "What's the FD rate for 2 years for me?" | Senior 2-year rate from the rate card (remembers she is a senior) | knowledge base |
| 6 | "Aur 3 saal ke liye?" | The 3-year senior rate, no need to repeat "senior" | knowledge base |
| 7 | "Can I close an FD before maturity in general?" | The rule with exceptions (tax-saver lock-in); then "check your own FD?" | knowledge base |
| 8 | "Nahi, theek hai. Dhanyavaad." | Short thanks | - |

## 3. Arjun - debit card trouble before a trip (English + Hinglish)

Number: **9000000103**. Debit card 5521 temporarily blocked yesterday, shop payments off, travelling abroad.

| # | Say | Expect | Systems |
|---|---|---|---|
| 1 | "My debit card is not working at shops." | Verify; then the cause (temporarily blocked, shop payments off, declined payments if any), and "unblock your card ending 5521?" - not "how much and when?" | CIF, Debit cards, core banking |
| 2 | "Yes, unblock it." | Done, with a reference; offers shop payments next | Debit cards (write) |
| 3 | "Haan, shop payments bhi chalu kar do." | Read-back, then done on yes | Debit cards (write) |
| 4 | "Can I use it abroad? I'm travelling next week." | General rule, then "check your own card?" | knowledge base |
| 5 | "Please switch on international payments on my debit card." | Read-back, a yes, then done | Debit cards (write) |
| 6 | "Which payments are on now?" | The current settings, one by one | Debit cards |
| 7 | "Connect me to someone from the cards team." | Transfer straight away (no extra question) | - |

## 4. Imran - charges and an unrecognised card payment (Hinglish + English)

Number: **9000000105**. Average-balance shortfall charge, an unrecognised ₹8,499 card spend (Singapore), a ₹799
debit, a double charge.

| # | Say | Expect | Systems |
|---|---|---|---|
| 1 | "Mere account se minimum balance ka charge kyun kata?" | Verify; the charge, last month's average vs required, how it is worked out; offer to review | CIF, core banking, knowledge base |
| 2 | "I don't recognise a transaction of 8,499 on my credit card." | Finds the ₹8,499 Singapore payment; protect first: permanent block offered | Credit cards |
| 3 | "Yes, block it." | Done; dispute + fraud team offered | Credit cards (write) |
| 4 | "Haan, dispute raise karo aur fraud team se baat karao." | Complaint reference, transfer to the fraud team | CRM (write) |
| 5 (new call) | "I think I was charged twice for something." | Finds the duplicate and offers to raise it | core banking |
| 6 | "Yes please." | Request raised with a reference and a date | CRM (write) |

## 5. A new customer choosing products (Hinglish + English + Hindi)

Number: **leave it empty** (unknown caller). Nothing here needs verification.

| # | Say | Expect | Systems |
|---|---|---|---|
| 1 | "Hello, mujhe AU Bank mein ek savings account kholna hai." | Asks one question at a time about the need (no list of all accounts, no code) | - |
| 2 | "Main college student hoon, zero balance chahiye." | Skips what it already knows; recommends 1-2 accounts with a reason | knowledge base |
| 3 | "What documents do I need?" | The documents, briefly | knowledge base |
| 4 | "Aur credit card ke liye? Main zyada online shopping karta hoon." | Card questions (spend, travel), then 1-2 cards with reasons and eligibility | knowledge base |
| 5 | "What's the difference between Zenith+ and Altura?" | Both sides compared; never one side made up | knowledge base |
| 6 | "Tell me about the Zenith+ card." | A short overview, then "rewards, fees or eligibility?" | knowledge base |
| 7 | "Aur iska forex markup?" | Just that figure | knowledge base |
| 8 | "मेरे दादाजी 70 साल के हैं, उनके लिए FD में सबसे अच्छा रेट क्या है?" | The senior rate for the best tenure, in Hindi | knowledge base |
| 9 | "Can someone call me to help me apply?" | Takes a callback request (or explains it needs a number) | CRM (write) |
| 10 | "Who won the cricket match yesterday?" | Politely stays on banking | - |

---

What to look for in all five: answers short and spoken (no lists, no "[1]"), one question at a time, the language
followed, nothing already said asked again, account data only after the code, every action read back and confirmed,
and no made-up figures.
