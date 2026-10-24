# RK Conciergerie website

Live at https://rkconciergerie.com (GitHub Pages).

- `src/app.html` is the source of the whole site: content, translations (EN / FR / AR), styles and scripts. **Edit this file.**
- `python3 tools/build.py` pre-renders it into a real page for every URL and language (`/`, `/owners/`, `/fr/proprietaires/`, `/ar/faq/` ...) and rewrites `sitemap.xml` and `robots.txt`. Commit the generated files together with the source.
- Build needs `pip install playwright` and `playwright install chromium`.
- Do not edit the generated `index.html` files by hand; the next build overwrites them.
