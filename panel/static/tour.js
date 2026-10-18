/* The first-login tour: a splash, then a few steps on each of the pages staff use
 * most, each lighting up the part it talks about, then a closing card. */

const box = document.getElementById("tour");
if (box) addEventListener("load", () => setTimeout(() => startTour(box), 250));

function startTour(box) {
  const phone = window.matchMedia("(max-width: 720px)");
  const chapters = box.dataset.chapters.split(",");
  const target = (step) => {
    const selector = (phone.matches && step.dataset.phoneTarget) || step.dataset.target;
    if (!selector) return null;
    const el = document.querySelector(selector);
    return el && el.getClientRects().length ? el : undefined;
  };
  const all = [...box.querySelectorAll("[data-step]")];
  if (!all.length) return;
  let steps = all;
  let at = 0;

  const shade = document.createElement("div");
  shade.className = "tour-shade";
  const hole = document.createElement("div");
  hole.className = "tour-hole";
  const card = document.createElement("div");
  card.className = "tour-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-live", "polite");
  card.innerHTML = `<div class="tour-top"><span class="tour-chapter"></span><span class="tour-dots"></span></div>
    <div class="tour-body"></div>
    <div class="tour-foot"><span class="tour-count"></span>
      <button type="button" class="link tour-skip">Skip</button>
      <button type="button" class="small ghost tour-back">Back</button>
      <button type="button" class="small tour-next">Next</button></div>`;
  document.body.append(shade, hole, card);
  const next = card.querySelector(".tour-next");

  function place() {
    const step = steps[at];
    const el = target(step);
    const middle = !el;
    shade.hidden = !middle;
    hole.hidden = middle;
    card.classList.toggle("middle", middle);
    card.classList.toggle("splash", "splash" in step.dataset || "final" in step.dataset);
    if (middle) { card.style.top = card.style.left = ""; return; }
    const r = el.getBoundingClientRect();
    const pad = 6;
    Object.assign(hole.style, { top: `${r.top - pad}px`, left: `${r.left - pad}px`,
      width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    const w = card.offsetWidth, h = card.offsetHeight, gap = 16;
    let top = r.bottom + gap;
    let left = r.left;
    if (top + h > innerHeight - 8) top = r.top - h - gap;
    if (top < 8) {
      // No room above or below: beside it if there's room, else over its top.
      if (r.right + gap + w < innerWidth) { left = r.right + gap; top = Math.max(8, Math.min(r.top, innerHeight - h - 8)); }
      else top = Math.max(8, Math.min(innerHeight - h - 8, r.top + gap));
    }
    card.style.top = `${top}px`;
    card.style.left = `${Math.max(8, Math.min(left, innerWidth - w - 8))}px`;
  }

  function show(n, back = false) {
    // Steps whose part isn't on screen (no server picked, a button this person
    // doesn't have) are passed over.
    while (n >= 0 && n < steps.length && target(steps[n]) === undefined) n += back ? -1 : 1;
    if (n < 0) n = 0;
    if (n >= steps.length) { finish(); return; }
    at = n;
    const step = steps[at];
    const chapter = Number(step.dataset.chapter ?? -1);
    const mine = steps.filter((s) => s.dataset.chapter === step.dataset.chapter);
    card.querySelector(".tour-chapter").textContent =
      "splash" in step.dataset ? "Welcome" : "final" in step.dataset ? "All done" : chapters[chapter] || "";
    card.querySelector(".tour-dots").innerHTML = chapters.map((_, i) =>
      `<i class="${i < chapter || "final" in step.dataset ? "done" : i === chapter ? "on" : ""}"></i>`).join("");
    card.querySelector(".tour-count").textContent = chapter >= 0 && !("splash" in step.dataset)
      ? `${mine.indexOf(step) + 1 - mine.filter((s) => "splash" in s.dataset).length} of ${mine.length - mine.filter((s) => "splash" in s.dataset).length}`
      : "";
    const body = card.querySelector(".tour-body");
    body.innerHTML = step.innerHTML;
    body.classList.remove("in");
    void body.offsetWidth;
    body.classList.add("in");
    card.querySelector(".tour-back").hidden = at === 0;
    card.querySelector(".tour-skip").textContent = "splash" in step.dataset ? "Skip for now" : "Skip";
    card.querySelector(".tour-skip").hidden = "final" in step.dataset;
    next.textContent = "splash" in step.dataset ? "Show me round"
      : "final" in step.dataset ? "Finish"
      : step.dataset.next ? `Next: ${chapters[chapter + 1] || "next page"} →`
      : at === steps.length - 1 ? "Done" : "Next";
    const el = target(step);
    if (el) el.scrollIntoView({ block: "center", behavior: "instant" });
    place();
    next.focus({ preventScroll: true });
  }

  function close() {
    shade.remove(); hole.remove(); card.remove();
    removeEventListener("resize", place);
    removeEventListener("scroll", place, true);
    document.removeEventListener("keydown", keys);
  }

  function finish() {
    const csrf = document.querySelector("input[name=csrf]");
    fetch("/tour/done", { method: "POST", credentials: "same-origin", headers: { "X-CSRF": csrf ? csrf.value : "" } });
    close();
    if (location.search.includes("tour=1")) history.replaceState(null, "", location.pathname + location.hash);
  }

  function forward() {
    const step = steps[at];
    if (step.dataset.next) { location.href = step.dataset.next; return; }
    if ("final" in step.dataset || at === steps.length - 1) { finish(); return; }
    show(at + 1);
  }

  function keys(event) {
    if (event.key === "Escape") finish();
    else if (event.key === "ArrowRight") forward();
    else if (event.key === "ArrowLeft" && at) show(at - 1, true);
  }

  next.addEventListener("click", forward);
  card.querySelector(".tour-back").addEventListener("click", () => show(at - 1, true));
  card.querySelector(".tour-skip").addEventListener("click", finish);
  addEventListener("resize", place);
  addEventListener("scroll", place, true);
  document.addEventListener("keydown", keys);
  show(0);
}
