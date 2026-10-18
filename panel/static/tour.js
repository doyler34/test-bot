/* First-login tour of the dashboard: each step lights up the part it talks about. */

const box = document.getElementById("tour");
if (box) startTour(box);

function startTour(box) {
  const phone = window.matchMedia("(max-width: 720px)");
  const target = (step) => {
    const selector = (phone.matches && step.dataset.phoneTarget) || step.dataset.target;
    const el = selector && document.querySelector(selector);
    return el && el.getClientRects().length ? el : null;
  };
  const steps = [...box.querySelectorAll("[data-step]")].filter((s) => !s.dataset.target || target(s));
  let at = 0;

  const shade = document.createElement("div");
  shade.className = "tour-shade";
  const hole = document.createElement("div");
  hole.className = "tour-hole";
  const card = document.createElement("div");
  card.className = "tour-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.innerHTML = `<div class="tour-body"></div>
    <div class="tour-foot"><span class="tour-count"></span>
      <button type="button" class="link tour-skip">Skip</button>
      <button type="button" class="small ghost tour-back">Back</button>
      <button type="button" class="small tour-next">Next</button></div>`;
  document.body.append(shade, hole, card);

  function place() {
    const el = target(steps[at]);
    shade.hidden = Boolean(el);
    hole.hidden = !el;
    card.classList.toggle("middle", !el);
    if (!el) { card.style.top = card.style.left = ""; return; }
    const r = el.getBoundingClientRect();
    const pad = 6;
    Object.assign(hole.style, { top: `${r.top - pad}px`, left: `${r.left - pad}px`,
      width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    const w = card.offsetWidth, h = card.offsetHeight, gap = 14;
    let top = r.bottom + gap;
    if (top + h > innerHeight - 8) top = r.top - h - gap;
    if (top < 8) top = Math.max(8, Math.min(innerHeight - h - 8, r.top + gap));
    let left = r.left;
    if (r.width < w && r.right + gap + w < innerWidth && r.height > h) { left = r.right + gap; top = Math.max(8, r.top); }
    card.style.top = `${top}px`;
    card.style.left = `${Math.max(8, Math.min(left, innerWidth - w - 8))}px`;
  }

  function show(n) {
    at = n;
    const step = steps[at];
    card.querySelector(".tour-body").innerHTML = step.innerHTML;
    card.querySelector(".tour-count").textContent = `${at + 1} of ${steps.length}`;
    card.querySelector(".tour-back").hidden = at === 0;
    card.querySelector(".tour-next").textContent = at === steps.length - 1 ? "Done" : "Next";
    const el = target(step);
    if (el) el.scrollIntoView({ block: "center", behavior: "instant" });
    place();
    card.querySelector(".tour-next").focus();
  }

  function finish() {
    const csrf = document.querySelector("input[name=csrf]");
    fetch("/tour/done", { method: "POST", credentials: "same-origin", headers: { "X-CSRF": csrf ? csrf.value : "" } });
    shade.remove(); hole.remove(); card.remove();
    removeEventListener("resize", place);
    removeEventListener("scroll", place, true);
    document.removeEventListener("keydown", keys);
    if (location.search.includes("tour=1")) history.replaceState(null, "", location.pathname);
  }

  function keys(event) {
    if (event.key === "Escape") finish();
    else if (event.key === "ArrowRight") card.querySelector(".tour-next").click();
    else if (event.key === "ArrowLeft" && at) show(at - 1);
  }

  card.querySelector(".tour-next").addEventListener("click", () => (at === steps.length - 1 ? finish() : show(at + 1)));
  card.querySelector(".tour-back").addEventListener("click", () => show(at - 1));
  card.querySelector(".tour-skip").addEventListener("click", finish);
  addEventListener("resize", place);
  addEventListener("scroll", place, true);
  document.addEventListener("keydown", keys);
  show(0);
}
