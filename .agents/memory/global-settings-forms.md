---
name: Global settings forms
description: Durable guidance for settings forms whose visible controls can be changed with JavaScript.
---

Global settings forms should submit the visible preset/custom controls and have the server resolve and validate the final values. Hidden fields maintained only by JavaScript are too easy to leave stale when scripts fail, a tab remains open across a deploy, or a browser restores form state.

**Why:** A stale hidden field made the SMTP global-limit save appear broken even though the route and database model were working.

**How to apply:** For similar settings, keep JavaScript limited to presentation (for example, showing a custom input), accept legacy field names briefly when practical, and show validation errors instead of silently preserving the old value.