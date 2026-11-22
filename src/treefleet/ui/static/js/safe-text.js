/** Safe rendering of untrusted strings: neutralize bidi / invisible controls. */

export const INVISIBLE_CHARS =
  /[\u202A-\u202E\u2066-\u2069\u061C\u200E\u200F\u200B\u200C\u200D\u2060\uFEFF]/g;

const PILL_RE = /\u2039U\+[0-9A-Fa-f]{4,6}\u203A/g;

function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
}

/** Replace listed invisible / bidi controls with a visible ‹U+XXXX› placeholder. */
export function sanitizeBidi(text) {
  return String(text == null ? "" : text).replace(INVISIBLE_CHARS, (ch) => {
    const code = ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0");
    return `\u2039U+${code}\u203A`;
  });
}

export function safeText(text) {
  return sanitizeBidi(text);
}

/**
 * HTML text context: escape, then wrap each listed code-point placeholder in a bidi-pill span.
 * For use with innerHTML sinks only.
 */
export function escText(value) {
  const escaped = escapeHtml(sanitizeBidi(value));
  return escaped.replace(PILL_RE, (placeholder) => {
    const code = placeholder.replace(/^\u2039|\u203A$/g, "");
    return `<span class="bidi-pill" aria-label="Unicode ${code}" title="${code}">${placeholder}</span>`;
  });
}

/**
 * Attribute context: escape, then keep plain ‹U+XXXX› placeholders (never markup).
 */
export function escAttr(value) {
  return escapeHtml(sanitizeBidi(value));
}

/** @deprecated Prefer escText (HTML body) or escAttr (attributes). */
export function esc(value) {
  return escText(value);
}

function makePill(placeholder) {
  const code = placeholder.replace(/^\u2039|\u203A$/g, "");
  const pill = document.createElement("span");
  pill.className = "bidi-pill";
  pill.setAttribute("aria-label", `Unicode ${code}`);
  pill.title = code;
  pill.appendChild(document.createTextNode(placeholder));
  return pill;
}

/**
 * Append sanitized text as text nodes and warning pills (never raw invisible chars).
 * Idempotent on already-sanitized ‹U+XXXX› placeholders.
 */
export function appendSafeText(parent, text) {
  const s = sanitizeBidi(text);
  if (!s) return;
  let last = 0;
  PILL_RE.lastIndex = 0;
  let match = PILL_RE.exec(s);
  while (match) {
    if (match.index > last) {
      parent.appendChild(document.createTextNode(s.slice(last, match.index)));
    }
    parent.appendChild(makePill(match[0]));
    last = match.index + match[0].length;
    match = PILL_RE.exec(s);
  }
  if (last < s.length) {
    parent.appendChild(document.createTextNode(s.slice(last)));
  }
}

/** Replace an element's content with safe text (clears children first). */
export function setSafeText(el, text) {
  while (el.firstChild) el.removeChild(el.firstChild);
  appendSafeText(el, text);
}
