/* Sidebar for the dashboard and the settings page.
 * Desktop: the button next to the logo collapses the sidebar to an icon rail (remembered in localStorage).
 * Under 900 px: the sidebar is an off-canvas drawer opened by the header's menu button, with a dark overlay;
 * it closes on an overlay click, Escape, a click outside or a link. */
(() => {
  const KEY = "btc-sidebar-collapsed";
  const $ = (id) => document.getElementById(id);
  const shell = document.querySelector(".shell"), side = $("sidebar");
  const menu = $("menuBtn"), collapse = $("collapseBtn"), scrim = $("navScrim");
  if (!shell || !side || !menu || !collapse || !scrim) return;

  const stored = () => { try { return localStorage.getItem(KEY) === "1"; } catch (_) { return false; } };
  const store = (on) => { try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (_) { /* private mode: not remembered */ } };

  function setCollapsed(on, remember) {
    shell.classList.toggle("collapsed", on);
    const label = on ? "Expand sidebar" : "Collapse sidebar";
    collapse.setAttribute("aria-expanded", String(!on));
    collapse.setAttribute("aria-label", label);
    collapse.title = label;
    if (remember) {
      store(on);
      window.dispatchEvent(new Event("resize"));      // charts take the freed width
    }
  }

  function setOpen(open, focusNav) {
    side.classList.toggle("open", open);
    scrim.hidden = !open;
    menu.setAttribute("aria-expanded", String(open));
    menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    if (open && focusNav) {
      const target = side.querySelector("nav a[aria-current]") || side.querySelector("nav a");
      if (target) target.focus();
    }
  }

  setCollapsed(stored(), false);
  collapse.addEventListener("click", () => setCollapsed(!shell.classList.contains("collapsed"), true));
  menu.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!side.classList.contains("open"), true); });
  scrim.addEventListener("click", () => setOpen(false));
  side.addEventListener("click", (e) => { if (e.target.closest("nav a")) setOpen(false); });
  document.addEventListener("click", (e) => {
    if (side.classList.contains("open") && !e.target.closest("#sidebar") && !e.target.closest("#menuBtn")) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && side.classList.contains("open")) { setOpen(false); menu.focus(); }
  });
  if (window.matchMedia) window.matchMedia("(max-width: 900px)").addEventListener("change", () => setOpen(false));
  window.BTCSidebar = { close: () => setOpen(false) };
})();
