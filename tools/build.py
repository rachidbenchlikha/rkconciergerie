#!/usr/bin/env python3
"""Pre-render every page of the site to static HTML.

Source of truth: src/app.html (the whole site, content and translations).
Output: index.html, owners/index.html, fr/proprietaires/index.html, ... plus sitemap.xml and robots.txt.

Run from the repo root:   python3 tools/build.py
Needs: pip install playwright && playwright install chromium
"""
import http.server, os, re, shutil, socketserver, sys, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://rkconciergerie.com"
SLUGS = {
    "en": {"stay": "stays", "owners": "owners", "furniture": "furnish", "about": "about", "faq": "faq", "contact": "contact"},
    "fr": {"stay": "sejours", "owners": "proprietaires", "furniture": "ameublement", "about": "a-propos", "faq": "faq", "contact": "contact"},
    "ar": {"stay": "stays", "owners": "owners", "furniture": "furnish", "about": "about", "faq": "faq", "contact": "contact"},
}
PREFIX = {"en": "", "fr": "fr/", "ar": "ar/"}
VIEWS = ["home", "stay", "owners", "furniture", "about", "faq", "contact"]


def rel_dir(lang, view):
    slug = "" if view == "home" else SLUGS[lang][view]
    return PREFIX[lang] + (slug + "/" if slug else "")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)


def main():
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Quiet)  # any free port
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # clean previously generated pages (never touches src/, tools/, img/)
    for lang in ("fr", "ar"):
        shutil.rmtree(ROOT / lang, ignore_errors=True)
    for slug in SLUGS["en"].values():
        shutil.rmtree(ROOT / slug, ignore_errors=True)

    count = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        # keep builds reproducible: no live availability, no external fonts
        page.route("**/avail.json", lambda r: r.abort())
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for lang in ("en", "fr", "ar"):
            for view in VIEWS:
                page.goto(f"http://127.0.0.1:{port}/src/app.html")
                page.wait_for_function("typeof setLang==='function' && typeof show==='function'")
                page.evaluate(f"setLang('{lang}'); show('{view}')")
                html = page.content()
                out = ROOT / rel_dir(lang, view) / "index.html"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(html, encoding="utf-8")
                count += 1
        b.close()
    srv.shutdown()
    if errors:
        print("PAGE ERRORS:", errors)
        sys.exit(1)

    # sitemap with language alternates
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for view in VIEWS:
        for lang in ("en", "fr", "ar"):
            lines.append("  <url>")
            lines.append(f"    <loc>{SITE}/{rel_dir(lang, view)}</loc>")
            for l2 in ("en", "fr", "ar"):
                lines.append(f'    <xhtml:link rel="alternate" hreflang="{l2}" href="{SITE}/{rel_dir(l2, view)}"/>')
            lines.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{SITE}/{rel_dir("en", view)}"/>')
            lines.append("  </url>")
    lines.append("</urlset>")
    (ROOT / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /src/\nDisallow: /tools/\n\nSitemap: {SITE}/sitemap.xml\n", encoding="utf-8")
    print(f"built {count} pages + sitemap.xml + robots.txt")


if __name__ == "__main__":
    main()
