import re
import json
import requests

class ThreadsExtractor:
    CHROME_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }

    @classmethod
    def is_threads(cls, url: str) -> bool:
        if not url:
            return False
        u = url.lower()
        return "threads.net" in u or "threads.com" in u

    @classmethod
    def extract_shortcode(cls, url: str) -> str:
        # Match post/<code>
        m = re.search(r'/post/([A-Za-z0-9_-]+)', url)
        if m:
            return m.group(1)
        # Match t/<code>
        m = re.search(r'/t/([A-Za-z0-9_-]+)', url)
        if m:
            return m.group(1)
        return ""

    @classmethod
    def extract(cls, url: str):
        shortcode = cls.extract_shortcode(url)
        if not shortcode:
            raise Exception("Hindi valid ang Threads post link. Siguraduhing may '/post/...' sa link.")

        # Query threads.com with macOS Chromium headers
        fetch_url = f"https://www.threads.com/@a/post/{shortcode}/"
        session = requests.Session()
        session.headers.update(cls.CHROME_HEADERS)

        resp = session.get(fetch_url, timeout=15)
        html = resp.text

        # Search for video_versions in SSR JSON
        video_url = None
        max_res = 0

        # Pattern 1: find video_versions array
        vv_matches = re.findall(r'"video_versions"\s*:\s*(\[[^\]]+\])', html)
        for block in vv_matches:
            try:
                # Clean up any unescaped characters
                arr = json.loads(block)
                for item in arr:
                    width = item.get("width", 0)
                    u = item.get("url")
                    if u and width >= max_res:
                        max_res = width
                        video_url = u
            except Exception:
                continue

        # Pattern 2: direct regex for .mp4 URLs on fbcdn.net / cdninstagram
        if not video_url:
            mp4_candidates = re.findall(r'https?://[a-zA-Z0-9.\-_/]*?(?:fbcdn\.net|cdninstagram\.com)[a-zA-Z0-9.\-_/%?=&#]+?\.mp4', html)
            if mp4_candidates:
                # Clean unicode slashes
                video_url = mp4_candidates[0].replace('\\/', '/').replace('\\u0026', '&')

        if not video_url:
            raise Exception("Hindi nahanap ang video stream sa Threads post. Baka photo post ito o private.")

        # Clean title
        title = f"Threads_Video_{shortcode}"
        m_title = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        if m_title:
            raw = m_title.group(1).split('|')[0].strip()
            title = re.sub(r'[^\w\s-]', '', raw).strip() or f"Threads_Video_{shortcode}"

        return {
            "title": title,
            "url": video_url,
            "ext": "mp4"
        }
