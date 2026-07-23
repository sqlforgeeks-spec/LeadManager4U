---
name: Search provider compatibility
description: Non-obvious compatibility constraints for compliant search and Bing Maps scraping in this environment.
---

Search providers can return HTTP 200 challenge pages or JavaScript-only shells, so status codes alone are not evidence of usable results. Block detection must inspect visible text rather than script content, and a parser-empty response should produce diagnostics or use a conservative compliant fallback instead of silently completing with zero leads.

**Why:** Google and Bing intermittently returned valid HTTP responses that contained no parseable SERP, while DuckDuckGo's server-rendered HTML remained usable. Generic block words inside scripts caused false positives.

**How to apply:** Keep provider fallback and pacing explicit in job logs. Bing Maps currently requires a real search-box Enter event before rendering cards; current business cards expose canonical data in `data-entity` JSON. Prefer Firefox/geckodriver when Chrome is unavailable in the Replit runtime.