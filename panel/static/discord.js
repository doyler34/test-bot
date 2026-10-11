// The Discord message editors: builds the form from the saved message, keeps
// the preview in step as you type, and sends the whole thing back as JSON.

const form = document.getElementById("dc-editor");
const read = (id) => JSON.parse(document.getElementById(id).textContent);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "on") for (const [event, fn] of Object.entries(value)) el.addEventListener(event, fn);
    else if (key === "class") el.className = value;
    else if (value === true) el.setAttribute(key, "");
    else if (value !== false && value != null) el.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null) el.append(child);
  return el;
}

// Just enough Discord markdown for a faithful preview. Escaped first, so
// nothing typed can become real HTML.
const escape = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function markdown(text) {
  const safe = escape(text);
  return safe
    .replace(/&lt;a?:(\w+):\d+&gt;/g, ":$1:")
    .replace(/&lt;#(\d+)&gt;/g, (_, id) => `<span class="mention">#${escape(channelName(id))}</span>`)
    .replace(/\*\*(.+?)\*\*/gs, "<b>$1</b>")
    .replace(/__(.+?)__/gs, "<u>$1</u>")
    .replace(/\*(.+?)\*/gs, "<i>$1</i>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

let channels = [];
function channelName(id) {
  const found = channels.find((c) => c.id === String(id));
  return found ? found.name : "channel";
}

if (form && (form.dataset.kind === "welcome" || form.dataset.kind === "post")) welcomeEditor(form.dataset.kind === "post");
if (form && form.dataset.kind === "greeting") greetingEditor();
if (form && form.dataset.kind === "names") namesEditor();
if (form && form.dataset.kind === "serverinfo") serverInfoEditor();
if (form && form.dataset.kind === "bans") bansEditor();
if (form && form.dataset.kind === "matchping") matchPingEditor();
if (form && form.dataset.kind === "weekly") weeklyEditor();
if (form && form.dataset.kind === "staffalerts") {
  form.addEventListener("submit", () => {
    const field = (name) => form.querySelector(`[name="${name}"]`);
    document.getElementById("dc-doc").value = JSON.stringify({ on: field("on").checked, count: field("count").value,
      seconds: field("seconds").value, ping_role: field("ping_role").value,
      mines_on: field("mines_on").checked, mines: field("mines").value, mines_ping_role: field("mines_ping_role").value });
  });
}
if (form && form.dataset.kind === "feedback") {
  form.addEventListener("submit", () => {
    const field = (name) => form.querySelector(`[name="${name}"]`);
    document.getElementById("dc-doc").value = JSON.stringify({ on: field("on").checked, thanks: field("thanks").value,
      done_dm: field("done_dm").value, ping_role: field("ping_role").value,
      topics: field("topics").value.split("\n").map((t) => t.trim()).filter(Boolean) });
  });
}
if (form && form.dataset.kind === "factions") factionsEditor();
if (form && form.dataset.kind === "channels") {
  form.addEventListener("submit", () => {
    const channels = {}, roles = {};
    for (const el of form.querySelectorAll("[data-channel]")) if (el.value) channels[el.dataset.channel] = el.value;
    for (const el of form.querySelectorAll("[data-role]")) if (el.value) roles[el.dataset.role] = el.value;
    document.getElementById("dc-doc").value = JSON.stringify({ channels, roles });
  });
}

function matchPingEditor() {
  const field = (name) => form.querySelector(`[name="${name}"]`);
  const servers = [...form.querySelectorAll("[data-server]")];
  function draw() {
    const preview = document.getElementById("dc-preview");
    const on = servers.find((s) => s.checked);
    if (!on) { preview.replaceChildren(h("p", { class: "muted" }, "Every server is off: no match alerts at all.")); return; }
    const name = on.dataset.label;
    const fill = (text) => text.split("{server}").join(name);
    const box = h("div", { class: "dc-embed" });
    box.style.borderLeftColor = "#2ECC71";
    const body = h("div", {});
    body.innerHTML = markdown(fill(field("text").value || field("text").dataset.default) + "\nStarted 2 minutes ago.\n\nExpires in 28 minutes (30 minutes after match start).");
    box.append(h("div", { class: "dc-embed-title" }, fill(field("title").value || field("title").dataset.default)), body,
      h("div", { class: "dc-embed-footer" }, "OYB • Match notifications"));
    const ping = field("ping").checked ? h("div", {}, h("span", { class: "mention" }, `@${name} Notifications`)) : null;
    preview.replaceChildren(h("div", { class: "dc-author" }, h("span", { class: "dc-avatar" }), h("b", {}, "OYB"), h("span", { class: "dc-app" }, "APP")), ping, box);
  }
  form.addEventListener("input", draw);
  form.addEventListener("change", draw);
  form.addEventListener("submit", () => {
    const own = (name) => { const el = field(name); return el.value.trim() === el.dataset.default ? "" : el.value.trim(); };
    document.getElementById("dc-doc").value = JSON.stringify({ title: own("title"), text: own("text"), ping: field("ping").checked,
      off: servers.filter((s) => !s.checked).map((s) => s.dataset.server) });
  });
  draw();
}

function weeklyEditor() {
  const field = (name) => form.querySelector(`[name="${name}"]`);
  function draw() {
    const preview = document.getElementById("dc-preview");
    if (!field("on").checked) { preview.replaceChildren(h("p", { class: "muted" }, "Off: nothing is posted when the board resets.")); return; }
    const fill = (name) => (field(name).value || field(name).dataset.default).split("{week}").join("28 Sep");
    const box = h("div", { class: "dc-embed" });
    box.style.borderLeftColor = "#D9A441";
    const body = h("div", {});
    body.innerHTML = markdown([fill("intro"), "🥇 MONEYSPREAD78 — 632 kills\n🥈 vDutch-- — 499 kills\n🥉 Broadside-1 — 484 kills", fill("outro")].join("\n\n"));
    box.append(h("div", { class: "dc-embed-title" }, fill("title")), body);
    preview.replaceChildren(h("div", { class: "dc-author" }, h("span", { class: "dc-avatar" }), h("b", {}, "OYB"), h("span", { class: "dc-app" }, "APP")), box);
  }
  form.addEventListener("input", draw);
  form.addEventListener("change", draw);
  form.addEventListener("submit", () => {
    const own = (name) => { const el = field(name); return el.value.trim() === el.dataset.default ? "" : el.value.trim(); };
    document.getElementById("dc-doc").value = JSON.stringify({ on: field("on").checked, title: own("title"), intro: own("intro"), outro: own("outro") });
  });
  draw();
}

function bansEditor() {
  const bridge = read("dc-bridge");
  channels = bridge.channels;
  const field = (name) => form.querySelector(`[name="${name}"]`);
  const author = () => h("div", { class: "dc-author" }, h("span", { class: "dc-avatar" }), h("b", {}, "OYB"), h("span", { class: "dc-app" }, "APP"));
  function embed(title, text, fields) {
    const box = h("div", { class: "dc-embed" });
    box.style.borderLeftColor = "#6E2F29";
    box.append(h("div", { class: "dc-embed-title" }, title));
    if (text) { const body = h("div", {}); body.innerHTML = markdown(text); box.append(body); }
    for (const [name, value] of fields) {
      const f = h("div", { class: "dc-field" }, h("b", {}, name));
      const v = h("div", {});
      v.innerHTML = markdown(value);
      f.append(v);
      box.append(f);
    }
    return box;
  }
  function fill(text) {
    return text.split("{server}").join(bridge.guild || "OYB").split("{account}").join("Your account **Buford**")
      .split("{length}").join("for **7 days**");
  }
  const privateThread = () => form.querySelector('[name="ticket_private"]:checked')?.value === "1";
  function draw() {
    const dm = document.getElementById("dc-preview");
    if (!field("dm_enabled").checked) dm.replaceChildren(h("p", { class: "muted" }, "Off: banned players get no DM."));
    else {
      const fields = [["Reason", "Spawning explosions"], ["Ends", "Tuesday 7 October 2026 21:40 (in 7 days)"]];
      if (field("appeal").value.trim()) fields.push(["Appeal", field("appeal").value.trim()]);
      dm.replaceChildren(author(), embed(fill(field("dm_title").value || field("dm_title").dataset.default),
        fill(field("dm_text").value || field("dm_text").dataset.default), fields));
    }
    const ticket = document.getElementById("dc-preview-ticket");
    if (!field("tickets_enabled").checked) ticket.replaceChildren(h("p", { class: "muted" }, "Off: nothing is posted in tickets."));
    else {
      const url = field("panel_url").value.trim().replace(/\/$/, "");
      const lines = ["**Length:** 7 days", "**Ends:** Tuesday 7 October 2026 21:40 (in 7 days)",
        "**Reason:** Spawning explosions", "**Banned by:** Gazlagom", "`4bd39e3d-a6e3-4090-a338-00e1dc08ca69`"];
      if (url) lines.push(`${url}/player/4bd39e3d-…`);
      const card = embed(field("ticket_title").value || field("ticket_title").dataset.default,
        "@Buford opened this ticket while banned on this account.", [["Buford", lines.join("\n")]]);
      const role = form.querySelector('[name="ticket_role"]').value;
      if (privateThread()) ticket.replaceChildren(h("p", { class: "dc-thread" }, "🔒 Ban info · private thread · ", role ? `only ${role}` : "pick a role"), author(), card);
      else ticket.replaceChildren(author(), card);
    }
    for (const el of form.querySelectorAll(".dc-private-role")) el.hidden = !privateThread();
  }
  document.getElementById("dc-appeal-channel").addEventListener("change", (event) => {
    if (!event.target.value) return;
    const appeal = field("appeal");
    appeal.value = (appeal.value ? appeal.value.trimEnd() + " " : "") + `<#${event.target.value}>`;
    event.target.value = "";
    draw();
  });
  for (const chip of form.querySelectorAll("[data-insert]")) {
    chip.addEventListener("click", () => {
      const box = field(chip.dataset.into);
      const at = box.selectionStart ?? box.value.length;
      box.value = box.value.slice(0, at) + chip.dataset.insert + box.value.slice(box.selectionEnd ?? at);
      box.focus();
      draw();
    });
  }
  form.addEventListener("input", draw);
  form.addEventListener("change", draw);
  form.addEventListener("submit", () => {
    const own = (name) => { const el = field(name); return el.value.trim() === (el.dataset.default || "") ? "" : el.value.trim(); };
    document.getElementById("dc-doc").value = JSON.stringify({
      dm_enabled: field("dm_enabled").checked, dm_title: own("dm_title"), dm_text: own("dm_text"),
      appeal: field("appeal").value.trim(), tickets_enabled: field("tickets_enabled").checked,
      ticket_channel: field("ticket_channel").value || null,
      ticket_categories: [...form.querySelectorAll('[name="ticket_categories"]:checked')].map((c) => c.value),
      ticket_title: own("ticket_title"), panel_url: field("panel_url").value.trim(),
      ticket_private: privateThread(), ticket_role: form.querySelector('[name="ticket_role"]').value });
  });
  draw();
}

function serverInfoEditor() {
  const bridge = read("dc-bridge");
  const preview = document.getElementById("dc-preview");
  const value = (el) => el.value.trim() || el.dataset.default;
  const key = (name) => form.querySelector(`[data-key="${name}"]`);
  const author = () => h("div", { class: "dc-author" }, h("span", { class: "dc-avatar" }), h("b", {}, "OYB"), h("span", { class: "dc-app" }, "APP"));
  function embed(colour, title, text, fields = []) {
    const box = h("div", { class: "dc-embed" });
    box.style.borderLeftColor = colour;
    box.append(h("div", { class: "dc-embed-title" }, title));
    const body = h("div", { class: "dc-embed-text" });
    body.innerHTML = markdown(text);
    box.append(body);
    for (const [name, value] of fields) {
      const field = h("div", { class: "dc-field" }, h("b", {}, name));
      const text = h("div", {});
      text.innerHTML = markdown(value);
      field.append(text);
      box.append(field);
    }
    return box;
  }
  function draw() {
    const fields = [...form.querySelectorAll("[data-setting]")].map((el, i) => {
      const server = bridge.servers.find((s) => s.id === el.dataset.setting);
      return [server ? server.label : el.dataset.setting, `${["🟢 Match running · 46 min", "🟡 Up, waiting for a match", "🟢 Match running · 1h 12m"][i % 3]}\n**Settings:** ${value(el)}`];
    });
    preview.replaceChildren(author(),
      embed("#2ECC71", value(key("title")), value(key("intro")), fields),
      embed("#5865F2", value(key("rules_title")), value(key("rules"))));
  }
  form.addEventListener("input", draw);
  form.addEventListener("submit", () => {
    // Anything left as the bot's own wording is stored blank, so it keeps following the bot.
    const own = (el) => (el.value.trim() === (el.dataset.default || "").trim() ? "" : el.value.trim());
    const settings = {};
    for (const el of form.querySelectorAll("[data-setting]")) if (own(el)) settings[el.dataset.setting] = own(el);
    document.getElementById("dc-doc").value = JSON.stringify({
      title: own(key("title")), intro: own(key("intro")), rules_title: own(key("rules_title")), rules: own(key("rules")), settings });
  });
  draw();
}

function factionsEditor() {
  const rows = [...form.querySelectorAll("[data-faction]")];
  const preview = document.getElementById("dc-preview");
  const look = (row) => {
    const name = row.querySelector('[data-look="name"]');
    return { name: name.value.trim() || name.placeholder, emoji: row.querySelector('[data-look="emoji"]').value.trim(),
             colour: row.querySelector('input[data-look="colour"].mono').value.trim() };
  };
  for (const row of rows) {
    const pair = row.querySelectorAll('[data-look="colour"]');
    for (const input of pair) input.addEventListener("input", () => {
      for (const other of pair) if (other !== input && /^#[0-9a-fA-F]{6}$/.test(input.value)) other.value = input.value;
    });
  }
  function draw() {
    const members = h("div", { class: "dc-members" }, rows.map((row, i) => {
      const l = look(row);
      const name = h("b", {}, ["Havoc", "Fennel", "Dusty"][i]);
      name.style.color = l.colour;
      const role = h("span", { class: "dc-role" }, l.name);
      role.style.borderColor = l.colour;
      return h("div", { class: "dc-member" }, name, " ", role);
    }));
    const buttons = h("div", { class: "dc-row" }, rows.map((row) => {
      const l = look(row);
      return h("span", { class: "dc-btn s-grey" }, (l.emoji ? l.emoji + " " : "") + l.name);
    }));
    preview.replaceChildren(h("p", { class: "kicker" }, "Members who picked one"), members,
      h("p", { class: "kicker" }, "Faction picker buttons"), buttons);
  }
  form.addEventListener("input", draw);
  form.addEventListener("submit", () => {
    const factions = {};
    for (const row of rows) factions[row.dataset.faction] = look(row);
    document.getElementById("dc-doc").value = JSON.stringify({ factions });
  });
  draw();
}

function namesEditor() {
  const inputs = [...form.querySelectorAll("[data-server]")];
  const preview = document.getElementById("dc-preview");
  const name = (input) => input.value.trim() || input.dataset.default;
  function draw() {
    const stats = h("div", { class: "dc-channels" }, h("div", { class: "dc-category" }, "⌄ SERVER STATUS"),
      inputs.map((input, i) => h("div", { class: "dc-channel" }, `${["🟢", "🟡", "🟢"][i % 3]} ${name(input)} · ${["46 min", "Waiting for match", "1h 12m"][i % 3]}`)));
    const buttons = h("div", { class: "dc-row" }, inputs.map((input) => h("span", { class: "dc-btn s-blurple" }, `🔔 ${name(input)}`)));
    preview.replaceChildren(h("p", { class: "kicker" }, "Stats channels"), stats,
      h("p", { class: "kicker" }, "Match notification buttons"), buttons);
  }
  form.addEventListener("input", draw);
  form.addEventListener("submit", () => {
    const names = {};
    for (const input of inputs) if (input.value.trim()) names[input.dataset.server] = input.value.trim();
    document.getElementById("dc-doc").value = JSON.stringify({ names });
  });
  draw();
}

function welcomeEditor(isPost) {
  let doc = read("dc-data");
  if (!doc.sections.length) doc.sections.push({ heading: "", text: "" });
  const defaults = read("dc-default");
  const bridge = read("dc-bridge");
  const types = read("dc-types");
  channels = bridge.channels;
  const STYLES = { green: "Green", blurple: "Blurple", grey: "Grey", red: "Red" };
  const FACTIONS = ["US", "USSR", "FIA"];
  const sectionsBox = document.getElementById("dc-sections");
  const buttonsBox = document.getElementById("dc-buttons");
  const preview = document.getElementById("dc-preview");
  const newId = () => Math.random().toString(16).slice(2, 10);

  // Channel list, grouped by category the way Discord shows them.
  const channelSelect = document.getElementById("dc-channel");
  function fillChannels() {
    channelSelect.replaceChildren(h("option", { value: "" }, isPost ? "Choose a channel…" : bridge.start
      ? `Start here channel from the bot's settings (#${channelName(bridge.start)})` : "The bot's Start here channel"));
    const groups = {};
    for (const c of channels) (groups[c.category || "No category"] ||= []).push(c);
    for (const [category, list] of Object.entries(groups)) {
      channelSelect.append(h("optgroup", { label: category }, list.map((c) => h("option", { value: c.id }, "#" + c.name))));
    }
    if (doc.channel_id && !channels.some((c) => c.id === String(doc.channel_id))) {
      channelSelect.append(h("option", { value: doc.channel_id }, `Channel ${doc.channel_id} (not seen by the bot)`));
    }
    channelSelect.value = doc.channel_id ? String(doc.channel_id) : "";
  }

  function bindTop() {
    for (const input of form.querySelectorAll(".dc-edit > section:first-child [data-field]")) {
      const field = input.dataset.field;
      input.value = doc[field] == null ? "" : doc[field];
      input.addEventListener("input", () => {
        doc[field] = field === "channel_id" ? (input.value || null) : input.value;
        if (field === "colour") for (const other of form.querySelectorAll('[data-field="colour"]')) if (other !== input) other.value = input.value;
        draw();
      });
    }
  }

  function mover(list, index, redraw) {
    const move = (step) => () => {
      const to = index + step;
      if (to < 0 || to >= list.length) return;
      [list[index], list[to]] = [list[to], list[index]];
      redraw();
    };
    return h("span", { class: "dc-move" },
      h("button", { type: "button", class: "small", title: "Move up", on: { click: move(-1) } }, "↑"),
      h("button", { type: "button", class: "small", title: "Move down", on: { click: move(1) } }, "↓"),
      h("button", { type: "button", class: "small danger", title: "Remove", on: { click: () => { list.splice(index, 1); redraw(); } } }, "✕"));
  }

  function field(label, input, cls = "") {
    return h("label", { class: cls }, label, input);
  }

  function bound(obj, key, attrs = {}, tag = "input") {
    const input = h(tag, attrs);
    if (tag === "select") for (const [value, text] of attrs.options) input.append(h("option", { value }, text));
    if (attrs.type === "checkbox") input.checked = !!obj[key];
    else input.value = obj[key] == null ? "" : obj[key];
    input.addEventListener(attrs.type === "checkbox" || tag === "select" ? "change" : "input", () => {
      obj[key] = attrs.type === "checkbox" ? input.checked : input.value;
      if (attrs.redraw) drawButtons();
      draw();
    });
    return input;
  }

  function drawSections() {
    sectionsBox.replaceChildren(...doc.sections.map((section, i) => h("div", { class: "dc-item" },
      h("div", { class: "row" }, h("b", {}, `Section ${i + 1}`), mover(doc.sections, i, drawSections)),
      field("Heading (optional)", bound(section, "heading", { maxlength: 256 })),
      field("Text", bound(section, "text", { rows: 5 }, "textarea")))));
    draw();
  }

  function roleInput(button) {
    const box = h("div", { class: "dc-role" });
    const pick = h("select");
    pick.append(h("option", { value: "" }, bridge.roles.length ? "Choose a role…" : "The bot hasn't sent the role list yet"));
    for (const role of bridge.roles) {
      pick.append(h("option", { value: role.id, disabled: !!role.problem }, role.problem ? `${role.name} (can't use: ${role.problem})` : role.name));
    }
    pick.append(h("option", { value: "new" }, "+ Make a new role…"));
    const name = h("input", { placeholder: "New role's name", maxlength: 100 });
    name.value = button.role_name || "";
    const known = button.role_id && bridge.roles.some((r) => r.id === String(button.role_id));
    pick.value = known ? String(button.role_id) : (button.role_name || !bridge.roles.length ? "new" : "");
    if (button.role_id && !known) pick.append(h("option", { value: String(button.role_id), selected: true }, `Role ${button.role_id} (not seen by the bot)`));
    const sync = () => {
      name.hidden = pick.value !== "new";
      if (pick.value === "new") { button.role_id = null; button.role_name = name.value; }
      else { button.role_id = pick.value || null; button.role_name = ""; }
      draw();
    };
    pick.addEventListener("change", sync);
    name.addEventListener("input", sync);
    name.hidden = pick.value !== "new";
    box.append(field("Role", pick), name);
    return box;
  }

  let open = null;

  function summary(button) {
    const what = types[button.type] || button.type;
    const swatch = h("span", { class: `dc-swatch s-${button.type === "url" ? "grey" : button.style}` });
    return h("span", { class: "dc-summary" }, h("span", { class: "muted" }, `Line ${button.line}`), swatch,
      h("b", {}, [button.emoji, button.label].filter(Boolean).join(" ") || "(no label)"), h("span", { class: "muted" }, what));
  }

  function drawButtons() {
    const lines = [1, 2, 3, 4, 5].map((n) => [n, `Line ${n}`]);
    buttonsBox.replaceChildren(...doc.buttons.map((button, i) => {
      if (open !== button.id) {
        return h("div", { class: "dc-item dc-closed" },
          h("div", { class: "row" },
            h("button", { type: "button", class: "dc-open", title: "Edit this button", on: { click: () => { open = button.id; drawButtons(); } } }, summary(button)),
            mover(doc.buttons, i, drawButtons)));
      }
      const extra = [];
      if (button.type === "faction") extra.push(field("Faction", bound(button, "faction", { options: FACTIONS.map((f) => [f, f]) }, "select")));
      if (button.type === "role" || button.type === "pick") {
        extra.push(roleInput(button));
        if (button.type === "pick") {
          extra.push(field("Group", bound(button, "group", { maxlength: 40, placeholder: "e.g. Squad" })));
          extra.push(h("label", { class: "check" }, bound(button, "locked", { type: "checkbox" }), " Locked: once picked, only an admin can change it (applies to the whole group)"));
        }
        extra.push(h("label", { class: "check" }, bound(button, "linked_only", { type: "checkbox" }), " Only for players who've linked their Reforger account"));
      }
      if (button.type === "url") extra.push(field("Web address", bound(button, "url", { placeholder: "https://", maxlength: 512 })));
      const styles = button.type === "url" ? h("span", { class: "muted" }, "Link buttons are always grey.")
        : h("span", { class: "dc-styles" }, Object.entries(STYLES).map(([value, label]) => h("label", { class: `dc-style s-${value}` },
          h("input", { type: "radio", name: `style-${button.id}`, value, checked: button.style === value,
            on: { change: () => { button.style = value; drawButtons(); } } }), label)));
      const head = h("button", { type: "button", class: "dc-open", title: "Close", on: { click: () => { open = null; drawButtons(); } } }, summary(button), " ▴");
      const item = h("div", { class: "dc-item dc-editing", on: { input: () => head.replaceChildren(summary(button), " ▴") } },
        h("div", { class: "row" }, head, mover(doc.buttons, i, drawButtons)),
        h("div", { class: "grid" },
          field("What it does", bound(button, "type", { options: Object.entries(types), redraw: true }, "select"), "span"),
          field("Label", bound(button, "label", { maxlength: 80 })),
          field("Emoji", bound(button, "emoji", { maxlength: 40, placeholder: "e.g. 🎟️" })),
          field("Line", bound(button, "line", { options: lines, redraw: true }, "select"))),
        h("div", {}, h("span", { class: "muted" }, "Colour "), styles),
        ...extra);
      return item;
    }));
    document.getElementById("dc-count").textContent = `${doc.buttons.length} of 25`;
    draw();
  }

  function draw() {
    const text = doc.sections.map((s) => s.heading && s.text ? `**${s.heading}**\n${s.text}` : (s.heading ? `**${s.heading}**` : s.text))
      .filter(Boolean).join("\n\n");
    const embed = h("div", { class: "dc-embed" });
    embed.style.borderLeftColor = /^#[0-9a-f]{6}$/i.test(doc.colour) ? doc.colour : "#A9BC8C";
    if (doc.title) embed.append(h("div", { class: "dc-embed-title" }, doc.title));
    const body = h("div", { class: "dc-embed-text" });
    body.innerHTML = markdown(text);
    embed.append(body);
    if (!isPost) embed.append(h("div", { class: "dc-embed-footer" }, "OYB • Start here"));
    const rows = [1, 2, 3, 4, 5].map((line) => doc.buttons.filter((b) => Number(b.line) === line)).filter((row) => row.length);
    const buttons = rows.map((row) => h("div", { class: "dc-row" }, row.map((b) =>
      h("span", { class: `dc-btn s-${b.type === "url" ? "grey" : b.style}` }, b.emoji ? b.emoji.replace(/<a?:(\w+):\d+>/, ":$1:") + " " : "", b.label, b.type === "url" ? " ↗" : ""))));
    preview.replaceChildren(h("div", { class: "dc-author" }, h("span", { class: "dc-avatar" }), h("b", {}, "OYB"), h("span", { class: "dc-app" }, "APP")), embed, ...buttons);
  }

  document.getElementById("dc-add-section").addEventListener("click", () => {
    doc.sections.push({ heading: "", text: "" });
    drawSections();
  });
  document.getElementById("dc-add-button").addEventListener("click", () => {
    if (doc.buttons.length >= 25) return;
    const line = Math.max(1, ...doc.buttons.map((b) => Number(b.line)));
    const id = newId();
    doc.buttons.push({ id, type: "role", label: "", emoji: "", style: "grey", line, role_id: null, role_name: "", linked_only: false });
    open = id;
    drawButtons();
  });
  document.getElementById("dc-reset")?.addEventListener("click", () => {
    if (!confirm("Replace everything in the editor with the original Start here message? Nothing is saved until you press Save or Publish.")) return;
    doc = structuredClone(defaults);
    fillChannels();
    bindTopValues();
    drawSections();
    drawButtons();
  });
  function bindTopValues() {
    for (const input of form.querySelectorAll(".dc-edit > section:first-child [data-field]")) {
      const value = doc[input.dataset.field];
      input.value = value == null ? "" : value;
    }
  }
  form.addEventListener("submit", () => {
    document.getElementById("dc-doc").value = JSON.stringify(doc);
  });

  fillChannels();
  bindTop();
  drawSections();
  drawButtons();
}

function greetingEditor() {
  const bridge = read("dc-bridge");
  channels = bridge.channels;
  const text = form.querySelector('[name="text"]');
  const preview = document.getElementById("dc-preview");
  const where = () => form.querySelector('[name="where"]:checked').value;
  const sample = { "{user}": "@NewPlayer", "{name}": "NewPlayer", "{server}": bridge.guild || "OYB",
                   "{members}": "1,204", "{start}": bridge.start ? `<#${bridge.start}>` : "#start-here" };
  function draw() {
    let filled = text.value;
    for (const [key, value] of Object.entries(sample)) filled = filled.split(key).join(value);
    const body = h("div", { class: "dc-text" });
    body.innerHTML = markdown(filled).replace(/@NewPlayer/g, '<span class="mention">@NewPlayer</span>');
    preview.replaceChildren(
      h("p", { class: "kicker" }, where() === "dm" ? "Sent to them privately" : "Posted in the channel"),
      h("div", { class: "dc-author" }, h("span", { class: "dc-avatar" }), h("b", {}, "OYB"), h("span", { class: "dc-app" }, "APP")), body);
    form.querySelector(".dc-channel-field").hidden = where() === "dm";
  }
  form.addEventListener("input", draw);
  form.addEventListener("change", draw);
  for (const chip of form.querySelectorAll("[data-insert]")) {
    chip.addEventListener("click", () => {
      const at = text.selectionStart ?? text.value.length;
      text.value = text.value.slice(0, at) + chip.dataset.insert + text.value.slice(text.selectionEnd ?? at);
      text.focus();
      draw();
    });
  }
  form.addEventListener("submit", () => {
    document.getElementById("dc-doc").value = JSON.stringify({
      enabled: form.querySelector('[name="enabled"]').checked, where: where(),
      channel_id: form.querySelector('[name="channel_id"]').value || null, text: text.value });
  });
  draw();
}
