"use strict";

const chatEl = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const MAX_HISTORY = 12;

let history = []; // [{role: "user"|"assistant", content}]
let disclaimerShown = false;
let busy = false;
let lastQuestion = "";

// ---------- rendering helpers ----------

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

/** Small, safe markdown subset: paragraphs, bullet and numbered lists, **bold**. */
function renderMarkdown(text) {
  const html = [];
  let list = null;
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
  for (const raw of text.split(/\n/)) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      if (list !== tag) { closeList(); html.push(`<${tag}>`); list = tag; }
      html.push(`<li>${inline((bullet || numbered)[1])}</li>`);
    } else if (line === "") {
      closeList();
    } else {
      closeList();
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return html.join("");
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function scrollToBottom() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

function addUserMessage(text) {
  const bubble = el("div", { class: "bubble", dir: "auto", text });
  chatEl.append(el("div", { class: "msg user" }, [bubble]));
  scrollToBottom();
}

function addTyping() {
  const node = el("div", { class: "msg bot typing", "aria-label": "Assistant is typing" }, [
    el("div", { class: "bubble" }, [el("span"), el("span"), el("span")]),
  ]);
  chatEl.append(node);
  scrollToBottom();
  return node;
}

function sourcesBlock(sources) {
  const items = sources.map((s) => {
    const link = el("a", { href: s.url, target: "_blank", rel: "noopener", text: s.title });
    const section = s.sections && s.sections.length ? el("span", { class: "section", text: ` · ${s.sections.join(" · ")}` }) : "";
    return el("li", {}, [link, section]);
  });
  return el("div", { class: "sources" }, [el("div", { class: "sources-label", text: "Sources" }), el("ul", {}, items)]);
}

function whyBlock(data) {
  const d = data.debug || {};
  const lines = [
    `intent: ${data.intent} · language: ${data.language} · status: ${data.status} · ${d.ms ?? "?"} ms`,
    `searched for: ${d.standalone_question || ""}`,
    `keywords: ${d.search_query || ""}`,
    "passages retrieved:",
    ...(d.retrieved || []).map((r) => `  ${r.id} ${r.chunk} (score ${r.score}) ${r.section}`),
  ];
  if (d.checks && d.checks.length) lines.push("safety checks that fired:", ...d.checks.map((c) => `  - ${c}`));
  return el("details", { class: "why" }, [el("summary", { text: "How I found this" }), el("pre", { text: lines.join("\n") })]);
}

function leadForm(quoteUrl) {
  const node = document.getElementById("leadTemplate").content.firstElementChild.cloneNode(true);
  node.querySelector("textarea[name=question]").value = lastQuestion;
  node.querySelector("a.link").href = quoteUrl;
  const status = node.querySelector(".lead-status");
  node.addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = (name) => node.elements.namedItem(name); // not node.name: that's the form's own attribute
    const payload = {
      name: field("name").value.trim(),
      email: field("email").value.trim(),
      question: field("question").value.trim(),
      consent: field("consent").checked,
    };
    status.className = "lead-status";
    if (!payload.name || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(payload.email)) {
      status.textContent = "Please enter your name and a valid email.";
      status.classList.add("error");
      return;
    }
    if (!payload.consent) {
      status.textContent = "Please tick the box so PlacidWay may contact you.";
      status.classList.add("error");
      return;
    }
    const button = node.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const res = await fetch("/api/lead", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Request failed");
      status.textContent = "Thanks! Your request was saved.";
      node.querySelectorAll("input, textarea").forEach((field) => (field.disabled = true));
    } catch (err) {
      status.textContent = typeof err.message === "string" ? err.message : "Something went wrong. Please try again.";
      status.classList.add("error");
      button.disabled = false;
    }
  });
  return node;
}

function addBotMessage(data) {
  const bubble = el("div", { class: "bubble", dir: "auto", html: renderMarkdown(data.answer || "") });
  if (data.disclaimer) {
    bubble.append(el("div", { class: "disclaimer", dir: "auto", text: data.disclaimer }));
    disclaimerShown = true;
  }
  if (data.sources && data.sources.length) bubble.append(sourcesBlock(data.sources));
  if (data.offer_quote) {
    const button = el("button", { type: "button", class: "primary", text: "Request a free quote" });
    const actions = el("div", { class: "actions" }, [button]);
    button.addEventListener("click", () => {
      button.remove();
      actions.append(leadForm(data.quote_url));
      scrollToBottom();
    });
    bubble.append(actions);
  }
  bubble.append(whyBlock(data));
  chatEl.append(el("div", { class: "msg bot" }, [bubble]));
  scrollToBottom();
}

function addError(message, retryText) {
  const retry = el("button", { type: "button", class: "ghost small", text: "Try again" });
  const bubble = el("div", { class: "bubble" }, [el("p", { class: "error-text", text: message }), retry]);
  const node = el("div", { class: "msg bot" }, [bubble]);
  retry.addEventListener("click", () => { node.remove(); ask(retryText, { echo: false }); });
  chatEl.append(node);
  scrollToBottom();
}

// ---------- talking to the API ----------

async function ask(text, { echo = true } = {}) {
  if (busy || !text.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  lastQuestion = text;
  if (echo) addUserMessage(text);
  const typing = addTyping();
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: history.slice(-MAX_HISTORY), disclaimer_shown: disclaimerShown }),
    });
    const data = await res.json().catch(() => ({}));
    typing.remove();
    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "Sorry, something went wrong.";
      addError(detail, text);
      return;
    }
    addBotMessage(data);
    history.push({ role: "user", content: text }, { role: "assistant", content: data.answer || "" });
  } catch {
    typing.remove();
    addError("I couldn't reach the server. Check your connection and try again.", text);
  } finally {
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autosize();
  ask(text);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
}
input.addEventListener("input", autosize);

document.getElementById("chips").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (chip) ask(chip.textContent.trim());
});

document.getElementById("newChatBtn").addEventListener("click", () => {
  history = [];
  disclaimerShown = false;
  chatEl.querySelectorAll(".msg:not(.welcome)").forEach((node) => node.remove());
  input.focus();
});

// ---------- sources panel ----------

const panel = document.getElementById("sourcesPanel");
const sourcesBtn = document.getElementById("sourcesBtn");
let sourcesLoaded = false;

async function loadSources() {
  const meta = document.getElementById("sourcesMeta");
  const list = document.getElementById("sourcePages");
  try {
    const res = await fetch("/api/sources");
    const data = await res.json();
    const built = data.built_at ? new Date(data.built_at).toLocaleString() : "unknown";
    meta.textContent = `Content last fetched: ${built}`;
    list.replaceChildren(
      ...data.pages.map((p) =>
        el("li", {}, [
          el("a", { href: p.url, target: "_blank", rel: "noopener", text: p.title }),
          el("span", { class: "meta", text: `${p.page_type} · ${p.words} words · ${p.chunks} passages` }),
        ]),
      ),
    );
    sourcesLoaded = true;
  } catch {
    meta.textContent = "Couldn't load the source list.";
  }
}

function togglePanel(open) {
  panel.hidden = !open;
  sourcesBtn.setAttribute("aria-expanded", String(open));
  if (open && !sourcesLoaded) loadSources();
}
sourcesBtn.addEventListener("click", () => togglePanel(panel.hidden));
document.getElementById("closeSources").addEventListener("click", () => togglePanel(false));

input.focus();
