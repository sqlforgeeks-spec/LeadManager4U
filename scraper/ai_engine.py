"""
AI Engine — rule-based intelligence for LeadManager4U.
Features:
  - Lead scoring (0–100)
  - Industry detection from search phrase
  - Email template generation
  - Smart dashboard tips
  - Duplicate detection helpers
"""
import re
from difflib import SequenceMatcher

# ─── Industry detection ────────────────────────────────────────────────────────

INDUSTRY_KEYWORDS = {
    "dental": ["dentist", "dental", "orthodontist", "teeth", "smile", "braces", "implant"],
    "legal": ["lawyer", "attorney", "law firm", "legal", "solicitor", "barrister", "paralegal", "advocate"],
    "medical": ["doctor", "physician", "clinic", "medical", "hospital", "health", "GP", "surgeon", "specialist"],
    "pharma": ["pharmacy", "pharmacist", "chemist", "medicine", "drug store", "pharmaceutical", "dispensary", "medicines"],
    "clinic": ["clinic", "health centre", "urgent care", "outpatient", "diagnostic", "pathology", "radiology", "lab"],
    "restaurant": ["restaurant", "cafe", "food", "pizza", "burger", "bistro", "diner", "eatery", "bakery", "canteen", "catering", "takeaway"],
    "plumbing": ["plumber", "plumbing", "pipes", "drain", "water heater", "bathroom fitting"],
    "electrical": ["electrician", "electrical", "wiring", "HVAC", "AC repair", "solar panel"],
    "cleaning": ["cleaning", "cleaner", "maid", "janitorial", "housekeeping", "pressure wash", "sanitization"],
    "real_estate": ["real estate", "realtor", "property", "mortgage", "estate agent", "homes for sale", "property dealer", "flat", "apartment", "villa", "plot", "land"],
    "marketing": ["marketing", "SEO", "digital agency", "advertising", "social media", "PPC", "branding", "content"],
    "accounting": ["accountant", "accounting", "bookkeeping", "CPA", "tax", "auditor", "finance", "GST", "VAT", "payroll"],
    "construction": ["contractor", "construction", "builder", "roofing", "renovation", "remodel", "civil", "architect"],
    "salon": ["salon", "barber", "hair", "beauty", "nail", "spa", "waxing", "grooming", "threading"],
    "fitness": ["gym", "fitness", "yoga", "personal trainer", "crossfit", "pilates", "zumba", "wellness"],
    "education": ["school", "tutor", "tutoring", "academy", "training", "coaching", "university", "institute", "college"],
    "tech": ["software", "IT", "technology", "app developer", "web design", "programming", "startup", "SaaS", "data"],
    "photography": ["photographer", "photography", "videographer", "wedding photo", "studio"],
    "landscaping": ["landscaping", "lawn", "garden", "tree service", "pest control", "nursery"],
    "automotive": ["auto", "car", "mechanic", "garage", "tires", "oil change", "dealership", "service centre", "workshop"],
    "pet": ["vet", "veterinary", "pet store", "grooming", "dog", "animal", "kennel"],
    "insurance": ["insurance", "broker", "coverage", "policy", "life insurance", "health insurance", "motor insurance"],
    "retail": ["shop", "store", "retail", "boutique", "supermarket", "mart", "outlet", "showroom", "kiosk"],
    "ecommerce": ["ecommerce", "online store", "products", "wholesale", "dropship"],
    "logistics": ["shipping", "logistics", "delivery", "freight", "courier", "trucking", "warehouse", "supply chain"],
    "hotel": ["hotel", "motel", "resort", "guest house", "inn", "hostel", "bed and breakfast"],
    "event": ["event", "wedding planner", "party", "decorator", "caterer", "venue", "conference", "exhibition"],
    "financial": ["bank", "finance company", "loan", "credit", "investment", "wealth", "mutual fund", "trading"],
}

INDUSTRY_TEMPLATES = {
    "dental": {
        "subjects": [
            "⚠️ {name} — are you losing patients to competitors nearby?",
            "Most dental practices miss 60% of new-patient enquiries — here's why",
            "3 patients chose a competitor over {name} this week (fixable)",
        ],
        "openers": [
            "I was researching dental practices in your area and noticed {name} has a strong reputation — but most practices like yours quietly lose new patients simply because enquiries go unanswered after hours.",
            "Did you know the average dental practice misses over 60% of inbound calls during peak hours? I help practices like {name} capture those patients automatically — without adding staff.",
            "I came across {name} and wanted to share something important: your competitors are actively targeting patients in your postcode right now. The good news is there's a straightforward fix.",
        ],
        "cta": "I'd love to show you exactly how we've helped dental practices add 15–30 new patients per month. Can we schedule a 15-minute call this week?",
    },
    "legal": {
        "subjects": [
            "High-value clients are searching for {name}'s services — are you visible?",
            "Law firms that don't do this lose £50k+ in cases every year",
            "Urgent: your firm's online presence may be costing you clients",
        ],
        "openers": [
            "I came across {name} while researching top law firms in the area. Your reputation is clearly strong — but high-value clients searching online right now may not be finding you first.",
            "Most law firms lose significant revenue not because of poor service, but because their lead pipeline is invisible to the right clients. I specialize in changing that for firms like {name}.",
            "I work exclusively with law firms and solicitors to ensure they're the first choice clients see when they need representation — before they contact a competitor.",
        ],
        "cta": "I'd like to share a brief audit of where {name} stands online. Could we find 15 minutes this week? The insight alone will be valuable.",
    },
    "medical": {
        "subjects": [
            "Patients in your area can't find {name} online — let's change that",
            "Every missed call to {name} is a patient choosing another practice",
            "How top clinics in your city are filling appointment slots 30 days out",
        ],
        "openers": [
            "I came across {name} and can see you're providing real value to patients. The challenge most medical practices face today is that patients won't wait — if they can't book instantly, they move on.",
            "Healthcare has changed: 78% of patients now search online before booking. I help medical practices like {name} show up first and convert those searches into confirmed appointments.",
            "I work with GP practices and specialist clinics to help them reduce no-shows, fill gaps in their schedule, and attract the right patients consistently.",
        ],
        "cta": "Would 15 minutes this week be possible? I'd like to walk you through exactly what we've done for similar practices nearby.",
    },
    "pharma": {
        "subjects": [
            "Is {name} capturing every prescription referral opportunity?",
            "Pharmacies that do this see 40% more walk-in customers",
            "Quick question about {name}'s visibility to local patients",
        ],
        "openers": [
            "I came across {name} and noticed an opportunity most pharmacies and medicine suppliers overlook: their best customers are already searching for them online — but finding competitors instead.",
            "Pharmacies and dispensaries that optimise their local presence see dramatically more walk-ins and repeat customers. I help businesses like {name} capture that demand before it goes elsewhere.",
            "I specialise in helping pharmacies and healthcare businesses build a steady stream of new customers through targeted outreach and better local visibility.",
        ],
        "cta": "Could we get on a quick call this week? I'd love to share what's working for pharmacies like {name} right now.",
    },
    "clinic": {
        "subjects": [
            "Patients near {name} are booking elsewhere — here's why",
            "One simple change tripled appointment bookings for a clinic like {name}",
            "Is {name} appearing when local patients search for your services?",
        ],
        "openers": [
            "I came across {name} and wanted to ask: when a patient nearby searches for your specialty right now, does your clinic appear? If not, you're losing bookings every single day.",
            "I help diagnostic centres, clinics, and healthcare providers fill their appointment books consistently — without relying on walk-ins or word of mouth alone.",
            "Clinics that proactively reach patients online are fully booked weeks in advance. I'd love to show you how {name} can achieve the same.",
        ],
        "cta": "Are you available for a quick 15-minute call this week? I'll share specific insights for {name} at no charge.",
    },
    "restaurant": {
        "subjects": [
            "Tables sitting empty at {name}? This is how to fix it",
            "How {name} can add 50+ covers a week without extra marketing spend",
            "Restaurants in your area are fully booked — here's their secret",
        ],
        "openers": [
            "I came across {name} and love what you're doing — great food deserves a full house every night. Most restaurants lose 20–30% of potential bookings simply because hungry customers can't find them at the right moment.",
            "I help restaurants and cafes turn their quiet nights into fully booked evenings through smart, targeted outreach to local food lovers who are already looking for somewhere like {name}.",
            "I was researching top dining spots in the area and {name} stood out. The restaurants that consistently pack out are doing one thing differently — and it's simpler than you'd expect.",
        ],
        "cta": "Could we jump on a quick 15-minute call? I'd love to share the specific strategy that's working for restaurants in your area right now.",
    },
    "real_estate": {
        "subjects": [
            "Buyers and sellers in your area can't find {name} — urgent",
            "How top estate agents close 3× more deals with one change",
            "{name}: the market is moving fast — are your leads keeping up?",
        ],
        "openers": [
            "I came across {name} and can see you're active in the local property market. The challenge for most agents right now is that serious buyers and sellers are searching online 24/7 — and the first agent they contact usually wins.",
            "Property enquiries move fast. I help estate agents and property dealers like {name} be the first call serious buyers and sellers make — not the third or fourth.",
            "In today's market, {name}'s reputation is your best asset. I specialise in making sure that reputation reaches the right people at the exact moment they're ready to act.",
        ],
        "cta": "I'd love to share a 10-minute overview of what's working for agencies like {name} right now. Would this week suit you?",
    },
    "retail": {
        "subjects": [
            "Shoppers near {name} are buying from competitors online — stop the leak",
            "How local shops like {name} are winning back customers from big chains",
            "{name}: here's how to turn window-shoppers into loyal buyers",
        ],
        "openers": [
            "I came across {name} and can see you're running a solid operation. The reality for retail today is fierce — online giants and big chains are competing for your local customers every single day.",
            "I help independent shops and retail businesses like {name} build a loyal customer base that keeps coming back, without spending a fortune on advertising.",
            "Local businesses that connect personally with their customers are outperforming big chains on repeat business. I'd love to show you how {name} can do the same.",
        ],
        "cta": "Could we find 15 minutes this week? I'll share exactly how similar shops in your area are growing their loyal customer base right now.",
    },
    "construction": {
        "subjects": [
            "{name}: high-value projects in your area are going to competitors",
            "Contractors who do this win 2× more tenders",
            "Homeowners in your area can't find {name} — let's fix that",
        ],
        "openers": [
            "I came across {name} while looking for top contractors in the area — your work clearly speaks for itself. The challenge is that homeowners and developers with big projects search online first, and the contractor they find first usually wins the job.",
            "I work specifically with builders and contractors to help them secure a consistent pipeline of quality projects — so you're never relying on referrals alone.",
            "Most skilled contractors undersell themselves online. I help businesses like {name} showcase their expertise to the right clients at exactly the moment those clients are ready to hire.",
        ],
        "cta": "Would you have 10 minutes for a call this week? I'd love to share what's working for contractors in your area.",
    },
    "salon": {
        "subjects": [
            "Empty appointment slots at {name}? Here's a fast fix",
            "How salons like {name} fill their books 4 weeks in advance",
            "Your next 50 loyal clients are searching for {name} right now",
        ],
        "openers": [
            "I came across {name} and love what you're doing — great service deserves a fully booked diary. Most salons lose 30–40% of potential appointments simply because new clients can't find them when they're searching.",
            "I help salons, spas, and beauty businesses fill appointment gaps and build a base of loyal returning clients through smart, targeted outreach.",
            "In the beauty industry, the first business a customer finds online is almost always the one they book with. I make sure that business is {name}.",
        ],
        "cta": "Could we get on a quick 15-minute call? I'll share the exact approach filling salons like {name} 4–6 weeks out.",
    },
    "fitness": {
        "subjects": [
            "Members in your area are joining competitors — here's why",
            "How gyms like {name} add 30+ new members a month without paid ads",
            "{name}: January is coming — are you ready to capture the rush?",
        ],
        "openers": [
            "I came across {name} and can see you're building something great. The fitness industry is incredibly competitive — the gyms that win aren't always the best equipped, they're the ones that reach potential members first.",
            "I help gyms, yoga studios, and fitness businesses grow their membership base through targeted outreach to locals who are actively looking for a place to train.",
            "People within 3 miles of {name} are searching for a gym right now. I want to make sure they find you — not a competitor.",
        ],
        "cta": "Are you free for a quick 15-minute call this week? I'd love to share what's driving consistent member growth for fitness businesses like {name}.",
    },
    "accounting": {
        "subjects": [
            "Tax season costs {name}'s clients more than it should — here's why",
            "Accountants who add this service retain 40% more clients",
            "Quick question about {name}'s client pipeline this quarter",
        ],
        "openers": [
            "I came across {name} and can see you're running a respected practice. Most accounting firms struggle not with the quality of their work, but with attracting the right new clients consistently.",
            "I work with accountants and bookkeeping firms to build a predictable flow of new client enquiries — so your pipeline is always healthy, not just around tax deadlines.",
            "Business owners in your area are actively looking for a trusted accountant right now. I can help make sure they find {name} first.",
        ],
        "cta": "Would 15 minutes this week work for a quick call? I'd love to share what's working for accounting practices in your area.",
    },
    "cleaning": {
        "subjects": [
            "Property managers in your area need {name} — do they know you?",
            "Cleaning businesses that do this double their contracts in 90 days",
            "{name}: commercial clients are searching for your services right now",
        ],
        "openers": [
            "I came across {name} and can see you're delivering quality service. Most cleaning businesses grow slowly because their best potential clients — offices, property managers, facilities teams — simply don't know they exist.",
            "I help cleaning companies and facility service businesses like {name} connect with commercial clients who need regular, reliable cleaning contracts.",
            "There are property managers and business owners in your area right now actively looking for a dependable cleaning partner. I want to make sure they call {name} first.",
        ],
        "cta": "Could we get on a quick 15-minute call this week? I'll explain exactly how we're filling contract books for cleaning businesses like {name}.",
    },
    "marketing": {
        "subjects": [
            "Collaboration opportunity: {name} + our client portfolio",
            "Agency owners doing this are adding £10k/month in retainers",
            "Quick question for {name}'s team — potential client referral",
        ],
        "openers": [
            "I came across {name} and was genuinely impressed by your work. I manage lead generation for a network of businesses that regularly need exactly the services you offer — and I'd love to explore whether there's a referral or white-label opportunity.",
            "I work with marketing agencies and digital consultancies to help them secure high-value retainer clients who are actively ready to invest. No cold pitching — just pre-qualified introductions.",
            "Most agencies spend more time chasing clients than serving them. I've built a model that reverses that — and I think it could work really well for {name}.",
        ],
        "cta": "Would you be open to a quick 20-minute call? I'd love to explore whether there's a genuine fit — and if not, I'll say so honestly.",
    },
    "tech": {
        "subjects": [
            "Companies in your sector need {name}'s tech — do they know you exist?",
            "How tech businesses like {name} shorten their sales cycle by 60%",
            "Your next enterprise client is searching online right now",
        ],
        "openers": [
            "I came across {name} and was impressed by your technical capability. The challenge for most technology businesses isn't the product — it's consistently reaching decision-makers who are ready to buy.",
            "I help software companies and IT service providers build a pipeline of qualified enterprise enquiries — so your team spends time closing, not cold calling.",
            "Businesses in your target market are evaluating vendors right now. I specialise in making sure tech companies like {name} are on their shortlist.",
        ],
        "cta": "I'd love to share a specific strategy that's working for tech companies in your space. Are you free for a 20-minute call this week?",
    },
    "hotel": {
        "subjects": [
            "Travellers near {name} are booking competitors — here's why",
            "How hotels like {name} increase direct bookings by 35%",
            "{name}: your best guests are searching online right now",
        ],
        "openers": [
            "I came across {name} and can see you offer a great experience. The hospitality industry is incredibly competitive online — travellers book within minutes, and the property they find first almost always wins.",
            "I help hotels and guesthouses reduce reliance on OTA platforms and build direct bookings from guests who are ready to travel.",
            "Most hotels pay 15–25% commission on every booking through booking platforms. I help properties like {name} capture those guests directly — and keep that margin.",
        ],
        "cta": "Could we find 15 minutes this week? I'd love to share the direct booking strategy that's working for hotels in your market.",
    },
    "insurance": {
        "subjects": [
            "Policy renewals {name} is missing this quarter — urgent",
            "Insurance brokers who do this retain 80% of clients year on year",
            "Your next 30 policy clients are searching online right now",
        ],
        "openers": [
            "I came across {name} and wanted to reach out about something most insurance businesses overlook: potential clients researching policies online convert within 48 hours — whoever reaches them first wins.",
            "I help insurance brokers and agents build a consistent flow of new policy enquiries through targeted outreach to people actively comparing options in your area.",
            "In insurance, trust is everything. I help businesses like {name} reach the right customers at the right moment — before they commit to a competitor.",
        ],
        "cta": "Would a 15-minute call this week work? I'll share the specific outreach model that's driving new policy enquiries for brokers like {name}.",
    },
    "financial": {
        "subjects": [
            "High-net-worth clients near {name} are looking for an advisor",
            "One conversation could be worth £50k+ to {name} this quarter",
            "Are {name}'s potential clients finding you — or your competitors?",
        ],
        "openers": [
            "I came across {name} and can see you offer serious financial expertise. The challenge most financial service businesses face is that their best potential clients — those with real assets to manage — are searching quietly online before committing to anyone.",
            "I help financial advisors, wealth managers, and investment firms build relationships with high-value clients who are actively looking for trusted guidance.",
            "Trust in financial services takes time to build — but it has to start somewhere. I help businesses like {name} get in front of the right people at exactly the moment they're ready to act.",
        ],
        "cta": "I'd love to share how we're connecting financial firms with pre-qualified client enquiries. Are you free for 20 minutes this week?",
    },
    "event": {
        "subjects": [
            "Couples planning weddings in your area can't find {name}",
            "Event planners who do this are booked 12 months out",
            "{name}: peak season is coming — is your diary full?",
        ],
        "openers": [
            "I came across {name} and love the work you do — events and occasions deserve the best. The problem is that most couples and corporate clients search online and book whoever appears first, not necessarily the best.",
            "I help event planners, wedding decorators, and venue coordinators fill their calendars well ahead of peak season through targeted outreach to clients who are actively planning right now.",
            "There are people in your area planning weddings, corporate events, and celebrations right now. I make sure the first business they contact is {name}.",
        ],
        "cta": "Could we get on a quick call this week? I'll walk you through the booking strategy that's filling event businesses like {name} months in advance.",
    },
    "logistics": {
        "subjects": [
            "E-commerce businesses in your area need {name}'s shipping capacity",
            "Logistics partners who do this secure 5-year contracts",
            "{name}: companies are right now searching for reliable delivery partners",
        ],
        "openers": [
            "I came across {name} and can see you have strong operational capability. The businesses that need reliable logistics partners most urgently are often the hardest to reach — I can change that for {name}.",
            "I help logistics, courier, and freight businesses build relationships with e-commerce brands and manufacturers who need dependable shipping partnerships.",
            "Supply chain reliability is the biggest concern for growing businesses right now. I help logistics companies like {name} position themselves as the obvious, trusted choice.",
        ],
        "cta": "Would 15 minutes this week work? I'd love to share how we're connecting logistics providers like {name} with high-volume shippers in your region.",
    },
    "automotive": {
        "subjects": [
            "Car owners near {name} are choosing a competitor for their next service",
            "Workshops doing this see 50+ new customers per month",
            "{name}: your next 100 loyal customers are searching for you right now",
        ],
        "openers": [
            "I came across {name} and can see you're running a solid operation. Most automotive workshops and dealerships lose new customers before they even walk through the door — simply because a competitor was easier to find online.",
            "I help garages, workshops, and dealerships like {name} attract a steady flow of new customers and turn first-time visitors into loyal regulars.",
            "Car owners in your area are searching for a trusted mechanic or dealership right now. I want to make sure the business they find is {name}.",
        ],
        "cta": "Could we find 15 minutes this week? I'd love to share the specific strategy that's adding new customers for automotive businesses like {name}.",
    },
    "education": {
        "subjects": [
            "Students in your area are choosing other institutes over {name}",
            "Coaching centres that do this triple enrolments in one term",
            "{name}: exam season is approaching — are your seats full?",
        ],
        "openers": [
            "I came across {name} and can see you're building something really valuable. The challenge for most coaching centres and academies is that parents and students search online and enrol quickly — usually with whoever they find first.",
            "I help educational institutes, tutors, and training academies fill their seats consistently through targeted outreach to parents and students who are actively looking to enrol.",
            "Enrolment decisions happen fast — especially before key exam seasons. I specialise in making sure {name} is the first and most trusted option families consider.",
        ],
        "cta": "Are you free for a quick 15-minute call? I'd love to share the enrolment strategy that's working for institutes like {name} right now.",
    },
    "default": {
        "subjects": [
            "{name} is losing customers to competitors — here's what to do",
            "One conversation could change {name}'s growth trajectory this quarter",
            "Quick question for {name} — potential opportunity worth exploring",
        ],
        "openers": [
            "I came across {name} and can see you're doing great work. I wanted to reach out because I believe there's a real opportunity to help you reach more of the right customers at exactly the moment they're ready to buy.",
            "I specialise in helping businesses like {name} build a consistent pipeline of new customer enquiries — without relying purely on referrals or paid advertising.",
            "I was researching businesses in your space and {name} stood out. I'd like to share an approach that's generating strong results for similar businesses right now.",
        ],
        "cta": "Would you be open to a quick 15-minute call this week? I'll share something specific and genuinely useful for {name} — and if it's not a fit, I'll say so honestly.",
    },
}


def detect_industry(search_phrase: str) -> str:
    """Detect the industry from a search phrase."""
    phrase_lower = search_phrase.lower()
    best_match = "default"
    best_score = 0
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in phrase_lower:
                score = len(kw)
                if score > best_score:
                    best_score = score
                    best_match = industry
    return best_match


_URGENCY_CLOSERS = [
    "Spots for new clients this month are limited — I'd love to make sure {name} gets priority.",
    "I'm only working with a handful of new businesses this quarter, so please do reach out soon if this sounds relevant.",
    "The businesses that act on this now will have a clear advantage over competitors who wait.",
]

_SOCIAL_PROOF = [
    "We've already helped similar businesses in your area see measurable results within 30 days.",
    "Several businesses just like {name} have seen a significant uplift in enquiries after one short conversation with us.",
    "The approach I'd share has been proven across dozens of businesses in your industry — the results speak for themselves.",
]

def generate_email_templates(search_phrase: str, count: int = 3) -> list:
    """
    Generate high-conversion email subject + body templates for a search phrase.
    Uses industry-specific hooks, urgency triggers, and social proof.
    Returns list of {subject, body, industry} dicts.
    """
    industry = detect_industry(search_phrase)
    tmpl = INDUSTRY_TEMPLATES.get(industry, INDUSTRY_TEMPLATES["default"])

    templates = []
    subjects = tmpl["subjects"]
    openers = tmpl["openers"]
    cta = tmpl["cta"]

    for i in range(min(count, len(subjects))):
        subject = subjects[i % len(subjects)]
        opener = openers[i % len(openers)]
        social_proof = _SOCIAL_PROOF[i % len(_SOCIAL_PROOF)]
        urgency = _URGENCY_CLOSERS[i % len(_URGENCY_CLOSERS)]

        body = (
            f"Hi {{name}},\n\n"
            f"{opener}\n\n"
            f"{social_proof}\n\n"
            f"{cta}\n\n"
            f"{urgency}\n\n"
            f"Best regards,\n{{from_name}}"
        )
        templates.append({"subject": subject, "body": body, "industry": industry})

    return templates


# ─── Lead scoring ──────────────────────────────────────────────────────────────

def score_lead(lead) -> int:
    """
    Score a lead 0–100 based on data quality.
    Points breakdown:
      - Has name:    10
      - Has email:   35
      - Has phone:   25
      - Has website: 20
      - Has address: 10
    """
    score = 0
    name = (lead.get("name") or "").strip()
    email = (lead.get("email") or "").strip()
    phone = (lead.get("phone") or "").strip()
    website = (lead.get("website") or "").strip()
    address = (lead.get("address") or "").strip()

    if name and len(name) > 2:
        score += 10
    if email and "@" in email:
        # Bonus for business emails (not gmail/yahoo/hotmail)
        domain = email.split("@")[-1].lower()
        if domain not in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"}:
            score += 40
        else:
            score += 30
    if phone:
        cleaned = re.sub(r"[^\d]", "", phone)
        if len(cleaned) >= 7:
            score += 25
    if website and website.startswith("http"):
        score += 15
        # Bonus for having own domain
        from urllib.parse import urlparse
        try:
            netloc = urlparse(website).netloc
            if netloc and not any(x in netloc for x in ["facebook", "instagram", "twitter", "linkedin"]):
                score += 5
        except Exception:
            pass
    if address:
        score += 10

    return min(100, score)


def score_lead_label(score: int) -> str:
    """Return a quality label for a score."""
    if score >= 80:
        return "hot"
    elif score >= 50:
        return "warm"
    elif score >= 25:
        return "cold"
    else:
        return "weak"


def score_lead_color(score: int) -> str:
    """Return a CSS color class for the score."""
    if score >= 80:
        return "text-success"
    elif score >= 50:
        return "text-primary"
    elif score >= 25:
        return "text-muted"
    else:
        return "text-danger"


# ─── Duplicate detection ───────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Normalize a business name for comparison."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in ["llc", "ltd", "inc", "corp", "co", "company", "the ", "and ", "&"]:
        name = name.replace(suffix, "")
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def similarity_score(a: str, b: str) -> float:
    """Return string similarity ratio 0.0–1.0."""
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def find_duplicates(leads: list, threshold: float = 0.85) -> list:
    """
    Find pairs of likely-duplicate leads.
    Returns list of (index_a, index_b, similarity) tuples.
    Efficient O(n) grouping by first-3-chars, then O(group²) similarity.
    """
    duplicates = []
    groups = {}
    for i, lead in enumerate(leads):
        name = _normalize_name(lead.get("name", ""))
        if not name:
            continue
        key = name[:3]
        groups.setdefault(key, []).append(i)

    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        for a_pos, i in enumerate(idxs):
            for j in idxs[a_pos + 1:]:
                name_a = leads[i].get("name", "")
                name_b = leads[j].get("name", "")
                sim = similarity_score(name_a, name_b)
                if sim >= threshold:
                    # Also check website/email match for confirmation
                    website_a = leads[i].get("website", "")
                    website_b = leads[j].get("website", "")
                    if website_a and website_b and website_a == website_b:
                        sim = max(sim, 0.99)
                    duplicates.append((i, j, round(sim, 2)))

    return duplicates


# ─── Smart AI tips ─────────────────────────────────────────────────────────────

def generate_smart_tips(stats: dict) -> list:
    """
    Generate contextual AI-powered tips based on platform stats.
    More nuanced than the basic version in views.py.
    """
    tips = []
    total = stats.get("total_leads", 0)
    emails = stats.get("leads_with_email", 0)
    phones = stats.get("leads_with_phone", 0)
    campaigns = stats.get("campaigns", 0)
    active = stats.get("active_jobs", 0)
    smtp = stats.get("smtp_profiles", 0)

    # Email rate analysis
    if total > 0:
        email_rate = emails / total
        phone_rate = phones / total

        if email_rate < 0.1 and total > 20:
            tips.append({
                "icon": "🎯",
                "type": "warning",
                "text": f"Only {int(email_rate*100)}% of your leads have emails. Use Search Engine Scraper to enrich — it visits websites to extract real contact emails.",
            })
        elif email_rate > 0.7:
            tips.append({
                "icon": "🔥",
                "type": "success",
                "text": f"Excellent! {int(email_rate*100)}% email coverage. Your leads are campaign-ready. Create a campaign now!",
            })

        if phone_rate > 0.5 and email_rate < 0.3:
            tips.append({
                "icon": "📞",
                "type": "info",
                "text": f"You have {phones:,} phone numbers but fewer emails. Consider a cold-call outreach or try scraping their websites for emails.",
            })

    # Campaign suggestions
    if emails > 0 and campaigns == 0:
        tips.append({
            "icon": "✉️",
            "type": "success",
            "text": f"You have {emails:,} leads with emails but no campaigns yet. Create your first campaign — it takes under 2 minutes!",
        })
    elif emails > 50 and campaigns > 0 and smtp == 0:
        tips.append({
            "icon": "🔑",
            "type": "warning",
            "text": "Save your SMTP credentials as a profile to speed up campaign setup. Go to SMTP Profiles in the sidebar.",
        })

    # Load management
    if active > 3:
        tips.append({
            "icon": "⚡",
            "type": "warning",
            "text": f"{active} jobs running simultaneously may trigger rate limits. Slow-speed jobs are safer for large targets.",
        })

    # Empty state onboarding
    if total == 0:
        tips.append({
            "icon": "🚀",
            "type": "info",
            "text": "Welcome! Start with a Google Maps or Bing Maps scrape to collect local business leads. Then use Search Engines to enrich them with emails.",
        })

    # Scale suggestion
    if total > 5000:
        tips.append({
            "icon": "📊",
            "type": "info",
            "text": f"You have {total:,} leads! Consider segmenting them by source or job before sending campaigns for better deliverability.",
        })

    return tips[:3]


# ─── Email personalization helpers ────────────────────────────────────────────

def personalize_subject(template: str, lead: dict) -> str:
    """Fill subject line placeholders with lead data."""
    return template.format(
        name=lead.get("name", "there"),
        email=lead.get("email", ""),
        phone=lead.get("phone", ""),
        website=lead.get("website", ""),
        location=lead.get("location", ""),
    )


def personalize_body(template: str, lead: dict, from_name: str = "") -> str:
    """Fill body placeholders with lead data."""
    return template.format(
        name=lead.get("name", "there"),
        email=lead.get("email", ""),
        phone=lead.get("phone", ""),
        website=lead.get("website", ""),
        location=lead.get("location", ""),
        from_name=from_name or "the team",
    )
