/** Safe rendering of untrusted strings: neutralize bidi / invisible controls. */

export const INVISIBLE_CHARS =
  /[\u202A-\u202E\u2066-\u2069\u061C\u200E\u200F\u200B\u200C\u200D\u2060\uFEFF]/g;

const PILL_RE = /\u2039U\+[0-9A-Fa-f]{4,6}\u203A/g;

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
