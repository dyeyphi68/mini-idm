import re
import urllib.parse
import urllib.request
import requests

class ShopeeExtractor:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"

    @classmethod
    def is_shopee(cls, url: str) -> bool:
        if not url:
            return False
        u = url.lower()
        return "shopee." in u or "shp.ee" in u

    @classmethod
    def extract(cls, url: str):
        target_url = url.strip()

        # Follow redirects if short link
        session = requests.Session()
        session.headers.update({"User-Agent": cls.USER_AGENT})

        if "shp.ee" in target_url:
            try:
                resp = session.get(target_url, allow_redirects=True, timeout=10)
                target_url = resp.url
            except Exception as e:
                pass

        # Fetch page HTML
        resp = session.get(target_url, timeout=15)
        html = resp.text

        # 1. Search for vod.susercontent.com mp4 URLs
        matches = re.findall(r'https?://[a-zA-Z0-9.\-_/]*vod\.susercontent\.com[a-zA-Z0-9.\-_/]+?\.mp4', html)
        
        clean_url = None
        for m in matches:
            # Unescape unicode or slashes if any
            clean_m = m.replace('\\/', '/').replace('\\u0026', '&')
            
            # Remove watermark suffix: .16003551773112283.2560.mp4 -> .mp4
            candidate = re.sub(r'\.\d+\.\d+\.mp4', '.mp4', clean_m)
            
            # Test candidate with HEAD request
            try:
                head = session.head(candidate, timeout=8)
                if head.status_code == 200:
                    clean_url = candidate
                    break
            except Exception:
                continue

        if not clean_url and matches:
            clean_url = matches[0].replace('\\/', '/')

        if not clean_url:
            raise Exception("Hindi nahanap ang direct video stream sa Shopee page. Siguraduhing Shopee Video link ito.")

        # Extract title
        title = "Shopee_Video"
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        if title_match:
            raw_title = title_match.group(1).split('|')[0].strip()
            title = re.sub(r'[^\w\s-]', '', raw_title).strip() or "Shopee_Video"

        return {
            "title": title,
            "url": clean_url,
            "ext": "mp4"
        }
