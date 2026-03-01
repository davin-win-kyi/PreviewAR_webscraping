#!/usr/bin/env python3
"""
select_best_product_image.py

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
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
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

import requests
from PIL import Image

MODEL_ALIAS_EXPANDER = "gpt-5"
MODEL_IMAGE_RANKER = "gpt-5"

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


def expand_product_aliases_via_gpt5(seed_names: List[str]) -> List[str]:
    if not seed_names:
        return []

    prompt = (
        "You are helping expand concise product nouns for ranking images.\n"
        "Rules:\n"
        " - Return ONLY a JSON array of short names.\n"
        " - Include plural/singular variants if common (e.g., 'sofa','sofas').\n"
        " - Include things that you may also have with the object. For example couches may have pillows.\n"
        " - Exclude brands, model numbers, materials unless essential to identity.\n"
        " - Keep each item <= 3 words. No duplicates. Lowercase.\n\n"
        f"Seed names: {seed_names}\n"
    )

    resp = client.chat.completions.create(
        model=MODEL_ALIAS_EXPANDER,
        messages=[
            {"role": "system", "content": "You are a precise, terse product taxonomy assistant."},
            {"role": "user", "content": prompt},
        ],
    )

    content = (resp.choices[0].message.content or "").strip()

    aliases: List[str] = []
    try:
        obj = json.loads(content)
        if isinstance(obj, list):
            aliases = obj
        elif isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    aliases = v
                    break
    except Exception:
        aliases = []

    pool = {s.strip().lower() for s in seed_names if isinstance(s, str) and s.strip()}
    for a in aliases:
        if isinstance(a, str) and a.strip():
            pool.add(a.strip().lower())

    return sorted(pool)


def rank_images_with_gpt5(
    image_urls: List[str],
    product_names: List[str],
    dimensions: Optional[Dict[str, Any]] = None,
    object_type: Optional[str] = None,
) -> Dict[str, Any]:
    if not image_urls:
        return {"image_url": None, "reasoning": "No images provided.", "scores": {}}

    instruction = (
        "You are ranking candidate product images by how unobstructed the MAIN object is.\n"
        "Consider the product identity from the provided names and (optionally) the object_type.\n"
        "Measurement overlays are permitted.\n"
        "Hard rules:\n"
        " - Minimize objects covering/obscuring the main object (occlusions). Best is 0.\n"
        " - If tie: prefer front-facing, centered, entire object in frame.\n"
        " - Okay to have an image with measurement overlays.\n"
        " - Output strictly in JSON with keys: best_image_url, reasoning, scores.\n"
        "   Where 'scores' maps each URL to an object with: occlusion_score (integer; lower is better), notes.\n"
    )

    payload = {
        "object_type": object_type or "",
        "product_names": product_names,
        "dimensions_hint": dimensions or {},
        "image_urls": image_urls,
    }

    resp = client.chat.completions.create(
        model=MODEL_IMAGE_RANKER,
        messages=[
            {"role": "system", "content": "You are a meticulous product image judge."},
            {"role": "user", "content": instruction},
            {"role": "user", "content": f"Payload:\n{json.dumps(payload, ensure_ascii=False)}"},
        ],
    )

    content = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(content)
    except Exception:
        data = {}

    return {
        "image_url": data.get("best_image_url"),
        "reasoning": data.get("reasoning", ""),
        "scores": data.get("scores", {}),
    }


def choose_dimensions_with_gpt(
    potential_dimension_values: List[str],
    model: str = "gpt-5",
) -> Dict[str, Optional[float]]:
    client_local = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

    resp = client_local.responses.create(
        model=model,
        input=[{"role": "user", "content": user}],
    )

    try:
        data: Dict[str, Any] = json.loads(resp.output_text)
    except Exception:
        data = {}

    def _num(x):
        return None if x is None else float(x)

    return {
        "length": _num(data.get("length")),
        "width": _num(data.get("width")),
        "height": _num(data.get("height")),
        "source_string": data.get("source_string"),
    }


def save_best_image(image_url: str, out_path: str | Path = "best_image.png") -> str:
    if not image_url:
        raise ValueError("image_url is empty.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    r = requests.get(image_url, headers=headers, timeout=20)
    r.raise_for_status()

    img = Image.open(io.BytesIO(r.content))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format="PNG", optimize=True)
    return str(out_path)


def get_best_image_url(
    url: str,
    *,
    object_type: Optional[str] = None,
    max_images: int = 30,
    print_scrape: bool = False,
    output_dir: str | Path = "outputs",
    best_image_filename: str = "best_image.png",
    model: str = "gpt-5",
    save_result_json: bool = True,
    result_json_filename: str = "selection_result.json",
) -> Dict[str, Any]:
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

    expanded_names = expand_product_aliases_via_gpt5(seed_names)

    best = rank_images_with_gpt5(
        image_urls,
        expanded_names,
        dims,
        object_type=object_type,
    )

    result: Dict[str, Any] = {
        "url": url,
        "object_type": object_type,
        "company_name": company,
        "product_names": expanded_names,
        "product_title": product_title,
        "high_level_description": high_level_description,
        "attributes": attributes,
        "dimensions": dims,
        "all_image_urls": image_urls,
        "best_image": {
            "image_url": best.get("image_url"),
            "reasoning": best.get("reasoning"),
        },
        "scores": best.get("scores", {}),
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    if best.get("image_url"):
        out_img = output_dir / best_image_filename
        saved_path = save_best_image(best["image_url"], out_img)
        result["best_image"]["saved_path"] = str(saved_path)

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
    parser.add_argument("--best-image-filename", default="best_image.png")
    parser.add_argument("--max-images", type=int, default=30)
    parser.add_argument("--print-scrape", action="store_true")
    parser.add_argument("--no-save-json", action="store_true")
    parser.add_argument("--model", default="gpt-5")

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

    # --- NEW: runtime benchmarking ---
    runtime_rows: List[Dict[str, Any]] = []

    for it in items:
        url = it["link"]
        object_type = it["object_type"]

        per_output_dir = output_root / safe_folder_name(url, object_type)

        t0 = time.perf_counter()
        try:
            result = get_best_image_url(
                url,
                object_type=object_type,
                output_dir=per_output_dir,
                best_image_filename=args.best_image_filename,
                max_images=args.max_images,
                print_scrape=args.print_scrape,
                model=args.model,
                save_result_json=not args.no_save_json,
            )
            dt = time.perf_counter() - t0

            # attach runtime to per-item result too (handy)
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
                {"link": url, "object_type": object_type, "runtime_sec": dt, "status": "fail", "error": str(e)}
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

    # --- NEW: write runtimes.json ---
    ok_times = [r["runtime_sec"] for r in runtime_rows if r.get("status") == "ok" and isinstance(r.get("runtime_sec"), (int, float))]
    avg = (sum(ok_times) / len(ok_times)) if ok_times else None

    runtimes_out = {
        "runtimes": runtime_rows,
        "average_runtime_sec": avg,
    }

    runtimes_path = output_root / "runtimes.json"
    runtimes_path.write_text(json.dumps(runtimes_out, indent=2, ensure_ascii=False))
    print(f"Runtimes written to: {runtimes_path}")