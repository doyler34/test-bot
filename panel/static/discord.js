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

if (form && form.dataset.kind === "welcome") welcomeEditor();
if (form && form.dataset.kind === "greeting") greetingEditor();
if (form && form.dataset.kind === "names") namesEditor();

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

function welcomeEditor() {
  let doc = read("dc-data");
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
    channelSelect.replaceChildren(h("option", { value: "" }, bridge.start
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
      return h("div", { class: "dc-item dc-editing" },
        h("div", { class: "row" },
          h("button", { type: "button", class: "dc-open", title: "Close", on: { click: () => { open = null; drawButtons(); } } }, summary(button), " ▴"),
          mover(doc.buttons, i, drawButtons)),
        h("div", { class: "grid" },
          field("What it does", bound(button, "type", { options: Object.entries(types), redraw: true }, "select"), "span"),
          field("Label", bound(button, "label", { maxlength: 80 })),
          field("Emoji", bound(button, "emoji", { maxlength: 40, placeholder: "e.g. 🎟️" })),
          field("Line", bound(button, "line", { options: lines, redraw: true }, "select"))),
        h("div", {}, h("span", { class: "muted" }, "Colour "), styles),
        ...extra);
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
    embed.append(body, h("div", { class: "dc-embed-footer" }, "OYB • Start here"));
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
  document.getElementById("dc-reset").addEventListener("click", () => {
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
