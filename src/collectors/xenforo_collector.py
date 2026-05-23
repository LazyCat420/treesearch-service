"""
xenforo_collector.py
--------------------
XenForo forum collector — fetches and parses threads/posts from XenForo forums
(e.g. Rollitup, THCFarmer, ICMag) using BeautifulSoup HTML parsing.
Uses scraper-service Playwright proxy to bypass Cloudflare.
"""

import re
import html
import logging
import hashlib
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
from src.scraper_client import ScraperClient
from src.collectors.search_helper import search_search_engine_for_site

logger = logging.getLogger(__name__)

class XenForoCollector:
    def __init__(self, base_url: str, forum_name: str = "xenforo"):
        self.base_url = base_url.rstrip("/")
        self.forum_name = forum_name

    async def search(self, query: str, limit: int = 50) -> list[dict]:
        """
        Search the XenForo forum using search engines (DDG/Google) first,
        falling back to XenForo's internal search endpoint.
        """
        domain = urllib.parse.urlparse(self.base_url).netloc
        results = []
        
        # 1. Try search engines
        try:
            thread_urls = await search_search_engine_for_site(query, domain, limit=5)
            logger.info(f"[xenforo] Search engine found thread URLs: {thread_urls}")
            
            for thread_url in thread_urls:
                if len(results) >= limit:
                    break
                try:
                    posts = await self.get_thread_posts(
                        thread_url=thread_url,
                        limit=min(limit - len(results), 15)
                    )
                    results.extend(posts)
                except Exception as e:
                    logger.error(f"[xenforo] Failed to fetch posts for thread {thread_url}: {e}")
        except Exception as e:
            logger.warning(f"[xenforo] Search engine query failed for {domain}: {e}")

        # 2. Fallback to internal search endpoint
        if not results:
            logger.info(f"[xenforo] Falling back to internal search for '{query}' on {domain}")
            search_url = f"{self.base_url}/search/search"
            params = {"keywords": query, "type": "post", "order": "relevance"}
            full_url = f"{search_url}?{urllib.parse.urlencode(params)}"
            
            scraper = ScraperClient()
            try:
                # Use Playwright + wait_for since XenForo search form redirects and requires CSRF/JS sometimes
                res = await scraper.scrape(
                    full_url, 
                    engine="playwright", 
                    options={"raw_html": True, "wait_for": ".block-row"}
                )
                html_content = res.get("content")
                if html_content:
                    soup = BeautifulSoup(html_content, "html.parser")
                    for item in soup.select(".block-row"):
                        title_el = item.select_one("h3 a")
                        if not title_el:
                            continue
                            
                        title = title_el.get_text(strip=True)
                        result_url = urllib.parse.urljoin(self.base_url, title_el.get("href", ""))
                        
                        snippet_el = item.select_one(".contentRow-snippet")
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                        
                        author_el = item.select_one(".contentRow-minor a.username, .contentRow-minor .username")
                        author = author_el.get_text(strip=True) if author_el else ""
                        
                        time_el = item.select_one("time[datetime]")
                        created_at = None
                        if time_el:
                            try:
                                created_at = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00")).isoformat()
                            except Exception:
                                pass
                                
                        image_urls = self._extract_images(snippet_el) if snippet_el else []
                        
                        results.append({
                            "id": hashlib.md5(result_url.encode()).hexdigest()[:12],
                            "thread_id": "",
                            "title": title,
                            "body": snippet,
                            "author": author,
                            "created_at": created_at,
                            "url": result_url,
                            "forum_name": self.forum_name,
                            "subforum": "search",
                            "image_urls": image_urls,
                            "post_number": 1,
                        })
                        if len(results) >= limit:
                            break
            except Exception as ex:
                logger.error(f"[xenforo] Internal search failed for {domain}: {ex}")
            finally:
                await scraper.close()

        return results[:limit]

    async def get_thread_posts(self, thread_url: str, limit: int = 30) -> list[dict]:
        """Get all posts from a specific XenForo thread, paginating up to 3 pages."""
        all_posts = []
        thread_id = ""
        
        # Extract thread ID from URL (e.g. threads/some-strain.12345/)
        id_match = re.search(r"\.(\d+)/?$", thread_url.split("/page-")[0])
        if id_match:
            thread_id = id_match.group(1)
            
        scraper = ScraperClient()
        try:
            for page in range(1, 4):
                if len(all_posts) >= limit:
                    break
                    
                page_url = thread_url
                if page > 1:
                    page_url = f"{thread_url.rstrip('/')}/page-{page}"
                    
                logger.info(f"[xenforo] Scraping thread page: {page_url}")
                
                # Fetch page with Playwright and wait for XenForo post elements
                res = await scraper.scrape(
                    page_url,
                    engine="playwright",
                    options={"raw_html": True, "wait_for": "article.message"}
                )
                html_content = res.get("content")
                if not html_content:
                    break
                    
                soup = BeautifulSoup(html_content, "html.parser")
                
                title_el = soup.select_one("h1.p-title-value")
                title = title_el.get_text(strip=True) if title_el else ""
                
                post_elements = soup.select("article.message")
                if not post_elements:
                    break
                    
                for post_el in post_elements:
                    body_el = post_el.select_one(".message-body .bbWrapper")
                    if not body_el:
                        continue
                        
                    content_el = post_el.select_one(".message-content") or post_el
                    image_urls = self._extract_images(content_el)
                    body = body_el.get_text(separator=" ", strip=True)
                    if not body or len(body) < 10:
                        continue
                        
                    post_id = post_el.get("data-content", "").replace("post-", "")
                    
                    author_el = post_el.select_one(".message-name a, .message-name span")
                    author = author_el.get_text(strip=True) if author_el else ""
                    
                    time_el = post_el.select_one("time.u-dt[datetime], time[datetime]")
                    created_at = None
                    if time_el:
                        try:
                            created_at = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00")).isoformat()
                        except Exception:
                            pass
                            
                    post_num_el = post_el.select_one(".message-attribution-opposite a")
                    post_number = 1
                    if post_num_el:
                        try:
                            post_number = int(post_num_el.get_text(strip=True).lstrip("#"))
                        except ValueError:
                            pass
                            
                    reaction_el = post_el.select_one(".reactionsBar")
                    reaction_score = 0
                    if reaction_el:
                        score_text = reaction_el.get_text(strip=True)
                        nums = re.findall(r"\d+", score_text)
                        if nums:
                            reaction_score = int(nums[0])
                            
                    all_posts.append({
                        "id": post_id or hashlib.md5(body[:100].encode()).hexdigest()[:12],
                        "thread_id": thread_id,
                        "title": title,
                        "body": body[:5000],
                        "author": author,
                        "created_at": created_at,
                        "url": f"{self.base_url}/posts/{post_id}/" if post_id else page_url,
                        "forum_name": self.forum_name,
                        "subforum": "",
                        "post_number": post_number,
                        "reaction_score": reaction_score,
                        "image_urls": image_urls,
                    })
                    
                    if len(all_posts) >= limit:
                        break
        except Exception as e:
            logger.error(f"[xenforo] Error scraping thread {thread_url}: {e}")
        finally:
            await scraper.close()
            
        return all_posts

    def _extract_images(self, element) -> list[str]:
        if not element:
            return []
        try:
            images = []
            for img in element.find_all("img"):
                src = img.get("src") or img.get("data-url")
                if not src:
                    continue
                src_lower = src.lower()
                if any(term in src_lower for term in [
                    "emoji", "smilie", "avatar", "smiley", "icon", 
                    "profile", "logo", "flag", "badge", "gravatar",
                    "/styles/default/xenforo/smilies", "/attachments/emoticon"
                ]):
                    continue
                
                # Check dimensions
                width = img.get("width")
                height = img.get("height")
                try:
                    if width and int(width) < 50:
                        continue
                    if height and int(height) < 50:
                        continue
                except ValueError:
                    pass
                    
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = self.base_url + src
                    
                if src not in images:
                    images.append(src)
            return images
        except Exception as e:
            logger.error(f"[xenforo] Failed to extract images: {e}")
            return []
