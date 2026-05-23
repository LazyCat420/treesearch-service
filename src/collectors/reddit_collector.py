"""
reddit_collector.py
-------------------
Reddit collector — queries and parses Reddit posts using search engines and public JSON API endpoints.
Uses scraper-service as a proxy.
"""

import re
import logging
import hashlib
from datetime import datetime
from src.scraper_client import ScraperClient
from src.collectors.search_helper import search_search_engine_for_site

logger = logging.getLogger(__name__)

class RedditCollector:
    def __init__(self):
        pass

    async def search(self, query: str, subreddits: list[str], limit: int = 20) -> list[dict]:
        """
        Search Reddit for matching posts in specific subreddits.
        Uses search engines first to locate threads, then fetches JSON representation.
        """
        results = []
        seen_ids = set()
        
        # 1. Search engines first
        try:
            for sub in subreddits:
                domain = f"reddit.com/r/{sub}"
                urls = await search_search_engine_for_site(query, domain, limit=10)
                
                for url in urls:
                    # Match comments path: reddit.com/r/<subpath>/comments/<id>/
                    match = re.search(r"reddit\.com/r/[^/]+/comments/([a-z0-9]+)", url, re.IGNORECASE)
                    if match:
                        post_id = match.group(1)
                        if post_id in seen_ids:
                            continue
                        seen_ids.add(post_id)
                        
                        json_url = f"https://www.reddit.com/r/{sub}/comments/{post_id}.json"
                        post_data = await self._fetch_post_json(json_url, sub)
                        if post_data:
                            results.append(post_data)
                            if len(results) >= limit:
                                break
                if len(results) >= limit:
                    break
        except Exception as e:
            logger.warning(f"[reddit] Search engine search failed: {e}")

        # 2. Fallback to native Reddit search API
        if not results:
            logger.info(f"[reddit] Falling back to Reddit native search API for '{query}'")
            multi_sub = "+".join(subreddits)
            search_url = f"https://www.reddit.com/r/{multi_sub}/search.json"
            params = {
                "q": query,
                "restrict_sr": "on",
                "sort": "relevance",
                "limit": limit,
                "type": "link",
                "include_over_18": "on"
            }
            
            import urllib.parse
            full_url = f"{search_url}?{urllib.parse.urlencode(params)}"
            
            scraper = ScraperClient()
            try:
                res = await scraper.scrape(full_url, engine="http")
                data = res.get("data")
                if not data or not isinstance(data, dict):
                    # Try playwright if http fails
                    res = await scraper.scrape(full_url, engine="playwright", options={"raw_html": True})
                    content = res.get("content")
                    if content:
                        import json
                        if "<pre>" in content:
                            json_str = re.search(r"<pre[^>]*>(.*?)</pre>", content, re.DOTALL).group(1)
                            data = json.loads(json_str)
                        else:
                            data = json.loads(content)
                            
                if data and isinstance(data, dict):
                    children = data.get("data", {}).get("children", [])
                    for child in children:
                        post = child.get("data", {})
                        if post:
                            post_id = post.get("id")
                            if post_id not in seen_ids:
                                seen_ids.add(post_id)
                                results.append(self._serialize_reddit_post(post, post.get("subreddit", multi_sub)))
            except Exception as ex:
                logger.error(f"[reddit] Native search fallback failed: {ex}")
            finally:
                await scraper.close()
                
        return results[:limit]

    async def _fetch_post_json(self, json_url: str, subreddit: str) -> dict | None:
        scraper = ScraperClient()
        try:
            res = await scraper.scrape(json_url, engine="http")
            data = res.get("data")
            if not data or not isinstance(data, list):
                # Try playwright
                res = await scraper.scrape(json_url, engine="playwright", options={"raw_html": True})
                content = res.get("content")
                if content:
                    import json
                    if "<pre>" in content:
                        json_str = re.search(r"<pre[^>]*>(.*?)</pre>", content, re.DOTALL).group(1)
                        data = json.loads(json_str)
                    else:
                        data = json.loads(content)
                        
            if isinstance(data, list) and len(data) > 0:
                post_info = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
                if post_info:
                    return self._serialize_reddit_post(post_info, subreddit)
        except Exception as e:
            logger.warning(f"[reddit] Failed to fetch post JSON from {json_url}: {e}")
        finally:
            await scraper.close()
        return None

    def _serialize_reddit_post(self, post: dict, subreddit: str) -> dict:
        created_utc = post.get("created_utc", 0)
        created_at = datetime.fromtimestamp(created_utc).isoformat() if created_utc else None
        
        # Extract images
        image_urls = []
        url = post.get("url", "")
        if any(url.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
            image_urls.append(url)
        elif "i.redd.it" in url or "i.imgur.com" in url:
            image_urls.append(url)
            
        media_metadata = post.get("media_metadata") or {}
        for key, meta in media_metadata.items():
            if meta.get("status") == "valid" and meta.get("e") == "Image":
                source = meta.get("s", {})
                img_url = source.get("u") or source.get("gif") or ""
                if img_url:
                    img_url = img_url.replace("&amp;", "&")
                    image_urls.append(img_url)
                    
        if not image_urls:
            preview = post.get("preview", {})
            for img_data in preview.get("images", []):
                source = img_data.get("source", {})
                img_url = source.get("url", "")
                if img_url:
                    img_url = img_url.replace("&amp;", "&")
                    image_urls.append(img_url)

        return {
            "id": post.get("id", hashlib.md5(post.get("title", "").encode()).hexdigest()[:12]),
            "title": post.get("title", ""),
            "body": post.get("selftext", ""),
            "score": post.get("score", 0),
            "url": f"https://reddit.com{post.get('permalink', '')}",
            "subreddit": subreddit,
            "created_at": created_at,
            "author": post.get("author", ""),
            "num_comments": post.get("num_comments", 0),
            "post_number": 1,
            "image_urls": image_urls,
        }
