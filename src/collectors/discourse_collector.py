"""
discourse_collector.py
-----------------------
Discourse forum collector — fetches and parses threads/posts from Discourse forums (e.g. Overgrow.com)
using public JSON API endpoints. Uses scraper-service as a proxy fallback.
"""

import re
import html
import logging
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
from src.scraper_client import ScraperClient
from src.collectors.search_helper import search_search_engine_for_site

logger = logging.getLogger(__name__)

class DiscourseCollector:
    def __init__(self, base_url: str = "https://overgrow.com", forum_name: str = "overgrow"):
        self.base_url = base_url.rstrip("/")
        self.forum_name = forum_name

    async def search(self, query: str, limit: int = 30) -> list[dict]:
        """
        Search Discourse using search engines first (DDG/Google),
        falling back to the internal Discourse JSON search endpoint.
        """
        domain = urllib.parse.urlparse(self.base_url).netloc
        results = []
        
        # 1. Search engines first
        try:
            urls = await search_search_engine_for_site(query, domain, limit=5)
            topic_ids = []
            for href in urls:
                # Format: https://overgrow.com/t/topic-name/12345
                m = re.search(r"/t/[^/]+/(\d+)", href)
                if m:
                    tid = int(m.group(1))
                    if tid not in topic_ids:
                        topic_ids.append(tid)
            
            logger.info(f"[discourse] Search engine found topic IDs: {topic_ids}")
            for tid in topic_ids:
                if len(results) >= limit:
                    break
                try:
                    posts = await self.get_topic_posts(tid, limit=15)
                    results.extend(posts)
                except Exception as e:
                    logger.error(f"[discourse] Failed to fetch posts for topic {tid}: {e}")
        except Exception as e:
            logger.warning(f"[discourse] Search engine search failed: {e}")

        # 2. Fallback to internal search API if no results
        if not results:
            logger.info(f"[discourse] Falling back to internal search for '{query}'")
            search_url = f"{self.base_url}/search.json?q={urllib.parse.quote(query)}"
            
            scraper = ScraperClient()
            try:
                # Try http first
                res = await scraper.scrape(search_url, engine="http")
                data = res.get("data")
                if not data or not isinstance(data, dict):
                    # Fallback to playwright
                    res = await scraper.scrape(search_url, engine="playwright", options={"raw_html": True})
                    # Content is JSON string
                    content = res.get("content")
                    if content:
                        import json
                        # Playwright might wrap JSON in <pre> tags
                        if "<pre>" in content:
                            json_str = re.search(r"<pre[^>]*>(.*?)</pre>", content, re.DOTALL).group(1)
                            data = json.loads(json_str)
                        else:
                            data = json.loads(content)
                            
                if data and isinstance(data, dict):
                    topics = data.get("topics", [])
                    posts_data = data.get("posts", [])
                    topic_titles = {t["id"]: t for t in topics}
                    
                    for post_data in posts_data[:limit]:
                        topic_id = post_data.get("topic_id", 0)
                        topic_info = topic_titles.get(topic_id, {})
                        
                        created = post_data.get("created_at", "")
                        created_at = None
                        if created:
                            try:
                                created_at = datetime.fromisoformat(created.replace("Z", "+00:00")).isoformat()
                            except Exception:
                                pass
                                
                        cooked = post_data.get("cooked", "")
                        image_urls = self._extract_images(cooked)
                        body = self._strip_html(cooked) or post_data.get("blurb", "")
                        
                        if not body or len(body) < 10:
                            continue
                            
                        results.append({
                            "id": str(post_data.get("id", "")),
                            "topic_id": topic_id,
                            "title": topic_info.get("title", ""),
                            "body": body,
                            "author": post_data.get("username", ""),
                            "created_at": created_at,
                            "url": f"{self.base_url}/t/{topic_info.get('slug', '')}/{topic_id}/{post_data.get('post_number', 1)}",
                            "forum_name": self.forum_name,
                            "category": str(topic_info.get("category_id", "")),
                            "tags": topic_info.get("tags", []),
                            "post_number": post_data.get("post_number", 1),
                            "like_count": post_data.get("like_count", 0),
                            "image_urls": image_urls,
                        })
            except Exception as ex:
                logger.error(f"[discourse] Internal search API failed: {ex}")
            finally:
                await scraper.close()
                
        return results[:limit]

    async def get_topic_posts(self, topic_id: int, limit: int = 30) -> list[dict]:
        """Get all posts in a specific Discourse topic (thread)."""
        url = f"{self.base_url}/t/{topic_id}.json"
        
        scraper = ScraperClient()
        try:
            res = await scraper.scrape(url, engine="http")
            data = res.get("data")
            if not data or not isinstance(data, dict):
                # Fallback to playwright
                res = await scraper.scrape(url, engine="playwright", options={"raw_html": True})
                content = res.get("content")
                if content:
                    import json
                    if "<pre>" in content:
                        json_str = re.search(r"<pre[^>]*>(.*?)</pre>", content, re.DOTALL).group(1)
                        data = json.loads(json_str)
                    else:
                        data = json.loads(content)
                        
            if not data or not isinstance(data, dict):
                return []
                
            title = data.get("title", "")
            category_id = data.get("category_id", 0)
            tags = data.get("tags", [])
            slug = data.get("slug", "")
            post_stream = data.get("post_stream", {})
            posts = post_stream.get("posts", [])
            
            results = []
            for post_data in posts[:limit]:
                created = post_data.get("created_at", "")
                created_at = None
                if created:
                    try:
                        created_at = datetime.fromisoformat(created.replace("Z", "+00:00")).isoformat()
                    except Exception:
                        pass
                        
                cooked = post_data.get("cooked", "")
                image_urls = self._extract_images(cooked)
                body = self._strip_html(cooked)
                
                if not body or len(body) < 10:
                    continue
                    
                results.append({
                    "id": str(post_data.get("id", "")),
                    "topic_id": topic_id,
                    "title": title,
                    "body": body,
                    "author": post_data.get("username", ""),
                    "created_at": created_at,
                    "url": f"{self.base_url}/t/{slug}/{topic_id}/{post_data.get('post_number', 1)}",
                    "forum_name": self.forum_name,
                    "category": str(category_id),
                    "tags": tags,
                    "post_number": post_data.get("post_number", 1),
                    "reply_count": post_data.get("reply_count", 0),
                    "like_count": post_data.get("actions_summary", [{}])[0].get("count", 0) if post_data.get("actions_summary") else 0,
                    "image_urls": image_urls,
                })
            return results
        except Exception as e:
            logger.error(f"[discourse] Failed to get topic posts for {topic_id}: {e}")
            return []
        finally:
            await scraper.close()

    def _extract_images(self, content_html: str) -> list[str]:
        if not content_html:
            return []
        try:
            soup = BeautifulSoup(content_html, "html.parser")
            images = []
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-orig-src")
                if not src:
                    continue
                # Skip emoticons, avatars, small icons
                src_lower = src.lower()
                if any(term in src_lower for term in [
                    "emoji", "emoticon", "avatar", "smiley", "icon", 
                    "profile", "logo", "flag", "badge", "gravatar",
                    "/images/emoji/", "/plugins/discourse-"
                ]):
                    continue
                
                # Make absolute
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = self.base_url + src
                    
                if src not in images:
                    images.append(src)
            return images
        except Exception as e:
            logger.error(f"[discourse] Failed to extract images: {e}")
            return []

    def _strip_html(self, content_html: str) -> str:
        if not content_html:
            return ""
        text = re.sub(r"<[^>]+>", " ", content_html)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
