"""
services/category_classifier.py
─────────────────────────────────
AI-powered invoice category classification using Groq.
Used by the orchestrator and WhatsApp invoice intake.
"""

import logging
from typing import Optional
import httpx

from core.config import settings

logger = logging.getLogger("autotwin_ai.category_classifier")

INVOICE_CATEGORIES = [
    "Cloud",
    "SaaS",
    "Infrastructure",
    "Payments",
    "Design",
    "CRM",
    "Productivity",
    "Monitoring",
    "Supplies",
    "Travel",
    "Food",
    "Utilities",
    "Other",
]

# Keyword rules for fast, offline classification before hitting the LLM
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["aws", "amazon web", "azure", "gcp", "google cloud", "cloudflare", "digitalocean", "linode", "vercel", "netlify"], "Cloud"),
    (["github", "gitlab", "jira", "linear", "notion", "confluence", "slack", "zoom", "loom"], "Productivity"),
    (["stripe", "razorpay", "paypal", "paytm", "cashfree", "braintree", "adyen"], "Payments"),
    (["salesforce", "hubspot", "zoho crm", "pipedrive", "freshsales"], "CRM"),
    (["figma", "adobe", "canva", "sketch", "invision", "zeplin"], "Design"),
    (["datadog", "new relic", "grafana", "sentry", "pagerduty", "splunk"], "Monitoring"),
    (["kubernetes", "docker", "terraform", "ansible", "jenkins", "circleci"], "Infrastructure"),
    (["office 365", "microsoft 365", "google workspace", "gsuite", "dropbox"], "SaaS"),
    (["electricity", "water", "internet", "broadband", "telephone", "airtel", "jio", "bsnl"], "Utilities"),
    (["flight", "hotel", "uber", "ola", "indigo", "makemytrip", "goibibo", "train", "railway"], "Travel"),
    (["restaurant", "swiggy", "zomato", "food", "canteen", "lunch", "dinner", "cafe"], "Food"),
    (["stationery", "printer", "toner", "paper", "pen", "office supplies", "furniture"], "Supplies"),
]


def _keyword_classify(vendor: str, hint: Optional[str] = None) -> Optional[str]:
    """Fast keyword-based classifier — no LLM call needed for obvious vendors."""
    text = f"{vendor} {hint or ''}".lower()
    for keywords, category in _KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            return category
    return None


def _extract_category_from_message(message: str) -> Optional[str]:
    """
    Check if the user explicitly stated a category in their message.
    Supports formats like:
      - "category: Cloud"
      - "Cloud expense"
      - "this is a travel invoice"
    """
    if not message:
        return None
    msg_lower = message.lower()
    for cat in INVOICE_CATEGORIES:
        cat_lower = cat.lower()
        if (
            f"category: {cat_lower}" in msg_lower
            or f"category:{cat_lower}" in msg_lower
            or f"{cat_lower} expense" in msg_lower
            or f"{cat_lower} invoice" in msg_lower
            or f"it's {cat_lower}" in msg_lower
            or f"its {cat_lower}" in msg_lower
        ):
            return cat
    return None


async def classify_invoice_category(
    vendor: str,
    amount: float = 0.0,
    message_hint: Optional[str] = None,
) -> str:
    """
    Classify an invoice into one of the predefined categories.

    Priority order:
    1. Explicit category in user message
    2. Keyword rules (instant, no API call)
    3. Groq LLM classification
    4. Fallback to "Other"
    """

    # 1. Explicit mention in message
    if message_hint:
        explicit = _extract_category_from_message(message_hint)
        if explicit:
            logger.info(f"Category from message hint: {explicit}")
            return explicit

    # 2. Keyword rules
    keyword_result = _keyword_classify(vendor, message_hint)
    if keyword_result:
        logger.info(f"Category from keyword rules: {keyword_result}")
        return keyword_result

    # 3. Groq LLM
    if not settings.GROQ_API_KEY:
        return "Other"

    prompt = (
        f"Classify this invoice into exactly ONE of these categories:\n"
        f"{', '.join(INVOICE_CATEGORIES)}\n\n"
        f"Vendor: {vendor}\n"
        f"Amount: {amount}\n"
        + (f"User note: {message_hint}\n" if message_hint else "")
        + "\nReply with ONLY the category name. No punctuation, no explanation."
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 10,
                },
            )
            res.raise_for_status()
            raw = res.json()["choices"][0]["message"]["content"].strip()
            for cat in INVOICE_CATEGORIES:
                if cat.lower() in raw.lower():
                    logger.info(f"Category from Groq: {cat}")
                    return cat
    except Exception as e:
        logger.error(f"Category classification via Groq failed: {e}")

    return "Other"
