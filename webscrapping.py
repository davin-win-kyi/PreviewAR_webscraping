#!/usr/bin/env python3
"""
select_product_metadata.py

UPDATED to:
- Benchmark runtime per item (seconds)
- Write outputs/runtimes.json with:
    {
      "runtimes": [{"link":..., "object_type":..., "runtime_sec":..., "status":"ok|fail"}],
      "average_runtime_sec": <float|null>
    }

Input JSON:
{
  "furniture": [
    {"link": "...", "object_type": "mug"},
    {"link": "...", "object_type": "couch"}
  ]
}

NOTE:
- All "best image" logic has been removed:
  - No image ranking
  - No image downloading/saving
  - We only keep the scraped image_urls list
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Optional: load .env if present
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

try:
    from extract_url_info import extract_with_gpt5
except ImportError:
    print("ERROR: Could not import extract_with_gpt5 from extract_url_info.py", file=sys.stderr)
    raise

try:
    from generic_web_scraper import main as scrape_main
except ImportError:
    print("ERROR: Could not import main from generic_web_scraper.py", file=sys.stderr)
    raise

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: Please `pip install openai`.", file=sys.stderr)
    raise


MODEL_DIM_EXTRACTOR = "gpt-5"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
    sys.exit(1)

client = OpenAI(api_key=OPENAI_API_KEY)


def normalize_input_url(u: str) -> str:
    """
    Ensure Selenium gets a valid absolute URL.
    Fixes common cases like 'amazon.com/...' -> 'https://www.amazon.com/...'
    """
    u = (u or "").strip()
    if not u:
        return u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("amazon.com/"):
        return "https://www." + u
    if u.startswith("www.amazon.com/"):
        return "https://" + u
    if u.startswith("www."):
        return "https://" + u
    return "https://" + u


def normalize_url_list(urls: List[str], max_images: Optional[int] = None) -> List[str]:
    """Dedupes, strips, basic filtering; optionally truncate."""
    seen = set()
    cleaned: List[str] = []
    for u in urls:
        if not isinstance(u, str):
            continue
        s = u.strip()
        if not s or s in seen:
            continue
        parsed = urlparse(s)
        if parsed.scheme not in ("http", "https"):
            continue
        seen.add(s)
        cleaned.append(s)
        if max_images and len(cleaned) >= max_images:
            break
    return cleaned


def choose_dimensions_with_gpt(
    potential_dimension_values: List[str],
    model: str = MODEL_DIM_EXTRACTOR,
) -> Dict[str, Optional[float | str]]:
    """
    Extract product/item dimensions (NOT package/shipping) from noisy strings.

    Returns:
      {
        "length": float|null,
        "width": float|null,
        "height": float|null,
        "source_string": str|null
      }
    """
    user = (
        "You are extracting product dimensions from noisy candidate strings.\n\n"
        "CANDIDATE STRINGS:\n"
        + "\n".join(f"- {s}" for s in potential_dimension_values)
        + "\n\n"
        "GOAL:\n"
        "Return the PRODUCT/ITEM dimensions (NOT package/shipping).\n\n"
        "IMPORTANT:\n"
        "- It is valid to return PARTIAL dimensions.\n"
        "- If only width and height are present, return those and set length = null.\n"
        "- If only one dimension is present, return it and set the others to null.\n"
        "- Do NOT guess missing axes.\n\n"
        "AXIS MAPPING RULES:\n"
        "- D (depth) or L (length) -> length\n"
        "- W (width) -> width\n"
        "- H (height) -> height\n\n"
        "UNIT NORMALIZATION:\n"
        "- If inches are shown (\", in, inches), keep inches.\n"
        "- If cm, convert using 1 in = 2.54 cm.\n"
        "- If mm, convert using 25.4 mm = 1 in.\n\n"
        "STRICT OUTPUT FORMAT (valid JSON only):\n"
        "{\n"
        '  "length": number|null,\n'
        '  "width": number|null,\n'
        '  "height": number|null,\n'
        '  "source_string": string|null\n'
        "}\n"
    )

    resp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": user}],
    )

    try:
        data: Dict[str, Any] = json.loads(resp.output_text)
    except Exception:
        data = {}

    def _num(x: Any) -> Optional[float]:
        if x is None:
            return None
        try:
            return float(x)
        except Exception:
            return None

    return {
        "length": _num(data.get("length")),
        "width": _num(data.get("width")),
        "height": _num(data.get("height")),
        "source_string": data.get("source_string"),
    }


def process_product_url(
    url: str,
    *,
    object_type: Optional[str] = None,
    max_images: int = 30,
    print_scrape: bool = False,
    output_dir: str | Path = "outputs",
    model: str = MODEL_DIM_EXTRACTOR,
    save_result_json: bool = True,
    result_json_filename: str = "selection_result.json",
) -> Dict[str, Any]:
    """
    Collect metadata + image URL list + dimensions.
    (No best-image selection and no image downloads.)
    """
    output_dir = Path(output_dir)

    info = extract_with_gpt5(url)
    if not isinstance(info, dict):
        raise RuntimeError("extract_with_gpt5 did not return a dict.")

    company = info.get("company_name") or ""
    seed_names = info.get("product_name") or []
    if not isinstance(seed_names, list):
        seed_names = [str(seed_names)]

    scrape_payload = scrape_main(url, company)

    # common scraper formats: tuple/list/dict/str
    if isinstance(scrape_payload, tuple) and len(scrape_payload) > 0:
        scrape_payload = scrape_payload[0]
    if isinstance(scrape_payload, list) and scrape_payload:
        scrape_payload = scrape_payload[0]

    if isinstance(scrape_payload, str):
        data = json.loads(scrape_payload)
    elif isinstance(scrape_payload, dict):
        data = scrape_payload
    else:
        raise RuntimeError(f"Unexpected scraper output format: {type(scrape_payload)}")

    if print_scrape:
        print(json.dumps(data, indent=2, ensure_ascii=False))

    product_title = data.get("product_title")
    high_level_description = data.get("high_level_description") or ""
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}

    # ensure attribute keys exist (stable schema)
    attributes.setdefault("color", None)
    attributes.setdefault("material", None)
    attributes.setdefault("style", None)
    attributes.setdefault("seating_capacity", None)
    attributes.setdefault("assembly_required", None)
    attributes.setdefault("special_features", [])

    image_urls = normalize_url_list(data.get("image_urls", []), max_images=max_images)

    dims = choose_dimensions_with_gpt(
        data.get("potential_dimension_values", []),
        model=model,
    )

    result: Dict[str, Any] = {
        "url": url,
        "object_type": object_type,
        "company_name": company,
        "product_name_seeds": seed_names,  # kept as-is from extract_with_gpt5
        "product_title": product_title,
        "high_level_description": high_level_description,
        "attributes": attributes,
        "dimensions": dims,
        "all_image_urls": image_urls,
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    if save_result_json:
        out_json = output_dir / result_json_filename
        out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        result["result_json_path"] = str(out_json)

    return result


def read_furniture_from_json(path: str | Path) -> List[Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON file not found: {p}")

    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Root JSON must be an object with key 'furniture'.")

    items = data.get("furniture")
    if not isinstance(items, list):
        raise ValueError("JSON must contain key 'furniture' with a list.")

    cleaned: List[Dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        link = it.get("link")
        obj = it.get("object_type")
        if isinstance(link, str) and link.strip() and isinstance(obj, str) and obj.strip():
            cleaned.append(
                {"link": normalize_input_url(link.strip()), "object_type": obj.strip().lower()}
            )

    if not cleaned:
        raise ValueError("No valid entries found in 'furniture' (need {link, object_type}).")

    return cleaned


def safe_folder_name(url: str, object_type: Optional[str] = None) -> str:
    prefix = (object_type or "item").strip().lower()
    u = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:80].strip("_")
    return f"{prefix}__{u}"[:90]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--links-json",
        default="target_links.json",
        help="Path to JSON file containing {'furniture': [{'link':..., 'object_type':...}, ...]}",
    )

    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--max-images", type=int, default=30)
    parser.add_argument("--print-scrape", action="store_true")
    parser.add_argument("--no-save-json", action="store_true")
    parser.add_argument("--model", default=MODEL_DIM_EXTRACTOR)

    args = parser.parse_args()

    try:
        items = read_furniture_from_json(args.links_json)
    except Exception as e:
        print(f"ERROR reading furniture JSON: {e}", file=sys.stderr)
        sys.exit(2)

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    all_results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    # runtime benchmarking
    runtime_rows: List[Dict[str, Any]] = []

    for it in items:
        url = it["link"]
        object_type = it["object_type"]

        per_output_dir = output_root / safe_folder_name(url, object_type)

        t0 = time.perf_counter()
        try:
            result = process_product_url(
                url,
                object_type=object_type,
                output_dir=per_output_dir,
                max_images=args.max_images,
                print_scrape=args.print_scrape,
                model=args.model,
                save_result_json=not args.no_save_json,
            )
            dt = time.perf_counter() - t0

            result["runtime_sec"] = dt
            all_results.append(result)

            runtime_rows.append(
                {"link": url, "object_type": object_type, "runtime_sec": dt, "status": "ok"}
            )
            print(f"[OK] ({object_type}) {url}  ({dt:.2f}s)")

        except Exception as e:
            dt = time.perf_counter() - t0
            failures.append({"url": url, "object_type": object_type, "error": str(e)})
            runtime_rows.append(
                {
                    "link": url,
                    "object_type": object_type,
                    "runtime_sec": dt,
                    "status": "fail",
                    "error": str(e),
                }
            )
            print(f"[FAIL] ({object_type}) {url}: {e}  ({dt:.2f}s)", file=sys.stderr)

    aggregate = {
        "n_items": len(items),
        "n_success": len(all_results),
        "n_failures": len(failures),
        "results": all_results,
        "failures": failures,
    }

    aggregate_path = output_root / "aggregate_results.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False))
    print(f"\nAggregate written to: {aggregate_path}")

    # write runtimes.json
    ok_times = [
        r["runtime_sec"]
        for r in runtime_rows
        if r.get("status") == "ok" and isinstance(r.get("runtime_sec"), (int, float))
    ]
    avg = (sum(ok_times) / len(ok_times)) if ok_times else None

    runtimes_out = {
        "runtimes": runtime_rows,
        "average_runtime_sec": avg,
    }

    runtimes_path = output_root / "runtimes.json"
    runtimes_path.write_text(json.dumps(runtimes_out, indent=2, ensure_ascii=False))
    print(f"Runtimes written to: {runtimes_path}")