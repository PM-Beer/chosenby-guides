#!/usr/bin/env python3
"""Build the brand guide feed from a Content Engine export.

Usage: python3 publish.py <export_dir> <feed_repo_dir>

<export_dir> holds the Content Engine's database export: content/<id>.json and
guides/<contentId>__<brand>.json (ArtifactData list with out_dir writes exactly this).
Every guide whose status is "approved" (Publish pressed) or "live" goes into
<feed_repo_dir>/<brand>.json; drafts and unpublished guides are left out, so
Unpublish removes a guide on the next run. Prints one line per brand and a final
line PUBLISH-PLAN: <comma-separated guide ids that are approved and now in the feed>.
"""
import json, os, sys, glob, datetime

BRANDS = ["careerhacks", "hrguru", "hireme", "careerreport", "jobsearchplaybook",
          "landthejob", "futureofwork", "sidehustlecareers", "remotejobreport"]


def load(path):
    d = json.load(open(path))
    return d.get("data", d)


def md_to_guide(md):
    """Turn a Markdown guide (older Content Engine drafts) into the structured guide shape."""
    import re
    lines = md.replace("\r", "").split("\n")
    title, sections, cur = "", [], None
    for ln in lines:
        if ln.startswith("# ") and not title:
            title = ln[2:].strip(); cur = {"h": "", "lines": []}; sections.append(cur); continue
        if ln.startswith("## "):
            cur = {"h": ln[3:].strip(), "lines": []}; sections.append(cur); continue
        if cur is None:
            cur = {"h": "", "lines": []}; sections.append(cur)
        cur["lines"].append(ln)

    def split_blocks(text_lines):
        """Return [(kind, text)] where kind is 'p' or 'code'."""
        out, buf, code, inc = [], [], [], False
        for ln in text_lines:
            if ln.strip().startswith("```"):
                if inc:
                    out.append(("code", "\n".join(code).strip())); code = []
                else:
                    if buf: out.append(("p", "\n".join(buf).strip())); buf = []
                inc = not inc; continue
            (code if inc else buf).append(ln)
        if buf: out.append(("p", "\n".join(buf).strip()))
        return [(k, t) for k, t in out if t]

    def clean(t):
        t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()

    blurb, intro, steps, tip = "", "", [], ""
    for sec in sections:
        h, body = sec["h"], sec["lines"]
        if not h:
            blurb = clean("\n".join(body)); continue
        if h.lower().startswith("why"):
            intro = clean("\n".join(body)); continue
        if h.lower().endswith("tip"):
            keep = [l for l in body if not l.lower().startswith("got this from")]
            tip = clean("\n".join(keep)); continue
        # split on bold "Step N:" lines; text before the first one becomes its own lead-in
        chunks, cur = [], {"title": h, "lines": []}
        for ln in body:
            m = re.match(r"\s*\*\*(Step [^*]+?)\*\*\s*(.*)", ln)
            if m:
                if any(x.strip() for x in cur["lines"]) and cur["title"] != h or chunks or any(x.strip() for x in cur["lines"]):
                    chunks.append(cur)
                cur = {"title": m.group(1).rstrip(".").strip(), "lines": [m.group(2)] if m.group(2) else []}
            else:
                cur["lines"].append(ln)
        chunks.append(cur)
        lead = ""
        for ch in chunks:
            blocks = split_blocks(ch["lines"])
            paras = [clean(t) for k, t in blocks if k == "p"]
            codes = [t for k, t in blocks if k == "code"]
            if ch["title"] == h and len(chunks) > 1 and not codes:
                lead = "\n\n".join(paras); continue
            body_txt = "\n\n".join(([lead] if lead else []) + paras); lead = ""
            steps.append({"title": ch["title"], "body": body_txt, "prompt": "\n\n".join(codes)})
    for i, st in enumerate(steps):
        if not st["title"].lower().startswith("step"):
            st["title"] = f"Step {i+1}: {st['title']}"
    half = max(1, (len(steps) + 1) // 2)
    return {"title": title, "short": "", "blurb": blurb, "inside": [s["title"].split(":", 1)[-1].strip() for s in steps],
            "intro": intro, "free": steps[:half], "locked": steps[half:], "tip": tip}


def main(export_dir, feed_dir):
    content = {}
    for f in glob.glob(os.path.join(export_dir, "content", "*.json")):
        c = load(f)
        content[os.path.basename(f)[:-5]] = c
    # every brand that already has a list, plus any brand a guide names: a new brand gets its file automatically
    known = set(BRANDS) | {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(feed_dir, "*.json"))}
    for f in glob.glob(os.path.join(export_dir, "guides", "*.json")):
        b = load(f).get("brand")
        if b and b.isalnum() and b.islower():
            known.add(b)
    feed = {b: [] for b in sorted(known)}
    queued = []
    for f in glob.glob(os.path.join(export_dir, "guides", "*.json")):
        gid = os.path.basename(f)[:-5]
        g = load(f)
        if g.get("status") not in ("approved", "live") or g.get("brand") not in feed:
            continue
        G, c = g.get("guide"), content.get(g.get("contentId"))
        if not G and g.get("body"):
            G = md_to_guide(g["body"])
        if not G or not c or not c.get("slug") or not G.get("free"):
            print(f"SKIP {gid}: missing guide body, content or slug")
            continue
        feed[g["brand"]].append({
            "slug": c["slug"], "word": c.get("keyword", ""), "topic": c.get("stage", ""),
            "tool": "ChatGPT or Claude", "title": G.get("title") or c.get("guideTitle", ""),
            "short": G.get("short", ""), "blurb": G.get("blurb", ""), "inside": G.get("inside", []),
            "intro": G.get("intro", ""), "free": G.get("free", []), "locked": G.get("locked", []),
            "tip": G.get("tip", ""), "contentId": g["contentId"],
            "publishedAt": g.get("liveAt") or g.get("approvedAt") or g.get("updatedAt", ""),
        })
        if g["status"] == "approved":
            queued.append(gid)
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for b, items in feed.items():
        items.sort(key=lambda x: x["publishedAt"], reverse=True)
        path = os.path.join(feed_dir, b + ".json")
        old = json.load(open(path)).get("guides") if os.path.exists(path) else None
        if old != items:
            json.dump({"brand": b, "updatedAt": now, "guides": items}, open(path, "w"), indent=1, ensure_ascii=False)
        print(f"{b}: {len(items)} guide(s){'' if old != items else ' (unchanged)'}")
    print("PUBLISH-PLAN: " + ",".join(sorted(queued)))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
