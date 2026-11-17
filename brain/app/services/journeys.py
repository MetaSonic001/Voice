"""Multi-step journeys: card actions, fraud, complaints and disputes, transfers to a person, new-customer leads.

Each journey ends a turn either with an answer or with a question waiting for a yes ("pending"): "Shall I block
card ending 6712?", "Shall I raise a complaint?", "Shall I connect you to our cards team?". The caller's reply to
that question is read in code (services/confirm.py), never guessed by the LLM, and only a clear yes acts.

Returns `Step`s (what to say + what happened); the conversation core turns them into events, traces and audit.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain import Customer
from app.services import actions as card_actions
from app.services.audit import mask_number
from app.services.confirm import block_preference, read_reply
from app.services.desk import DEPARTMENTS, TAT_DAYS, add_working_days, department_name
from app.services.personal import PersonalQuery, _duplicates, _txn_line, day, money, note_text, period_range
from app.services.workflows import Candidate, find_candidates, recent_card_spend, recent_debits
from app.services.personal import answer as personal_answer

logger = logging.getLogger(__name__)
PENDING_TTL = timedelta(minutes=5)
_CREDIT = re.compile(r"credit|क्रेडिट|zenith|ananta|laksya", re.I)
_DEBIT = re.compile(r"debit|डेबिट|atm card|rupay|visa", re.I)


@dataclass
class Step:
    sentences: list[str]
    outcome: str  # answered | clarify | confirm | action_done | action_failed | cancelled | ticket | handoff | callback
    route: str | None = None
    data: dict = field(default_factory=dict)  # for the trace / audit (action result, ticket, handoff)
    handoff: dict | None = None  # {id, department, name}: the call now waits for a person
    pending: dict | None = None  # the question now waiting for an answer (None: nothing waiting)
    fields: list[str] = field(default_factory=list)  # account data disclosed


J = {
    "which_setting": {"en": "Which setting would you like me to change on your card: online, shop, tap-to-pay, ATM or international payments?",
                      "hi": "आप अपने कार्ड पर कौन सी सेटिंग बदलना चाहेंगे: ऑनलाइन, दुकान, टैप-टू-पे, ATM या इंटरनेशनल पेमेंट?",
                      "hinglish": "Aap apne card par kaunsi setting badalna chahenge: online, shop, tap-to-pay, ATM ya international payments?"},
    "pos_still_off": {"en": "And about the card not working at shops: shop payments are still switched off on it.",
                      "hi": "और कार्ड दुकानों पर न चलने के बारे में: इस पर दुकान पर पेमेंट अभी भी बंद है।",
                      "hinglish": "Aur card ke shops par na chalne ke baare mein: is par shop payments abhi bhi band hain."},
    "cancelled": {"en": "No problem, I won't. Is there anything else I can help you with?",
                  "hi": "ठीक है, मैं नहीं करूँगी। क्या मैं किसी और चीज़ में मदद कर सकती हूँ?",
                  "hinglish": "Theek hai, main nahi karungi. Kya main kisi aur cheez mein madad kar sakti hoon?"},
    "offer_transfer": {"en": "I can connect you to our {dept} team. Shall I?", "hi": "मैं आपको हमारी {dept} टीम से जोड़ सकती हूँ। जोड़ दूँ?",
                       "hinglish": "Main aapko hamari {dept} team se connect kar sakti hoon. Kar doon?"},
    "ask_transfer": {"en": "Of course. I'll connect you to our {dept} team. Is that okay?",
                     "hi": "ज़रूर। मैं आपको हमारी {dept} टीम से जोड़ती हूँ। ठीक है?",
                     "hinglish": "Zaroor. Main aapko hamari {dept} team se connect karti hoon. Theek hai?"},
    "ask_transfer_rm": {"en": "Of course. I'll connect you to our Priority Banking team, where your relationship manager {rm} sits. Is that okay?",
                        "hi": "ज़रूर। मैं आपको प्रायोरिटी बैंकिंग टीम से जोड़ती हूँ, जहाँ आपके रिलेशनशिप मैनेजर {rm} हैं। ठीक है?",
                        "hinglish": "Zaroor. Main aapko Priority Banking team se connect karti hoon, jahan aapke relationship manager {rm} hain. Theek hai?"},
    "connecting": {"en": "Connecting you to our {dept} team now. I've shared what we discussed, so you won't need to repeat it. Please stay on the line.",
                   "hi": "मैं आपको अभी हमारी {dept} टीम से जोड़ रही हूँ। मैंने हमारी बातचीत उन्हें बता दी है, आपको दोबारा बताना नहीं पड़ेगा। कृपया लाइन पर बने रहिए।",
                   "hinglish": "Main aapko abhi hamari {dept} team se connect kar rahi hoon. Maine hamari baat unhe bata di hai, aapko dobara batana nahi padega. Please line par bane rahiye."},
    "after_hours": {"en": "Our {dept} team is available from {hours}, so I've booked a callback for you instead. Your reference is {ref}, and they'll call you on your registered number.",
                    "hi": "हमारी {dept} टीम {hours} अवेलेबल रहती है, इसलिए मैंने आपके लिए कॉलबैक बुक कर दिया है। आपका रेफ़रेंस {ref} है, वे आपके रजिस्टर्ड नंबर पर कॉल करेंगे।",
                    "hinglish": "Hamari {dept} team {hours} available rehti hai, isliye maine aapke liye callback book kar diya hai. Aapka reference {ref} hai, woh aapke registered number par call karenge."},
    "not_allowed": {"en": "That's not something I can do on this call, but our {dept} team can help you with it. Shall I connect you?",
                    "hi": "यह मैं इस कॉल पर नहीं कर सकती, लेकिन हमारी {dept} टीम इसमें मदद कर सकती है। क्या मैं आपको जोड़ दूँ?",
                    "hinglish": "Yeh main is call par nahi kar sakti, lekin hamari {dept} team isme madad kar sakti hai. Kya main aapko connect kar doon?"},
    "sorry": {"en": "I'm sorry about that.", "hi": "इसके लिए मुझे खेद है।", "hinglish": "Iske liye sorry."},
    "fraud_sorry": {"en": "Thanks for telling me. Let's make sure your account is safe.", "hi": "बताने के लिए धन्यवाद। चलिए पक्का करते हैं कि आपका अकाउंट सुरक्षित है।",
                    "hinglish": "Batane ke liye thank you. Chaliye pakka karte hain ki aapka account safe hai."},
    "fraud_open_otp_shared": {"en": "Okay, let's secure your account first.", "hi": "ठीक है, पहले आपका अकाउंट सुरक्षित करते हैं।",
                              "hinglish": "Theek hai, pehle aapka account secure karte hain."},
    "fraud_open_unrecognised_debit": {"en": "Let's look into that payment right away.", "hi": "चलिए, उस पेमेंट को अभी देखते हैं।",
                                      "hinglish": "Chaliye, us payment ko abhi dekhte hain."},
    "fraud_open_lost_card": {"en": "Okay, let's make sure nobody can use that card.",
                             "hi": "ठीक है, पहले पक्का करते हैं कि उस कार्ड का कोई इस्तेमाल न कर सके।",
                             "hinglish": "Theek hai, pehle pakka karte hain ki us card ka koi use na kar sake."},
    "fraud_recent": {"en": "These are your latest debits: {list}. Is it one of these?",
                     "hi": "ये आपके सबसे हाल के डेबिट हैं: {list}। क्या इनमें से कोई है?",
                     "hinglish": "Yeh aapke latest debits hain: {list}. Kya inmein se koi hai?"},
    "fraud_no_recent": {"en": "I don't see any debits in the last week. Roughly when did it happen, and how much was it?",
                        "hi": "पिछले हफ़्ते में मुझे कोई डेबिट नहीं दिख रहा। यह लगभग कब हुआ, और कितनी रकम थी?",
                        "hinglish": "Pichhle hafte mein mujhe koi debit nahi dikh raha. Yeh lagbhag kab hua, aur kitni amount thi?"},
    "otp_recent": {"en": "Here's what went out in the last two days: {list}.",
                   "hi": "पिछले दो दिनों में ये पेमेंट हुए हैं: {list}।",
                   "hinglish": "Pichhle do din mein yeh payments hue hain: {list}."},
    "otp_none": {"en": "Good news: I don't see any payments from your account in the last two days.",
                 "hi": "अच्छी बात यह है कि पिछले दो दिनों में आपके अकाउंट से कोई पेमेंट नहीं हुआ है।",
                 "hinglish": "Achhi baat yeh hai ki pichhle do din mein aapke account se koi payment nahi hua hai."},
    "otp_next": {"en": "Please don't share it with anyone again; the bank never asks for it. To be safe, I'd like to connect you to our fraud team now so they can secure your net banking, UPI and cards. Shall I?",
                 "hi": "कृपया इसे दोबारा किसी को न बताइए; बैंक कभी यह नहीं माँगता। सुरक्षा के लिए मैं आपको अभी फ्रॉड टीम से जोड़ना चाहूँगी, ताकि वे आपकी नेट बैंकिंग, UPI और कार्ड सुरक्षित कर सकें। जोड़ दूँ?",
                 "hinglish": "Please ise dobara kisi ko mat bataiye; bank kabhi yeh nahi maangta. Safety ke liye main aapko abhi fraud team se connect karna chahungi, taaki woh aapki net banking, UPI aur cards secure kar sakein. Kar doon?"},
    "lead_offer_apply": {"en": "Would you like someone from our team to call you and help you apply?",
                         "hi": "क्या आप चाहेंगे कि हमारी टीम आपको कॉल करके आवेदन में मदद करे?",
                         "hinglish": "Kya aap chahenge ki hamari team aapko call karke apply karne mein madad kare?"},
    "fraud_seen": {"en": "I can see {what}.", "hi": "मुझे {what} दिख रहा है।", "hinglish": "Mujhe {what} dikh raha hai."},
    "fraud_txn": {"en": "a payment of ₹{amt} at {who} on {date} on your {card}", "hi": "{date} को {who} पर ₹{amt} का पेमेंट, आपके {card} से",
                  "hinglish": "{date} ko {who} par ₹{amt} ka payment, aapke {card} se"},
    "fraud_followup": {"en": "Shall I also raise a dispute for {what} and connect you to our fraud team, so they can take it forward?",
                       "hi": "क्या मैं {what} के लिए विवाद भी दर्ज कर दूँ और आपको हमारी फ्रॉड टीम से जोड़ दूँ, ताकि वे आगे देखें?",
                       "hinglish": "Kya main {what} ke liye dispute bhi raise kar doon aur aapko hamari fraud team se connect kar doon, taaki woh aage dekhein?"},
    "fraud_followup_generic": {"en": "Shall I connect you to our fraud team now, so they can check your recent transactions with you?",
                               "hi": "क्या मैं आपको अभी फ्रॉड टीम से जोड़ दूँ, ताकि वे आपके हाल के ट्रांज़ैक्शन आपके साथ जाँच सकें?",
                               "hinglish": "Kya main aapko abhi fraud team se connect kar doon, taaki woh aapke recent transactions aapke saath check kar sakein?"},
    "open_request": {"en": "By the way, I can see you have an open {kind} with us about {topic}. Is today's call about that?",
                     "hi": "वैसे, मुझे आपका एक खुला {kind} दिख रहा है ({topic})। क्या आज की कॉल उसी के बारे में है?",
                     "hinglish": "Waise, mujhe aapki ek open {kind} dikh rahi hai ({topic}). Kya aaj ki call usi ke baare mein hai?"},
    "fraud_none": {"en": "I couldn't find a payment like that in your recent activity. Could you tell me roughly when it happened, which account or card, and who it was paid to?",
                   "hi": "आपके हाल के ट्रांज़ैक्शन में ऐसा कोई पेमेंट नहीं मिला। क्या आप बता सकते हैं कि यह लगभग कब हुआ, किस अकाउंट या कार्ड से, और किसे पेमेंट हुआ?",
                   "hinglish": "Aapke recent transactions mein aisa koi payment nahi mila. Kya aap bata sakte hain ki yeh lagbhag kab hua, kis account ya card se, aur kisko payment hua?"},
    "fraud_many": {"en": "I can see {n} payments that could be it: {list}. Which one is it?",
                   "hi": "मुझे {n} पेमेंट दिख रहे हैं जो यह हो सकते हैं: {list}। इनमें से कौन सा है?",
                   "hinglish": "Mujhe {n} payments dikh rahe hain jo yeh ho sakte hain: {list}. Inmein se kaunsa hai?"},
    "fraud_pending": {"en": "That one is still processing, so it may still be reversed by itself.",
                      "hi": "वह पेमेंट अभी प्रोसेस हो रहा है, इसलिए हो सकता है वह अपने आप वापस हो जाए।",
                      "hinglish": "Woh payment abhi process ho raha hai, isliye ho sakta hai woh apne aap reverse ho jaye."},
    "fraud_reversed": {"en": "That payment was reversed, so the money is back in your account. If there's another one you don't recognise, tell me.",
                       "hi": "वह पेमेंट वापस हो चुका है, इसलिए पैसा आपके अकाउंट में लौट आया है। कोई और पेमेंट पहचान में न आए तो बताइए।",
                       "hinglish": "Woh payment reverse ho chuka hai, isliye paisa aapke account mein wapas aa gaya hai. Koi aur payment pehchaan mein na aaye toh bataiye."},
    "fraud_upi_followup": {"en": "Shall I raise a fraud complaint for it and connect you to our fraud team? You can also report it on the cyber crime helpline, 1930.",
                           "hi": "क्या मैं इसके लिए धोखाधड़ी की शिकायत दर्ज कर दूँ और आपको फ्रॉड टीम से जोड़ दूँ? आप साइबर क्राइम हेल्पलाइन 1930 पर भी शिकायत कर सकते हैं।",
                           "hinglish": "Kya main iske liye fraud complaint raise kar doon aur aapko fraud team se connect kar doon? Aap cyber crime helpline 1930 par bhi report kar sakte hain."},
    "fraud_bank_down": {"en": "I'm sorry, I can't check your account right now, and I don't want you to wait. I can connect you to our fraud team straight away. Shall I?",
                        "hi": "माफ़ कीजिए, अभी मैं आपका अकाउंट नहीं देख पा रही, और मैं नहीं चाहती कि आप इंतज़ार करें। मैं आपको सीधे फ्रॉड टीम से जोड़ सकती हूँ। जोड़ दूँ?",
                        "hinglish": "Sorry, abhi main aapka account nahi dekh pa rahi, aur main nahi chahti ki aap wait karein. Main aapko seedha fraud team se connect kar sakti hoon. Kar doon?"},
    "lost_recent": {"en": "I can see {n} recent payments on that card, the latest ₹{amt} at {who}. If any of them isn't yours, I can raise a dispute and connect you to our fraud team. Shall I?",
                    "hi": "उस कार्ड पर मुझे {n} हाल के पेमेंट दिख रहे हैं, सबसे ताज़ा {who} पर ₹{amt}। अगर इनमें से कोई आपका नहीं है, तो मैं विवाद दर्ज कर सकती हूँ और आपको फ्रॉड टीम से जोड़ सकती हूँ। जोड़ दूँ?",
                    "hinglish": "Us card par mujhe {n} recent payments dikh rahe hain, sabse latest {who} par ₹{amt}. Agar inmein se koi aapka nahi hai, toh main dispute raise kar sakti hoon aur fraud team se connect kar sakti hoon. Kar doon?"},
    "lost_none": {"en": "I don't see any recent payments on that card. Shall I connect you to our fraud team, in case you want them to check anything else?",
                  "hi": "उस कार्ड पर मुझे कोई हाल का पेमेंट नहीं दिख रहा। क्या मैं आपको फ्रॉड टीम से जोड़ दूँ, अगर आप कुछ और जँचवाना चाहें?",
                  "hinglish": "Us card par mujhe koi recent payment nahi dikh raha. Kya main aapko fraud team se connect kar doon, agar aap kuch aur check karwana chahein?"},
    "fraud_advice": {"en": "Please don't share your OTP, PIN or CVV with anyone. You can also report it to the cyber crime helpline on 1930.",
                     "hi": "कृपया अपना OTP, PIN या CVV किसी को न बताएँ। आप साइबर क्राइम हेल्पलाइन 1930 पर भी शिकायत कर सकते हैं।",
                     "hinglish": "Please apna OTP, PIN ya CVV kisi ko mat bataiye. Aap cyber crime helpline 1930 par bhi report kar sakte hain."},
    "already_blocked": {"en": "Your {card} is already blocked, so it can't be used.", "hi": "आपका {card} पहले से ब्लॉक है, इसलिए इस्तेमाल नहीं हो सकता।",
                        "hinglish": "Aapka {card} pehle se blocked hai, isliye use nahi ho sakta."},
    "issue_details": {"en": "I'm sorry about that; let me look into it. Roughly how much was it, and when did it happen?",
                      "hi": "इसके लिए खेद है; मैं देखती हूँ। लगभग कितने रुपये थे, और यह कब हुआ?",
                      "hinglish": "Iske liye sorry; main dekhti hoon. Lagbhag kitne rupaye the, aur yeh kab hua?"},
    "offer_ticket": {"en": "Would you like me to raise a {kind} with our {dept} team? They'll get back to you by {date}.",
                     "hi": "क्या मैं हमारी {dept} टीम के पास {kind} दर्ज कर दूँ? वे {date} तक आपसे संपर्क करेंगे।",
                     "hinglish": "Kya main hamari {dept} team ke paas {kind} raise kar doon? Woh {date} tak aapse contact karenge."},
    "kind_complaint": {"en": "complaint", "hi": "शिकायत", "hinglish": "complaint"},
    "kind_dispute": {"en": "dispute", "hi": "विवाद", "hinglish": "dispute"},
    "kind_review": {"en": "request to review the charge", "hi": "चार्ज रिव्यू की रिक्वेस्ट", "hinglish": "charge review request"},
    "ticket_done": {"en": "Done. I've raised it, and your reference number is {ref}. Our {dept} team will get back to you by {date}.",
                    "hi": "हो गया। मैंने दर्ज कर दिया है, आपका रेफ़रेंस नंबर {ref} है। हमारी {dept} टीम {date} तक आपसे संपर्क करेगी।",
                    "hinglish": "Ho gaya. Maine raise kar diya hai, aapka reference number {ref} hai. Hamari {dept} team {date} tak aapse contact karegi."},
    "txn_not_found": {"en": "I couldn't find a transaction like that in your recent activity.",
                      "hi": "आपके हाल के ट्रांज़ैक्शन में ऐसा कोई ट्रांज़ैक्शन नहीं मिला।",
                      "hinglish": "Aapke recent transactions mein aisa koi transaction nahi mila."},
    "lead_offer": {"en": "Would you like someone from our team to call you to help you open it?",
                   "hi": "क्या आप चाहेंगे कि हमारी टीम आपको अकाउंट खुलवाने में मदद के लिए कॉल करे?",
                   "hinglish": "Kya aap chahenge ki hamari team aapko account kholne mein madad ke liye call kare?"},
    "lead_done": {"en": "Lovely. Someone from our team will call you on this number by {date}; your reference is {ref}. You can also open a Digital account yourself in the AU 0101 app with a quick video KYC.",
                  "hi": "बढ़िया। हमारी टीम {date} तक इसी नंबर पर आपको कॉल करेगी; आपका रेफ़रेंस {ref} है। आप AU 0101 ऐप में वीडियो KYC से खुद भी डिजिटल अकाउंट खोल सकते हैं।",
                  "hinglish": "Badhiya. Hamari team {date} tak isi number par aapko call karegi; aapka reference {ref} hai. Aap AU 0101 app mein video KYC se khud bhi Digital account khol sakte hain."},
    "lead_no_number": {"en": "You can open a Digital account yourself in the AU 0101 app with a quick video KYC, or visit any AU branch.",
                       "hi": "आप AU 0101 ऐप में वीडियो KYC से खुद डिजिटल अकाउंट खोल सकते हैं, या किसी भी AU ब्रांच में जा सकते हैं।",
                       "hinglish": "Aap AU 0101 app mein video KYC se khud Digital account khol sakte hain, ya kisi bhi AU branch mein ja sakte hain."},
    "busy_callback": {"en": "I'm sorry for the wait; everyone in our {dept} team is busy right now. I've booked a callback for you, reference {ref}, and they'll call you on your registered number by {date}. Thank you for calling AU Bank.",
                      "hi": "इंतज़ार के लिए माफ़ी; हमारी {dept} टीम अभी व्यस्त है। मैंने आपके लिए कॉलबैक बुक कर दिया है, रेफ़रेंस {ref}, और वे {date} तक आपके रजिस्टर्ड नंबर पर कॉल करेंगे। AU बैंक को कॉल करने के लिए धन्यवाद।",
                      "hinglish": "Wait ke liye sorry; hamari {dept} team abhi busy hai. Maine aapke liye callback book kar diya hai, reference {ref}, aur woh {date} tak aapke registered number par call karenge. AU Bank ko call karne ke liye thank you."},
    "restate_transfer": {"en": "I'd connect you to our {dept} team, who handle {what}. Shall I go ahead?",
                         "hi": "मैं आपको हमारी {dept} टीम से जोड़ूँगी, जो {what} देखती है। जोड़ दूँ?",
                         "hinglish": "Main aapko hamari {dept} team se connect karungi, jo {what} dekhti hai. Kar doon?"},
}


def j(key: str, lang: str, **values) -> str:
    return J[key].get(lang, J[key]["en"]).format(**values)


def pending(kind: str, lang: str, /, **data) -> dict:
    return {**data, "kind": kind, "lang": lang, "at": datetime.now().astimezone().isoformat()}


def expired(p: dict) -> bool:
    try:
        return datetime.now().astimezone() - datetime.fromisoformat(p["at"]) > PENDING_TTL
    except (KeyError, ValueError):
        return True


ISSUES = {  # issue type -> (department, ticket kind, ticket category, keyword to search transactions)
    "atm_cash_not_dispensed": ("payments", "complaint", "atm_cash_not_dispensed", "atm"),
    "payment_failed_debited": ("payments", "complaint", "payment_failed_debited", ""),
    "transfer_not_received": ("payments", "complaint", "transfer_not_received", ""),
    "double_charge": ("payments", "dispute", "double_charge", ""),
    "charge_review": ("deposits", "service_request", "charge_review", ""),
    "service_complaint": ("general", "complaint", "service_complaint", ""),
    "other": ("general", "complaint", "service_complaint", ""),
}


_ORDINALS = (("first", r"\b(first|1st|pehla|pehle|pahla)\b|पहला|पहले"), ("second", r"\b(second|2nd|dusra|doosra|dusre)\b|दूसरा|दूसरे"),
             ("third", r"\b(third|3rd|teesra|tisra)\b|तीसरा|तीसरे"))


def _pick_candidate(raw: str, cands: list[Candidate]) -> Candidate | None:
    """Which of the listed payments the caller means: "the second one", or the amount said. None if it can't tell."""
    low = raw.lower()
    for i, (_, pattern) in enumerate(_ORDINALS):
        if re.search(pattern, low) and i < len(cands):
            return cands[i]
    amounts = {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", low) if n.replace(",", "").replace(".", "").isdigit()}
    hits = [c for c in cands if any(abs(c.amount - a) <= max(a * 0.02, 1) for a in amounts)]
    return hits[0] if len(hits) == 1 else None


class Journeys:
    def __init__(self, bank, auth, service_desk, handoffs, audit, settings):
        self.bank, self.auth, self.desk, self.handoffs, self.audit, self.s = bank, auth, service_desk, handoffs, audit, settings

    # ───────── answers to a waiting question ─────────

    def resolve(self, session_id: str, p: dict, raw: str, lang: str, channel: str) -> Step | None:
        """The caller's reply to a pending question. None = not an answer to it (carry on normally)."""
        reply = read_reply(raw)
        kind = p["kind"]
        if kind == "action" and (pref := block_preference(raw)) and p["plan"]["action"] in ("temp_block", "block"):
            customer = self.auth.verified_customer(session_id)
            if customer:  # "make it permanent" while confirming a temporary block: re-plan and confirm again
                plan, text, _ = card_actions.plan(customer, {"type": "block", "card": p["plan"]["kind"],
                                                             "card_last4": p["plan"]["last4"], "block": pref}, lang)
                if plan:
                    return Step([text], "confirm", pending={**p, "plan": plan.to_dict(), "at": datetime.now().astimezone().isoformat()})
        if kind == "fraud_pick":  # the answer to "which one is it?": an ordinal or the amount, read in code
            pick = _pick_candidate(raw, [Candidate(**c) for c in p["cands"]])
            customer = self.auth.verified_customer(session_id) if pick else None
            if pick and customer:
                return self._fraud_one(session_id, customer, pick, p["req"], lang)
            return None
        if kind == "action_target":  # the answer to "which card?": last digits or debit / credit, read in code
            digits = re.search(r"(?<!\d)(\d{4})(?!\d)", raw)
            card = "credit" if _CREDIT.search(raw) else "debit" if _DEBIT.search(raw) else ""
            if not digits and reply in ("yes", "no") and len(raw.split()) <= 3:  # "yes" names no card: ask again, never pick one
                customer = self.auth.verified_customer(session_id)
                return self.action(session_id, customer, p["req"], lang, then=p.get("then") or None) if customer else None
            customer = self.auth.verified_customer(session_id) if (digits or card) else None
            if customer:
                req = p["req"] | {"card_last4": digits.group(1) if digits else p["req"].get("card_last4", ""),
                                  "card": card or p["req"].get("card", "")}
                return self.action(session_id, customer, req, lang, then=p.get("then") or None)
            return None
        if reply == "no":
            if len(raw.split()) > 5:  # a "no" with a new request after it: drop the question, answer the request
                return None
            return Step([j("cancelled", lang)], "cancelled", data={"declined": kind})
        if reply == "question":
            if len(raw.split()) > 6:  # a new question, not "which card?": answer it instead
                return None
            if kind == "handoff":
                dept = p["department"]
                return Step([j("restate_transfer", lang, dept=department_name(dept, lang), what=DEPARTMENTS[dept][3])],
                            "confirm", pending=p)
            if kind == "action":
                return Step([p["question"]], "confirm", pending=p)  # restate: it names the exact card
            return None
        if reply != "yes":
            return None
        if kind == "more":
            return Step(p["lines"], "answered")
        customer = self.auth.verified_customer(session_id) if self.auth else None
        if kind == "action":
            return self._do_action(session_id, customer, p, lang)
        if kind == "ticket":
            return self._raise(session_id, customer, p, lang)
        if kind == "fraud_followup":
            ticket = self._raise(session_id, customer, p, lang) if p.get("ticket") else None
            step = self.start_handoff(session_id, customer, "fraud", p.get("reason", "suspected fraud"), lang, channel)
            if ticket:
                step.sentences = ticket.sentences[:1] + step.sentences
                step.data["ticket"] = ticket.data.get("ticket")
            return step
        if kind == "handoff":
            return self.start_handoff(session_id, customer, p["department"], p.get("reason", "caller asked"), lang, channel)
        if kind == "lead":
            return self._lead(session_id, p, lang)
        return None

    def open_request_note(self, customer: Customer, lang: str) -> str:
        """Long-term memory, kept small: an open request from the last 30 days is only ASKED about, naming the kind and the
        topic and nothing else, until the caller says it is what they are calling about. Never assumed."""
        try:
            rows = self.desk.open_requests(customer.cif, 30)
        except Exception:  # a memory problem must never get in the way of verification
            logger.exception("open requests lookup failed")
            return ""
        if not rows:
            return ""
        kinds = {"complaint": ("complaint", "शिकायत", "complaint"), "dispute": ("dispute", "विवाद", "dispute"),
                 "service_request": ("request", "अनुरोध", "request")}
        i = {"en": 0, "hi": 1, "hinglish": 2}.get(lang, 0)
        topic = rows[0]["category"].replace("_", " ")
        return j("open_request", lang, kind=kinds.get(rows[0]["kind"], kinds["service_request"])[i], topic=topic)

    def offer_pending(self, session_id: str, customer: Customer, offer: dict, lang: str) -> dict | None:
        """The waiting question behind an offer made at the end of an answer."""
        if "handoff" in offer:
            return pending("handoff", lang, department=offer["handoff"], reason="offered after an answer")
        if "ticket" in offer:
            dept, kind, category, _ = {"charge_review": ISSUES["charge_review"],
                                       "double_charge": ISSUES["double_charge"]}.get(offer["ticket"], ISSUES["other"])
            return pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                           summary=f"Caller asked for a {category.replace('_', ' ')} after seeing their account")
        if "more" in offer:  # the rest of a list read three at a time
            return pending("more", lang, lines=offer["more"])
        if "action" in offer:
            plan, text, _ = card_actions.plan(customer, offer["action"], lang)
            return pending("action", lang, plan=plan.to_dict(), question=text, then={}) if plan else None
        return None

    # ───────── card actions ─────────

    def action(self, session_id: str, customer: Customer, req: dict, lang: str, then: dict | None = None) -> Step:
        if req.get("type") in ("other", "", "none"):
            dept = req.get("department") or "general"
            return self.offer_transfer(dept, lang, "a request the agent can't do on a call", text_key="not_allowed")
        plan, text, needs_card = card_actions.plan(customer, req, lang)
        if plan:
            if plan.action == "unblock" and req.get("type") == "controls" and not then:
                then = {"kind": "controls_after_unblock", "req": req}  # the settings change the caller asked for comes next
            return Step([text], "confirm", pending=pending("action", lang, plan=plan.to_dict(), question=text, then=then or {}))
        if needs_card and text:
            return Step([text], "clarify", pending=pending("action_target", lang, req=req, then=then or {}))
        if not text:  # never an empty reply: nothing to change was understood, so ask what to change
            return Step([j("which_setting", lang)], "clarify")
        return Step([text], "answered")

    def _do_action(self, session_id: str, customer: Customer | None, p: dict, lang: str) -> Step:
        if customer is None:  # verification expired while waiting
            return Step([j("cancelled", lang)], "cancelled", data={"reason": "verification expired"})
        plan = card_actions.CardAction(**p["plan"])
        done, text, record = card_actions.execute(self.bank, customer, plan, lang)
        self.auth.invalidate(session_id)  # the next read must see the change
        data = {"action": plan.to_dict(), "result": record, "done": done}
        self.audit.event(session_id, "card_action", action=plan.action, card=f"{plan.kind} ending {plan.last4}",
                         changes=plan.changes, done=done, reference=record.get("reference"), error=record.get("error"))
        if not done:
            dept = "credit_cards" if plan.kind == "credit" else "debit_cards"
            return Step([text], "action_failed", data=data, pending=pending("handoff", lang, department=dept,
                                                                            reason=f"card {plan.action} failed in the system"))
        then = p.get("then") or {}
        if done and then.get("kind") == "controls_after_unblock":  # found by the judge: the unblock alone left the request undone
            fresh = self.auth.verified_customer(session_id)
            nxt = self.action(session_id, fresh, then["req"], lang) if fresh else None
            if nxt and nxt.pending:
                return Step([text] + nxt.sentences, "confirm", data=data, pending=nxt.pending)
        if then.get("kind") == "fraud_followup":
            if then.get("lost"):  # a lost card: what was spent on it lately?
                recent = recent_card_spend(customer, plan.kind, plan.last4, self.s.demo_today)
                follow = j("lost_recent", lang, n=len(recent), amt=money(recent[0].amount), who=recent[0].who) if recent \
                    else j("lost_none", lang)
            else:
                follow = j("fraud_followup", lang, what=then["what"]) if then.get("what") else j("fraud_followup_generic", lang)
            return Step([text, j("fraud_advice", lang), follow], "action_done", data=data,
                        pending=pending("fraud_followup", lang, **{k: v for k, v in then.items() if k != "kind"}))
        if plan.kind == "debit" and not then:  # the problem the caller called about is not left behind (live test cfdda8da)
            fresh = self.auth.verified_customer(session_id)
            card = next((d for d in (fresh.debit_cards if fresh else []) if d.last4 == plan.last4), None)
            if card and card.status == "active" and not card.pos and self.auth.was_read(session_id, "debit_card:declines"):
                nxt = self.action(session_id, fresh, {"type": "controls", "card": "debit", "card_last4": card.last4, "block": "",
                                                      "pos": "on", "atm": "", "ecom": "", "contactless": "", "international": ""}, lang)
                if nxt.pending:
                    return Step([text, j("pos_still_off", lang)] + nxt.sentences, "confirm", data=data, pending=nxt.pending)
        return Step([text], "action_done", data=data)

    # ───────── fraud ─────────

    def fraud(self, session_id: str, customer: Customer, req: dict, q: PersonalQuery, lang: str) -> Step:
        """Protect first. A lost or stolen card is blocked (after one yes); an unrecognised debit is looked up from what the
        caller said and never guessed; then a dispute or fraud complaint and the fraud team."""
        risk = req.get("risk") or []
        opening = self._opening(req, lang)
        if "lost_card" in risk and not q.amount:
            return self._lost_card(session_id, customer, req, lang)
        if "otp_shared" in risk and not (q.amount or q.keyword or q.period):
            return self._otp_shared(customer, req, lang, opening)
        found = find_candidates(customer, q, self.s.demo_today)
        found = [c for c in found if c.status != "failed"]
        if len(found) > 1 and not (req.get("card") or req.get("card_last4")):
            shown = found[:3]
            listing = "; ".join(f"₹{money(c.amount)} {c.who}, {day(c.date, lang)}" for c in shown)
            return Step(opening + [j("fraud_many", lang, n=len(shown), list=listing)], "clarify",
                        pending=pending("fraud_pick", lang, cands=[c.to_dict() for c in shown], req=req | {"opened": True}))
        if found:
            return self._fraud_one(session_id, customer, found[0], req, lang)
        if req.get("card") or req.get("card_last4"):  # no matching payment, but they named the card: protect it anyway
            return self._protect_card(session_id, customer, req, lang, opening + [j("txn_not_found", lang)], "", None)
        if not (q.amount or q.keyword or q.period):  # nothing to match on: show the latest debits and ask (never guess)
            shown = recent_debits(customer, self.s.demo_today, days=7)[:3]
            if shown:
                listing = "; ".join(f"₹{money(c.amount)} {c.who}, {day(c.date, lang)}" for c in shown)
                return Step(opening + [j("fraud_recent", lang, list=listing)], "clarify", fields=["account:transactions"],
                            pending=pending("fraud_pick", lang, cands=[c.to_dict() for c in shown], req=req | {"opened": True}))
            return Step(opening + [j("fraud_no_recent", lang)], "clarify", fields=["account:transactions"])
        return Step(opening + [j("fraud_none", lang)], "clarify")

    def _opening(self, req: dict, lang: str) -> list[str]:
        """The first line of a fraud report, unless it was already said before verification (never twice)."""
        if req.get("opened"):
            return []
        risk = req.get("risk") or []
        key = next((f"fraud_open_{r}" for r in ("otp_shared", "lost_card", "unrecognised_debit") if r in risk), "fraud_sorry")
        return [j(key, lang)]

    def _otp_shared(self, customer: Customer, req: dict, lang: str, opening: list[str]) -> Step:
        """"I shared my OTP": do not hunt for one payment. Say what went out lately, then get the fraud team to secure
        everything (net banking, UPI, cards) - the caller can still say no."""
        shown = recent_debits(customer, self.s.demo_today, days=2)[:3]
        listing = "; ".join(f"₹{money(c.amount)} {c.who}, {day(c.date, lang)}" for c in shown)
        seen = j("otp_recent", lang, list=listing) if shown else j("otp_none", lang)
        return Step(opening + [seen, j("otp_next", lang)], "confirm", fields=["account:transactions"],
                    pending=pending("fraud_followup", lang, what="", reason="caller shared an OTP / PIN", ticket=None))

    def fraud_bank_down(self, lang: str) -> Step:
        """The bank's systems cannot be read: say so, and still get the caller to the fraud team."""
        return Step([j("fraud_bank_down", lang)], "confirm",
                    pending=pending("handoff", lang, department="fraud", reason="suspected fraud; bank systems unavailable"))

    def _fraud_one(self, session_id: str, customer: Customer, cand: Candidate, req: dict, lang: str) -> Step:
        what = f"₹{money(cand.amount)} ({cand.who})"
        card = card_actions.card_label(cand.card_kind, "", cand.card_last4, lang) if cand.card_kind else ""
        seen = j("fraud_seen", lang, what=j("fraud_txn", lang, amt=money(cand.amount), who=cand.who, date=day(cand.date, lang), card=card)) \
            if card else j("fraud_seen", lang, what=f"₹{money(cand.amount)} {cand.who}, {day(cand.date, lang)}")
        lines = self._opening(req, lang) + [seen]
        if cand.status == "reversed":
            return Step(lines + [j("fraud_reversed", lang)], "answered", fields=["account:transactions"])
        if cand.status == "pending":
            lines.append(j("fraud_pending", lang))
        if cand.is_card_payment:  # a card payment: block the card, then dispute + fraud team
            then = {"kind": "fraud_followup", "what": what, "reason": f"suspected fraud: {what}",
                    "ticket": {"kind": "dispute", "category": "unrecognised_transaction", "department": "fraud",
                               "summary": f"Caller reports an unrecognised card transaction {what}"}}
            return self._protect_card(session_id, customer, req | {"card": cand.card_kind, "card_last4": cand.card_last4}, lang,
                                      lines, what, then)
        # UPI / IMPS / NEFT: the card is not involved, so nothing is blocked; a fraud complaint, 1930 and the fraud team
        ticket = {"kind": "complaint", "category": "unrecognised_transaction", "department": "fraud",
                  "summary": f"Caller reports an unrecognised {cand.channel} transfer {what}, ref {cand.reference}",
                  "detail": {"reference": cand.reference, "amount": cand.amount, "status": cand.status}}
        return Step(lines + [j("fraud_upi_followup", lang)], "confirm", fields=["account:transactions"],
                    pending=pending("fraud_followup", lang, what=what, reason=f"suspected fraud: {what}", ticket=ticket))

    def _protect_card(self, session_id: str, customer: Customer, req: dict, lang: str, lines: list[str], what: str,
                      then: dict | None) -> Step:
        then = then or {"kind": "fraud_followup", "what": what, "reason": "suspected fraud", "ticket": None}
        step = self.action(session_id, customer, {"type": "block", "card": req.get("card") or "", "card_last4": req.get("card_last4") or "",
                                                  "block": req.get("block") or "permanent"}, lang, then=then)
        if step.outcome == "answered":  # already blocked: go straight to the dispute / fraud team
            follow = j("fraud_followup", lang, what=what) if what else j("fraud_followup_generic", lang)
            return Step(lines + step.sentences + [follow], "confirm",
                        pending=pending("fraud_followup", lang, **{k: v for k, v in then.items() if k != "kind"}))
        step.sentences = lines + step.sentences
        return step

    def _lost_card(self, session_id: str, customer: Customer, req: dict, lang: str) -> Step:
        """"I lost my card": one clear yes to the block (lost or stolen = permanent, misplaced = temporary), then what was
        spent on it lately, a dispute for anything unrecognised, and the replacement that comes with a permanent block."""
        then = {"kind": "fraud_followup", "what": "", "reason": "lost or stolen card", "ticket": None, "lost": True}
        return self._protect_card(session_id, customer, req, lang, self._opening(req, lang), "", then)

    def _find_account_txn(self, c: Customer, q: PersonalQuery, keyword: str = ""):
        """The newest account debit matching what the caller said, as ("debit", transaction, debit card). None if they gave
        nothing to match (never the latest by default)."""
        span = period_range(q.period, self.s.demo_today)
        key = (keyword or q.keyword or "").lower()
        if not (q.amount or span or key):
            return None
        best = None
        for a in c.accounts:
            for x in a.transactions:
                if x.kind != "Debit" or (q.amount and abs(x.amount - q.amount) > max(q.amount * 0.1, 1)):
                    continue
                if span and not span[0] <= x.date <= span[1]:
                    continue
                if key and key not in f"{x.description} {x.counterparty} {x.channel}".lower():
                    continue
                if best is None or x.date > best[1].date:
                    best = ("debit", x, a.debit_card)
        return best

    # ───────── complaints and disputes ─────────

    def issue(self, session_id: str, customer: Customer, issue_type: str, q: PersonalQuery, lang: str) -> Step:
        dept, kind, category, keyword = ISSUES.get(issue_type, ISSUES["other"])
        due = day(self._due(category), lang)
        offer_key = {"dispute": "kind_dispute", "service_request": "kind_review"}.get(kind, "kind_complaint")
        offer = j("offer_ticket", lang, kind=j(offer_key, lang), dept=department_name(dept, lang), date=due)
        if issue_type == "double_charge":
            found = _duplicates(customer, lang, self.s.demo_today)
            lines = found.sentences[:1]
            summary = "Caller reports a double charge" + (f": {lines[0]}" if found.outcome == "answered" else "")
            return Step(lines + [offer], "confirm", fields=found.fields,
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept, summary=summary))
        if issue_type == "charge_review":
            charges = personal_answer(customer, PersonalQuery(topic="charges", detail="charges", account=q.account or "all"),
                                      lang, self.s.demo_today)
            lines = [s for s in charges.sentences if "review" not in s and "जाँच" not in s][:3]
            return Step(lines + [offer], "confirm", fields=charges.fields,
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                        summary="Caller asks for a charge to be reviewed"))
        if issue_type == "service_complaint" or issue_type == "other":
            return Step([j("sorry", lang), offer], "confirm",
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                        summary=f"Service complaint: {q.keyword or 'see call record'}"))
        found = self._find_account_txn(customer, q, keyword=keyword)
        if not found and not (q.amount or q.period):
            return Step([j("issue_details", lang)], "clarify")
        if not found:
            return Step([j("txn_not_found", lang), offer], "confirm",
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                        summary=f"{category.replace('_', ' ')}: transaction not found in recent activity"))
        _, x, _card = found
        lines = [_txn_line(x, lang)]
        if note := note_text(x, lang):
            lines.append(note)
        return Step(lines + [offer], "confirm", fields=["account:transactions"],
                    pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                    summary=f"{category.replace('_', ' ')}: ₹{money(x.amount)} {x.channel} on {x.date} "
                                            f"({x.counterparty or x.description}), status {x.status}, ref {x.reference}",
                                    detail={"reference": x.reference, "amount": x.amount, "status": x.status}))

    def _due(self, category: str):
        return add_working_days(self.s.demo_today, TAT_DAYS.get(category, 7))

    def _raise(self, session_id: str, customer: Customer | None, p: dict, lang: str) -> Step:
        spec = p.get("ticket") or p
        category = spec.get("category", "service_complaint")
        kind = spec.get("ticket_kind") or spec.get("kind", "complaint")
        ticket = self.desk.create_ticket(session_id=session_id, cif=customer.cif if customer else None, kind=kind,
                                         category=category, department=spec.get("department", "general"),
                                         summary=spec.get("summary", ""), detail=spec.get("detail") or {},
                                         tat_days=TAT_DAYS.get(category, 7))
        self.audit.event(session_id, "ticket", id=ticket["id"], kind=kind, category=category,
                         department=ticket["department"], due_by=str(ticket["due_by"]))
        text = j("ticket_done", lang, ref=ticket["id"], dept=department_name(ticket["department"], lang), date=day(ticket["due_by"], lang))
        return Step([text], "ticket", data={"ticket": ticket})

    # ───────── transfers to a person ─────────

    def offer_transfer(self, department: str, lang: str, reason: str, text_key: str = "offer_transfer",
                       customer: Customer | None = None) -> Step:
        department = department if department in DEPARTMENTS else "general"
        if customer and customer.segment == "priority" and text_key == "ask_transfer" and customer.rm_name:
            return Step([j("ask_transfer_rm", lang, rm=customer.rm_name)], "confirm",
                        pending=pending("handoff", lang, department="priority", reason=reason))
        return Step([j(text_key, lang, dept=department_name(department, lang))], "confirm",
                    pending=pending("handoff", lang, department=department, reason=reason))

    def start_handoff(self, session_id: str, customer: Customer | None, department: str, reason: str, lang: str,
                      channel: str) -> Step:
        department = department if department in DEPARTMENTS else "general"
        name = department_name(department, lang)
        if not self.handoffs.is_open(department):
            ticket = self.desk.create_ticket(session_id=session_id, cif=customer.cif if customer else None, kind="callback",
                                             category="callback", department=department, summary=f"Callback: {reason}",
                                             detail={}, tat_days=1)
            self.audit.event(session_id, "ticket", id=ticket["id"], kind="callback", department=department)
            return Step([j("after_hours", lang, dept=name, hours=self.s.desk_hours.replace("-", " to "), ref=ticket["id"])],
                        "callback", data={"ticket": ticket})
        note = self.handoff_note(session_id, customer, department, reason, lang)
        h = self.handoffs.create(session_id, department, reason, note, channel)
        self.audit.event(session_id, "handoff_requested", department=department, reason=reason, handoff=h["id"],
                         priority=h["priority"])
        return Step([j("connecting", lang, dept=name)], "handoff", route="handoff",
                    data={"handoff": h}, handoff={"id": h["id"], "department": department, "name": DEPARTMENTS[department][0]})

    def handoff_note(self, session_id: str, customer: Customer | None, department: str, reason: str, lang: str) -> dict:
        """The brief the person sees before picking up: built from the record, not written by the AI."""
        with self.audit.db.connection() as conn:
            s = conn.execute("SELECT caller_number, verified_at, facts FROM sessions WHERE id = %s", (session_id,)).fetchone() or {}
            turns = conn.execute("SELECT question, answer, intent, outcome FROM traces WHERE session_id = %s "
                                 "ORDER BY created_at DESC LIMIT 4", (session_id,)).fetchall()
            done = conn.execute("SELECT kind, detail FROM call_events WHERE session_id = %s AND kind IN ('card_action', 'ticket') "
                                "ORDER BY created_at", (session_id,)).fetchall()
            alerts = conn.execute("SELECT message FROM alerts WHERE session_id = %s", (session_id,)).fetchall()
        actions = []
        for e in done:
            d = e["detail"]
            actions.append(f"{d.get('action', '').replace('_', ' ')} {d.get('card', '')}: "
                           f"{'done, ref ' + str(d.get('reference')) if d.get('done') else 'failed'}" if e["kind"] == "card_action"
                           else f"ticket {d.get('id')} ({d.get('category', d.get('kind'))}), due {d.get('due_by', '')}")
        asked = [t["question"] for t in reversed(turns)]
        who = f"{customer.name} ({customer.segment})" if customer else "Caller (not verified)"
        summary = (f"{who} needs the {DEPARTMENTS[department][0]} team: {reason}. "
                   + (f"Last asked: \"{asked[-1]}\". " if asked else "")
                   + (f"Already done: {'; '.join(actions)}. " if actions else "Nothing has been changed on the account. "))
        headline = f"{reason[:1].upper()}{reason[1:]}" + (f"; done: {actions[-1]}" if actions else "; nothing changed on the account yet")
        return {"headline": headline, "summary": summary, "reason": reason, "department": DEPARTMENTS[department][0], "language": lang,
                "verified": bool(customer), "verified_at": s.get("verified_at"), "customer": customer.name if customer else None,
                "cif": customer.cif if customer else None, "segment": customer.segment if customer else None,
                "rm": customer.rm_name if customer else None, "caller": mask_number(s.get("caller_number")),
                "asked": asked[-3:], "done": actions, "facts": s.get("facts") or {}, "alerts": [a["message"] for a in alerts]}

    def handoff_timeout(self, session_id: str, handoff_id: str, lang: str) -> str:
        """Nobody picked up in time: book a callback instead, and say so."""
        h = self.handoffs.get(handoff_id)
        department = h["department"] if h else "general"
        with self.audit.db.connection() as conn:
            row = conn.execute("SELECT customer_id FROM sessions WHERE id = %s", (session_id,)).fetchone() or {}
        ticket = self.desk.create_ticket(session_id=session_id, cif=row.get("customer_id"), kind="callback", category="callback",
                                         department=department, summary=f"Callback: nobody free ({h['reason'] if h else ''})",
                                         detail={"handoff": handoff_id}, tat_days=1)
        self.handoffs.set_status(handoff_id, "callback", disposition=f"callback {ticket['id']}")
        self.audit.event(session_id, "ticket", id=ticket["id"], kind="callback", department=department)
        return j("busy_callback", lang, dept=department_name(department, lang), ref=ticket["id"], date=day(ticket["due_by"], lang))

    # ───────── new customers ─────────

    def lead_offer(self, lang: str, apply: bool = False) -> Step:
        """A callback from the sales team: to open an account, or (apply=True) to apply for a card or a loan."""
        return Step([j("lead_offer_apply" if apply else "lead_offer", lang)], "confirm", pending=pending("lead", lang))

    def _lead(self, session_id: str, p: dict, lang: str) -> Step:
        with self.audit.db.connection() as conn:
            row = conn.execute("SELECT caller_number, facts FROM sessions WHERE id = %s", (session_id,)).fetchone() or {}
        if not row.get("caller_number"):
            return Step([j("lead_no_number", lang)], "answered")
        ticket = self.desk.create_ticket(session_id=session_id, cif=None, kind="lead", category="lead", department="sales",
                                         summary="New customer would like help opening an account",
                                         detail={"interest": (row.get("facts") or {}).get("product", ""), "consent": "callback"},
                                         tat_days=1)
        self.audit.event(session_id, "ticket", id=ticket["id"], kind="lead", department="sales")
        return Step([j("lead_done", lang, date=day(ticket["due_by"], lang), ref=ticket["id"])], "ticket", data={"ticket": ticket})
