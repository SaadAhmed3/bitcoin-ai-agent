"""In-page checks used by the e2e tests (manual accessibility checks; axe-core is not available offline)."""
from __future__ import annotations

ACCESSIBLE_NAME_JS = r"""
() => {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
      && getComputedStyle(el).visibility !== "hidden" && !el.closest("[hidden]");
  const nameOf = (el) => {
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label");
    const by = el.getAttribute("aria-labelledby");
    if (by) return by.split(/\s+/).map((id) => (document.getElementById(id) || {}).textContent || "").join(" ");
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.textContent; }
    const wrap = el.closest("label");
    if (wrap) return wrap.textContent;
    if (el.tagName === "BUTTON" || el.tagName === "A") return el.textContent || el.getAttribute("title") || "";
    return el.getAttribute("title") || el.getAttribute("placeholder") || "";
  };
  const out = [];
  document.querySelectorAll("button, a[href], input:not([type=hidden]), select, textarea").forEach((el) => {
    if (!visible(el) || el.closest(".modebar")) return;      // Plotly's own toolbar is out of scope
    if (!nameOf(el).trim()) out.push(el.outerHTML.slice(0, 120));
  });
  return out;
}
"""

H_SCROLL_JS = r"""
() => {
  const d = document.documentElement, out = [];
  if (d.scrollWidth > d.clientWidth + 1) out.push(`page scrollWidth ${d.scrollWidth} > ${d.clientWidth}`);
  // Elements that stick out of the viewport and are not inside a scroll container.
  document.querySelectorAll("body *").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > d.clientWidth + 1) {
      let p = el.parentElement, clipped = false;
      while (p && p !== document.body) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === "auto" || ox === "scroll" || ox === "hidden") { clipped = true; break; }
        p = p.parentElement;
      }
      if (!clipped && !el.closest(".sidebar")) out.push(`${el.tagName.toLowerCase()}#${el.id}.${el.className} right=${Math.round(r.right)}`);
    }
  });
  return out.slice(0, 10);
}
"""


def accessible_name_problems(page) -> list[str]:
    return page.evaluate(ACCESSIBLE_NAME_JS)


def no_horizontal_scroll(page) -> list[str]:
    return page.evaluate(H_SCROLL_JS)

# Visible text that is only a placeholder: a bare dash, "— · —%", or the words NaN/null/undefined anywhere.
PLACEHOLDER_JS = r"""
() => {
  const bad = [];
  const words = /(^|[^A-Za-z])(undefined|NaN|null)([^A-Za-z]|$)/;
  const bare = /^[\s—–\-·%$]*$/;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!el || el.closest("svg, script, style, .modebar, textarea") || !el.getClientRects().length) continue;
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none") continue;
    const t = n.textContent.trim();
    if (!t) continue;
    if ((bare.test(t) && /[—–]/.test(t)) || /—\s*·\s*—|—%/.test(t) || words.test(t)) bad.push(`${el.tagName.toLowerCase()}#${el.id}: ${t.slice(0, 60)}`);
  }
  return bad;
}
"""

# WCAG contrast of an element's text against its effective (blended) background.
CONTRAST_JS = r"""
(sel) => {
  const parse = (c) => {
    let m = /rgba?\(([^)]+)\)/.exec(c);
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = /color\(srgb ([^)]+)\)/.exec(c);
    if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
    return [0, 0, 0, 0];
  };
  const blend = (top, bottom) => { const a = top[3]; return [0, 1, 2].map((i) => top[i] * a + bottom[i] * (1 - a)).concat(1); };
  const el = document.querySelector(sel);
  if (!el) return null;
  const layers = [];
  for (let e = el; e; e = e.parentElement) layers.push(parse(getComputedStyle(e).backgroundColor));
  let bg = [255, 255, 255, 1];
  for (const l of layers.reverse()) if (l[3] > 0) bg = blend(l, bg);
  const fg = blend(parse(getComputedStyle(el).color), bg);
  const lum = (c) => { const [r, g, b] = c.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}
"""


def placeholder_problems(page) -> list[str]:
    return page.evaluate(PLACEHOLDER_JS)


def contrast(page, selector: str) -> float | None:
    return page.evaluate(CONTRAST_JS, selector)
