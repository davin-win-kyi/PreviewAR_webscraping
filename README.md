# PreviewAR Webscrapping --- Best Product Image Selector

This repository runs a pipeline over a set of product links to:

1)  Identify retailer + product nouns (GPT)
2)  Scrape the product page for images + candidate dimension strings
    (Selenium)
3)  Expand product-name aliases (GPT)
4)  Rank candidate images and select the best one (GPT)
5)  Download and save the selected image
6)  Benchmark runtime per link and compute average runtime

------------------------------------------------------------------------

## Setup (Conda)

Conda environment name:

previewar_webscrapping

Create environment:

    conda create -n previewar_webscrapping python=3.10 -y
    conda activate previewar_webscrapping

Install dependencies:

    pip install -r requirements.txt

Make sure Chrome + compatible ChromeDriver are installed.

------------------------------------------------------------------------

## OpenAI API Key

Set your API key:

    export OPENAI_API_KEY="sk-..."

Or create a .env file:

    OPENAI_API_KEY=sk-...

------------------------------------------------------------------------

## Input JSON Structure

The script reads product URLs from:

target_links.json

Required structure:

{ "furniture": \[ { "link": "https://www.amazon.com/...", "object_type":
"chair" }, { "link": "https://www.amazon.com/...", "object_type": "lamp"
} \] }

IMPORTANT:

-   Always use full absolute URLs including https://
-   Do NOT use bare links like: amazon.com/... That will cause Selenium
    to throw "invalid argument"

Valid object_type values: mug, couch, table, lamp, chair, plant, bed,
vase, bowl, cabinet

------------------------------------------------------------------------

## Run the Script

    python select_best_product_image.py         --links-json target_links.json         --output-dir outputs         --max-images 30

------------------------------------------------------------------------

## Outputs

Per item: - best_image.png - selection_result.json

Aggregate: - aggregate_results.json - runtimes.json

Runtime JSON format:

{ "runtimes": \[ { "link": "https://...", "object_type": "chair",
"runtime_sec": 12.34, "status": "ok" } \], "average_runtime_sec": 10.21
}

------------------------------------------------------------------------

## Troubleshooting

Selenium error: Message: invalid argument\
→ Your URL is missing https://

Scraper output format mismatch\
→ Ensure scraper returns either dict, JSON string, list, or
tuple(analysis, raw_path, filtered_path)

------------------------------------------------------------------------
