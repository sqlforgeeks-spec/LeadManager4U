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
    "dental": ["dentist", "dental", "orthodontist", "teeth", "smile"],
    "legal": ["lawyer", "attorney", "law firm", "legal", "solicitor", "barrister"],
    "medical": ["doctor", "physician", "clinic", "medical", "hospital", "health", "GP", "surgeon"],
    "restaurant": ["restaurant", "cafe", "food", "pizza", "burger", "bistro", "diner", "eatery", "bakery"],
    "plumbing": ["plumber", "plumbing", "pipes", "drain", "water heater"],
    "electrical": ["electrician", "electrical", "wiring", "HVAC", "AC repair"],
    "cleaning": ["cleaning", "cleaner", "maid", "janitorial", "housekeeping", "pressure wash"],
    "real_estate": ["real estate", "realtor", "property", "mortgage", "estate agent", "homes for sale"],
    "marketing": ["marketing", "SEO", "digital agency", "advertising", "social media", "PPC"],
    "accounting": ["accountant", "accounting", "bookkeeping", "CPA", "tax", "auditor"],
    "construction": ["contractor", "construction", "builder", "roofing", "renovation", "remodel"],
    "salon": ["salon", "barber", "hair", "beauty", "nail", "spa", "waxing"],
    "fitness": ["gym", "fitness", "yoga", "personal trainer", "crossfit", "pilates"],
    "education": ["school", "tutor", "tutoring", "academy", "training", "coaching", "university"],
    "tech": ["software", "IT", "technology", "app developer", "web design", "programming"],
    "photography": ["photographer", "photography", "videographer", "wedding photo"],
    "landscaping": ["landscaping", "lawn", "garden", "tree service", "pest control"],
    "automotive": ["auto", "car", "mechanic", "garage", "tires", "oil change", "dealership"],
    "pet": ["vet", "veterinary", "pet store", "grooming", "dog", "animal"],
    "insurance": ["insurance", "broker", "coverage", "policy", "life insurance"],
    "ecommerce": ["shop", "store", "ecommerce", "online store", "products", "wholesale"],
    "logistics": ["shipping", "logistics", "delivery", "freight", "courier", "trucking"],
}

INDUSTRY_TEMPLATES = {
    "dental": {
        "subjects": [
            "Quick question about your dental practice, {name}",
            "Helping dental practices like {name} attract more patients",
            "Partnership opportunity for {name}",
        ],
        "openers": [
            "I came across {name} and noticed you're providing excellent dental care.",
            "I specialize in working with dental practices to help them grow their patient base.",
            "Your dental practice caught my attention while I was researching top providers in the area.",
        ],
        "cta": "Would you be open to a quick 15-minute call to explore how we could help your practice attract more patients?",
    },
    "legal": {
        "subjects": [
            "Growing your law firm's client base — {name}",
            "A quick note for {name}'s team",
            "Helping law firms like {name} get more leads",
        ],
        "openers": [
            "I came across {name} and was impressed by your firm's reputation.",
            "I work specifically with law firms to help them generate more qualified client inquiries.",
            "As someone who follows the legal industry closely, I noticed {name} and wanted to reach out.",
        ],
        "cta": "Would you be interested in a brief call to discuss how we've helped similar firms grow?",
    },
    "restaurant": {
        "subjects": [
            "Helping {name} reach more local customers",
            "Quick idea for {name}",
            "Increasing foot traffic for {name}",
        ],
        "openers": [
            "I discovered {name} recently and love what you're doing with your menu.",
            "I help local restaurants and cafes attract more diners through targeted outreach.",
            "Your restaurant caught my attention and I wanted to share an idea that's worked well for similar businesses.",
        ],
        "cta": "Could we schedule a quick chat about how we could help bring more customers through your doors?",
    },
    "construction": {
        "subjects": [
            "More project leads for {name}",
            "Reaching out to {name} — construction opportunity",
            "Helping contractors like {name} grow",
        ],
        "openers": [
            "I came across {name} while looking for top contractors in the area.",
            "I work with construction companies and contractors to help them secure more projects.",
            "Your work caught my eye and I wanted to reach out with an idea.",
        ],
        "cta": "Would you have 10 minutes for a call to see if there's a fit?",
    },
    "marketing": {
        "subjects": [
            "Collaboration opportunity with {name}",
            "Quick idea for {name}'s clients",
            "A partnership that could benefit {name}",
        ],
        "openers": [
            "I came across {name} and was impressed by your portfolio.",
            "As someone in the digital space, I thought there might be a natural synergy between our work.",
            "I've been following agencies like {name} and wanted to explore a potential collaboration.",
        ],
        "cta": "Would you be open to a brief call to explore whether there's a mutual opportunity here?",
    },
    "default": {
        "subjects": [
            "Quick question for {name}",
            "Reaching out to {name}",
            "A note for {name}'s team",
        ],
        "openers": [
            "I came across {name} and wanted to reach out.",
            "I discovered {name} recently and thought it would be worth connecting.",
            "I've been researching businesses in your space and wanted to get in touch with {name}.",
        ],
        "cta": "Would you be open to a quick 10-minute call to explore if there's a way I can help?",
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


def generate_email_templates(search_phrase: str, count: int = 3) -> list:
    """
    Generate email subject + body template suggestions for a given search phrase.
    Returns list of {subject, body} dicts.
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
        body = (
            f"Hi {{name}},\n\n"
            f"{opener}\n\n"
            f"I wanted to reach out because I believe we could create real value together.\n\n"
            f"{cta}\n\n"
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
