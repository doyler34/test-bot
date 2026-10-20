// Keeps a server's console page live: re-reads the log every few seconds and
// stays at the bottom unless you've scrolled up to read something.
const live = document.querySelector("[data-live]");
if (live) {
  const atBottom = () => live.scrollHeight - live.scrollTop - live.clientHeight < 40;
  live.scrollTop = live.scrollHeight;
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const reply = await fetch(live.dataset.live, { credentials: "same-origin" });
      if (!reply.ok) return;
      const text = await reply.text();
      if (text === live.textContent) return;
      const stick = atBottom();
      live.textContent = text;
      if (stick) live.scrollTop = live.scrollHeight;
    } catch (e) { /* try again next time */ }
  }, 3000);
}
