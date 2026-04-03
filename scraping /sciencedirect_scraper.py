#!/usr/bin/env python3
"""
ScienceDirect Paper Scraper
Extracts paper titles and authors from ScienceDirect search results.
Uses Playwright with stealth to bypass bot protection.

Usage:
    pip install playwright playwright-stealth
    playwright install chromium
    python sciencedirect_scraper.py <url> -n 50 -o output.txt
"""

import argparse
import time
import random
from datetime import datetime


def extract_papers(url, max_papers=25, headless=True):
    """
    Extract paper titles and authors from ScienceDirect search results.
    
    Args:
        url: ScienceDirect search URL
        max_papers: Maximum number of papers to extract
        headless: Run browser in headless mode
    
    Returns:
        List of dicts with 'title' and 'authors' keys
    """
    import os
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
    
    papers = []
    
    # Use persistent profile to remember bot verification
    profile_dir = os.path.join(os.path.dirname(__file__), ".sciencedirect_profile")
    os.makedirs(profile_dir, exist_ok=True)
    
    with sync_playwright() as pw:
        # Use persistent context like BookMyShow script
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
            ],
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        
        page = browser.pages[0] if browser.pages else browser.new_page()
        Stealth().apply_stealth_sync(page)
        
        print(f"Loading page: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        
        # Always wait for user to confirm page is ready (bot check, loading, etc.)
        print("\n" + "="*50)
        print("Browser window opened!")
        print("If you see a bot verification, complete it ONCE.")
        print("(Using persistent profile - won't ask again next time)")
        print("Wait until the search results are fully visible.")
        input("👉 Press ENTER when you see the paper listings... ")
        print("="*50 + "\n")
        
        time.sleep(2)
        
        while len(papers) < max_papers:
            print(f"Collected {len(papers)} papers so far...")
            
            # Scroll to load more content
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(random.uniform(1, 2))
            
            # Get page text and parse
            try:
                # Find all result items
                result_items = page.query_selector_all("div.result-item-content, li.ResultItem, [data-testid='result-item']")
                
                if not result_items:
                    # Fallback: try to find any article links
                    result_items = page.query_selector_all("ol.search-result-list li, .SearchResult")
                
                for item in result_items:
                    if len(papers) >= max_papers:
                        break
                    
                    paper = {"title": "", "authors": []}
                    
                    # Extract title
                    title_elem = item.query_selector("h2 a, h2 span, .result-list-title-link, a[href*='/article/']")
                    if title_elem:
                        paper["title"] = title_elem.inner_text().strip()
                    
                    # Skip if we already have this paper
                    if paper["title"] and any(p["title"] == paper["title"] for p in papers):
                        continue
                    
                    # Extract authors
                    author_container = item.query_selector(".Authors, .author-group, .result-item-authors")
                    if author_container:
                        authors_text = author_container.inner_text().strip()
                        # Clean up author string
                        authors_text = authors_text.replace("...", "").replace(" and ", ", ")
                        paper["authors"] = [a.strip() for a in authors_text.split(", ") if a.strip()]
                    
                    if paper["title"]:
                        papers.append(paper)
                        print(f"  [{len(papers)}] {paper['title'][:60]}...")
                
            except Exception as e:
                print(f"Error extracting from page: {e}")
            
            # Try to go to next page if we need more papers
            if len(papers) < max_papers:
                try:
                    next_btn = page.query_selector("button[aria-label='Next page'], a.pagination-link-next, .next-link")
                    if next_btn and next_btn.is_visible():
                        print("Going to next page...")
                        next_btn.click()
                        time.sleep(4)
                    else:
                        # Scroll more to see if lazy loading kicks in
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(2)
                        
                        # Check if we've reached the end
                        new_items = page.query_selector_all("div.result-item-content, li.ResultItem")
                        if len(new_items) == len(result_items):
                            print("No more papers to load.")
                            break
                except Exception as e:
                    print(f"Pagination error: {e}")
                    break
        
        browser.close()
    
    return papers


def save_results(papers, output_file):
    """Save extracted papers to a text file."""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"ScienceDirect Search Results\n")
        f.write(f"Extracted on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total papers found: {len(papers)}\n")
        f.write("=" * 80 + "\n\n")
        
        for i, paper in enumerate(papers, 1):
            f.write(f"Paper {i}:\n")
            f.write(f"  Title: {paper['title']}\n")
            authors_str = ", ".join(paper["authors"]) if paper["authors"] else "Not available"
            f.write(f"  Authors: {authors_str}\n")
            f.write("-" * 40 + "\n")
    
    print(f"\nResults saved to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape paper titles and authors from ScienceDirect search results."
    )
    parser.add_argument(
        "url",
        help="ScienceDirect search URL to scrape"
    )
    parser.add_argument(
        "-o", "--output",
        default="sciencedirect_papers.txt",
        help="Output file name (default: sciencedirect_papers.txt)"
    )
    parser.add_argument(
        "-n", "--max-papers",
        type=int,
        default=25,
        help="Maximum number of papers to extract (default: 25)"
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Run browser in visible mode (not headless)"
    )
    
    args = parser.parse_args()
    
    print(f"Starting ScienceDirect scraper...")
    print(f"URL: {args.url}")
    print(f"Max papers: {args.max_papers}")
    
    try:
        papers = extract_papers(args.url, args.max_papers, headless=not args.visible)
        
        if papers:
            save_results(papers, args.output)
            print(f"\nSuccessfully extracted {len(papers)} papers!")
        else:
            print("\nNo papers found. The page structure may have changed or site blocked the request.")
            
    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
