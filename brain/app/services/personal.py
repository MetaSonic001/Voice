"""Answers about the verified caller's own products, from the bank's systems, through fixed templates (en / hi / hinglish).

No LLM writes these answers. The understand step only picks a topic, one detail from a fixed list and filters;
the figures come straight from the bank data, so they cannot be invented. Only the last 4 digits of any account,
card, FD or loan are ever said. Where the data can't answer, the reply says what it can see and offers a person.
"""

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from app.domain import Account, Customer, Loan, Transaction

TOPICS = ("balance", "transactions", "account", "charges", "fd", "loan", "credit_card", "debit_card", "summary", "other")
PERIODS = ("", "today", "yesterday", "last_7_days", "this_week", "this_month", "last_month", "last_30_days")
DETAILS = (
    "",  # the usual answer for the topic
    # account / balance / charges
    "available_balance", "salary_account", "linked_debit_card", "minimum_balance", "balance_maintained", "charges",
    "benefits", "status",
    # FD
    "all_fds", "first_maturing", "maturity_amount", "rate", "auto_renew", "premature_value",
    # loan
    "outstanding", "next_emi", "emi_status", "emis_count", "emi_increase", "bounce_charges", "foreclosure",
    # credit card
    "amount_due", "payment_status", "available_limit", "international", "lounge", "annual_fee", "statement",
    # debit card
    "controls", "declines",
    # transactions / summary
    "duplicate", "upcoming", "inactive", "highest",
)
# Set only by code corrections (`understanding.asked_only`), never offered to the LLM: kept out of DETAILS so the
# router's schema (and every cached eval request) stays the same.
CODE_DETAILS = ("loan_count", "loan_count_left", "hold_check", "available_only", "total_scope", "failed_only", "pending_check", "hold_why", "emi_last_paid", "emi_recovery", "repaid_amount")
_HI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


@dataclass
class PersonalQuery:
    topic: str = "other"
    detail: str = ""  # one of DETAILS
    account: str = ""  # which savings account: "salary", "4521", "digital", "second", "all", or "" (not said)
    item: str = ""  # which FD / loan / card: last 4 digits ("7824", "6712") or a type ("home", "personal", "zenith")
    card: str = ""  # debit | credit | "" (for card questions)
    direction: str = "any"  # any | debit | credit
    period: str = ""
    count: int = 0  # number of transactions asked for (0 = default 5)
    min_amount: float = 0
    amount: float = 0  # an amount the caller mentions ("the ₹799 debit", "around 2,000")
    keyword: str = ""  # a word to match in transactions: channel, merchant or person ("upi", "rajesh", "restaurant")
    total: bool = False  # "how much did I spend": sum the matching transactions


@dataclass
class PersonalAnswer:
    sentences: list[str]
    outcome: str = "answered"  # answered | clarify
    fields: list[str] = field(default_factory=list)  # what was disclosed (audit)
    needs_kb: str = ""  # the answer needs the knowledge base too (e.g. "benefits of the Value savings account")
    offer: dict = field(default_factory=dict)  # the answer ends with an offer a "yes" should carry out:
    # {"handoff": dept} | {"ticket": category} | {"action": request}
    kb_optional: bool = False  # the knowledge-base part only adds to the bank facts: if the KB has no answer, say only those
    after: list[str] = field(default_factory=list)  # said after the knowledge-base part (e.g. the offer to review a charge)


# ───────── wording ─────────
# Short, warm and natural: what a good phone banker would say. {placeholders} are filled in code.

# Why money is on hold, said in the caller's language: the bank gives the reason in English ("a pending card payment at a
# fuel station"). A Hindi sentence with an English aside in brackets was read out as one run-on phrase (PM review, 17 Nov).
# Production: the core bank sends a hold reason code, mapped here the same way.
_HOLD_REASONS = {"a pending card payment at a fuel station": ("फ्यूल स्टेशन पर कार्ड से किया गया एक पेमेंट अभी पेंडिंग है",
                                                             "fuel station par card se kiya gaya ek payment abhi pending hai")}


def hold_reason(reason: str | None, lang: str) -> str:
    """The hold reason as a clause in the caller's language; English (or an unknown reason) as the bank gave it."""
    if lang == "en" or not reason:
        return reason or "a pending payment"
    known = _HOLD_REASONS.get(reason.strip().lower())
    if known:
        return known[0 if lang == "hi" else 1]
    return "एक पेमेंट अभी पेंडिंग है" if lang == "hi" else "ek payment abhi pending hai"


T = {
    "acct": {"en": "your {type} account ending {l4}", "hi": "आपके {l4} वाले {type} अकाउंट",
             "hinglish": "aapke {type} account ending {l4}"},
    "balance": {"en": "You have ₹{amt} in {acct}.", "hi": "{acct} में ₹{amt} हैं।", "hinglish": "{acct} mein ₹{amt} hain."},
    "balance_hold": {"en": "Of that, ₹{hold} is on hold for {reason}, so you can use ₹{avail} right now.",  # reason: "a pending card payment"
                     "hi": "इसमें से ₹{hold} होल्ड पर है, क्योंकि {reason}। इसलिए अभी आप ₹{avail} इस्तेमाल कर सकते हैं।",
                     "hinglish": "Isme se ₹{hold} hold par hai, kyunki {reason}. Isliye abhi aap ₹{avail} use kar sakte hain."},
    # "how much can I use right now?": the usable amount per account, not the whole balance again
    "avail_line": {"en": "You can use ₹{avail} from {acct} right now.", "hi": "{acct} से अभी आप ₹{avail} इस्तेमाल कर सकते हैं।",
                   "hinglish": "{acct} se abhi aap ₹{avail} use kar sakte hain."},
    "avail_hold": {"en": "₹{hold} of it is on hold for {reason}.", "hi": "इसमें से ₹{hold} होल्ड पर है, क्योंकि {reason}।",
                   "hinglish": "Isme se ₹{hold} hold par hai, kyunki {reason}."},
    # "has that 2,500 been deducted?" about a hold: a hold is not a completed payment
    "hold_not_debit": {"en": "No. The ₹{hold} is on hold for {reason}. It hasn't been taken from your account as a completed payment yet. Until it clears, you can use ₹{avail}.",
                       "hi": "नहीं। ₹{hold} होल्ड पर है, क्योंकि {reason}। यह अभी पूरे पेमेंट की तरह आपके अकाउंट से कटा नहीं है। जब तक यह क्लियर नहीं होता, आप ₹{avail} इस्तेमाल कर सकते हैं।",
                       "hinglish": "Nahi. ₹{hold} hold par hai, kyunki {reason}. Yeh abhi complete payment ki tarah aapke account se kata nahi hai. Jab tak yeh clear nahi hota, aap ₹{avail} use kar sakte hain."},
    "hold_what": {"en": "The ₹{hold} is on hold on {acct}, for {reason}. The payment hasn't been settled yet, so the money hasn't left your account. Until it clears, you can use ₹{avail}.",
                  "hi": "{acct} में ₹{hold} होल्ड पर है, क्योंकि {reason}। यह अभी आपके अकाउंट से कटा नहीं है। जब तक यह क्लियर नहीं होता, आप ₹{avail} इस्तेमाल कर सकते हैं।",
                  "hinglish": "{Acct} mein ₹{hold} hold par hai, kyunki {reason}. Yeh abhi aapke account se kata nahi hai. Jab tak yeh clear nahi hota, aap ₹{avail} use kar sakte hain."},
    # "does that include money people sent me?" after a total: totals are money paid out only (successful debits)
    "total_scope": {"en": "No. That total is only money you paid out. Money people sent you isn't included.",
                    "hi": "नहीं। उस टोटल में सिर्फ़ आपके भेजे गए पैसे हैं। आपको मिले पैसे शामिल नहीं हैं।",
                    "hinglish": "Nahi. Us total mein sirf aapke bheje hue paise hain. Aapko mile paise shamil nahi hain."},
    "txn_empty": {"en": "I don't see any transactions on your account ending {l4}.", "hi": "आपके अकाउंट {l4} में कोई ट्रांज़ैक्शन नहीं दिख रहा।",
                  "hinglish": "Aapke account ending {l4} mein koi transaction nahi dikh raha."},
    "hold_pending": {"en": "The ₹{hold} on {acct}, for {reason}, is still on hold. I don't see a reversal recorded yet.",
                     "hi": "{acct} में ₹{hold} अभी भी होल्ड पर है, क्योंकि {reason}। इसका कोई रिवर्सल अभी दर्ज नहीं दिख रहा।",
                     "hinglish": "{Acct} mein ₹{hold} abhi bhi hold par hai, kyunki {reason}. Iska koi reversal abhi record mein nahi dikh raha."},
    "no_hold_any": {"en": "I don't see any amount on hold on your accounts.", "hi": "आपके अकाउंट में कोई अमाउंट होल्ड पर नहीं दिख रहा।",
                    "hinglish": "Aapke account mein koi amount hold par nahi dikh raha."},
    "avail_after_hold": {"en": "{Acct} has ₹{bal}. ₹{hold} of it is on hold for {reason}, so after the hold you can use ₹{avail}.",
                         "hi": "{acct} में ₹{bal} हैं। इसमें से ₹{hold} होल्ड पर है, क्योंकि {reason}। होल्ड के बाद आप ₹{avail} इस्तेमाल कर सकते हैं।",
                         "hinglish": "{Acct} mein ₹{bal} hain. Isme se ₹{hold} hold par hai, kyunki {reason}. Hold ke baad aap ₹{avail} use kar sakte hain."},
    "avail_inactive": {"en": "{Acct} shows ₹{avail}, but the account is inactive, so payments from it may not go through until it's active again.",
                       "hi": "{acct} में ₹{avail} दिख रहे हैं, लेकिन यह अकाउंट इनएक्टिव है, इसलिए दोबारा एक्टिव होने तक इससे पेमेंट शायद न हो पाए।",
                       "hinglish": "{Acct} mein ₹{avail} dikh rahe hain, lekin yeh account inactive hai, isliye dobara active hone tak isse payment shayad na ho paaye."},
    "pending_still": {"en": "The ₹{amt} payment to {who} on {date} is still processing. It hasn't been reversed or completed yet.",
                      "hi": "{date} का {who} को ₹{amt} का पेमेंट अभी प्रोसेस हो रहा है। यह न रिवर्स हुआ है, न पूरा हुआ है।",
                      "hinglish": "{date} ka {who} ko ₹{amt} ka payment abhi process ho raha hai. Yeh na reverse hua hai, na complete hua hai."},
    "pending_reversed": {"en": "Yes. The ₹{amt} payment to {who} on {date} was reversed, so the money came back to your account.",
                         "hi": "जी हाँ। {date} का {who} को ₹{amt} का पेमेंट रिवर्स हो गया, पैसे आपके अकाउंट में वापस आ गए।",
                         "hinglish": "Ji haan. {date} ka {who} ko ₹{amt} ka payment reverse ho gaya, paise aapke account mein wapas aa gaye."},
    "pending_none": {"en": "I don't see any pending payment on your accounts right now.", "hi": "आपके अकाउंट में अभी कोई पेंडिंग पेमेंट नहीं दिख रहा।",
                     "hinglish": "Aapke account mein abhi koi pending payment nahi dikh raha."},
    "txn_each_overview": {"en": "I'll go through your {n} accounts one at a time, starting with the account ending {l4}.",
                          "hi": "मैं आपके {n} अकाउंट एक-एक करके बताती हूँ, पहले {l4} वाला अकाउंट।",
                          "hinglish": "Main aapke {n} accounts ek ek karke batati hoon, pehle {l4} wala account."},
    "txn_each_intro": {"en": "On your account ending {l4}, here are the last {k}.", "hi": "आपके अकाउंट {l4} के पिछले {k} ट्रांज़ैक्शन ये हैं।",
                       "hinglish": "Aapke account ending {l4} ke pichhle {k} transactions yeh hain."},
    "txn_each_only": {"en": "I found only {k} transaction{s} on your account ending {l4}.", "hi": "आपके अकाउंट {l4} में सिर्फ़ {k} ट्रांज़ैक्शन है।",
                      "hinglish": "Aapke account ending {l4} mein sirf {k} transaction hai."},
    "no_hold": {"en": "There's nothing on hold, so all of it is available.", "hi": "कुछ भी होल्ड पर नहीं है, पूरा अमाउंट इस्तेमाल कर सकते हैं।",
                "hinglish": "Kuch bhi hold par nahi hai, poora amount available hai."},
    "which": {"en": "You have {n} accounts. {list}. Which one would you like me to check?",
              "hi": "आपके {n} अकाउंट हैं। {list}। आप किस अकाउंट के बारे में जानना चाहेंगे?",
              "hinglish": "Aapke {n} accounts hain. {list}. Aap kaunsa account check karwana chahenge?"},
    "which_item": {"en": "You have {n} {what}. {list}. Which one do you mean?",
                   "hi": "आपके {n} {what} हैं। {list}। आप किसकी बात कर रहे हैं?",
                   "hinglish": "Aapke {n} {what} hain. {list}. Aap kiski baat kar rahe hain?"},
    "no_match": {"en": "I couldn't find one that matches. {list}. Which one did you mean?",
                 "hi": "मुझे ऐसा कोई नहीं मिला। {list}। आप किसकी बात कर रहे हैं?",
                 "hinglish": "Mujhe aisa koi nahi mila. {list}. Aap kiski baat kar rahe hain?"},
    "salary_is": {"en": "Your salary comes into {acct}{employer}.", "hi": "आपकी सैलरी {acct} में आती है{employer}।",
                  "hinglish": "Aapki salary {acct} mein aati hai{employer}."},
    "no_salary": {"en": "I don't see salary credits on any of your accounts, so I can't tell which one is your salary account.",
                  "hi": "आपके किसी अकाउंट में सैलरी क्रेडिट नहीं दिख रहा, इसलिए सैलरी अकाउंट नहीं बता सकती।",
                  "hinglish": "Aapke kisi account mein salary credit nahi dikh raha, isliye salary account nahi bata sakti."},
    "linked_dc": {"en": "{Acct} has a {variant} debit card ending {l4}, and it's {status}.",
                  "hi": "{acct} के साथ {l4} वाले {variant} डेबिट कार्ड जुड़ा है, और यह {status} है।",
                  "hinglish": "{acct} ke saath {variant} debit card ending {l4} linked hai, aur yeh {status} hai."},
    "no_dc_on": {"en": "There's no debit card on {acct}.", "hi": "{acct} पर कोई डेबिट कार्ड नहीं है।",
                 "hinglish": "{acct} par koi debit card nahi hai."},
    "variant": {"en": "That's your {type} savings account, ending {l4}.", "hi": "यह आपका {l4} वाला {type} सेविंग्स अकाउंट है।",
                "hinglish": "Yeh aapka {l4} wala {type} savings account hai."},
    "acct_status": {"en": "{Acct} is {status}.", "hi": "{acct} {status} है।", "hinglish": "{acct} {status} hai."},
    "inactive_note": {"en": "It's been inactive for a while. A quick visit to the app or a branch makes it active again.",
                      "hi": "यह कुछ समय से इनएक्टिव है। ऐप या ब्रांच से इसे फिर से एक्टिव किया जा सकता है।",
                      "hinglish": "Yeh kuch time se inactive hai. App ya branch se ise phir se active kar sakte hain."},
    "amb_rule": {"en": "{Acct} needs an average monthly balance of ₹{req}{branch}.",
                 "hi": "{acct} में ₹{req} का एवरेज मंथली बैलेंस रखना होता है{branch}।",
                 "hinglish": "{acct} mein ₹{req} ka average monthly balance rakhna hota hai{branch}."},
    "amb_none": {"en": "Good news: {acct} has no minimum balance requirement.",
                 "hi": "अच्छी बात यह है कि {acct} में कोई मिनिमम बैलेंस ज़रूरी नहीं है।",
                 "hinglish": "Achhi baat yeh hai ki {acct} mein koi minimum balance zaroori nahi hai."},
    "amb_now_ok": {"en": "This month your average so far is ₹{mtd}, so you're above it.",
                   "hi": "इस महीने अब तक आपका एवरेज ₹{mtd} है, यानी आप इससे ऊपर हैं।",
                   "hinglish": "Is mahine ab tak aapka average ₹{mtd} hai, yaani aap upar hain."},
    "amb_now_short": {"en": "This month your average so far is ₹{mtd}, which is ₹{gap} short, so a charge may apply if it stays that way.",
                      "hi": "इस महीने अब तक आपका एवरेज ₹{mtd} है, जो ₹{gap} कम है। ऐसा ही रहा तो चार्ज लग सकता है।",
                      "hinglish": "Is mahine ab tak aapka average ₹{mtd} hai, jo ₹{gap} kam hai. Aisa hi raha toh charge lag sakta hai."},
    "charge_line": {"en": "₹{amt} was charged on {date} for {what}{rev}.", "hi": "{date} को {what} के लिए ₹{amt} का चार्ज लगा{rev}।",
                    "hinglish": "{date} ko {what} ke liye ₹{amt} ka charge laga{rev}."},
    "amb_charge_why": {"en": "That's because last month's average balance was ₹{last}, below the ₹{req} needed.",
                       "hi": "ऐसा इसलिए क्योंकि पिछले महीने का एवरेज बैलेंस ₹{last} था, जो ज़रूरी ₹{req} से कम था।",
                       "hinglish": "Aisa isliye kyunki pichhle mahine ka average balance ₹{last} tha, jo zaroori ₹{req} se kam tha."},
    "no_charges": {"en": "I don't see any charges on {acct} recently.", "hi": "{acct} पर हाल में कोई चार्ज नहीं दिख रहा।",
                   "hinglish": "{acct} par recently koi charge nahi dikh raha."},
    "charge_dispute": {"en": "If you think a charge is wrong, I can raise a request to review it.",
                       "hi": "अगर आपको कोई चार्ज गलत लगता है, तो मैं उसे रिव्यू करवाने के लिए रिक्वेस्ट डाल सकती हूँ।",
                       "hinglish": "Agar aapko koi charge galat lagta hai, toh main uske review ke liye request raise kar sakti hoon."},
    # transactions
    "txn_intro": {"en": "Here are the last {k} transactions on your account ending {l4}.",
                  "hi": "आपके {l4} वाले अकाउंट के पिछले {k} ट्रांज़ैक्शन ये हैं।",
                  "hinglish": "Aapke account ending {l4} ke pichhle {k} transactions yeh hain."},
    "txn_intro_salary": {"en": "Here are the last {k} transactions on your salary account ending {l4}.",
                         "hi": "आपके सैलरी {l4} वाले अकाउंट के पिछले {k} ट्रांज़ैक्शन ये हैं।",
                         "hinglish": "Aapke salary account ending {l4} ke pichhle {k} transactions yeh hain."},
    "txn_found": {"en": "I found {k} on your account ending {l4}.", "hi": "आपके {l4} वाले अकाउंट में मुझे {k} ट्रांज़ैक्शन मिले।",
                  "hinglish": "Aapke account ending {l4} mein mujhe {k} transactions mile."},
    "txn_merged_intro": {"en": "Here are your latest {k} transactions across your accounts.",
                         "hi": "आपके सभी अकाउंट के सबसे नए {k} ट्रांज़ैक्शन ये हैं।",
                         "hinglish": "Aapke saare accounts ke latest {k} transactions yeh hain."},
    "txn_merged_found": {"en": "I found {k} across your accounts.", "hi": "आपके अकाउंट में मुझे {k} ट्रांज़ैक्शन मिले।",
                         "hinglish": "Aapke accounts mein mujhe {k} transactions mile."},
    # fewer transactions than the caller asked for: say so, so it never sounds like the request was ignored
    "txn_only": {"en": "There are only {k} transactions on your account ending {l4}.",
                 "hi": "आपके {l4} वाले अकाउंट में सिर्फ़ {k} ट्रांज़ैक्शन हैं।",
                 "hinglish": "Aapke account ending {l4} mein sirf {k} transactions hain."},
    "txn_only_one": {"en": "There's only one transaction on your account ending {l4}.",
                     "hi": "आपके {l4} वाले अकाउंट में सिर्फ़ एक ट्रांज़ैक्शन है।",
                     "hinglish": "Aapke account ending {l4} mein sirf ek transaction hai."},
    "txn_merged_only": {"en": "There are only {k} transactions across your accounts.", "hi": "आपके अकाउंट में सिर्फ़ {k} ट्रांज़ैक्शन हैं।",
                        "hinglish": "Aapke accounts mein sirf {k} transactions hain."},
    "acct_inactive_note": {"en": "That account is inactive, which is why there's so little on it.",
                           "hi": "यह अकाउंट इनएक्टिव है, इसलिए इसमें बहुत कम ट्रांज़ैक्शन हैं।",
                           "hinglish": "Yeh account inactive hai, isliye ismein bahut kam transactions hain."},
    "more_offer": {"en": "Want the other {k}?", "hi": "बाकी {k} भी बताऊँ?", "hinglish": "Baaki {k} bhi bataun?"},
    # one transaction as a spoken sentence (built in _txn_line): when, what, how much, to or from whom, and its status.
    # "Nothing extra" for a completed one; the other statuses say plainly what happened to the money.
    "txn_tail": {
        "en": {"success": {"Debit": "", "Credit": ""}, "pending": {"Debit": ", and it's still processing", "Credit": ", and it's still processing"},
               "failed": {"Debit": ". It was unsuccessful, so no money was deducted", "Credit": ", but it didn't come through"},
               "returned": {"Debit": ". It was returned, so the payment wasn't completed", "Credit": ", but it was returned"},
               "reversed": {"Debit": ", and it was reversed, so the money came back to you", "Credit": ", but it was later reversed"},
               "unknown": {"Debit": ", but I can't confirm whether it went through", "Credit": ", but I can't confirm whether it came through"}},
        "hi": {"success": {"Debit": "", "Credit": ""}, "pending": {"Debit": ", जो अभी प्रोसेस हो रहा है", "Credit": ", जो अभी प्रोसेस हो रहा है"},
               "failed": {"Debit": "। यह पूरा नहीं हुआ, इसलिए कोई पैसा नहीं कटा", "Credit": ", लेकिन पैसे नहीं आए"},
               "returned": {"Debit": "। यह रिटर्न हो गया, इसलिए पेमेंट पूरा नहीं हुआ", "Credit": ", लेकिन यह रिटर्न हो गया"},
               "reversed": {"Debit": ", जो रिवर्स होकर वापस आ गया", "Credit": ", लेकिन बाद में रिवर्स हो गया"},
               "unknown": {"Debit": ", लेकिन इसका स्टेटस कन्फ़र्म नहीं हो पाया", "Credit": ", लेकिन इसका स्टेटस कन्फ़र्म नहीं हो पाया"}},
        "hinglish": {"success": {"Debit": "", "Credit": ""}, "pending": {"Debit": ", jo abhi process ho raha hai", "Credit": ", jo abhi process ho raha hai"},
                     "failed": {"Debit": ". Yeh complete nahi hua, isliye koi paisa nahi kata", "Credit": ", lekin paise nahi aaye"},
                     "returned": {"Debit": ". Yeh return ho gaya, isliye payment complete nahi hua", "Credit": ", lekin yeh return ho gaya"},
                     "reversed": {"Debit": ", jo reverse hokar wapas aa gaya", "Credit": ", lekin baad mein reverse ho gaya"},
                     "unknown": {"Debit": ", lekin iska status confirm nahi ho paaya", "Credit": ", lekin iska status confirm nahi ho paaya"}}},
    "txn_total": {"en": "That adds up to ₹{amt} across {k} payments from the account ending {l4}.",
                  "hi": "{l4} वाले अकाउंट से {k} पेमेंट्स में कुल ₹{amt} हुए।",
                  "hinglish": "Account ending {l4} se {k} payments mein total ₹{amt} hue."},
    "txn_none": {"en": "I don't see a matching transaction on the account ending {l4}.",
                 "hi": "{l4} वाले अकाउंट में ऐसा कोई ट्रांज़ैक्शन नहीं दिख रहा।",
                 "hinglish": "Account ending {l4} mein aisa koi transaction nahi dikh raha."},
    # what the bank's records say about a transaction (structured note keys from the core banking system)
    "note_atm_not_dispensed": {"en": "The ATM recorded that the cash wasn't dispensed, so the ₹{amt} will come back to your account automatically by {due}. If it's later than that, the bank pays you ₹100 for every day of delay.",
                               "hi": "ATM के रिकॉर्ड में कैश नहीं निकला दिखा है, इसलिए ₹{amt} {due} तक अपने आप आपके अकाउंट में वापस आ जाएँगे। इससे देर हुई तो बैंक हर दिन की देरी के ₹100 देगा।",
                               "hinglish": "ATM ke record mein cash nahi nikla dikha hai, isliye ₹{amt} {due} tak apne aap aapke account mein wapas aa jayenge. Isse der hui toh bank har din ki deri ke ₹100 dega."},
    "note_upi_pending": {"en": "It's waiting for confirmation from the other person's bank. If it doesn't go through, the money comes back to you automatically by the next working day.",
                         "hi": "यह अभी सामने वाले के बैंक के कन्फ़र्मेशन का इंतज़ार कर रहा है। अगर यह पूरा नहीं हुआ, तो पैसे अगले वर्किंग डे तक अपने आप वापस आ जाएँगे।",
                         "hinglish": "Yeh abhi saamne wale ke bank ke confirmation ka wait kar raha hai. Agar yeh complete nahi hua, toh paise agle working day tak apne aap wapas aa jayenge."},
    "note_emi_insufficient": {"en": "the EMI was presented at {time}, when the balance was ₹{balance}, and your salary came in at {credit_time}",
                              "hi": "EMI {time} पर पेश हुई थी, तब बैलेंस ₹{balance} था, और आपकी सैलरी {credit_time} पर आई",
                              "hinglish": "EMI {time} par present hui thi, tab balance ₹{balance} tha, aur aapki salary {credit_time} par aayi"},
    "note_declined_blocked": {"en": "It was declined because the card was temporarily blocked, so no money was taken.",
                              "hi": "यह इसलिए डिक्लाइन हुआ क्योंकि कार्ड टेम्पररी ब्लॉक था, इसलिए कोई पैसा नहीं कटा।",
                              "hinglish": "Yeh isliye decline hua kyunki card temporarily blocked tha, isliye koi paisa nahi kata."},
    "reason_insufficient_balance": {"en": "the account didn't have enough balance when it was presented", "hi": "उस समय अकाउंट में काफ़ी बैलेंस नहीं था",
                                    "hinglish": "us waqt account mein enough balance nahi tha"},
    "dup_found": {"en": "Yes, it looks like a double charge: ₹{amt} at {who} went through twice on {date}, a minute apart.",
                  "hi": "हाँ, यह डबल चार्ज लगता है: {date} को {who} पर ₹{amt} एक मिनट के अंतर से दो बार कटे।",
                  "hinglish": "Haan, yeh double charge lagta hai: {date} ko {who} par ₹{amt} ek minute ke gap mein do baar kate."},
    "dup_none": {"en": "I don't see any duplicate payments recently.", "hi": "हाल में कोई डबल पेमेंट नहीं दिख रहा।",
                 "hinglish": "Recently koi double payment nahi dikh raha."},
    "dispute_offer": {"en": "I can raise a dispute for it right away if you'd like.",
                      "hi": "आप चाहें तो मैं अभी इसकी शिकायत दर्ज कर सकती हूँ।",
                      "hinglish": "Aap chahein toh main abhi iska dispute raise kar sakti hoon."},
    # FD
    "fd": {"en": "Your FD ending {l4} has ₹{p} at {rate}%. It {verb} on {date} for ₹{mat}.",
           "hi": "आपकी {l4} वाली FD में ₹{p} हैं, {rate}% पर। यह {date} को ₹{mat} के साथ {verb}।",
           "hinglish": "Aapki FD ending {l4} mein ₹{p} hain, {rate}% par. Yeh {date} ko ₹{mat} ke saath {verb}."},
    "fd_verb": {"en": ("matures", "matured"), "hi": ("मैच्योर होगी", "मैच्योर हो चुकी है"), "hinglish": ("mature hogi", "mature ho chuki hai")},
    "fd_count": {"en": "You have {n} fixed deposits.", "hi": "आपकी {n} FD हैं।", "hinglish": "Aapki {n} FDs hain."},
    "fd_first": {"en": "The first to mature is your FD ending {l4}, on {date}.", "hi": "सबसे पहले {l4} वाली FD {date} को मैच्योर होगी।",
                 "hinglish": "Sabse pehle FD ending {l4} {date} ko mature hogi."},
    "fd_amount_then": {"en": "You'll receive about ₹{mat} then.", "hi": "उस दिन आपको लगभग ₹{mat} मिलेंगे।",
                       "hinglish": "Us din aapko lagbhag ₹{mat} milenge."},
    "fd_amount": {"en": "On maturity you'll receive about ₹{mat} from your FD ending {l4}, on {date}.",
                  "hi": "मैच्योरिटी पर आपकी {l4} वाली FD से {date} को लगभग ₹{mat} मिलेंगे।",
                  "hinglish": "Maturity par aapki FD ending {l4} se {date} ko lagbhag ₹{mat} milenge."},
    "fd_payout": {"en": "It pays interest every quarter, so the ₹{p} principal comes back on maturity.",
                  "hi": "इसमें ब्याज हर तीन महीने में मिलता है, इसलिए मैच्योरिटी पर ₹{p} प्रिंसिपल अमाउंट वापस मिलेगा।",
                  "hinglish": "Isme interest har quarter milta hai, isliye maturity par ₹{p} principal wapas milega."},
    "fd_rate": {"en": "Your FD ending {l4} earns {rate}% a year.", "hi": "आपकी {l4} वाली FD पर {rate}% सालाना ब्याज है।",
                "hinglish": "Aapki FD ending {l4} par {rate}% saalana interest hai."},
    "fd_renew": {"en": "Yes, your FD ending {l4} is set to renew automatically when it matures.",
                 "hi": "हाँ, आपकी {l4} वाली FD मैच्योरिटी पर अपने आप रिन्यू होगी।",
                 "hinglish": "Haan, aapki FD ending {l4} maturity par apne aap renew hogi."},
    "fd_no_renew": {"en": "No, your FD ending {l4} won't renew on its own. The money goes to your savings account on maturity.",
                    "hi": "नहीं, आपकी {l4} वाली FD अपने आप रिन्यू नहीं होगी। मैच्योरिटी पर पैसे सेविंग्स अकाउंट में आएँगे।",
                    "hinglish": "Nahi, aapki FD ending {l4} apne aap renew nahi hogi. Maturity par paise savings account mein aayenge."},
    "fd_break": {"en": "If you closed your FD ending {l4} today, you'd get roughly ₹{amt}. That's an estimate with the 1% early-closure penalty. The exact amount is confirmed when it's closed.",
                 "hi": "अगर आप आज {l4} वाली FD तोड़ें, तो लगभग ₹{amt} मिलेंगे। यह 1% प्रीमैच्योर पेनल्टी के साथ अनुमान है। सही अमाउंट बंद करते समय पता चलेगा।",
                 "hinglish": "Agar aap aaj FD ending {l4} todein, toh lagbhag ₹{amt} milenge. Yeh 1% premature penalty ke saath estimate hai. Exact amount close karte waqt confirm hoga."},
    "fd_break_how": {"en": "I can't close it on this call, but you can do it in the AU 0101 app or at a branch.",
                     "hi": "मैं इस कॉल पर इसे बंद नहीं कर सकती, लेकिन आप AU 0101 ऐप या ब्रांच से कर सकते हैं।",
                     "hinglish": "Main is call par ise close nahi kar sakti, lekin aap AU 0101 app ya branch se kar sakte hain."},
    "no_fd": {"en": "I don't see any fixed deposit in your name.", "hi": "आपके नाम पर कोई FD नहीं दिख रही।",
              "hinglish": "Aapke naam par koi FD nahi dikh rahi."},
    # loans
    "loan": {"en": "Your {type} loan ending {l4} has ₹{out} outstanding.", "hi": "आपके {l4} वाले {type} लोन पर ₹{out} बकाया है।",
             "hinglish": "Aapke {type} loan ending {l4} par ₹{out} outstanding hai."},
    "emi_next": {"en": "Your next EMI of ₹{emi} is due on {date}.", "hi": "आपकी अगली EMI ₹{emi} की है, {date} को।",
                 "hinglish": "Aapki agli EMI ₹{emi} ki hai, {date} ko."},
    "loan_count_one": {"en": "You have one active loan, your {type} loan ending {l4}.", "hi": "आपके नाम पर अभी एक लोन चल रहा है, आपका {l4} वाला {type} लोन।",
                       "hinglish": "Aapke naam par abhi ek loan chal raha hai, aapka {type} loan ending {l4}."},
    "loan_count": {"en": "You have {n} active loans: {list}.", "hi": "आपके नाम पर अभी {n} लोन चल रहे हैं: {list}।",
                   "hinglish": "Aapke naam par abhi {n} loans chal rahe hain: {list}."},
    "emi_next_of": {"en": "The EMI on your {type} loan{ending} is ₹{emi}, due on {date}.",
                    "hi": "आपके {pre}{type} लोन की अगली EMI ₹{emi} की है, {date} को।",
                    "hinglish": "Aapke {type} loan{ending} ki agli EMI ₹{emi} ki hai, {date} ko."},
    "emi_count": {"en": "You've paid {paid} EMIs and {left} are left.", "hi": "आपने {paid} EMI भर दी हैं और {left} बाकी हैं।",
                  "hinglish": "Aapne {paid} EMIs bhar di hain aur {left} baaki hain."},
    "emi_paid": {"en": "Your EMI of ₹{emi} due on {date} was paid successfully.",
                 "hi": "{date} की ₹{emi} की EMI भर दी गई।",
                 "hinglish": "{date} ki ₹{emi} ki EMI successfully pay ho gayi."},
    "emi_last_debit": {"en": "The last EMI taken from your account for your {type} loan{ending} was ₹{emi}, on {date}.",
                       "hi": "आपके {pre}{type} लोन की आख़िरी EMI ₹{emi} की थी, जो {date} को आपके अकाउंट से कटी।",
                       "hinglish": "Aapke {type} loan{ending} ki aakhri EMI ₹{emi} ki thi, jo {date} ko aapke account se kati."},
    "emi_paid_due_only": {"en": "The last EMI paid on your {type} loan{ending} was ₹{emi}, due on {date}. I can't see the exact day it was debited.",
                          "hi": "आपके {pre}{type} लोन की आख़िरी भरी गई EMI ₹{emi} की थी, जिसकी तारीख़ {date} थी। यह किस दिन कटी, वह मुझे नहीं दिख रहा।",
                          "hinglish": "Aapke {type} loan{ending} ki aakhri bhari gayi EMI ₹{emi} ki thi, jiski date {date} thi. Yeh kis din kati, woh mujhe nahi dikh raha."},
    "emi_last_failed": {"en": "The ₹{emi} EMI presented on {date} was unsuccessful, so no money was deducted for it.",
                        "hi": "{date} को ₹{emi} की EMI नहीं कट पाई, इसलिए उसके लिए कोई पैसा नहीं कटा।",
                        "hinglish": "{date} ko ₹{emi} ki EMI nahi kat paayi, isliye uske liye koi paisa nahi kata."},
    "emi_not_recovered": {"en": "No, the ₹{emi} EMI on your {type} loan{ending}, due on {date}, hasn't been paid yet. It was returned because {reason}.",
                          "hi": "नहीं, आपके {pre}{type} लोन की {date} की ₹{emi} की EMI अभी तक नहीं भरी गई है। यह वापस आ गई थी, क्योंकि {reason}।",
                          "hinglish": "Nahi, aapke {type} loan{ending} ki {date} ki ₹{emi} ki EMI abhi tak pay nahi hui hai. Yeh return ho gayi thi, kyunki {reason}."},
    "repaid_of": {"en": "On your {type} loan{ending}, you've paid {paid} EMIs. Of the ₹{total} loan amount, ₹{repaid} has been repaid and ₹{out} is still outstanding.",
                  "hi": "आपके {pre}{type} लोन की {paid} EMI भरी जा चुकी हैं। ₹{total} के लोन में से ₹{repaid} चुकाए जा चुके हैं और ₹{out} बकाया है।",
                  "hinglish": "Aapke {type} loan{ending} ki {paid} EMIs bhar di gayi hain. ₹{total} ke loan mein se ₹{repaid} repay ho chuke hain aur ₹{out} outstanding hai."},
    "repaid_across": {"en": "Across your {n} loans, ₹{amt} has been repaid so far.", "hi": "आपके {n} लोन में अब तक कुल ₹{amt} चुकाए जा चुके हैं।",
                      "hinglish": "Aapke {n} loans mein ab tak kul ₹{amt} repay ho chuke hain."},
    "emi_paid_day": {"en": "Your last EMI on your {type} loan{ending}, ₹{emi}, was paid on {paid}.",
                     "hi": "आपके {pre}{type} लोन की ₹{emi} की आख़िरी EMI {paid} को भर दी गई।",
                     "hinglish": "Aapke {type} loan{ending} ki ₹{emi} ki aakhri EMI {paid} ko pay ho gayi."},
    "emi_paid_on": {"en": "The EMI on your {type} loan{ending}, ₹{emi} due on {date}, was paid. It was taken from your account on {paid}.",
                    "hi": "आपके {pre}{type} लोन की {date} की ₹{emi} की EMI भर दी गई। यह {paid} को आपके अकाउंट से कटी।",
                    "hinglish": "Aapke {type} loan{ending} ki {date} ki ₹{emi} ki EMI pay ho gayi. Yeh {paid} ko aapke account se kati."},
    "emis_across": {"en": "Across your {n} loans, you've paid {total} EMIs so far: {parts}.",
                    "hi": "आपके {n} लोन में अब तक कुल {total} EMI भरी गई हैं: {parts}।",
                    "hinglish": "Aapke {n} loans mein ab tak kul {total} EMIs bhari gayi hain: {parts}."},
    "emi_paid_of": {"en": "The EMI on your {type} loan{ending}, ₹{emi} due on {date}, was paid successfully.",
                    "hi": "आपके {pre}{type} लोन की {date} की ₹{emi} की EMI भर दी गई।",
                    "hinglish": "Aapke {type} loan{ending} ki {date} ki ₹{emi} ki EMI successfully pay ho gayi."},
    "emi_count_of": {"en": "On your {type} loan{ending}, you've paid {paid} EMIs and {left} are left.",
                     "hi": "आपके {pre}{type} लोन की {paid} EMI भर दी गई हैं और {left} बाकी हैं।",
                     "hinglish": "Aapke {type} loan{ending} ki {paid} EMIs bhar di gayi hain aur {left} baaki hain."},
    "emi_no_history": {"en": "I can't see any EMI paid yet on your {type} loan.",
                       "hi": "आपके {type} लोन पर अभी तक कोई EMI भरी हुई नहीं दिख रही।",
                       "hinglish": "Aapke {type} loan par abhi tak koi EMI pay hui nahi dikh rahi."},
    "emi_bounced": {"en": "The EMI on your {type} loan{ending}, ₹{emi} due on {date}, didn't go through: {reason}.",
                    "hi": "आपके {pre}{type} लोन की {date} की ₹{emi} की EMI नहीं कटी: {reason}।",
                    "hinglish": "Aapke {type} loan{ending} ki {date} ki ₹{emi} ki EMI nahi kati: {reason}."},
    "emi_bounce_charge": {"en": "A bounce charge of ₹{amt} was added for it.", "hi": "इसके लिए ₹{amt} का बाउंस चार्ज लगा है।",
                          "hinglish": "Iske liye ₹{amt} ka bounce charge laga hai."},
    "emi_bounce_time": {"en": "Looking at your account, {note}.", "hi": "आपके अकाउंट में देखें तो {note}।", "hinglish": "Account mein dekhein toh {note}."},
    "no_bounce": {"en": "Good news: no bounce or late-payment charges on your {type} loan.",
                  "hi": "अच्छी बात यह है कि आपके {type} लोन पर कोई बाउंस या लेट-पेमेंट चार्ज नहीं है।",
                  "hinglish": "Achhi baat yeh hai ki aapke {type} loan par koi bounce ya late-payment charge nahi hai."},
    "emi_up": {"en": "Your EMI went up from ₹{old} to ₹{new} because the {type} loan is on a floating rate, and the rate moved from {r0}% to {r1}% on {date}.",
               "hi": "आपकी EMI ₹{old} से ₹{new} हो गई, क्योंकि {type} लोन फ्लोटिंग रेट पर है और {date} को रेट {r0}% से {r1}% हुआ।",
               "hinglish": "Aapki EMI ₹{old} se ₹{new} ho gayi, kyunki {type} loan floating rate par hai aur {date} ko rate {r0}% se {r1}% hua."},
    "emi_same": {"en": "Your {type} loan EMI hasn't changed. It's ₹{emi}.", "hi": "आपके {type} लोन की EMI नहीं बदली। यह ₹{emi} है।",
                 "hinglish": "Aapke {type} loan ki EMI nahi badli. Yeh ₹{emi} hai."},
    "foreclose": {"en": "To close your {type} loan today you'd need about ₹{amt}: ₹{out} outstanding{charge}. That's an estimate. The loan team gives the exact figure.",
                  "hi": "आज {type} लोन बंद करने के लिए लगभग ₹{amt} चाहिए: ₹{out} बकाया{charge}। यह अनुमान है। सही अमाउंट लोन टीम बताएगी।",
                  "hinglish": "Aaj {type} loan close karne ke liye lagbhag ₹{amt} chahiye: ₹{out} outstanding{charge}. Yeh estimate hai. Exact amount loan team batayegi."},
    "foreclose_team": {"en": "The exact amount to close it, including any charge, comes from our loan team as a foreclosure letter. I can connect you to them.",
                       "hi": "इसे बंद करने की सही अमाउंट, चार्ज के साथ, हमारी लोन टीम फोरक्लोज़र लेटर में बताती है। मैं आपको उनसे जोड़ सकती हूँ।",
                       "hinglish": "Ise close karne ka exact amount, charges ke saath, hamari loan team foreclosure letter mein batati hai. Main aapko unse connect kar sakti hoon."},
    "no_payment_made": {"en": "I haven't made any payment.", "hi": "मैंने कोई पेमेंट नहीं किया है।", "hinglish": "Maine koi payment nahi kiya hai."},
    "no_loan": {"en": "I don't see any loan in your name.", "hi": "आपके नाम पर कोई लोन नहीं दिख रहा।",
                "hinglish": "Aapke naam par koi loan nahi dikh raha."},
    # credit cards
    "cc_due": {"en": "On your {variant} card ending {l4}, the total due is ₹{total} and the minimum due is ₹{min}, by {date}.",
               "hi": "आपके {l4} वाले {variant} कार्ड पर टोटल ड्यू ₹{total} और मिनिमम ड्यू ₹{min} है, {date} तक।",
               "hinglish": "Aapke {variant} card ending {l4} par total due ₹{total} aur minimum due ₹{min} hai, {date} tak."},
    "cc_paid_up": {"en": "Your {variant} card ending {l4} has nothing due right now.", "hi": "आपके {l4} वाले {variant} कार्ड पर अभी कुछ भी ड्यू नहीं है।",
                   "hinglish": "Aapke {variant} card ending {l4} par abhi kuch due nahi hai."},
    "cc_limit": {"en": "Your {variant} card ending {l4} has ₹{avail} available out of a ₹{limit} limit.",
                 "hi": "आपके {l4} वाले {variant} कार्ड में ₹{limit} की लिमिट में से ₹{avail} अवेलेबल है।",
                 "hinglish": "Aapke {variant} card ending {l4} mein ₹{limit} ki limit mein se ₹{avail} available hai."},
    "cc_pay_processing": {"en": "I can see your payment of ₹{amt} from {date}. It's still being processed, which is why the outstanding hasn't changed yet. It usually shows within 1 to 2 working days.",
                          "hi": "{date} का आपका ₹{amt} का पेमेंट दिख रहा है। यह अभी प्रोसेस हो रहा है, इसलिए बकाया नहीं बदला। आमतौर पर 1 se 2 वर्किंग डे में दिख जाता है।",
                          "hinglish": "{date} ka aapka ₹{amt} ka payment dikh raha hai. Yeh abhi process ho raha hai, isliye outstanding nahi badla. Aamtaur par 1 se 2 working days mein dikh jaata hai."},
    "cc_pay_posted": {"en": "Your last payment of ₹{amt} on {date} has gone through.", "hi": "{date} का आपका ₹{amt} का पिछला पेमेंट हो चुका है।",
                      "hinglish": "{date} ka aapka ₹{amt} ka last payment ho chuka hai."},
    "cc_statement": {"en": "Your latest statement for the card ending {l4} was generated on {date}, and the previous bill was {paid}.",
                     "hi": "{l4} वाले कार्ड का पिछला स्टेटमेंट {date} को बना, और पिछला बिल {paid}।",
                     "hinglish": "Card ending {l4} ka latest statement {date} ko generate hua, aur pichhla bill {paid}."},
    "paid_words": {"en": ("paid in full", "not fully paid"), "hi": ("पूरा भर दिया गया था", "पूरा नहीं भरा गया था"),
                   "hinglish": ("poora pay hua tha", "poora pay nahi hua tha")},
    "cc_lounge": {"en": "Your {variant} card has {total} lounge visits a quarter. You've used {used}, so {left} are left.",
                  "hi": "आपके {variant} कार्ड पर हर तीन महीने में {total} लाउंज विज़िट हैं। आपने {used} इस्तेमाल किए, {left} बाकी हैं।",
                  "hinglish": "Aapke {variant} card par har quarter {total} lounge visits hain. Aapne {used} use kiye, {left} baaki hain."},
    "cc_no_lounge": {"en": "Your {variant} card doesn't include complimentary lounge visits.", "hi": "आपके {variant} कार्ड में फ़्री लाउंज विज़िट नहीं हैं।",
                     "hinglish": "Aapke {variant} card mein complimentary lounge visits nahi hain."},
    "cc_fee": {"en": "The ₹{fee} annual fee on your {variant} card was charged on {date}. It's waived only when the previous year's spend reaches ₹{waiver}. Your spend was ₹{spend}.",
               "hi": "आपके {variant} कार्ड पर ₹{fee} की सालाना फीस {date} को लगी। यह तभी माफ़ होती है जब पिछले साल ₹{waiver} खर्च हों। आपका खर्च ₹{spend} था।",
               "hinglish": "Aapke {variant} card par ₹{fee} ki annual fee {date} ko lagi. Yeh tabhi waive hoti hai jab pichhle saal ₹{waiver} spend ho. Aapka spend ₹{spend} tha."},
    "cc_no_fee": {"en": "Your {variant} card has no annual fee charged.", "hi": "आपके {variant} कार्ड पर कोई सालाना फीस नहीं लगी है।",
                  "hinglish": "Aapke {variant} card par koi annual fee nahi lagi hai."},
    "cc_intl_on": {"en": "International use is switched on for your card ending {l4}, and you have ₹{avail} available, so a payment of about ₹{amt} would fit.",
                   "hi": "आपके {l4} वाले कार्ड पर इंटरनेशनल इस्तेमाल चालू है और ₹{avail} अवेलेबल है, तो लगभग ₹{amt} का पेमेंट हो सकता है।",
                   "hinglish": "Aapke card ending {l4} par international use chalu hai aur ₹{avail} available hai, toh lagbhag ₹{amt} ka payment ho sakta hai."},
    "cc_intl_off": {"en": "International use is switched off on your card ending {l4}. I can switch it on for you if you'd like.",
                    "hi": "आपके {l4} वाले कार्ड पर इंटरनेशनल इस्तेमाल बंद है। आप चाहें तो मैं इसे चालू कर सकती हूँ।",
                    "hinglish": "Aapke card ending {l4} par international use band hai. Aap chahein toh main ise chalu kar sakti hoon."},
    "cc_txns": {"en": "Here are the recent spends on your card ending {l4}.", "hi": "आपके {l4} वाले कार्ड पर हाल के खर्च ये हैं।",
                "hinglish": "Aapke card ending {l4} par recent spends yeh hain."},
    "cc_txn": {"en": "{when}, you spent ₹{amt} at {who}{intl}.", "hi": "{when} आपने {who} पर ₹{amt} खर्च किए{intl}।",
               "hinglish": "{when} aapne {who} par ₹{amt} kharch kiye{intl}."},
    "cc_intl": {"en": ", an international payment", "hi": ", जो इंटरनेशनल पेमेंट था", "hinglish": ", jo international payment tha"},
    "no_cc": {"en": "I don't see a credit card in your name.", "hi": "आपके नाम पर कोई क्रेडिट कार्ड नहीं दिख रहा।",
              "hinglish": "Aapke naam par koi credit card nahi dikh raha."},
    # debit cards
    "dc_state": {"en": "Your debit card ending {l4} is {status}. {controls}", "hi": "आपका {l4} वाला डेबिट कार्ड {status} है। {controls}",
                 "hinglish": "Aapka debit card ending {l4} {status} hai. {controls}"},
    "dc_controls": {"en": "Online payments are {ecom}, shop payments {pos}, tap-to-pay {contactless}, ATM withdrawals {atm}, and international use {international}.",
                    "hi": "ऑनलाइन पेमेंट {ecom}, दुकान पर पेमेंट {pos}, टैप-टू-पे {contactless}, ATM से पैसे निकालना {atm}, और इंटरनेशनल इस्तेमाल {international} है।",
                    "hinglish": "Online payments {ecom}, shop payments {pos}, tap-to-pay {contactless}, ATM withdrawal {atm}, aur international use {international} hai."},
    "on_off": {"en": ("on", "off"), "hi": ("चालू", "बंद"), "hinglish": ("chalu", "band")},
    "status_words": {"en": {"active": "active", "temp_blocked": "temporarily blocked", "blocked": "permanently blocked", "inactive": "inactive"},
                     "hi": {"active": "चालू", "temp_blocked": "टेम्पररी ब्लॉक", "blocked": "परमानेंट ब्लॉक", "inactive": "इनएक्टिव"},
                     "hinglish": {"active": "active", "temp_blocked": "temporarily blocked", "blocked": "permanently blocked", "inactive": "inactive"}},
    "dc_declines": {"en": "I can see {n} declined payments of ₹{amt} at {who} on {date}. They were declined because {why}, so no money was taken.",
                    "hi": "{date} को {who} पर ₹{amt} के {n} पेमेंट डिक्लाइन हुए। ये इसलिए डिक्लाइन हुए क्योंकि {why}, इसलिए कोई पैसा नहीं कटा।",
                    "hinglish": "{date} ko {who} par ₹{amt} ke {n} payments decline hue. Yeh isliye decline hue kyunki {why}, isliye koi paisa nahi kata."},
    "dc_blocked_why": {"en": "your card is temporarily blocked", "hi": "आपका कार्ड टेम्पररी ब्लॉक है",
                       "hinglish": "aapka card temporarily blocked hai"},
    "dc_pos_off_why": {"en": "shop payments are switched off on your card", "hi": "आपके कार्ड पर दुकान पर पेमेंट बंद है",
                       "hinglish": "aapke card par shop payments band hain"},
    "dc_cause": {"en": "Your debit card ending {l4} won't work at shops right now because {why}.",
                 "hi": "आपका {l4} वाला डेबिट कार्ड अभी दुकानों पर नहीं चलेगा क्योंकि {why}।",
                 "hinglish": "Aapka debit card ending {l4} abhi shops par nahi chalega kyunki {why}."},
    "dc_unknown_why": {"en": "of the card's settings", "hi": "कार्ड की सेटिंग्स के कारण", "hinglish": "card ki settings ki wajah se"},
    "dc_offer_unblock": {"en": "Would you like me to unblock your card ending {l4}?",
                         "hi": "क्या मैं आपका {l4} वाला कार्ड अनब्लॉक कर दूँ?",
                         "hinglish": "Kya main aapka card ending {l4} unblock kar doon?"},
    "dc_pos_also_off": {"en": "Shop payments are also switched off on it. We can switch those on next.",
                        "hi": "इस पर दुकान पर पेमेंट भी बंद है। उसके बाद हम उसे भी चालू कर सकते हैं।",
                        "hinglish": "Is par shop payments bhi band hain. Uske baad hum unhe bhi chalu kar sakte hain."},
    "dc_offer_pos": {"en": "Would you like me to switch shop payments on for your card ending {l4}?",
                     "hi": "क्या मैं आपके {l4} वाले कार्ड पर दुकान पर पेमेंट चालू कर दूँ?",
                     "hinglish": "Kya main aapke card ending {l4} par shop payments chalu kar doon?"},
    "dc_no_declines": {"en": "I don't see any declined payments on your debit card recently.", "hi": "हाल में आपके डेबिट कार्ड पर कोई डिक्लाइन हुआ पेमेंट नहीं दिख रहा।",
                       "hinglish": "Recently aapke debit card par koi declined payment nahi dikh raha."},
    "no_dc": {"en": "I don't see a debit card in your name.", "hi": "आपके नाम पर कोई डेबिट कार्ड नहीं दिख रहा।",
              "hinglish": "Aapke naam par koi debit card nahi dikh raha."},
    "offer_change": {"en": "Would you like me to change anything?", "hi": "क्या आप कुछ बदलवाना चाहेंगे?", "hinglish": "Kya aap kuch change karwana chahenge?"},
    # summary
    "sum_accounts": {"en": "You have {n} savings {word} with ₹{amt} in total.", "hi": "आपके {n} सेविंग्स अकाउंट हैं, कुल ₹{amt}।",
                     "hinglish": "Aapke {n} savings accounts hain, total ₹{amt}."},
    "sum_fd": {"en": "{n} active {word} worth ₹{amt}.", "hi": "{n} एक्टिव FD, कुल ₹{amt}।", "hinglish": "{n} active FD, total ₹{amt}."},
    "sum_loan": {"en": "{n} {word} with ₹{amt} outstanding.", "hi": "{n} लोन, ₹{amt} बकाया।", "hinglish": "{n} loan, ₹{amt} outstanding."},
    "sum_cc": {"en": "{n} credit {word} with ₹{amt} outstanding.", "hi": "{n} क्रेडिट कार्ड, ₹{amt} बकाया।",
               "hinglish": "{n} credit card, ₹{amt} outstanding."},
    "sum_total": {"en": "Your total deposits come to ₹{dep}{loans}.", "hi": "आपकी कुल जमा ₹{dep} है{loans}।",
                  "hinglish": "Aapki total deposits ₹{dep} hain{loans}."},
    "sum_loans_total": {"en": ", and total loans outstanding are ₹{amt}", "hi": ", और कुल लोन बकाया ₹{amt}", "hinglish": ", aur total loans outstanding ₹{amt}"},
    "highest": {"en": "Your highest balance is in {acct}, with ₹{amt}.", "hi": "सबसे ज़्यादा बैलेंस {acct} में है, ₹{amt}।",
                "hinglish": "Sabse zyada balance {acct} mein hai, ₹{amt}."},
    "highest_loan": {"en": "The loan with the most outstanding is your {type} loan, at ₹{amt}.",
                     "hi": "सबसे ज़्यादा बकाया आपके {type} लोन पर है, ₹{amt}।", "hinglish": "Sabse zyada outstanding aapke {type} loan par hai, ₹{amt}."},
    "upcoming": {"en": "In the next 7 days: {items}.", "hi": "अगले 7 दिनों में: {items}।", "hinglish": "Agle 7 din mein: {items}."},
    "upcoming_none": {"en": "Nothing is due in the next 7 days: no EMI, card bill or FD maturity.",
                      "hi": "अगले 7 दिनों में कुछ भी ड्यू नहीं है: न EMI, न कार्ड बिल, न FD मैच्योरिटी।",
                      "hinglish": "Agle 7 din mein kuch due nahi hai: na EMI, na card bill, na FD maturity."},
    "inactive": {"en": "Yes, {items} {verb} inactive.", "hi": "हाँ, {items} इनएक्टिव है।", "hinglish": "Haan, {items} inactive hai."},
    "inactive_none": {"en": "No, everything you hold is active.", "hi": "नहीं, आपके सभी अकाउंट और उत्पाद एक्टिव हैं।",
                      "hinglish": "Nahi, aapke sabhi accounts aur products active hain."},
    "cannot_see": {"en": "I'm not able to see that detail, but someone from our team can help with it.",
                   "hi": "यह जानकारी मुझे नहीं दिख रही, लेकिन हमारी टीम इसमें मदद कर सकती है।",
                   "hinglish": "Yeh detail mujhe nahi dikh rahi, lekin hamari team isme madad kar sakti hai."},
}

VERIFY_LOAN = {"home": ("home", "होम", "home"), "personal": ("personal", "पर्सनल", "personal"),
               "vehicle": ("vehicle", "व्हीकल", "vehicle"), "gold": ("gold", "गोल्ड", "gold"), "business": ("business", "बिज़नेस", "business")}
CHARGE_KB = {  # what to look up in the knowledge base to explain a charge found in the bank data
    "amb_shortfall": "How is the charge for not maintaining the average monthly balance worked out on the AU {type} savings account?",
    "debit_card_annual_fee": "What is the annual fee for the AU debit card on the {type} savings account?",
    "sms_alerts": "What are the SMS alert charges on AU savings accounts?",
    "emi_bounce": "What is the charge when a loan EMI bounces?",
}
CHARGE_NAMES = {"amb_shortfall": ("not keeping the minimum balance", "न्यूनतम बैलेंस न रखने", "minimum balance na rakhne"),
                "sms_alerts": ("SMS alerts", "SMS अलर्ट", "SMS alerts"),
                "debit_card_annual_fee": ("the debit card annual fee", "डेबिट कार्ड सालाना फीस", "debit card annual fee"),
                "emi_bounce": ("an EMI bounce", "EMI बाउंस", "EMI bounce")}
_L = {"en": 0, "hi": 1, "hinglish": 2}


def fill(table: dict, key: str, lang: str, **values) -> str:
    """One fixed sentence from a template table (personal.T, journeys.J, actions.W, conversation.TEMPLATES), in the
    caller's language (English when a language is missing), with the values filled in."""
    if "acct" in values and "Acct" not in values:  # "{Acct}" starts a sentence with the account
        values["Acct"] = _cap(values["acct"])
    text = table[key].get(lang, table[key]["en"])
    text = text.format(**values) if values else text
    return re.sub(r"आपके (\d{4}) वाला", r"आपके \1 वाले", text)  # "आपके 6712 वाले कार्ड पर" (oblique case)


def t(key: str, lang: str, **values) -> str:
    return fill(T, key, lang, **values)


def money(amount: float) -> str:
    """Indian digit grouping: 946213 -> '9,46,213'."""
    whole, paise = divmod(round(amount * 100), 100)
    s = str(whole)
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    out = (head + "," + tail) if head else tail
    return f"{out}.{paise:02d}" if paise else out


def day(d: date | None, lang: str) -> str:
    """A date as a person says it on the phone: "10 December" ("10 दिसंबर"). The year only when it isn't obvious
    (more than about six months away); the full month name, because "Dec" is read letter by letter."""
    if d is None:
        return "-"
    year = f" {d.year}" if abs((d - date.today()).days) > 180 else ""
    return f"{d.day} {_HI_MONTHS[d.month - 1]}{year}" if lang == "hi" else f"{d.day} {d.strftime('%B')}{year}"


_HI_DAYS = ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")
# Part of the day said with a 12-hour time; a 24-hour "21:40" is how a statement is printed, not how people talk.
_DAY_PARTS = {"en": ("at night", "in the morning", "in the afternoon", "in the evening", "at night"),
              "hi": ("रात", "सुबह", "दोपहर", "शाम", "रात"), "hinglish": ("raat", "subah", "dopahar", "shaam", "raat")}


def _clock(time: str, lang: str) -> str:
    """"21:40" -> "9:40 in the evening" / "शाम 9:40" / "shaam 9:40"; "" if there is no time."""
    try:
        h, m = (int(x) for x in time.split(":")[:2])
    except ValueError:
        return ""
    part = _DAY_PARTS.get(lang, _DAY_PARTS["en"])[0 if h < 4 else 1 if h < 12 else 2 if h < 16 else 3 if h < 21 else 4]
    hh = f"{h % 12 or 12}:{m:02d}"
    return f"{hh} {part}" if lang == "en" else f"{part} {hh}"


def _when(d: date, time: str, lang: str, today: date | None = None) -> str:
    """When something happened, as a caller would say it: "On Friday, 6 November, at 9:15 in the morning", or "Today,
    at 9:15 in the morning" / "Yesterday, ..." when `today` is given."""
    clock = _clock(time, lang) if time else ""
    rel = {0: ("Today", "आज", "Aaj"), 1: ("Yesterday", "कल", "Kal")}.get((today - d).days) if today else None
    if rel:
        word = rel[_L.get(lang, 0)]
        if lang == "hi":
            return word + (f" {clock} बजे" if clock else "") + ","
        if lang == "hinglish":
            return word + (f" {clock} baje" if clock else "") + ","
        return word + (f", at {clock}" if clock else "")
    if lang == "hi":
        return f"{_HI_DAYS[d.weekday()]}, {day(d, lang)} को" + (f" {clock} बजे" if clock else "") + ","
    if lang == "hinglish":
        return f"{d.strftime('%A')}, {day(d, lang)} ko" + (f" {clock} baje" if clock else "") + ","
    return f"On {d.strftime('%A')}, {day(d, lang)}" + (f", at {clock}" if clock else "")


def period_range(period: str, today: date) -> tuple[date, date] | None:
    return {"today": (today, today), "yesterday": (today - timedelta(days=1),) * 2,
            "last_7_days": (today - timedelta(days=6), today), "this_week": (today - timedelta(days=today.weekday()), today),
            "this_month": (today.replace(day=1), today), "last_30_days": (today - timedelta(days=29), today),
            "last_month": ((today.replace(day=1) - timedelta(days=1)).replace(day=1), today.replace(day=1) - timedelta(days=1)),
            }.get(period)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ───────── answering ─────────


def answer(customer: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    handler = {"balance": _balance, "transactions": _transactions, "account": _account, "charges": _charges, "fd": _fd,
               "loan": _loan, "credit_card": _credit_card, "debit_card": _debit_card, "summary": _summary}.get(q.topic)
    if handler is None:
        return PersonalAnswer([t("cannot_see", lang)], offer={"handoff": "general"})
    return handler(customer, q, lang, today)


_KNOWN_TYPES = ("bsbda", "digital", "vikaas", "value", "platinum", "royale", "kisan", "swadesh", "aarambh")
_CHOICE_WORDS = [
    ("all", r"\b(both|all|every|dono|donon|sab|sabhi)\b|दोनों|सभी|सब"),
    ("other", r"\b(the other|other one|other account|another one)\b|doosra wala|dusra wala|दूसरा वाला"),
    ("first", r"\b(first|1st|pehla|pehle|pahla)\b|पहला|पहले"),
    ("second", r"\b(second|2nd|dusra|doosra|dusre|doosre)\b|दूसरा|दूसरे"),
    ("third", r"\b(third|3rd|teesra|tisra|teesre)\b|तीसरा|तीसरे"),
    ("salary", r"\bsalary\b|सैलरी|वेतन"),
]


def account_choice(text: str) -> str:
    """The account a caller names when answering "which account?" ("second one", "dusra wala", "0002", "salary"),
    read in code - not left to the LLM. Empty if the text doesn't name one."""
    low = text.lower()
    if m := re.search(r"(?<!\d)(\d{4})(?!\d)", low):
        return m.group(1)
    for value, pattern in _CHOICE_WORDS:
        if re.search(pattern, low):
            return value
    return next((k for k in _KNOWN_TYPES if re.search(rf"\b{k}\b", low)), "")


# (en, hi, hinglish, feminine in Hindi) for the things a caller may have several of
_NOUNS = {"account": ("account", "अकाउंट", "account", False), "FD": ("FD", "FD", "FD", True),
          "loan": ("loan", "लोन", "loan", False), "credit card": ("credit card", "क्रेडिट कार्ड", "credit card", False),
          "debit card": ("debit card", "डेबिट कार्ड", "debit card", False)}
_ORD = {"en": ("first", "second", "third", "fourth", "fifth", "sixth"),
        "hi": (("पहला", "दूसरा", "तीसरा", "चौथा", "पाँचवाँ", "छठा"), ("पहली", "दूसरी", "तीसरी", "चौथी", "पाँचवीं", "छठी")),
        "hinglish": (("pehla", "doosra", "teesra", "chautha", "paanchva", "chhatha"), ("pehli", "doosri", "teesri", "chauthi", "paanchvi", "chhathi"))}


def plural(noun: str, lang: str) -> str:
    word = _NOUNS[noun][{"hi": 1, "hinglish": 2}.get(lang, 0)]
    return word if lang == "hi" or word.endswith("s") else word + "s"


def _listing(items, lang: str, label, noun: str) -> str:
    """The caller's accounts / cards / FDs as one spoken sentence, so they can pick one: "One is your Digital account
    ending 4512, which is inactive, and the other is your Value account ending 4521." An inactive one is said as such."""
    en, hi, hinglish, fem = _NOUNS[noun]
    word = {"hi": hi, "hinglish": hinglish}.get(lang, en)
    parts = []
    for x in items:
        name = " ".join(p for p in (label(x), word) if p)
        inactive = getattr(x, "status", "") == "inactive"
        if lang == "hi":
            parts.append(f"{'आपकी' if fem else 'आपका'} {name}, जिसके आख़िरी चार अंक {x.last4} हैं" + (", जो अभी इनएक्टिव है" if inactive else ""))
        elif lang == "hinglish":
            parts.append(f"{'aapki' if fem else 'aapka'} {name}, jiske last 4 digits {x.last4} hain" + (", jo abhi inactive hai" if inactive else ""))
        else:
            parts.append(f"your {name} ending {x.last4}" + (", which is inactive" if inactive else ""))
    if len(parts) == 1:
        return {"hi": f"आपके पास {parts[0]}", "hinglish": f"Aapke paas {parts[0]}"}.get(lang, f"There's {parts[0]}")
    if len(parts) == 2:
        return {"hi": f"एक है {parts[0]}, और {'दूसरी' if fem else 'दूसरा'} है {parts[1]}",
                "hinglish": f"Ek hai {parts[0]}, aur {'doosri' if fem else 'doosra'} hai {parts[1]}"}.get(
            lang, f"One is {parts[0]}, and the other is {parts[1]}")
    if lang == "en":
        ords = _ORD["en"]
        said = [f"the {ords[i]} is {p}" for i, p in enumerate(parts[:6])]
    else:
        ords = _ORD[lang][1 if fem else 0]
        said = [f"{ords[i]} {'है' if lang == 'hi' else 'hai'} {p}" for i, p in enumerate(parts[:6])]
    joined = ", ".join(said[:-1]) + {"hi": ", और ", "hinglish": ", aur "}.get(lang, ", and ") + said[-1]
    return _cap(joined)


def _accounts(customer: Customer, hint: str, lang: str, default_all: bool = False):
    """The account(s) the caller means, or a question back when it's unclear. `default_all`: no account named
    means every account (searches like "was my salary credited", "UPI spend this week")."""
    accounts = customer.accounts
    listing = _listing(accounts, lang, lambda a: a.type, "account")
    h = hint.strip().lower()
    recognised = (h in ("all", "both", "every", "first", "second", "third") or "salary" in h
                  or any(ch.isdigit() for ch in h) or any(k in h for k in _KNOWN_TYPES))
    if len(accounts) == 1 or h in ("all", "both", "every") or (default_all and not recognised):
        return accounts, None
    if not recognised:
        return None, PersonalAnswer([t("which", lang, n=len(accounts), list=listing)], "clarify")
    digits = "".join(ch for ch in h if ch.isdigit())
    if digits:
        picked = [a for a in accounts if a.number.endswith(digits)]
    elif "salary" in h:
        picked = [a for a in accounts if a.is_salary]
        if not picked:
            return None, PersonalAnswer([t("no_salary", lang), t("which", lang, n=len(accounts), list=listing)], "clarify")
    elif h in ("first", "second", "third"):
        i = ("first", "second", "third").index(h)
        picked = accounts[i:i + 1]
    else:
        picked = [a for a in accounts if a.type.lower() in h or h in a.type.lower()]
    if not picked:
        return None, PersonalAnswer([t("no_match", lang, list=listing)], "clarify")
    if len(picked) > 1 and h not in ("all", "both"):
        return None, PersonalAnswer([t("which", lang, n=len(picked), list=_listing(picked, lang, lambda a: a.type, "account"))], "clarify")
    return picked, None


def _pick(items: list, hint: str, lang: str, what: tuple, label, keys) -> tuple[list | None, PersonalAnswer | None]:
    """Pick FD(s) / loan(s) / card(s) by last digits or type. One item, or "all", needs no question."""
    h = hint.strip().lower()
    if not h or h in ("all", "both", "every"):
        return items, None
    digits = "".join(ch for ch in h if ch.isdigit())
    picked = [x for x in items if digits and x.last4.endswith(digits[-4:])] if digits else \
        [x for x in items if any(h in k.lower() or k.lower() in h for k in keys(x))]
    if not picked:
        noun = {"FDs": "FD", "loans": "loan", "credit cards": "credit card"}.get(what[0], "account")
        return None, PersonalAnswer([t("no_match", lang, list=_listing(items, lang, label, noun))], "clarify")
    return picked, None


def _status(s: str, lang: str) -> str:
    return T["status_words"].get(lang, T["status_words"]["en"]).get(s, s)


def _acct(a: Account, lang: str) -> str:
    return t("acct", lang, type=a.type, l4=a.last4)


def _balance(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    accounts, ask = _accounts(c, q.account, lang, default_all=True)  # "my balance": every account, not "which one?"
    if ask:
        return ask
    if q.detail == "available_only":  # "how much can I use right now?"
        lines = []
        for a in accounts:
            avail = money(a.available if a.available is not None else a.balance)
            if a.status == "inactive":  # a balance is not permission to pay (PM review, 17 Nov)
                lines.append(_cap(t("avail_inactive", lang, avail=avail, acct=_acct(a, lang))))
            elif a.hold_amount:  # ledger, hold and available said apart, so "excluding the hold" is answered
                lines.append(_cap(t("avail_after_hold", lang, acct=_acct(a, lang), bal=money(a.balance), hold=money(a.hold_amount),
                                    reason=hold_reason(a.hold_reason, lang), avail=avail)))
            else:
                lines.append(_cap(t("avail_line", lang, avail=avail, acct=_acct(a, lang))))
        return PersonalAnswer(lines, fields=[f"account:{a.last4}:balance" for a in accounts])
    lines = []
    for a in accounts:
        lines.append(_cap(t("balance", lang, acct=_acct(a, lang), amt=money(a.balance))))
        if a.hold_amount:  # a hold is mentioned only when there is one ("nothing on hold" is noise on a phone call)
            lines.append(t("balance_hold", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang), avail=money(a.available or 0)))
    return PersonalAnswer(lines, fields=[f"account:{a.last4}:balance" for a in accounts])


def _account(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    """Which is my salary account, which debit card is linked, which variant I have, is it active, its rules."""
    if q.detail == "salary_account" and not q.account:
        salary = [a for a in c.accounts if a.is_salary]
        if not salary:
            return PersonalAnswer([t("no_salary", lang)], fields=["accounts:salary_flag"])
        lines = []
        for a in salary:
            employer = next((x.counterparty for x in a.transactions if x.description.lower().startswith("salary") and x.counterparty), "")
            lines.append(t("salary_is", lang, acct=_acct(a, lang), employer=f" from {employer}" if employer and lang != "hi" else
                           (f" ({employer})" if employer else "")))
        return PersonalAnswer(lines, fields=["accounts:salary_flag"])
    if q.detail == "minimum_balance" or q.detail == "balance_maintained":
        return _charges(c, q, lang, today)
    accounts, ask = _accounts(c, q.account, lang)
    if ask:
        return ask
    lines, fields = [], []
    for a in accounts:
        if q.detail == "linked_debit_card":
            card = a.debit_card
            lines.append(t("linked_dc", lang, Acct=_cap(_acct(a, lang)), acct=_acct(a, lang), variant=card.variant, l4=card.last4,
                           status=_status(card.status, lang)) if card else t("no_dc_on", lang, acct=_acct(a, lang)))
            fields.append(f"account:{a.last4}:debit_card")
        elif q.detail == "benefits":
            lines.append(t("variant", lang, type=a.type, l4=a.last4))
            fields.append(f"account:{a.last4}:variant")
            return PersonalAnswer(lines, fields=fields, needs_kb=f"What are the features and benefits of the AU {a.type} Savings Account?")
        elif q.detail == "status":
            lines.append(_cap(t("acct_status", lang, Acct=_cap(_acct(a, lang)), acct=_acct(a, lang), status=_status(a.status, lang))))
            if a.status != "active":
                lines.append(t("inactive_note", lang))
            fields.append(f"account:{a.last4}:status")
        else:
            lines.append(t("variant", lang, type=a.type, l4=a.last4))
            fields.append(f"account:{a.last4}:variant")
    return PersonalAnswer(lines, fields=fields)


def _charges(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    """Minimum balance needed, whether it's met this month, and charges applied (with the reason)."""
    accounts, ask = _accounts(c, q.account, lang)
    if ask:
        return ask
    lines, fields = [], []
    branch_note = {"urban": (" at an urban branch", " (शहरी ब्रांच)", " (urban branch)"),
                   "semi_urban": (" at a semi-urban branch", " (अर्ध-शहरी ब्रांच)", " (semi-urban branch)"),
                   "rural": (" at a rural branch", " (ग्रामीण ब्रांच)", " (rural branch)")}
    for a in accounts:
        fields.append(f"account:{a.last4}:balance_rules")
        if q.detail in ("minimum_balance", "balance_maintained", ""):
            if not a.amb_required:
                lines.append(t("amb_none", lang, acct=_acct(a, lang)))
            else:
                note = branch_note.get(a.branch_type, ("", "", ""))[_L.get(lang, 0)] if a.type == "Value" else ""
                lines.append(t("amb_rule", lang, Acct=_cap(_acct(a, lang)), acct=_acct(a, lang), req=money(a.amb_required), branch=note))
                if a.amb_this_month is not None and q.detail != "charges":
                    gap = a.amb_required - a.amb_this_month
                    lines.append(t("amb_now_short", lang, mtd=money(a.amb_this_month), gap=money(gap)) if gap > 0
                                 else t("amb_now_ok", lang, mtd=money(a.amb_this_month)))
        if q.detail in ("charges", "balance_maintained") or (q.detail == "" and a.charges):
            recent = [x for x in a.charges if (today - x.date).days <= 90]
            if not recent and q.detail == "charges":
                lines.append(t("no_charges", lang, acct=_acct(a, lang)))
            for ch in recent:
                name = CHARGE_NAMES.get(ch.type, (ch.type,) * 3)[_L.get(lang, 0)]
                lines.append(t("charge_line", lang, amt=money(ch.amount), date=day(ch.date, lang), what=name,
                               rev=" (since reversed)" if ch.reversed and lang != "hi" else (" (वापस हो चुका)" if ch.reversed else "")))
                if ch.type == "amb_shortfall" and a.amb_last_month is not None:
                    lines.append(t("amb_charge_why", lang, last=money(a.amb_last_month), req=money(a.amb_required)))
            if recent:
                kb = CHARGE_KB.get(recent[0].type, "").format(type=a.type) if q.detail == "charges" else ""
                if kb:  # bank data says what was charged and why; the approved charges document says how it is worked out
                    return PersonalAnswer(lines, fields=fields, offer={"ticket": "charge_review"}, needs_kb=kb, kb_optional=True,
                                          after=[t("charge_dispute", lang)])
                lines.append(t("charge_dispute", lang))
                return PersonalAnswer(lines, fields=fields, offer={"ticket": "charge_review"})
    return PersonalAnswer(lines, fields=fields)


def note_text(x: Transaction, lang: str) -> str:
    """The bank's note on a transaction, in the caller's language ('' if none)."""
    if not x.note:
        return ""
    try:
        note = json.loads(x.note)
    except ValueError:
        return ""
    key = f"note_{note.get('key')}"
    if key not in T:
        return ""
    values = {k: (money(v) if isinstance(v, (int, float)) else v) for k, v in note.items() if k != "key"}
    if "due" in values:
        values["due"] = day(date.fromisoformat(values["due"]), lang)
    return _cap(t(key, lang, amt=money(x.amount), **values))


def _matches(x: Transaction, q: PersonalQuery, keyword: str) -> bool:
    if q.amount and not (abs(x.amount - q.amount) <= max(q.amount * 0.1, 1)):
        return False
    if keyword:
        hay = f"{x.description} {x.counterparty} {x.channel}".lower()
        words = {"restaurant": ("restaurant", "barbeque", "dhaba", "cafe", "zomato", "swiggy"), "atm": ("atm",),
                 "salary": ("salary",), "refund": ("refund",), "upi": ("upi",), "emi": ("emi", "nach")}.get(keyword, (keyword,))
        return any(w in hay for w in words)
    return True


def _txn_line(x: Transaction, lang: str, acct4: str = "", today: date | None = None) -> str:
    """One transaction in words. The ending of the status table decides the verb: "paid" only after success."""
    tails = T["txn_tail"].get(lang, T["txn_tail"]["en"])
    desc = " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in x.description.split())  # "UPI transfer"
    desc = re.sub(r"\bPOS\b", "card", desc)  # "POS purchase" is statement jargon: a caller says "card purchase"
    returned = x.status == "failed" and bool(re.search(r"\breturned\b", desc))
    desc = re.sub(r"\s+(returned|failed|reversed)$", "", desc)  # "EMI auto-debit returned": the status is said once, below
    status = "returned" if returned else x.status
    tail = tails.get(status, tails["unknown"])[x.kind if x.kind in ("Debit", "Credit") else "Debit"]
    debit = x.kind != "Credit"
    at = x.channel in ("POS", "ECOM", "ATM")
    when, amt, who = _when(x.date, x.time, lang, today), money(x.amount), x.counterparty
    if lang == "hi":
        acct = f"आपके {acct4} वाले अकाउंट {'से' if debit else 'में'} " if acct4 else ""
        party = f"{who} {'पर' if at else 'को' if debit else 'से'} " if who else ""
        return f"{when} {acct}{party}₹{amt} का {desc} {'हुआ' if debit else 'आया'}{tail}।"
    if lang == "hinglish":
        acct = f"aapke account ending {acct4} {'se' if debit else 'mein'} " if acct4 else ""
        party = f"{who} {'par' if at else 'ko' if debit else 'se'} " if who else ""
        return f"{when} {acct}{party}₹{amt} ka {desc} {'hua' if debit else 'aaya'}{tail}."
    party = f" {'at' if at else 'to' if debit else 'from'} {who}" if who else ""
    acct = f" {'from' if debit else 'into'} your account ending {acct4}" if acct4 else ""
    first = desc.split()[0] if desc else ""
    vowel = first[:1].upper() in ("AEFHILMNORSX" if first.isupper() and len(first) > 1 else "AEIO")
    return f"{when}, there was {'an' if vowel else 'a'} {desc} of ₹{amt}{party}{acct}{tail}."


# ───────── searches and totals said as answers (PM review of call d48a5afe, 17 Nov) ─────────
# "Did Rajesh send money today?" is answered "No, I don't see any money from Rajesh today", not the generic "no such
# transaction on account (ending 9001)"; "how much did I spend through UPI this week?" gets the total with what it covers.
YES = {"en": "Yes.", "hi": "जी हाँ।", "hinglish": "Ji haan."}
_WHEN = {"today": ("today", "आज", "aaj"), "yesterday": ("yesterday", "कल", "kal"), "this_week": ("this week", "इस हफ़्ते", "is hafte"),
         "last_7_days": ("in the last 7 days", "पिछले 7 दिनों में", "pichhle 7 din mein"),
         "this_month": ("this month", "इस महीने", "is mahine"),
         "last_30_days": ("in the last 30 days", "पिछले 30 दिनों में", "pichhle 30 din mein"),
         "last_month": ("last month", "पिछले महीने", "pichhle mahine")}
_CHANNELS = {"upi": "UPI", "neft": "NEFT", "imps": "IMPS", "rtgs": "RTGS", "atm": "ATM", "nach": "NACH"}
# keywords that are a status or a kind of money, never a person ("No, I don't see any payment to Deducted" - 17 Nov)
_NOT_NAMES = {"deducted", "debited", "credited", "cut", "payment", "payments", "money", "transfer", "transaction", "transactions",
              "charged", "charge", "refund", "paid", "received", "sent", "spent", "cash", "bill", "hold", "pending", "failed"}
_TOPIC_WORDS = {"restaurant", "salary", "refund", "emi", "card", "shopping", "food", "fuel"} | set(_CHANNELS)


def _when_said(q: PersonalQuery, lang: str) -> str:
    return _WHEN[q.period][_L.get(lang, 0)] if q.period in _WHEN else ""


def _person(q: PersonalQuery) -> str:
    """The name the caller searched for ("Rajesh"), when the keyword is a person rather than a channel or a topic."""
    k = q.keyword.strip()
    if not k or k.lower() in _TOPIC_WORDS | _NOT_NAMES or any(ch.isdigit() for ch in k):
        return ""
    return k if k[:1].isupper() else k.title()


def _is_check(q: PersonalQuery) -> bool:
    """A yes/no check of money coming in ("was my salary credited this month?", "did Rajesh pay me today?")."""
    return not q.count and not q.total and (q.keyword.strip().lower() == "salary" or (q.direction == "credit" and bool(q.keyword)))


def _spent(q: PersonalQuery, lang: str, amt: str, k: int, l4: str) -> str:
    ch, when = _CHANNELS.get(q.keyword.strip().lower(), ""), _when_said(q, lang)
    if lang == "hi":
        text = f"{when} आपने{' ' + ch + ' से' if ch else ''} कुल ₹{amt} खर्च किए, {k} पेमेंट्स में" + (f", अकाउंट {l4} से" if l4 else "")
        return text.strip() + "।"
    if lang == "hinglish":
        text = f"{when} aapne{' ' + ch + ' se' if ch else ''} total ₹{amt} kharch kiye, {k} payments mein" + (f", account {l4} se" if l4 else "")
        return _cap(text.strip()) + "."
    return (f"You spent ₹{amt}{' through ' + ch if ch else ''}{' ' + when if when else ''}, across {k} payment"
            f"{'' if k == 1 else 's'}" + (f" from your account ending {l4}" if l4 else "") + ".")


def _nothing_found(q: PersonalQuery, lang: str, accounts: list, filtered: bool) -> str:
    """A search that found nothing says what was searched. (A bank system that could not be reached is a different
    reply: that raises BankError and the caller hears that the information can't be fetched right now.)"""
    if not filtered:  # no filter to match: "no transactions", not "no matching transaction"
        return t("txn_empty", lang, l4=" / ".join(a.last4 for a in accounts))
    i, name, when = _L.get(lang, 0), _person(q), _when_said(q, lang)
    ch, credit = _CHANNELS.get(q.keyword.strip().lower(), ""), q.direction == "credit"
    if name:
        what = ((f"any money from {name}", f"{name} से आया कोई पैसा", f"{name} se aaya koi paisa") if credit or q.direction == "any"
                else (f"any payment to {name}", f"{name} को गया कोई पेमेंट", f"{name} ko gaya koi payment"))[i]
    elif q.keyword.strip().lower() == "salary":
        what = ("a salary credit", "सैलरी क्रेडिट", "salary credit")[i]
    elif ch:
        what = ((f"any money received through {ch}", f"{ch} से आया कोई पैसा", f"{ch} se aaya koi paisa") if credit
                else (f"any {ch} payments", f"{ch} से कोई पेमेंट", f"{ch} se koi payment"))[i]
    else:
        what = ("a matching transaction", "ऐसा कोई ट्रांज़ैक्शन", "aisa koi transaction")[i]
    every = len(accounts) > 1  # searched every account: say so, so it doesn't sound like one was skipped
    named = accounts[0].last4 if not every and any(ch.isdigit() for ch in q.account) else ""  # "...credited to 4512?"
    if lang == "hi":
        where = 'आपके किसी भी अकाउंट में ' if every else f'आपके अकाउंट {named} में ' if named else ''
        return f"नहीं, {when + ' ' if when else ''}{where}{what} नहीं दिख रहा।"
    if lang == "hinglish":
        where = 'aapke kisi bhi account mein ' if every else f'aapke account ending {named} mein ' if named else ''
        return f"Nahi, {when + ' ' if when else ''}{where}{what} nahi dikh raha."
    where = ' on any of your accounts' if every else f' on your account ending {named}' if named else ''
    return f"No, I don't see {what}{where}{' ' + when if when else ''}."


def _numbered(lines: list[str], lang: str, last: bool = True) -> list[str]:
    """Several transactions read out one after another: "First, ... Second, ... Finally, ...", so the caller hears
    where each one starts and how many are left (PM review of call 9d31b6fd, 17 Nov). `last`: the list ends here."""
    if len(lines) < 2:
        return [_cap(x) for x in lines]
    words = _ORDINALS.get(lang, _ORDINALS["en"])
    out = []
    for i, line in enumerate(lines):
        word = words[-1] if last and i == len(lines) - 1 else words[i] if i < len(words) - 1 else ""
        out.append(f"{word}, {line[:1].lower() + line[1:] if lang != 'hi' else line}" if word else _cap(line))
    return out


_ORDINALS = {"en": ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Finally"),
             "hi": ("पहला", "दूसरा", "तीसरा", "चौथा", "पाँचवाँ", "छठा", "सातवाँ", "आठवाँ", "नौवाँ", "और आख़िरी"),
             "hinglish": ("Pehla", "Doosra", "Teesra", "Chautha", "Paanchva", "Chhatha", "Saatva", "Aathva", "Nauva", "Aur aakhri")}


def _each_account(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    k, sentences, fields = q.count or 5, [], []
    per = [(a, sorted((x for x in a.transactions if x.date <= today), key=lambda x: (x.date, x.time), reverse=True)[:k])
           for a in c.accounts]
    if len(per) > 1:  # say what is coming before the details: how many accounts, and where it starts
        sentences.append(t("txn_each_overview", lang, n=len(per), l4=per[0][0].last4))
    for a, items in per:
        fields.append(f"account:{a.last4}:transactions")
        if not items:
            sentences.append(t("txn_empty", lang, l4=a.last4))
            continue
        key = "txn_each_intro" if len(items) == k else "txn_each_only"
        sentences.append(t(key, lang, l4=a.last4, k=len(items), s="" if len(items) == 1 else "s"))
        sentences += _numbered([_txn_line(x, lang, today=today) for x in items], lang)
    return PersonalAnswer(sentences, fields=fields)


def _transactions(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if q.detail == "duplicate":
        return _duplicates(c, lang, today)
    if q.detail == "hold_check":  # "has that 2,500 been deducted?": a hold of that amount is said as a hold
        holds = [a for a in c.accounts if a.hold_amount]
        if not q.amount and len(holds) == 1:  # "what is this payment for?" right after the balance
            a = holds[0]
            return PersonalAnswer([t("hold_what", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang),
                                     acct=_acct(a, lang), avail=money(a.available or 0))], fields=[f"account:{a.last4}:balance"])
        held = next((a for a in holds if q.amount and abs(a.hold_amount - q.amount) <= 1), None)
        if held:
            return PersonalAnswer([t("hold_not_debit", lang, hold=money(held.hold_amount), reason=hold_reason(held.hold_reason, lang),
                                     avail=money(held.available or 0))], fields=[f"account:{held.last4}:balance"])
    if q.detail == "hold_why":  # "why is 2,500 rupees blocked?": the hold of that amount, or the only one
        holds = [a for a in c.accounts if a.hold_amount]
        match = [a for a in holds if q.amount and abs(a.hold_amount - q.amount) <= 1] or (holds if len(holds) == 1 else [])
        if len(match) == 1:
            a = match[0]
            return PersonalAnswer([t("hold_what", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang),
                                     acct=_acct(a, lang), avail=money(a.available or 0))], fields=[f"account:{a.last4}:balance"])
        if not holds:
            return PersonalAnswer([t("no_hold_any", lang)], fields=[f"account:{a.last4}:balance" for a in c.accounts])
    if q.detail == "total_scope":
        return PersonalAnswer([t("total_scope", lang)])
    if q.account == "each":  # "five transactions in each account": per account, each newest first
        return _each_account(c, q, lang, today)
    if q.detail == "pending_check":  # "did the pending payment get reversed?": its status now, from the record
        return _pending_check(c, lang, today, q.item == "hold")
    filtered = bool(q.period or q.direction != "any" or q.min_amount or q.amount or q.keyword or q.total or q.detail == "failed_only")
    accounts, ask = _accounts(c, q.account, lang, default_all=filtered)
    if ask:
        return ask
    fields, per_account = [], []
    span = period_range(q.period, today)
    keyword = q.keyword.strip().lower()
    for a in accounts:
        items = [x for x in a.transactions if x.date <= today]
        if span:
            items = [x for x in items if span[0] <= x.date <= span[1]]
        if q.direction in ("debit", "credit"):
            items = [x for x in items if x.kind.lower() == q.direction]
        if q.min_amount:
            items = [x for x in items if x.amount >= q.min_amount]
        items = [x for x in items if _matches(x, q, keyword)]
        if q.detail == "failed_only":  # "show failed transactions": attempts that did not go through
            items = [x for x in items if x.status == "failed"]
        if q.total:
            items = [x for x in items if x.kind == "Debit" and x.status == "success"]
        fields.append(f"account:{a.last4}:transactions")
        per_account.append((a, items))
    if q.total:  # "how much did I spend through UPI this week?": the total, said with what was added up
        sentences = [_spent(q, lang, money(sum(x.amount for x in items)), len(items), a.last4 if len(accounts) > 1 else "")
                     for a, items in per_account if items]
        return PersonalAnswer(sentences or [_nothing_found(q, lang, accounts, filtered)], fields=fields)
    merged = sorted(((a, x) for a, items in per_account for x in items), key=lambda p: (p[1].date, p[1].time), reverse=True)
    if not merged:
        return PersonalAnswer([_nothing_found(q, lang, accounts, filtered)], fields=fields)
    several = len(accounts) > 1  # name each line's account when more than one is in play
    shown = merged[: max(1, min(q.count or (3 if filtered else 5), 10))]
    lines = [_txn_line(x, lang, a.last4 if several else "", today) for a, x in shown]
    yes = f"{YES.get(lang, YES['en'])} " if _is_check(q) else ""  # "did my salary come?": the answer starts with yes
    notes = {id(x): note_text(x, lang) for _, x in shown if x.note and (filtered or len(shown) == 1)}
    short = bool(q.count) and len(shown) < q.count and not filtered  # asked for 5, only 1 exists
    inactive = [t("acct_inactive_note", lang)] if short and not several and accounts[0].status == "inactive" else []
    if len(shown) == 1:  # "here are the last 1" reads badly: just say it
        note = [notes[id(shown[0][1])]] if notes.get(id(shown[0][1])) else []
        if short and not several:
            return PersonalAnswer([t("txn_only_one", lang, l4=accounts[0].last4), lines[0]] + note + inactive, fields=fields)
        return PersonalAnswer([yes + _cap(lines[0])] + note, fields=fields)
    key = ("txn_merged_found" if filtered else "txn_merged_intro") if several else ("txn_found" if filtered else "txn_intro")
    if short:
        key = "txn_merged_only" if several else "txn_only"
    if key == "txn_intro" and q.account == "salary":  # the caller said "salary account": say it back
        key = "txn_intro_salary"
    sentences = [yes + t(key, lang, k=len(shown), l4=accounts[0].last4)]
    # Asked for a number ("my last five"): all of them, up to five at once (PM review, 17 Nov: "if the user asks for five,
    # provide five"). Not asked: three, then "Want the other 2?" - a long list is hard to follow on a call.
    chunk = 5 if q.count else 3
    out, rest = [], []
    said = _numbered(lines[:chunk], lang, last=len(lines) <= chunk) if len(shown) > 1 else lines[:chunk]
    for i, ((_, x), line) in enumerate(zip(shown, lines, strict=True)):
        if i < chunk:
            out.append(said[i])
            if notes.get(id(x)):
                out.append(notes[id(x)])
        else:
            rest.append(line)
    sentences += out
    if rest:  # the rest on request ("go on")
        sentences.append(t("more_offer", lang, k=len(rest)))
        return PersonalAnswer(sentences, fields=fields, offer={"more": rest})
    return PersonalAnswer(sentences + inactive, fields=fields)


def _pending_check(c: Customer, lang: str, today: date, hold_only: bool = False) -> PersonalAnswer:
    """What happened to a pending payment, from the bank's record: a hold still on the account, a transaction still
    processing or reversed. `hold_only`: the call was just about a hold, so "the pending payment" is that hold (PM review,
    17 Nov). A reversal is said only when the record says so, never guessed from a hold."""
    held = [a for a in c.accounts if a.hold_amount]
    lines = [t("hold_pending", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang), acct=_acct(a, lang)) for a in held]
    if hold_only and lines:
        return PersonalAnswer(lines, fields=[f"account:{a.last4}:balance" for a in held])
    open_ = [(a, x) for a in c.accounts for x in a.transactions if x.date <= today and x.status in ("pending", "reversed")]
    if not open_ and not lines:
        return PersonalAnswer([t("pending_none", lang)], fields=[f"account:{a.last4}:transactions" for a in c.accounts])
    for a, x in sorted(open_, key=lambda p: (p[1].date, p[1].time), reverse=True)[:3]:
        who = x.counterparty or x.description
        key = "pending_still" if x.status == "pending" else "pending_reversed"
        lines.append(t(key, lang, amt=money(x.amount), who=who, date=day(x.date, lang)))
    return PersonalAnswer(lines, fields=sorted({f"account:{a.last4}:transactions" for a, _ in open_} | {f"account:{a.last4}:balance" for a in held}))


def _duplicates(c: Customer, lang: str, today: date) -> PersonalAnswer:
    for a in c.accounts:
        debits = [x for x in a.transactions if x.kind == "Debit" and x.status == "success" and (today - x.date).days <= 30]
        for i, x in enumerate(debits):
            for y in debits[i + 1:]:
                if x.amount == y.amount and x.counterparty == y.counterparty and x.date == y.date and x.counterparty:
                    return PersonalAnswer([t("dup_found", lang, amt=money(x.amount), who=x.counterparty, date=day(x.date, lang)),
                                           t("dispute_offer", lang)], fields=[f"account:{a.last4}:transactions"],
                                          offer={"ticket": "double_charge"})
    return PersonalAnswer([t("dup_none", lang)], fields=["accounts:transactions"])


def _fd(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    active = [f for f in c.fds if f.status == "active"]
    if not c.fds:
        return PersonalAnswer([t("no_fd", lang)])
    future, past = T["fd_verb"].get(lang, T["fd_verb"]["en"])
    pool = active or c.fds
    if q.detail == "first_maturing":
        f = min(pool, key=lambda x: x.maturity_date)
        return PersonalAnswer([t("fd_first", lang, l4=f.last4, date=day(f.maturity_date, lang)),
                               t("fd_amount_then", lang, mat=money(f.maturity_amount))],
                              fields=[f"fd:{f.last4}:maturity"])
    picked, ask = _pick(pool, q.item, lang, ("FDs",), lambda f: "", lambda f: (f.number,))
    if ask:
        return ask
    if q.detail in ("maturity_amount", "rate", "auto_renew", "premature_value") and len(picked) > 1:
        soonest = min(picked, key=lambda x: x.maturity_date)
        if (soonest.maturity_date - today).days <= 45:  # "my FD maturing next month" -> the one maturing soon
            picked = [soonest]
        else:
            return PersonalAnswer([t("which_item", lang, n=len(picked), what=plural("FD", lang), list=_listing(picked, lang, lambda f: "", "FD"))], "clarify")
    lines, fields = [], []
    if len(picked) > 1:
        lines.append(t("fd_count", lang, n=len(picked)))
    for f in picked:
        fields.append(f"fd:{f.last4}")
        if q.detail == "rate":
            lines.append(t("fd_rate", lang, l4=f.last4, rate=f"{f.rate:g}"))
        elif q.detail == "auto_renew":
            lines.append(t("fd_renew" if f.auto_renew else "fd_no_renew", lang, l4=f.last4))
        elif q.detail == "maturity_amount":
            lines.append(t("fd_amount", lang, mat=money(f.maturity_amount), l4=f.last4, date=day(f.maturity_date, lang)))
            if f.payout == "quarterly":
                lines.append(t("fd_payout", lang, p=money(f.principal)))
        elif q.detail == "premature_value":
            days = (today - f.opened_on).days
            value = f.principal * (1 + max(f.rate - 1, 0) / 100 * days / 365) if f.payout == "cumulative" else f.principal
            lines += [t("fd_break", lang, l4=f.last4, amt=money(round(value, -1))), t("fd_break_how", lang)]
        else:
            matured = f.maturity_date < today
            lines.append(t("fd", lang, l4=f.last4, p=money(f.principal), rate=f"{f.rate:g}", verb=past if matured else future,
                           date=day(f.maturity_date, lang), mat=money(f.maturity_amount)))
    return PersonalAnswer(lines, fields=fields)


def foreclosure_pct(loan: Loan) -> float | None:
    """Foreclosure charge (% of the outstanding, before GST) from published rules, or None when there isn't one.
    Floating-rate loans to individuals: nil (RBI). Personal loans (Assets KB): not allowed before 6 months,
    5% between 6 and 12 months, 3% after 12 months."""
    if loan.rate_type == "floating" and loan.type != "business":
        return 0
    if loan.type == "personal":
        return None if loan.emis_paid < 6 else 5 if loan.emis_paid <= 12 else 3
    return None


def _loan_name(loan: Loan, lang: str) -> str:
    return VERIFY_LOAN.get(loan.type, (loan.type,) * 3)[_L.get(lang, 0)]


def _loan_ref(c: Customer, loan: Loan, lang: str) -> dict:
    """{ending} / {pre} for the EMI lines: the loan's last four digits, always (as banks say "your loan ending 0107"),
    so the caller always knows which loan an EMI belongs to (owner, 17 Nov)."""
    return {"ending": f" ending {loan.last4}" if lang != "hi" else "", "pre": f"{loan.last4} वाले " if lang == "hi" else ""}


def _join(names: list[str], lang: str) -> str:
    """["home loan ending 0101", "personal loan ending 0102"] -> "... and ..." ("होम लोन 0101 और पर्सनल लोन 0102")."""
    items = names  # each already says "<type> loan" (and its digits when there are several)
    word = {"hi": " और ", "hinglish": " aur "}.get(lang, " and ")
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + word + items[-1]


_ALL_LOANS = re.compile(r"\b(all|both|every|across|combined|together)\b.{0,25}\bloans?\b|\bloans?\b.{0,15}\b(all|both|combined|together)\b|"
                        r"\b(sab|sabhi|saare|sare|dono)\b.{0,10}\bloans?\b|सभी लोन|सारे लोन|दोनों लोन|सब लोन", re.I)


def loan_in_focus(q: PersonalQuery, raw: str, focus: str) -> PersonalQuery:
    """Which loan a loan question is about. "Across all my loans" is every loan; a question that names none keeps the
    loan the call is already about ("how much have I paid so far?" after the home loan EMI). PM review, 17 Nov: it
    answered for both loans. A count of loans is always about all of them."""
    if q.detail in ("loan_count", "loan_count_left"):
        return q
    if _ALL_LOANS.search(raw):
        return replace(q, item="all")
    if not q.item.strip() and focus:
        return replace(q, item=focus)
    return q


def _loan(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if not c.loans:
        return PersonalAnswer([t("no_loan", lang)])
    if q.detail in ("loan_count", "loan_count_left"):  # "how many loans do I have?": all of them, not the one in focus
        several = len(c.loans) > 1
        names = [f"{_loan_name(x, lang)} {'लोन' if lang == 'hi' else 'loan'}"
                 + ((f" {x.last4}" if lang == "hi" else f" ending {x.last4}") if several else "") for x in c.loans]
        lines = [t("loan_count_one", lang, type=_loan_name(c.loans[0], lang), l4=c.loans[0].last4) if len(names) == 1 else
                 t("loan_count", lang, n=len(names), list=_join(names, lang))]
        if q.detail == "loan_count_left" and len(c.loans) == 1:  # "kitne loan baaki hain": may mean the EMIs left too
            lines.append(t("emi_count", lang, paid=c.loans[0].emis_paid, left=c.loans[0].emis_left))
        return PersonalAnswer(lines, fields=[f"loan:{x.last4}" for x in c.loans])
    if q.detail in ("emis_count", "repaid_amount") and not q.item.strip() and len(c.loans) > 1:
        # "how many EMIs have I paid?" with nothing in the call saying which loan: asked, never summed across loans
        return PersonalAnswer([t("which_item", lang, n=len(c.loans), what=plural("loan", lang),
                                 list=_listing(c.loans, lang, lambda x: _loan_name(x, lang), "loan"))], "clarify")
    picked, ask = _pick(c.loans, q.item, lang, ("loans",), lambda x: x.type, lambda x: (x.type, x.number))
    if ask:
        return ask
    lines, fields = [], []
    for loan in picked:
        name, ref = _loan_name(loan, lang), _loan_ref(c, loan, lang)
        fields.append(f"loan:{loan.last4}")
        last = loan.emis[0] if loan.emis else None
        if q.detail == "emi_last_paid":  # "when did money last leave my account for my EMI?": the last EMI actually taken
            paid = next((e for e in loan.emis if e.status == "paid"), None)
            if paid and paid.paid_on:
                lines.append(t("emi_last_debit", lang, type=name, **ref, emi=money(paid.amount), date=day(paid.paid_on, lang)))
            elif paid:  # the bank gave no debit date: the due date is said as a due date
                lines.append(t("emi_paid_due_only", lang, type=name, **ref, emi=money(paid.amount), date=day(paid.due_date, lang)))
            else:
                lines.append(t("emi_no_history", lang, type=name))
            if last and last.status == "bounced":  # a returned EMI is not money that left the account
                lines.append(t("emi_last_failed", lang, emi=money(last.amount), date=day(last.due_date, lang)))
            continue
        if q.detail == "emi_recovery" and last:  # "my EMI bounced, has it been paid now?": yes or no first
            if last.status == "bounced":
                reason = t(f"reason_{last.reason}", lang) if f"reason_{last.reason}" in T else (last.reason or "")
                lines.append(t("emi_not_recovered", lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang), reason=reason))
                if last.charge:
                    lines.append(t("emi_bounce_charge", lang, amt=money(last.charge)))
            else:
                lines.append(YES.get(lang, YES["en"]) + " " + t("emi_paid_of", lang, type=name, **ref, emi=money(last.amount),
                                                                  date=day(last.due_date, lang)))
            continue
        if q.detail == "repaid_amount":  # "how much have I repaid?": an amount, from the loan's own figures
            lines.append(t("repaid_of", lang, type=name, **ref, paid=loan.emis_paid, repaid=money(max(loan.sanctioned - loan.outstanding, 0)),
                           total=money(loan.sanctioned), out=money(loan.outstanding)))
            continue
        if q.detail in ("emi_status", "bounce_charges") and last:
            if last.status == "bounced":
                reason = t(f"reason_{last.reason}", lang) if f"reason_{last.reason}" in T else (last.reason or "")
                lines.append(t("emi_bounced", lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang), reason=reason))
                if last.charge:
                    lines.append(t("emi_bounce_charge", lang, amt=money(last.charge)))
                note = next((note_text(x, lang) for a in c.accounts for x in a.transactions
                             if x.channel == "NACH" and x.status == "failed" and x.date == last.due_date and x.note), "")
                if note:
                    lines.append(t("emi_bounce_time", lang, note=note if note[:2].isupper() else note[:1].lower() + note[1:]))
            elif q.detail == "bounce_charges":
                lines.append(t("no_bounce", lang, type=name))
            elif last.paid_on:  # the day it was debited, from the bank's record; said once when it is the due date
                key = "emi_paid_day" if last.paid_on == last.due_date else "emi_paid_on"
                lines.append(t(key, lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang),
                               paid=day(last.paid_on, lang)))
            else:  # two loans: each payment says which loan it was
                lines.append(t("emi_paid_of", lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang)))
        elif q.detail in ("emi_status", "bounce_charges"):  # no past EMI on record: say so, never the next EMI instead
            lines.append(t("no_bounce" if q.detail == "bounce_charges" else "emi_no_history", lang, type=name))
        elif q.detail == "next_emi":  # each EMI says which loan it is ("Your gold loan EMI of ₹5,400 is due on...")
            lines.append(t("emi_next_of", lang, type=name, **ref, emi=money(loan.emi), date=day(loan.next_due, lang)))
        elif q.detail == "emis_count":
            lines.append(t("emi_count_of", lang, type=name, **ref, paid=loan.emis_paid, left=loan.emis_left))
        elif q.detail == "emi_increase":
            if loan.rate_changes:
                r = loan.rate_changes[0]
                lines.append(t("emi_up", lang, old=money(r.old_emi), new=money(r.new_emi), type=name, r0=f"{r.old_rate:g}",
                               r1=f"{r.new_rate:g}", date=day(r.date, lang)))
            else:
                lines.append(t("emi_same", lang, type=name, emi=money(loan.emi)))
        elif q.detail == "foreclosure":
            pct = foreclosure_pct(loan)
            if pct is None:  # no published rule for this loan type, or not yet allowed: the loan team quotes it
                lines.append(t("loan", lang, type=name, l4=loan.last4, out=money(loan.outstanding)))
                lines.append(t("foreclose_team", lang))
                return PersonalAnswer(lines, fields=fields, offer={"handoff": "loans"})
            charge = loan.outstanding * pct / 100 * 1.18
            note = "" if not pct else (f" plus about ₹{money(round(charge))} foreclosure charge" if lang == "en" else
                                       f" + लगभग ₹{money(round(charge))} फोरक्लोज़र चार्ज" if lang == "hi" else
                                       f" plus lagbhag ₹{money(round(charge))} foreclosure charge")
            lines.append(t("foreclose", lang, type=name, amt=money(round(loan.outstanding + charge)), out=money(loan.outstanding), charge=note))
        else:
            lines.append(t("loan", lang, type=name, l4=loan.last4, out=money(loan.outstanding)))
            if not q.detail and len(picked) == 1:  # an overview only: "outstanding" is answered alone, not with the EMI again
                lines.append(t("emi_next", lang, emi=money(loan.emi), date=day(loan.next_due, lang)))
    if q.detail == "emis_count" and len(picked) > 1:  # "across all my loans": the total first, then what each loan adds
        on = {"en": "{n} on your {type} loan ending {l4}", "hi": "{l4} वाले {type} लोन पर {n}", "hinglish": "{type} loan ending {l4} par {n}"}
        parts = [on.get(lang, on["en"]).format(n=x.emis_paid, type=_loan_name(x, lang), l4=x.last4) for x in picked]
        lines = [t("emis_across", lang, n=len(picked), total=sum(x.emis_paid for x in picked), parts=_join(parts, lang))]
    if q.detail == "repaid_amount" and len(picked) > 1:  # the total repaid first, then each loan's own figures
        lines = [t("repaid_across", lang, n=len(picked), amt=money(sum(max(x.sanctioned - x.outstanding, 0) for x in picked)))] + lines
    return PersonalAnswer(lines, fields=fields)


def _credit_card(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if not c.credit_cards:
        return PersonalAnswer([t("no_cc", lang)])
    picked, ask = _pick(c.credit_cards, q.item, lang, ("credit cards",), lambda x: x.variant, lambda x: (x.variant, x.last4))
    if ask:
        return ask
    lines, fields = [], []
    for k in picked:
        fields.append(f"credit_card:{k.last4}")
        if q.detail == "available_limit":
            lines.append(t("cc_limit", lang, variant=k.variant, l4=k.last4, avail=money(k.available), limit=money(k.limit)))
        elif q.detail == "payment_status":
            if k.last_payment_on:
                key = "cc_pay_processing" if k.last_payment_status == "processing" else "cc_pay_posted"
                lines.append(t(key, lang, amt=money(k.last_payment_amount or 0), date=day(k.last_payment_on, lang)))
        elif q.detail == "statement":
            yes, no = T["paid_words"].get(lang, T["paid_words"]["en"])
            lines.append(t("cc_statement", lang, l4=k.last4, date=day(k.statement_date, lang), paid=yes if k.previous_bill_paid else no))
        elif q.detail == "lounge":
            if k.lounge_per_quarter:
                lines.append(t("cc_lounge", lang, variant=k.variant, total=k.lounge_per_quarter, used=k.lounge_used_quarter,
                               left=max(k.lounge_per_quarter - k.lounge_used_quarter, 0)))
            else:
                lines.append(t("cc_no_lounge", lang, variant=k.variant))
        elif q.detail == "annual_fee":
            if k.annual_fee and k.annual_fee_charged_on:
                lines.append(t("cc_fee", lang, fee=money(k.annual_fee), variant=k.variant, date=day(k.annual_fee_charged_on, lang),
                               waiver=money(k.annual_fee_waiver_spend or 0), spend=money(k.spend_last_year or 0)))
            else:
                lines.append(t("cc_no_fee", lang, variant=k.variant))
        elif q.detail == "international":
            amount = q.amount or 0
            if k.international:
                lines.append(t("cc_intl_on", lang, l4=k.last4, avail=money(k.available), amt=money(amount or k.available)))
            else:
                lines.append(t("cc_intl_off", lang, l4=k.last4))
                return PersonalAnswer(lines, fields=fields, offer={"action": {
                    "type": "controls", "card": "credit", "card_last4": k.last4, "international": "on"}})
        elif (q.amount or q.keyword or q.period) and not q.detail:  # a particular spend ("the ₹8,499 transaction")
            items = [x for x in k.transactions if (not q.amount or abs(x.amount - q.amount) <= max(q.amount * 0.1, 1))
                     and (not q.keyword or q.keyword.lower() in x.merchant.lower())]
            span = period_range(q.period, today)
            items = [x for x in items if not span or span[0] <= x.date <= span[1]][:5]
            if items:
                lines.append(t("cc_txns", lang, l4=k.last4))
                lines += [t("cc_txn", lang, when=_when(x.date, "", lang), who=x.merchant, amt=money(x.amount),
                            intl=t("cc_intl", lang) if x.international else "") for x in items]
        else:
            if k.total_due:
                lines.append(t("cc_due", lang, variant=k.variant, l4=k.last4, total=money(k.total_due), min=money(k.minimum_due),
                               date=day(k.due_date, lang)))
            else:
                lines.append(t("cc_paid_up", lang, variant=k.variant, l4=k.last4))
    if not lines:
        lines = [t("txn_none", lang, l4=" / ".join(k.last4 for k in picked))]
    return PersonalAnswer(lines, fields=fields)


def _debit_card(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    cards = c.debit_cards
    if not cards:
        return PersonalAnswer([t("no_dc", lang)])
    hint = q.item or q.account
    picked = [d for d in cards if hint and (d.last4.endswith(hint[-4:]) or d.account_last4.endswith(hint[-4:]))] or cards
    if hint and "salary" in hint.lower():
        salary = {a.last4 for a in c.accounts if a.is_salary}
        picked = [d for d in cards if d.account_last4 in salary] or picked
    if len(picked) > 1 and q.detail != "declines":
        picked = [d for d in picked if d.status != "blocked"] or picked
        if len(picked) > 1:
            return PersonalAnswer([t("which_item", lang, n=len(picked), what=plural("debit card", lang),
                                     list=_listing(picked, lang, lambda d: d.variant, "debit card"))], "clarify")
    on, off = T["on_off"].get(lang, T["on_off"]["en"])
    lines, fields = [], []
    if q.detail == "declines":
        offer = None
        for d in picked:
            acct = next((a for a in c.accounts if a.last4 == d.account_last4), None)
            declined = [x for x in (acct.transactions if acct else []) if x.status == "failed" and x.channel in ("POS", "ECOM")]
            why = t("dc_blocked_why", lang) if d.status == "temp_blocked" else t("dc_pos_off_why", lang) if not d.pos else ""
            if declined:
                x = declined[0]
                same = [y for y in declined if y.date == x.date and y.amount == x.amount]
                lines.append(t("dc_declines", lang, n=len(same), amt=money(x.amount), who=x.counterparty, date=day(x.date, lang),
                               why=why or t("dc_unknown_why", lang)))
            elif why:  # nothing declined on record yet, but the card's settings explain it
                lines.append(t("dc_cause", lang, l4=d.last4, why=why))
            if offer is None and d.status == "temp_blocked":
                offer = {"type": "unblock", "card": "debit", "card_last4": d.last4}
                lines.append(t("dc_offer_unblock", lang, l4=d.last4)
                             + ("" if d.pos else " " + t("dc_pos_also_off", lang)))
            elif offer is None and not d.pos and d.status == "active":
                offer = {"type": "controls", "card": "debit", "card_last4": d.last4, "block": "", "pos": "on",
                         "atm": "", "ecom": "", "contactless": "", "international": ""}
                lines.append(t("dc_offer_pos", lang, l4=d.last4))
        return PersonalAnswer(lines or [t("dc_no_declines", lang)], fields=["debit_card:declines"],
                              offer={"action": offer} if offer else None)
    for d in picked:
        fields.append(f"debit_card:{d.last4}")
        controls = t("dc_controls", lang, **{k: on if getattr(d, k) else off for k in ("ecom", "pos", "contactless", "atm", "international")})
        lines.append(t("dc_state", lang, l4=d.last4, status=_status(d.status, lang), controls=controls))
    return PersonalAnswer(lines, fields=fields)


def _summary(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if q.detail == "upcoming":
        horizon = today + timedelta(days=7)
        items = [f"EMI ₹{money(x.emi)} on {day(x.next_due, lang)}" for x in c.loans if x.next_due and today <= x.next_due <= horizon]
        items += [f"card bill ₹{money(k.total_due)} on {day(k.due_date, lang)}" for k in c.credit_cards
                  if k.due_date and today <= k.due_date <= horizon and k.total_due]
        items += [f"FD ending {f.last4} maturing on {day(f.maturity_date, lang)}" for f in c.fds
                  if f.status == "active" and today <= f.maturity_date <= horizon]
        return PersonalAnswer([t("upcoming", lang, items="; ".join(items)) if items else t("upcoming_none", lang)], fields=["summary:upcoming"])
    if q.detail == "inactive":
        items = [f"your {a.type} account ending {a.last4}" for a in c.accounts if a.status != "active"]
        items += [f"your debit card ending {d.last4} ({_status(d.status, lang)})" for d in c.debit_cards if d.status != "active"]
        return PersonalAnswer([t("inactive", lang, items=" and ".join(items), verb="are" if len(items) > 1 else "is") if items
                               else t("inactive_none", lang)], fields=["summary:status"])
    if q.detail == "highest":
        top = max(c.accounts, key=lambda a: a.balance)
        lines = [t("highest", lang, acct=_acct(top, lang), amt=money(top.balance))]
        if c.loans:
            big = max(c.loans, key=lambda x: x.outstanding)
            lines.append(t("highest_loan", lang, type=_loan_name(big, lang), amt=money(big.outstanding)))
        return PersonalAnswer(lines, fields=["summary"])
    deposits = sum(a.balance for a in c.accounts)
    fds = [f for f in c.fds if f.status == "active"]
    lines = [t("sum_accounts", lang, n=len(c.accounts), word="account" if len(c.accounts) == 1 else "accounts", amt=money(deposits))]
    if fds:
        lines.append(_cap(t("sum_fd", lang, n=len(fds), word="FD" if len(fds) == 1 else "FDs", amt=money(sum(f.principal for f in fds)))))
    if c.loans:
        lines.append(_cap(t("sum_loan", lang, n=len(c.loans), word="loan" if len(c.loans) == 1 else "loans",
                            amt=money(sum(x.outstanding for x in c.loans)))))
    if c.credit_cards:
        lines.append(_cap(t("sum_cc", lang, n=len(c.credit_cards), word="card" if len(c.credit_cards) == 1 else "cards",
                            amt=money(sum(k.outstanding for k in c.credit_cards)))))
    loans = t("sum_loans_total", lang, amt=money(sum(x.outstanding for x in c.loans))) if c.loans else ""
    lines.append(t("sum_total", lang, dep=money(deposits + sum(f.principal for f in fds)), loans=loans))
    return PersonalAnswer(lines, fields=["summary"])
