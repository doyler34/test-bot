document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !confirm(message)) event.preventDefault();
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-autosubmit]")) event.target.form.submit();
});

for (const box of document.querySelectorAll("[data-refresh]")) {
  setInterval(async () => {
    if (document.hidden || box.contains(document.activeElement)) return;
    try {
      const response = await fetch(box.dataset.refresh, { credentials: "same-origin" });
      if (response.status === 401) return location.reload();
      if (response.ok) box.innerHTML = await response.text();
    } catch (err) {}
  }, 10000);
}
