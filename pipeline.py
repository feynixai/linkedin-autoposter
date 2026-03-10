"""
Daily pipeline: Fetch trends → GPT-5.4 generates post + image → Save draft.
Supports memory/preferences, web fetching, and link citations.
"""

import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import openai
import requests
from dotenv import load_dotenv

from db import save_draft, get_recent_topics, get_all_memories

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "generated_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# RSS feeds for tech/AI trends
FEEDS = [
    "https://hnrss.org/frontpage?count=15",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
]

# Influential tech/AI accounts to monitor on X
INFLUENTIAL_ACCOUNTS = [
    "sama",              # Sam Altman (OpenAI)
    "elonmusk",          # Elon Musk
    "karpathy",          # Andrej Karpathy
    "ylecun",            # Yann LeCun (Meta AI)
    "AnthropicAI",       # Anthropic
    "OpenAI",            # OpenAI
    "GoogleDeepMind",    # Google DeepMind
    "satyanadella",      # Satya Nadella (Microsoft)
    "demishassabis",     # Demis Hassabis (DeepMind)
    "ClementDelangue",   # Clement Delangue (Hugging Face)
    "DrJimFan",          # Jim Fan (NVIDIA)
    "bindureddy",        # Bindu Reddy
    "hardmaru",          # David Ha (Sakana AI)
]


def fetch_influential_tweets():
    """Fetch recent tweets from influential tech accounts."""
    tweets = []
    for handle in INFLUENTIAL_ACCOUNTS:
        try:
            resp = requests.get(f"https://api.fxtwitter.com/{handle}", timeout=10)
            if resp.ok:
                data = resp.json()
                # fxtwitter user timeline returns recent tweets
                user = data.get("user", {})
                if user:
                    # Get their latest tweet from timeline
                    timeline_resp = requests.get(
                        f"https://api.fxtwitter.com/{handle}/status/{user.get('last_tweet_id', '')}",
                        timeout=10,
                    )
                    if timeline_resp.ok:
                        tweet_data = timeline_resp.json().get("tweet", {})
                        if tweet_data and tweet_data.get("text"):
                            tweets.append({
                                "author": f"{tweet_data.get('author', {}).get('name', handle)} (@{handle})",
                                "text": tweet_data.get("text", ""),
                                "url": f"https://x.com/{handle}/status/{tweet_data.get('id', '')}",
                                "likes": tweet_data.get("likes", 0),
                                "retweets": tweet_data.get("retweets", 0),
                            })
        except Exception:
            continue
    # Sort by engagement (likes + retweets)
    tweets.sort(key=lambda t: t.get("likes", 0) + t.get("retweets", 0), reverse=True)
    return tweets


def fetch_url_content(url):
    """Fetch content from a URL (tweet, article, etc.) and return text."""
    try:
        parsed = urlparse(url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # Twitter/X — use fxtwitter API
        if parsed.hostname in ("x.com", "twitter.com"):
            path = parsed.path  # e.g. /claudeai/status/123456
            fx_url = f"https://api.fxtwitter.com{path}"
            resp = requests.get(fx_url, timeout=10)
            if resp.ok:
                data = resp.json()
                tweet = data.get("tweet", {})
                author = tweet.get("author", {}).get("name", "")
                handle = tweet.get("author", {}).get("screen_name", "")
                text = tweet.get("text", "")
                media = tweet.get("media", {})
                images = []
                videos = []
                video_thumbnails = []
                if media:
                    for item in media.get("all", []):
                        if item.get("type") == "photo":
                            if item.get("url"):
                                images.append(item["url"])
                        elif item.get("type") in ("video", "gif"):
                            if item.get("url"):
                                videos.append(item["url"])
                            if item.get("thumbnail_url"):
                                video_thumbnails.append(item["thumbnail_url"])
                return {
                    "type": "tweet",
                    "author": f"{author} (@{handle})" if handle else author,
                    "text": text,
                    "images": images,
                    "videos": videos,
                    "video_thumbnails": video_thumbnails,
                    "url": url,
                    "has_video": len(videos) > 0,
                }
            # Fallback: return URL as context
            return {"type": "tweet", "author": "", "text": f"Tweet at {url}", "images": [], "url": url}

        # Generic article — use readability for clean extraction
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()

        try:
            from readability import Document
            doc = Document(resp.text)
            title = doc.title()
            # Get clean text from readability's summary
            from lxml import etree
            summary_html = doc.summary()
            tree = etree.fromstring(summary_html, etree.HTMLParser())
            text = " ".join(tree.itertext()).strip()
            # Clean up whitespace
            text = re.sub(r'\s+', ' ', text)[:2000]
        except Exception:
            # Fallback to basic extraction
            title_match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""
            # Strip tags roughly
            text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()[:2000]

        # Try to extract og:image
        images = []
        og_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\'](.*?)["\']', resp.text, re.IGNORECASE)
        if og_match:
            images.append(og_match.group(1))

        return {
            "type": "article",
            "title": title,
            "text": text,
            "url": url,
            "images": images,
        }
    except Exception as e:
        return {"type": "error", "error": str(e), "url": url, "text": "", "images": []}


def download_image_from_url(image_url):
    """Download an image from a URL and save locally."""
    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        ext = "png"
        if "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "webp" in content_type:
            ext = "webp"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = os.path.join(IMAGES_DIR, f"web_{timestamp}.{ext}")
        with open(image_path, "wb") as f:
            f.write(resp.content)
        return image_path
    except Exception as e:
        print(f"Failed to download image: {e}")
        return None


def download_video_from_url(video_url):
    """Download a video from a URL and save locally."""
    try:
        resp = requests.get(video_url, timeout=60, stream=True)
        resp.raise_for_status()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_path = os.path.join(IMAGES_DIR, f"video_{timestamp}.mp4")
        with open(video_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return video_path
    except Exception as e:
        print(f"Failed to download video: {e}")
        return None


def _get_memory_prompt():
    """Build a prompt section from stored memories/preferences."""
    memories = get_all_memories()
    if not memories:
        return ""
    lines = [f"- {k}: {v}" for k, v in memories.items()]
    return "\nUSER PREFERENCES (always follow these):\n" + "\n".join(lines) + "\n"


def fetch_trends():
    """Fetch latest tech/AI headlines from RSS feeds."""
    headlines = []
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:8]:
                headlines.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                    "source": feed.feed.get("title", feed_url),
                    "link": entry.get("link", ""),
                })
        except Exception as e:
            print(f"Warning: Failed to fetch {feed_url}: {e}")
    return headlines


def generate_post_content(trends, recent_topics):
    """Use GPT-5.4 to generate a LinkedIn post from trends."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    trends_text = "\n".join(
        f"- [{t['source']}] {t['title']}: {t['summary']} ({t.get('link', '')})" for t in trends
    )
    recent_text = ", ".join(recent_topics[-20:]) if recent_topics else "None yet"
    memory_prompt = _get_memory_prompt()

    prompt = f"""You are a tech thought leader writing a daily LinkedIn post.
{memory_prompt}
TODAY'S TRENDING TECH/AI NEWS:
{trends_text}

TOPICS ALREADY POSTED RECENTLY (avoid repeating):
{recent_text}

Write a LinkedIn post that is ENGAGING and EASY TO READ — not boring paragraphs:

FORMAT:
- Start with a BOLD hook — a surprising stat, hot take, or provocative question (1 line)
- Use SHORT lines (1-2 sentences max) with blank lines between them
- Break complex ideas into numbered lists or "→" arrow progressions
- Add YOUR personal take or prediction — don't just summarize
- End with a question to drive comments
- 150-250 words, 3-5 hashtags at the end
- Max 2-3 emojis total
- Include source links where relevant

TONE: confident, conversational, like texting a smart friend — NOT corporate/formal

EXAMPLE STRUCTURE:
[Hook — 1 bold provocative line]

[What happened — 2-3 short lines]

Here's why this matters:

1. [Point one]
2. [Point two]
3. [Point three]

[Your hot take or prediction — 1-2 lines]

[Question to drive engagement]

#hashtags

Also provide an image prompt. The image MUST be EXPLANATORY and INFORMATIVE:
- Generate a DIAGRAM, FLOWCHART, INFOGRAPHIC, or COMPARISON CHART — NOT a generic scene
- Include TEXT LABELS in the image: key terms, numbers, names, arrows showing relationships
- Think: "If someone only saw this image, could they understand the core concept of my post?"
- Examples of GOOD image prompts:
  * "Block diagram on dark gradient background: Left side 'Traditional Stack' (boxes: Frontend, Backend, Database, Auth, Payments). Right side 'AI Agent Stack' (single box: AI Agent with arrows to all services). Bold title 'The Great Unbundling'. Clean tech aesthetic, blue/purple accents"
  * "Comparison infographic: Two columns — 'Open Source AI' vs 'Closed AI'. Each with 4 rows showing: Cost, Customization, Privacy, Speed with icons and short labels. Modern dark theme, green vs red indicators"
  * "Flowchart showing: User Query → AI Agent → branching arrows to Tools (Search, Code, Data) → Results → Response. Clean modern style with labeled nodes and arrows"
- Style: clean, modern, dark or gradient background, bold sans-serif typography, tech aesthetic
- NEVER: generic stock photos, random robots, abstract shapes, handshakes, people at laptops

Respond as JSON: {{"post": "...", "image_prompt": "detailed descriptive image prompt here", "topics": ["..."], "use_web_image": false}}"""

    response = client.chat.completions.create(
        model="gpt-5.4",
        max_completion_tokens=1024,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You respond only in valid JSON."},
            {"role": "user", "content": prompt},
        ],
    )

    return json.loads(response.choices[0].message.content)


def generate_image(image_prompt):
    """Generate an image using GPT-5.4 via Responses API."""
    import base64
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    response = client.responses.create(
        model="gpt-5.4",
        input=image_prompt,
        tools=[{"type": "image_generation", "size": "1024x1024", "quality": "high"}],
    )

    # Extract the image from response output
    image_data = None
    for item in response.output:
        if item.type == "image_generation_call":
            image_data = base64.b64decode(item.result)
            break

    if not image_data:
        raise RuntimeError("GPT-5.4 did not return an image")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = os.path.join(IMAGES_DIR, f"post_{timestamp}.png")
    with open(image_path, "wb") as f:
        f.write(image_data)

    return image_path


def run_pipeline():
    """Run the full pipeline: trends → content → image → draft."""
    print(f"[{datetime.now()}] Starting pipeline...")

    # 1. Fetch trends
    print("Fetching trends...")
    trends = fetch_trends()
    if not trends:
        print("No trends found. Skipping.")
        return None
    print(f"Found {len(trends)} headlines")

    # 2. Get recent topics to avoid repetition
    recent_topics = get_recent_topics(days=7)

    # 3. Generate post content
    print("Generating post content via GPT-5.4...")
    result = generate_post_content(trends, recent_topics)
    post_content = result["post"]
    image_prompt = result["image_prompt"]
    topics = result.get("topics", [])
    print(f"Generated post ({len(post_content)} chars) on topics: {topics}")

    # 4. Generate image
    print("Generating image via GPT-5.4...")
    image_path = generate_image(image_prompt)
    print(f"Image saved: {image_path}")

    # 5. Save draft
    draft_id = save_draft(post_content, image_prompt, image_path, topics)
    print(f"Draft saved with ID: {draft_id}")

    return draft_id


if __name__ == "__main__":
    run_pipeline()
