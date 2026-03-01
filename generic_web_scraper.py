import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementNotInteractableException
# from web_scraping_embeddings import get_rag_db, query_rag_for_dimensions, query_rag_for_images
from openai import OpenAI
import re
from dotenv import load_dotenv

load_dotenv()


# PRESS and Hold behavior for capatcha's that will pop up for Wayfair:
import time, random
from typing import Optional, Tuple
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, JavascriptException

_PRESS_HOLD_XPATH = (
    "//*[@role='button' and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'press')"
    " and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'hold')]"
    " | /*//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'press')"
    " and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'hold')]"
)

def _find_in_iframes(driver, by, value, timeout: float = 6.0):
    """Return (element, frame_index or None)."""
    wait = WebDriverWait(driver, timeout)
    # main doc
    try:
        el = wait.until(EC.presence_of_element_located((by, value)))
        return el, None
    except TimeoutException:
        pass
    # iframes
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for i, fr in enumerate(frames):
        try:
            driver.switch_to.frame(fr)
            try:
                el = wait.until(EC.presence_of_element_located((by, value)))
                return el, i
            except TimeoutException:
                pass
        finally:
            driver.switch_to.default_content()
    return None, None

def _mouse_press_and_hold(driver, el, duration: float):
    actions = ActionChains(driver)
    actions.move_to_element(el).perform()
    time.sleep(0.12)
    actions.click_and_hold(el).perform()
    t_end = time.time() + duration
    while time.time() < t_end:
        ActionChains(driver).move_by_offset(random.randint(-2,2), random.randint(-2,2)).perform()
        time.sleep(0.12 + random.random()*0.15)
    ActionChains(driver).release(el).perform()

def _js_pointer_press_and_hold(driver, el, duration: float):
    js = r"""
const el = arguments[0];
const holdMs = Math.max(0, Math.floor(arguments[1]*1000));
el.scrollIntoView({block:'center', inline:'center'});
function fire(type, opts={}) {
  const r = el.getBoundingClientRect();
  el.dispatchEvent(new PointerEvent(type, Object.assign({
    bubbles:true, cancelable:true, composed:true,
    pointerId:1, pointerType:'mouse', isPrimary:true, buttons:1,
    clientX:(r.left+r.right)/2, clientY:(r.top+r.bottom)/2
  }, opts)));
}
function mouse(type){ el.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,buttons:1})); }
function touch(type){
  try{
    const r=el.getBoundingClientRect();
    const t=new Touch({identifier:1,target:el,clientX:(r.left+r.right)/2,clientY:(r.top+r.bottom)/2});
    el.dispatchEvent(new TouchEvent(type,{bubbles:true,cancelable:true,touches:[t],targetTouches:[t],changedTouches:[t]}));
  }catch(_){}
}
fire('pointerover'); fire('pointerenter'); fire('pointerdown');
mouse('mouseover'); mouse('mouseenter'); mouse('mousedown'); touch('touchstart');
return new Promise(res=>{ setTimeout(()=>{ fire('pointerup',{buttons:0}); mouse('mouseup'); mouse('click'); touch('touchend'); res(true); }, holdMs); });
"""
    driver.execute_script(js, el, duration)
    time.sleep(duration + 0.25)

def press_and_hold(
    driver,
    *,
    duration: float = 4.0,
    timeout: float = 8.0,
    locator: Optional[Tuple[str, str]] = None,
):
    """
    Press & hold a button-like element.
    Args:
      driver    : Selenium WebDriver
      duration  : seconds to hold
      timeout   : seconds to search for element
      locator   : optional (By, value). If not given, finds element whose text contains 'press' and 'hold'
    Returns:
      True if element found and hold executed; False otherwise.
    """
    by, val = locator if locator else (By.XPATH, _PRESS_HOLD_XPATH)

    # Find element (main or iframes)
    el, frame_idx = _find_in_iframes(driver, by, val, timeout=timeout)
    if not el:
        return False

    # Switch into the frame if needed
    if frame_idx is not None:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        try:
            driver.switch_to.frame(frames[frame_idx])
        except Exception:
            driver.switch_to.default_content()

    # Ensure interactable and in view
    try:
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable(el))
    except TimeoutException:
        pass
    driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
    time.sleep(0.15)

    # Try native, then JS fallback
    try:
        _mouse_press_and_hold(driver, el, duration)
    except WebDriverException:
        try:
            _js_pointer_press_and_hold(driver, el, duration)
        except JavascriptException:
            # bail
            driver.switch_to.default_content()
            return False

    driver.switch_to.default_content()
    return True

##############################################################################


"""
    Handle Amazon's bot detection/safeguard buttons that appear before accessing the product page.
    
    Args:
        driver: Selenium WebDriver instance
        timeout: Maximum time to wait for buttons and page transitions (seconds)
    
    Returns:
        bool: True if safeguard was handled successfully or not present, False otherwise
    """
def handle_amazon_bot_safeguard(driver, timeout=15):
    try:
        time.sleep(2)
        
        # Check if we're already on the product page (no safeguard needed)
        # Amazon product pages typically have these indicators
        product_indicators = [
            "#productTitle",
            "#landingImage",
            "#productDetails_techSpec_section_1",
            "#add-to-cart-button",
            "[data-asin]"
        ]
        
        for indicator in product_indicators:
            try:
                driver.find_element(By.CSS_SELECTOR, indicator)
                print(f"Already on product page (found {indicator})")
                return True
            except NoSuchElementException:
                continue
        
        # Common safeguard button selectors and text patterns
        safeguard_patterns = [
            (By.XPATH, "//button[contains(., 'Continue shopping')]"),
            (By.XPATH, "//button[contains(., 'Show me the product')]"),
            (By.XPATH, "//button[contains(., 'Proceed')]"),
            (By.XPATH, "//button[contains(., 'Continue')]"),
            (By.XPATH, "//button[contains(., 'Try a different image')]"),
            (By.XPATH, "//a[contains(., 'Continue shopping')]"),
            (By.XPATH, "//a[contains(., 'Show me the product')]"),
            (By.XPATH, "//input[@type='submit' and contains(@value, 'Continue')]"),
            
            # CAPTCHA-related 
            (By.CSS_SELECTOR, "button[id*='captcha']"),
            (By.CSS_SELECTOR, "button[class*='captcha']"),
            (By.CSS_SELECTOR, "button[id*='verify']"),
            (By.CSS_SELECTOR, "button[class*='verify']"),
            
            # Common Amazon safeguard button IDs and classes
            (By.CSS_SELECTOR, "#continue-button"),
            (By.CSS_SELECTOR, "#continue"),
            (By.CSS_SELECTOR, ".a-button-primary"),
            (By.CSS_SELECTOR, "button[data-action='continue']"),
            (By.CSS_SELECTOR, "button[aria-label*='Continue']"),
            
            # Generic submit buttons in forms (often used for verification)
            (By.CSS_SELECTOR, "form button[type='submit']"),
            (By.CSS_SELECTOR, "form input[type='submit']"),
        ]
        
        button_clicked = False
        for by, selector in safeguard_patterns:
            try:
                # Try to find the element with a short wait
                element = WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((by, selector))
                )
                
                # Check if element is visible and clickable
                if element.is_displayed() and element.is_enabled():
                    print(f"Found safeguard button: {selector}")
                    # Scroll into view if needed
                    driver.execute_script("arguments[0].scrollIntoView(true);", element)
                    time.sleep(0.5)
                    
                    # Try clicking with JavaScript if normal click fails
                    try:
                        element.click()
                    except ElementNotInteractableException:
                        driver.execute_script("arguments[0].click();", element)
                    
                    button_clicked = True
                    print("Clicked safeguard button")
                    break
            except (TimeoutException, NoSuchElementException):
                continue
        
        # Wait for page transition after clicking
        if button_clicked:
            time.sleep(3)
            
            # Verify we're now on the product page
            for indicator in product_indicators:
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, indicator))
                    )
                    print(f"Successfully navigated to product page (found {indicator})")
                    return True
                except TimeoutException:
                    continue
            
            # If no product indicators found, check if URL changed or page content changed
            current_url = driver.current_url
            if 'amazon.com/dp/' in current_url or 'amazon.com/product/' in current_url:
                print("URL suggests we're on a product page")
                return True
        
        # If no button was clicked, check if we're already past the safeguard
        if not button_clicked:
            print("No safeguard button found - may already be on product page or safeguard not present")
            # Double check for product indicators
            for indicator in product_indicators:
                try:
                    driver.find_element(By.CSS_SELECTOR, indicator)
                    return True
                except NoSuchElementException:
                    continue
        
        return False
        
    except Exception as e:
        print(f"Error handling Amazon bot safeguard: {str(e)}")
        return False

def analyze_product_with_rag(html_content):
    """Use RAG to identify product dimensions and images"""
    # print("Loading RAG database...")
    # db, embeddings = get_rag_db()
    
    # print("Querying for product dimension selectors...")
    # query_rag_for_dimensions(db)
    
    # print("Querying for product image selectors...")
    # query_rag_for_images(db)
    
    # Use OpenAI to analyze the HTML with RAG context
    client = OpenAI()
    
    # Use the entire HTML file with a more efficient approach
    prompt = f"""
    You are a precise information extraction model. Extract product information from raw e-commerce HTML.

    # INPUT
    The (filtered) HTML of a product page:
    <HTML>
    {html_content}
    </HTML>

    # TASK
    Return a SINGLE JSON object with:
    1) product_title: best guess of the product title/name shown on the page (string or null)
    2) high_level_description: 1–2 sentences describing what the item is, intended use, and key traits that are explicitly supported by the HTML (string)
    3) attributes: important shopping attributes IF explicitly present (do not guess):
    - color (string or null)
    - material (string or null)
    - style (string or null)
    - seating_capacity (string or null)
    - assembly_required (string or null)
    - special_features (list of strings; empty list if none)
    4) potential_dimension_values: list of strings containing any physical dimensions found, normalized to inches
    5) image_urls: list of ALL product image URLs (primary + gallery), prefer highest-resolution

    # EXTRACTION RULES
    - Use only information supported by the HTML. If an attribute is not present, set it to null (or [] for lists).
    - Color: if a color word appears in the title, option labels, or specs, extract it (e.g., beige, black, gray/grey, navy, cream, ivory, walnut).
    - Prefer high-confidence text from visible content (headings, bullets, specs tables) and product JSON blobs.
    - Do NOT return CSS selectors; return actual values.
    - Deduplicate image URLs.
    - If units are in cm/mm, convert to inches (1 inch = 2.54 cm; 10 mm = 1 cm).
    - Output must be valid JSON. No prose. No markdown. No trailing commas.

    # OUTPUT FORMAT (STRICT)
    {{
    "product_title": null or string,
    "high_level_description": string,
    "attributes": {{
        "color": null or string,
        "material": null or string,
        "style": null or string,
        "seating_capacity": null or string,
        "assembly_required": null or string,
        "special_features": [string]
    }},
    "potential_dimension_values": [string],
    "image_urls": [string]
    }}
    """

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": prompt}],
    )
    
    return response.choices[0].message.content

import re
import time
import sys
from pathlib import Path
from typing import Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


# --- Optional helpers (comment out if you already define/import these elsewhere) ---
# def handle_amazon_bot_safeguard(driver) -> bool:
#     """Stub: handle Amazon bot checks if present. Return True if handled or not needed."""
#     return True
#
# def analyze_product_with_rag(html: str) -> str:
#     """Stub: your RAG analysis goes here. Return a printable string."""
#     return "RAG analysis placeholder."


def init_chrome(headless: bool = False) -> webdriver.Chrome:
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1280,1024")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=chrome_options)


def scrape_and_analyze_url(
    url: str,
    *,
    company: Optional[str] = None,
    headless: bool = False,
    out_dir: str = ".",
    output_prefix: str = "page",
) -> Tuple[str, Path, Path]:
    """
    Navigate to `url`, dump raw HTML, filter to img/td/span, run RAG, and return:
    (analysis_result, raw_html_path, filtered_html_path)
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    driver = init_chrome(headless=headless)
    try:
        driver.get(url)

        # Wait for DOM ready
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        # Only handle Amazon safeguard if company is explicitly Amazon
        if company and company.strip().lower() == "amazon":
            try:
                print("Checking for Amazon bot safeguard buttons...")
                handled = handle_amazon_bot_safeguard(driver)  # type: ignore[name-defined]
                if handled:
                    print("Successfully handled bot safeguard or already on product page")
                else:
                    print("Warning: Bot safeguard may not have been handled, proceeding anyway...")
            except NameError:
                # Helper not provided—continue silently
                pass


        time.sleep(2)  # small buffer for dynamic content

        # --- Retrieve HTML ---
        html = driver.page_source
        raw_path = out_path / f"{output_prefix}.html"
        raw_path.write_text(html, encoding="utf-8")

        is_ikea = ("ikea" in company)

        if is_ikea:
            tags_group = "li|span|td"   # include <li> for Home Depot
        else:
            tags_group = "span|td"      # exclude <li> otherwise

        pattern = re.compile(
            rf"""
            # <img ...> (self-closing or not)
            <img\b[^>]*>
            |
            # <{tags_group} ...> ... </same-tag>
            <({tags_group})\b[^>]*>         # \1 = tag name
                (?:
                    (?!</?\1\b)             # don't let the same tag start/end here
                    .
                )*?
            </\1>
            """,
            re.IGNORECASE | re.DOTALL | re.VERBOSE,
        )

        matches = [m.group(0) for m in pattern.finditer(html)]
        filtered_html = " ".join(matches)

        filtered_path = out_path / f"{output_prefix}_filtered.html"
        filtered_path.write_text(filtered_html, encoding="utf-8")

        # --- Analyze with RAG ---
        # print("Analyzing product with RAG...")
        try:
            analysis_result = analyze_product_with_rag(filtered_html)  # type: ignore[name-defined]
        except NameError:
            analysis_result = (
                "analyze_product_with_rag(filtered_html) not found.\n"
                "Provide your implementation or import it to get real results."
            )

        print("\n" + "=" * 50)
        print("PRODUCT ANALYSIS RESULT:")
        print("=" * 50)
        print(analysis_result)
        print("=" * 50)

        return analysis_result, raw_path, filtered_path

    finally:
        time.sleep(1)
        driver.quit()


def main(url: Optional[str] = None, company: Optional[str] = None) -> None:
    """
    Pass a URL and company into the scraper/analyzer.
    Only triggers Amazon safeguard logic when company == 'Amazon'.
    """
    return scrape_and_analyze_url(url, company=company, headless=False, out_dir=".", output_prefix="page")



if __name__ == "__main__":
    # Allow passing the URL on the command line:

    # Amazon
    # python script.py "https://example.com/product"
    # url = (
    #     "https://www.amazon.com/Sectional-Minimalist-Upholstered-Couch%EF%BC%8CNo-Assembly/dp/B0DMSNCX14/ref=sr_1_1_sspa"
    #     "?crid=3Q0OC9EF9BOT2"
    #     "&dib=eyJ2IjoiMSJ9.Uwy_-hTxn36mxYatk6YVYoZzfr9ccOrbiBYTzPXlkhX20Xljw7XFV30e8JTA_UIVAcnSUfDH6SdliqACjdbtTxjItAW9S6wE3RCmOValBQUGnzlCgRtfgk4fa-PzKL8th62Cz6rAe5mruSurnxNcQ4vdjN_j0FIIIrxNqwaXdeeWa4zdYX7h608_MdeH7Xej50FqMcTQb_HicnZzBSAQVlt295PrnBXwNELEt5T-1MFOtNIs_4fB2vVpJb6X5ZdbREdGQxJexPzxwM9GK0X86-1R1IhzscV8fquOFk9dwMk.SxonPO9dTDRt6Xrhq1MNRk2KVFfS9rSsWmQ8r_nFdNE"
    #     "&dib_tag=se"
    #     "&keywords=couch"
    #     "&qid=1762054233"
    #     "&sprefix=couch%2Caps%2C195"
    #     "&sr=8-1-spons"
    #     "&sp_csd=d2lkZ2V0TmFtZT1zcF9hdGY"
    #     "&th=1"
    # )

    # Ikea
    # url= "https://www.ikea.com/us/en/p/uppland-sofa-blekinge-white-s19384116/"

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

    main(url, "Ikea")