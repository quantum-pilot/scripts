# /// script
# dependencies = [
#   "beautifulsoup4",
#   "python-slugify",
#   "requests",
# ]
# ///

import logging
import os
import requests
from bs4 import BeautifulSoup
from slugify import slugify
from typing import Any, List, Dict
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

READ_SHORT = 120
READ_LONG  = 300
CLIP_ERROR_CONTENT = 100


def _aggregated_stories() -> Dict[str, Any]:
    rr = HTTP.get(
        "https://api.hcker.news/api/timeline?page=1&sort_by=score&filter=top20&limit=100",
        timeout=READ_SHORT,
    )
    if rr.status_code != 200:
        logging.error(f"HN stories fetch failed: {rr.status_code} {rr.text[:CLIP_ERROR_CONTENT]}")
    rr.raise_for_status()
    return rr.json()["stories"]


def _extract_comments(story_id: int, max_children: int = 5):
    r = HTTP.get(
        f"https://news.ycombinator.com/item?id={story_id}",
        headers={"User-Agent": "HN-digest-scraper (contact: lab@waffles.space)"},
        timeout=READ_SHORT,
    )
    if r.status_code != 200:
        logging.error(f"HN comments fetch failed: {r.status_code} {r.text[:CLIP_ERROR_CONTENT]}")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("tr.athing.comtr")

    def extract(row):
        def get_indent() -> int:
            ind = row.select_one("td.ind")
            if ind is None:
                return 0
            return int(ind["indent"])

        default = row.select_one("td.default")
        comhead = default.select_one(".comhead") if default else None
        author_el = comhead.select_one(".hnuser") if comhead else None
        text_el = default.select_one(".commtext") if default else None

        return {
            "author": author_el.get_text(strip=True) if author_el else None,
            "text": text_el.get_text(" ", strip=True) if text_el else "",
            "indent": get_indent(),
            "replies": [],
        }

    roots: list[dict] = []
    collect_depths = (0,)
    last_at_indent: dict[int, dict] = {}

    for row in rows:
        node = extract(row)
        d = node["indent"]

        for k in list(last_at_indent.keys()):
            if k > d:
                last_at_indent.pop(k, None)

        if d == 0:
            if len(roots) < max_children:
                roots.append(node)
                if 0 in collect_depths:
                    last_at_indent[0] = node
            else:
                break
        else:
            parent = last_at_indent.get(d - 1)
            if parent and (d - 1) in collect_depths and len(parent["replies"]) < max_children:
                parent["replies"].append(node)
                if d in collect_depths:
                    last_at_indent[d] = node
            else:
                continue

    return roots


def _make_session():
    s = requests.Session()
    retry = Retry(
        total=3, backoff_factor=0.5,
        status_forcelist=[429, 502, 503, 504, 524],
        allowed_methods=["GET", "POST"]
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


MCP_HOST = os.environ["MCP_HOST"]
MCP_TOKEN = os.environ["MCP_TOKEN"]
LITELLM_HOST = os.environ["LITELLM_HOST"]
LITELLM_KEY = os.environ["LITELLM_KEY"]
OWUI_HOST = os.environ["OWUI_HOST"]
OWUI_TOKEN = os.environ["OWUI_TOKEN"]
KNOWLEDGE_ID = os.environ["KNOWLEDGE_ID"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
HTTP = _make_session()


def _summarize(title: str, text: str, comments: str) -> str:
    sys = (
        "TITLE contains the title of the article. "
        "CONTENT contains the main text content of the article. If this is empty, the article content could not be fetched. "
        "COMMENTS contain top user comments on the article in numbered format. Each comment may have replies indented as bulleted list below it. "
        "Explain the article. Include any relevant context for uncommon topics. "
        "However, if the article is a showcase of some tool, product or service, focus on describing its uniqueness, purpose and key features instead of being detailed. "
        "Summarize the comments separately under 'Comments' sub-heading below, highlighting interesting points or perspectives. "
        "Make sure to include your take on the article and its comments separately below under 'LLM perspective' sub-heading. "
        "RULES for article explanation, comment summary and LLM perspective: "
        "Avoid redundancy, repetition, and fluff. "
        "Ignore URLs, ads, and irrelevant information accidentally included in content. "
        "Be concise and precise, but do not omit or compress important details. "
        "DO NOT include title in the summary. "
        "Respect markdown format in output and emit only in markdown format. "
    )
    usr = f"TITLE: {title}\nCONTENT:\n{text}\n\nCOMMENTS:\n{comments}"
    r = HTTP.post(
        f"http://{LITELLM_HOST}/v1/chat/completions",
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        json={
            "model": "gpt-5-medium-4096",
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": usr},
            ],
        },
        timeout=READ_LONG,
    )
    if r.status_code != 200:
        logging.error(f"LLM failed: {r.status_code} {r.text[:CLIP_ERROR_CONTENT]}")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _webfetch(url: str) -> str:
    rr = HTTP.post(
        f"http://{MCP_HOST}/fetch/fetch",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {MCP_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "max_length": 50_000,
            "raw": False,
            "start_index": 0,
            "url": url,
        },
        timeout=READ_SHORT,
    )
    if rr.status_code != 200:
        logging.error(f"Fetch failed: {rr.status_code} {rr.text[:CLIP_ERROR_CONTENT]}")
        logging.info("Attempting fallback...")
        rr = HTTP.post(
            "https://api.tavily.com/extract",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {TAVILY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"urls": [url]},
            timeout=READ_LONG,
        )
        if rr.status_code != 200:
            logging.error(f"Tavily fetch failed: {rr.status_code} {rr.text[:CLIP_ERROR_CONTENT]}")
        else:
            return rr.json()["results"][0]["raw_content"]
    rr.raise_for_status()
    return rr.text


def _upload_markdown(md: str, filename: str) -> str:
    files = {"file": (filename, md.encode("utf-8"), "text/markdown")}
    rr = HTTP.post(
        f"http://{OWUI_HOST}/api/v1/files/",
        headers={
            "Authorization": f"Bearer {OWUI_TOKEN}",
            "Accept": "application/json",
        },
        files=files,
        timeout=READ_SHORT,
    )
    if rr.status_code != 200:
        logging.error(f"Upload failed: {rr.status_code} {rr.text[:CLIP_ERROR_CONTENT]}")
    rr.raise_for_status()
    return rr.json()["id"]


def _attach_to_kb(file_id: str):
    rr = HTTP.post(
        f"http://{OWUI_HOST}/api/v1/knowledge/{KNOWLEDGE_ID}/file/add",
        headers={
            "Authorization": f"Bearer {OWUI_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"file_id": file_id},
        timeout=READ_SHORT,
    )
    if rr.status_code != 200:
        logging.error(f"Attach to KB failed: {rr.status_code} {rr.text[:CLIP_ERROR_CONTENT]}")
    rr.raise_for_status()


def _fetch_kb_files() -> List[str]:
    rr = HTTP.get(
        f"http://{OWUI_HOST}/api/v1/knowledge/{KNOWLEDGE_ID}",
        headers={
            "Authorization": f"Bearer {OWUI_TOKEN}",
            "Accept": "application/json",
        },
        timeout=READ_SHORT,
    )
    if rr.status_code != 200:
        logging.error(f"Fetch KB files failed: {rr.status_code} {rr.text[:CLIP_ERROR_CONTENT]}")
    rr.raise_for_status()
    return [f["meta"]["name"] for f in rr.json()["files"]]


if __name__ == "__main__":
    http = _make_session()
    dt_start = datetime.now()
    utc_yday = (
        (dt_start - timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    )
    stories = [
        s for s in _aggregated_stories() if s["utc_day"][:10] == utc_yday
    ]
    stories.sort(key=lambda x: x["score"], reverse=True)

    uploaded = []
    existing_files = _fetch_kb_files()

    for i, s in enumerate(stories[:20]):
        sid = s["id"]
        title = s["title"]
        url = s.get("url")
        logging.info(f"Processing {i+1}/{len(stories)}: {title} ({sid})")
        filename = f"{utc_yday}-{(slugify(title)[:50] if url else sid)}.md"
        if filename in existing_files:
            logging.info(f"Skipping already uploaded: {filename}")
            continue

        score = s["score"]

        page_content = ""
        hn_link = f"https://news.ycombinator.com/item?id={sid}"
        resolved_url = url or hn_link
        if url:
            try:
                logging.info(f"Fetching article content from {url}...")
                page_content = _webfetch(url)
            except Exception as e:
                logging.error(f"Fetch failed for {url}: {e}")

        comments = ""
        try:
            logging.info(f"Fetching comments for post {sid}...")
            res = _extract_comments(int(sid))
            comments = [(c["text"], [r["text"] for r in c["replies"]]) for c in res]
            concat_replies = lambda replies: "\n" + "\n".join(
                f"    - {r}" for r in replies
            )
            comments = "\n".join(
                f"{i + 1}. {c}{concat_replies(replies)}"
                for i, (c, replies) in enumerate(comments)
            )
        except Exception as e:
            logging.error(f"Comments fetch failed for {hn_link}: {e}")

        md = f"# {title}\n\n- Score: {score} | [HN]({hn_link}) | Link: {resolved_url}\n\n"
        if page_content or comments:
            try:
                logging.info(f"Generating summary for {resolved_url}...")
                page_summary = _summarize(title, page_content, comments)
                md += "\n" + page_summary + "\n\n"
            except Exception as e:
                logging.error(f"LLM summary failed: {e}")

        try:
            logging.info(f"Uploading to OWUI as {filename}...")
            file_id = _upload_markdown(md, filename)
            _attach_to_kb(file_id)
            uploaded.append({"story_id": sid, "file_id": file_id, "title": title})
        except Exception as e:
            logging.error(f"OWUI API failure: {e}")
