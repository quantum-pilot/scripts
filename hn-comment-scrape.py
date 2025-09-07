# /// script
# dependencies = [
#   "beautifulsoup4",
#   "requests",
# ]
# ///

import json
import requests
import sys
from bs4 import BeautifulSoup

MAX_CHILDREN = 5

story_id = int(sys.argv[1])


if __name__ == "__main__":
    r = requests.get(
        f"https://news.ycombinator.com/item?id={story_id}",
        headers={"User-Agent": "HN-comment-scraper (contact: you@example.com)"},
        timeout=15,
    )
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
            if len(roots) < MAX_CHILDREN:
                roots.append(node)
                if 0 in collect_depths:
                    last_at_indent[0] = node
            else:
                break
        else:
            parent = last_at_indent.get(d - 1)
            if parent and (d - 1) in collect_depths and len(parent["replies"]) < MAX_CHILDREN:
                parent["replies"].append(node)
                if d in collect_depths:
                    last_at_indent[d] = node
            else:
                continue

    print(json.dumps(roots))
