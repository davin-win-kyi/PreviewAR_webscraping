#!/usr/bin/env python3
import os
import json
import sys
import re
from typing import Optional, Dict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import tldextract

# OpenAI SDK (Responses API)
from openai import OpenAI  # pip install openai

"""
Usage:
  export OPENAI_API_KEY=sk-...
  python extract_product_info.py "https://www.amazon.com/..."
"""

from dotenv import load_dotenv

load_dotenv()

def fetch_title(url: str, timeout: int = 10) -> Optional[str]:
    """Best-effort fetch of the page title to boost GPT accuracy."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        # Trim excessive whitespace
        if title:
            title = re.sub(r"\s+", " ", title)
        return title
    except Exception:
        return None

def domain_to_brand(url: str) -> Optional[str]:
    """
    Lightweight brand guess from domain (used as a hint, GPT still decides).
    """
    try:
        ext = tldextract.extract(url)
        domain = ext.domain
        if not domain:
            return None
        # Simple prettify: 'bestbuy' -> 'Best Buy'
        brand = re.sub(r"[-_]", " ", domain).strip()
        brand = brand.capitalize() if " " not in brand else " ".join(w.capitalize() for w in brand.split())
        # A few hand-tuned fixes
        fixes = {
            "Ikea": "IKEA",
            "Wayfair": "Wayfair",
            "Amazon": "Amazon",
            "Ebay": "eBay",
            "Best buy": "Best Buy",
            "Crateandbarrel": "Crate & Barrel",
        }
        return fixes.get(brand, brand)
    except Exception:
        return None

def extract_with_gpt5(url: str) -> Dict[str, str]:
    """
    Calls GPT-5 (Responses API) with structured outputs to extract:
    { "company_name": str, "product_name": str }
    """
    # Optional context to improve accuracy
    title = fetch_title(url)
    brand_hint = domain_to_brand(url)
    parsed = urlparse(url)

    # Build the prompt
    user_context = {
        "url": url,
        "hostname": parsed.hostname or "",
        "brand_hint_from_domain": brand_hint or "",
        "page_title": title or "",
        "instructions": (
            "Identify the e-commerce company/retailer hosting this product page "
            "and the names for the product being sold on the url page."
            "For example, names can be: sectional, couch, table, bed, chair, lamp, etc."
            "Output the information in the folllowing JSON format:"
            "{ \"company_name\": str, \"product_name\": list of str }"
        ),
    }

    client = OpenAI()  # Reads OPENAI_API_KEY from env

    # Strict JSON schema for structured outputs
    response = client.responses.create(
        model="gpt-5",
        input=[
            {
                "role": "user",
                "content": json.dumps(user_context),
            },
        ],
    )

    print("Output text: ", response.output_text)

    data = json.loads(response.output_text)
    # Minimal sanity check
    if not isinstance(data, dict) or "company_name" not in data or "product_name" not in data:
        raise ValueError("Model did not return the expected fields.")
    return {"company_name": data["company_name"], "product_name": data["product_name"]}

def main():
    url = (
        "https://www.amazon.com/Sectional-Minimalist-Upholstered-Couch%EF%BC%8CNo-Assembly/dp/B0DMSNCX14/ref=sr_1_1_sspa"
        "?crid=3Q0OC9EF9BOT2"
        "&dib=eyJ2IjoiMSJ9.Uwy_-hTxn36mxYatk6YVYoZzfr9ccOrbiBYTzPXlkhX20Xljw7XFV30e8JTA_UIVAcnSUfDH6SdliqACjdbtTxjItAW9S6wE3RCmOValBQUGnzlCgRtfgk4fa-PzKL8th62Cz6rAe5mruSurnxNcQ4vdjN_j0FIIIrxNqwaXdeeWa4zdYX7h608_MdeH7Xej50FqMcTQb_HicnZzBSAQVlt295PrnBXwNELEt5T-1MFOtNIs_4fB2vVpJb6X5ZdbREdGQxJexPzxwM9GK0X86-1R1IhzscV8fquOFk9dwMk.SxonPO9dTDRt6Xrhq1MNRk2KVFfS9rSsWmQ8r_nFdNE"
        "&dib_tag=se"
        "&keywords=couch"
        "&qid=1762054233"
        "&sprefix=couch%2Caps%2C195"
        "&sr=8-1-spons"
        "&sp_csd=d2lkZ2V0TmFtZT1zcF9hdGY"
        "&th=1"
    )
    result = extract_with_gpt5(url)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
