/** diff-render.js — Unified/Split diff renderer for TreeFleet.
 * Vanilla ES module, no dependencies, dark only.
 * Parser lives in diff-parse.js; this module renders the parsed diff.
 */

import { parseUnifiedDiff, wordDiff } from './diff-parse.js';
import { appendSafeText } from './safe-text.js';

const VIEW_MODE_STORAGE = 'treefleet_diff_view';
const COLLAPSED_STORAGE = 'treefleet_diff_collapsed';
const BATCH_SIZE = 200;
const MAX_INITIAL_ROWS = 5000;
const LARGE_FILE_LINES = 400;
const GUTTER_WIDTH = 44;
const WORD_DIFF_THRESHOLD = 0.6;

/** Create text node — never innerHTML */
function text(str) {
  return document.createTextNode(String(str || ''));
}

function el(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.appendChild(text(content));
  return node;
}

function icon(name, ariaLabel) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', ariaLabel ? 'false' : 'true');
  if (ariaLabel) svg.setAttribute('aria-label', ariaLabel);
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttributeNS('http://www.w3.org/1999/xlink', 'href', `/static/icons.svg#icon-${name}`);
  svg.appendChild(use);
  return svg;
}

function getViewMode(viewSupported) {
  if (!viewSupported) return 'unified';
  try {
    const stored = localStorage.getItem(VIEW_MODE_STORAGE);
    return stored === 'split' ? 'split' : 'unified';
  } catch {
    return 'unified';
  }
}

function setViewMode(mode) {
  try {
    localStorage.setItem(VIEW_MODE_STORAGE, mode);
  } catch {
    /* ignore */
  }
}

function getCollapsedSet() {
  try {
    const raw = localStorage.getItem(COLLAPSED_STORAGE);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function updateCollapsedSet(set) {
  try {
    localStorage.setItem(COLLAPSED_STORAGE, JSON.stringify([...set]));
  } catch {
    /* ignore */
  }
}

/**
 * Create diff pane renderer
 * @param {HTMLElement} root - Container element
 * @param {object} options - { auth, getWorktreePath, onFetch }
 * @returns {object} - { render, refresh }
 */
export function createDiffPane(root, options = {}) {
  const { auth, getWorktreePath, onFetch } = options;

  let currentData = null;
  let currentViewMode = 'unified';
  let currentWrap = false;
  let collapsedPaths = getCollapsedSet();
  let largeSeen = new Set();
  let activeFilePath = null;
  let scrollTop = 0;
  let fetchToken = 0;
  let fileHashes = new Map();
  let keysBound = false;

  function hashText(value) {
    const s = String(value || '');
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16);
  }

  function filePatchHash(file, rawDiff) {
    if (file && file.raw) return hashText(file.raw);
    const path = file && file.path ? file.path : '';
    const marker = path ? `diff --git a/${path}` : '';
    let chunk = '';
    if (marker && rawDiff && rawDiff.includes(marker)) {
      const start = rawDiff.indexOf(marker);
      const next = rawDiff.indexOf('\ndiff --git ', start + 1);
      chunk = next === -1 ? rawDiff.slice(start) : rawDiff.slice(start, next);
    } else if (file && file.hunks) {
      chunk = JSON.stringify(file.hunks);
    }
    return hashText(
      `${path}|${file && file.status}|${file && file.additions}|${file && file.deletions}|${chunk}`
    );
  }

  function getContainerWidth() {
    return root.clientWidth || 600;
  }

  function supportsSplit() {
    return getContainerWidth() >= 900;
  }

  function formatBytes(bytes) {
    const kb = bytes / 1024;
    if (kb < 1024) return `${Math.round(kb)} KB`;
    return `${(kb / 1024).toFixed(1)} MB`;
  }

  function copyToClipboard(text) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  }

  function captureState(container) {
    const scroller = container.querySelector('.diff-scroller');
    if (scroller) scrollTop = scroller.scrollTop;
    const active = container.querySelector('.diff-file-card.is-active');
    if (active) activeFilePath = active.dataset.path;
  }

  function restoreState(container) {
    if (activeFilePath) {
      const card = container.querySelector(`.diff-file-card[data-path="${CSS.escape(activeFilePath)}"]`);
      if (card) {
        card.classList.add('is-active');
        card.scrollIntoView({ block: 'nearest' });
      }
    }
    const scroller = container.querySelector('.diff-scroller');
    if (scroller && scrollTop > 0) {
      scroller.scrollTop = scrollTop;
    }
  }

  /** Render a single unified line */
  function renderUnifiedLine(line) {
    // Use diff-line class for test compatibility
    const row = el('tr', `diff-line diff-row diff-${line.type}`);
    if (line.type === 'add') row.classList.add('add');
    if (line.type === 'del') row.classList.add('del');
    
    // Old line number
    const oldTd = el('td', 'diff-ln diff-ln-old ln');
    if (line.oldNo != null) {
      oldTd.appendChild(text(line.oldNo));
    }
    row.appendChild(oldTd);

    // New line number
    const newTd = el('td', 'diff-ln diff-ln-new ln');
    if (line.newNo != null) {
      newTd.appendChild(text(line.newNo));
    }
    row.appendChild(newTd);

    // Sign column — visible ASCII +/- plus sr-only label (state never color-only).
    const signTd = el('td', 'diff-sign');
    if (line.type === 'add') {
      signTd.appendChild(text('+'));
      signTd.appendChild(el('span', 'visually-hidden', 'added'));
    } else if (line.type === 'del') {
      signTd.appendChild(text('-'));
      signTd.appendChild(el('span', 'visually-hidden', 'removed'));
    } else {
      signTd.appendChild(text('\u00a0'));
    }
    row.appendChild(signTd);

    // Code cell — safe text / pills only; never raw bidi controls.
    const codeTd = el('td', 'diff-code code');
    if (line.segments) {
      line.segments.forEach((seg) => {
        if (seg.changed) {
          const mark = el('mark', 'diff-word-highlight');
          appendSafeText(mark, seg.text);
          codeTd.appendChild(mark);
        } else {
          appendSafeText(codeTd, seg.text);
        }
      });
    } else {
      appendSafeText(codeTd, line.text);
    }
    row.appendChild(codeTd);
    // Filler so row tint spans the full card width when the table is stretched.
    row.appendChild(el('td', 'diff-fill'));

    return row;
  }

  /** Render a single split row (may be add-only, del-only, or both) */
  function renderSplitRow(leftLine, rightLine) {
    const row = el('tr', 'diff-line diff-row diff-split-row');
    const leftIsDel = !!(leftLine && leftLine.type === 'del');
    const rightIsAdd = !!(rightLine && rightLine.type === 'add');
    if (leftIsDel) row.classList.add('del', 'diff-del');
    if (rightIsAdd) row.classList.add('add', 'diff-add');

    function paintSide(cells, kind) {
      cells.forEach((td) => {
        td.classList.remove('diff-side-del', 'diff-side-add', 'diff-side-pad');
        td.classList.add(kind);
      });
    }

    // Left side (old/del) — tint only when it holds a deletion; else neutral pad.
    const leftLnTd = el('td', 'diff-ln diff-ln-old ln');
    if (leftLine && leftLine.oldNo != null) {
      leftLnTd.appendChild(text(leftLine.oldNo));
    }
    row.appendChild(leftLnTd);

    const leftSignTd = el('td', 'diff-sign');
    if (leftIsDel) {
      leftSignTd.appendChild(text('-'));
      leftSignTd.appendChild(el('span', 'visually-hidden', 'removed'));
    } else {
      leftSignTd.appendChild(text('\u00a0'));
    }
    row.appendChild(leftSignTd);

    const leftCodeTd = el('td', 'diff-code diff-code-old code');
    if (leftLine) {
      if (leftLine.segments) {
        leftLine.segments.forEach((seg) => {
          if (seg.changed) {
            const mark = el('mark', 'diff-word-highlight');
            appendSafeText(mark, seg.text);
            leftCodeTd.appendChild(mark);
          } else {
            appendSafeText(leftCodeTd, seg.text);
          }
        });
      } else {
        appendSafeText(leftCodeTd, leftLine.text);
      }
    }
    row.appendChild(leftCodeTd);
    paintSide([leftLnTd, leftSignTd, leftCodeTd], leftIsDel ? 'diff-side-del' : 'diff-side-pad');

    // Right side (new/add) — tint only when it holds an addition; else neutral pad.
    const rightLnTd = el('td', 'diff-ln diff-ln-new ln');
    if (rightLine && rightLine.newNo != null) {
      rightLnTd.appendChild(text(rightLine.newNo));
    }
    row.appendChild(rightLnTd);

    const rightSignTd = el('td', 'diff-sign');
    if (rightIsAdd) {
      rightSignTd.appendChild(text('+'));
      rightSignTd.appendChild(el('span', 'visually-hidden', 'added'));
    } else {
      rightSignTd.appendChild(text('\u00a0'));
    }
    row.appendChild(rightSignTd);

    const rightCodeTd = el('td', 'diff-code diff-code-new code');
    if (rightLine) {
      if (rightLine.segments) {
        rightLine.segments.forEach((seg) => {
          if (seg.changed) {
            const mark = el('mark', 'diff-word-highlight');
            appendSafeText(mark, seg.text);
            rightCodeTd.appendChild(mark);
          } else {
            appendSafeText(rightCodeTd, seg.text);
          }
        });
      } else {
        appendSafeText(rightCodeTd, rightLine.text);
      }
    }
    row.appendChild(rightCodeTd);
    paintSide([rightLnTd, rightSignTd, rightCodeTd], rightIsAdd ? 'diff-side-add' : 'diff-side-pad');

    const fill = el('td', 'diff-fill diff-side-pad');
    row.appendChild(fill);

    return row;
  }

  /** Compute word diff for paired del/add lines */
  function computeWordDiff(allLines) {
    const pairs = [];
    for (let i = 0; i < allLines.length; i++) {
      const line = allLines[i];
      if (line.type === 'del') {
        const nextLine = allLines[i + 1];
        if (nextLine && nextLine.type === 'add') {
          const oldText = line.text || '';
          const newText = nextLine.text || '';
          const similarity = Math.min(oldText.length, newText.length) / Math.max(oldText.length, newText.length, 1);
          if (similarity >= WORD_DIFF_THRESHOLD) {
            pairs.push({ delIndex: i, addIndex: i + 1, oldText, newText });
          }
        }
      }
    }

    pairs.forEach(({ delIndex, addIndex, oldText, newText }) => {
      // wordDiff(a,b) highlights tokens present in b relative to a.
      allLines[delIndex].segments = wordDiff(newText, oldText).filter((s) => s.text);
      allLines[addIndex].segments = wordDiff(oldText, newText).filter((s) => s.text);
    });
  }

  /** Pair lines for split view */
  function pairLinesForSplit(allLines) {
    const rows = [];
    let i = 0;
    while (i < allLines.length) {
      const line = allLines[i];
      if (line.type === 'ctx') {
        rows.push({ left: line, right: line });
        i++;
      } else if (line.type === 'del') {
        const nextLine = allLines[i + 1];
        if (nextLine && nextLine.type === 'add') {
          rows.push({ left: line, right: nextLine });
          i += 2;
        } else {
          rows.push({ left: line, right: null });
          i++;
        }
      } else if (line.type === 'add') {
        rows.push({ left: null, right: line });
        i++;
      } else {
        i++;
      }
    }
    return rows;
  }

  /** Render hunk header */
  function renderHunkHeader(hunk, viewMode) {
    const row = el('tr', 'diff-row diff-hunk');
    const td = el('td', 'diff-hunk-header');
    td.colSpan = viewMode === 'split' ? 7 : 5;
    appendSafeText(td, hunk.header || '');
    if (hunk.section) {
      td.appendChild(text(' '));
      appendSafeText(td, hunk.section);
    }
    row.appendChild(td);
    return row;
  }

  /** Render file table in batches */
  function renderFileTable(file, viewMode, wrap) {
    // Add diff-body for test compatibility
    const table = el('table', 'diff-table diff-body');
    table.classList.toggle('diff-wrap', wrap);

    const colgroup = document.createElement('colgroup');
    const colClasses = viewMode === 'split'
      ? ['diff-col-ln', 'diff-col-sign', 'diff-col-code', 'diff-col-ln', 'diff-col-sign', 'diff-col-code', 'diff-col-fill']
      : ['diff-col-ln', 'diff-col-ln', 'diff-col-sign', 'diff-col-code', 'diff-col-fill'];
    colClasses.forEach((cls) => {
      const col = document.createElement('col');
      col.className = cls;
      colgroup.appendChild(col);
    });
    table.appendChild(colgroup);
    if (viewMode === 'split') table.classList.add('diff-split');

    const tbody = document.createElement('tbody');
    table.appendChild(tbody);

    const allLines = [];
    file.hunks.forEach(hunk => {
      allLines.push(...hunk.lines);
    });

    computeWordDiff(allLines);

    const rows = [];
    file.hunks.forEach((hunk) => {
      rows.push({ type: 'hunk', hunk });
      if (viewMode === 'split') {
        pairLinesForSplit(hunk.lines).forEach((pair) => {
          rows.push({ type: 'split', pair });
        });
      } else {
        hunk.lines.forEach((line) => {
          rows.push({ type: 'line', line });
        });
      }
    });

    const initialCap = Math.min(rows.length, MAX_INITIAL_ROWS);

    function appendBatch(start, limit) {
      const end = Math.min(start + BATCH_SIZE, limit);
      for (let i = start; i < end; i++) {
        const item = rows[i];
        if (item.type === 'hunk') {
          tbody.appendChild(renderHunkHeader(item.hunk, viewMode));
        } else if (item.type === 'split') {
          tbody.appendChild(renderSplitRow(item.pair.left, item.pair.right));
        } else {
          tbody.appendChild(renderUnifiedLine(item.line));
        }
      }
      if (end < limit) {
        requestAnimationFrame(() => appendBatch(end, limit));
      }
    }

    appendBatch(0, initialCap);
    if (rows.length > MAX_INITIAL_ROWS) {
      const expandRow = el('tr', 'diff-row diff-expand-row');
      const td = el('td', 'diff-expand-cell');
      td.colSpan = viewMode === 'split' ? 7 : 5;
      const remaining = rows.length - MAX_INITIAL_ROWS;
      const btn = el('button', 'btn ghost diff-expand-btn', `Expand ${remaining} more lines`);
      btn.type = 'button';
      btn.addEventListener('click', () => {
        expandRow.remove();
        appendBatch(MAX_INITIAL_ROWS, rows.length);
      });
      td.appendChild(btn);
      expandRow.appendChild(td);
      tbody.appendChild(expandRow);
    }

    return table;
  }

  /** Render file card */
  function renderFileCard(file, index, viewMode, wrap) {
    const card = el('div', 'diff-file-card');
    card.dataset.path = file.path || '';
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', file.path || 'file');

    const lineCount = (file.hunks || []).reduce((sum, h) => sum + h.lines.length, 0);
    if (lineCount > LARGE_FILE_LINES && file.path && !largeSeen.has(file.path)) {
      largeSeen.add(file.path);
      collapsedPaths.add(file.path);
      updateCollapsedSet(collapsedPaths);
    }
    const isCollapsed = collapsedPaths.has(file.path);
    const shouldCollapse = lineCount > LARGE_FILE_LINES;

    // Header
    const header = el('div', 'diff-file-header');
    const chevronBtn = el('button', 'diff-chevron-btn');
    chevronBtn.type = 'button';
    chevronBtn.setAttribute('aria-expanded', String(!isCollapsed));
    chevronBtn.appendChild(icon('chevron-right'));
    header.appendChild(chevronBtn);

    // Status badge
    const statusBadge = el('span', 'diff-status-badge');
    if (file.status === 'added') {
      statusBadge.appendChild(text('Added'));
    } else if (file.status === 'deleted') {
      statusBadge.appendChild(text('Deleted'));
    } else if (file.status === 'renamed') {
      statusBadge.appendChild(text(`Renamed ${file.similarity != null ? `(${file.similarity}%)` : ''}`));
      if (file.oldPath) {
        statusBadge.appendChild(text(' '));
        const oldSpan = el('span', 'diff-old-path');
        appendSafeText(oldSpan, file.oldPath);
        statusBadge.appendChild(oldSpan);
        statusBadge.appendChild(text(' → '));
      }
    } else if (file.modeOld && file.modeNew && file.modeOld !== file.modeNew) {
      statusBadge.appendChild(text(`Mode changed ${file.modeOld} → ${file.modeNew}`));
    } else if (file.binary) {
      statusBadge.appendChild(text('Binary'));
    } else {
      statusBadge.appendChild(text('Modified'));
    }
    header.appendChild(statusBadge);

    // Path
    const pathWrap = el('div', 'diff-file-path');
    pathWrap.dir = 'ltr';
    pathWrap.style.unicodeBidi = 'isolate';
    const pathParts = (file.path || '').split('/');
    if (pathParts.length > 1) {
      const dir = el('span', 'diff-path-dir');
      appendSafeText(dir, pathParts.slice(0, -1).join('/') + '/');
      pathWrap.appendChild(dir);
    }
    const filename = el('span', 'diff-path-file');
    appendSafeText(filename, pathParts[pathParts.length - 1] || file.path || '');
    pathWrap.appendChild(filename);
    header.appendChild(pathWrap);

    // Copy path button
    const copyBtn = el('button', 'btn icon-btn ghost diff-copy-path-btn');
    copyBtn.type = 'button';
    copyBtn.title = 'Copy path';
    copyBtn.setAttribute('aria-label', 'Copy path');
    copyBtn.appendChild(icon('copy'));
    copyBtn.addEventListener('click', () => {
      copyToClipboard(file.path || '');
      copyBtn.title = 'Copied';
      setTimeout(() => { copyBtn.title = 'Copy path'; }, 1200);
    });
    header.appendChild(copyBtn);

    // Counts — hide "+0 -0" when there are no line changes.
    const addN = file.additions || 0;
    const delN = file.deletions || 0;
    if (!file.binary && !file.notLoaded && (addN > 0 || delN > 0)) {
      const counts = el('span', 'diff-file-counts');
      const add = el('span', 'diff-count-add');
      add.appendChild(text(`+${addN}`));
      counts.appendChild(add);
      counts.appendChild(text(' '));
      const del = el('span', 'diff-count-del');
      del.appendChild(text(`-${delN}`));
      counts.appendChild(del);
      header.appendChild(counts);
    }

    card.appendChild(header);

    // Body — large files keep the "Expand N lines" CTA visible (body not hidden).
    const body = el('div', 'diff-file-body');
    const showLargeCta =
      shouldCollapse && isCollapsed && !file.binary && !file.notLoaded && !file.tooLarge;

    if (file.binary) {
      body.hidden = isCollapsed;
      const notice = el('p', 'diff-notice', 'Binary file not shown');
      body.appendChild(notice);
    } else if (file.tooLarge) {
      body.hidden = isCollapsed;
      const notice = el('p', 'diff-notice', `File too large to show (${formatBytes(file.tooLarge)})`);
      body.appendChild(notice);
    } else if (file.notLoaded) {
      body.hidden = isCollapsed;
      const notice = el('p', 'diff-notice');
      const loadBtn = el('button', 'btn ghost', 'Load diff');
      loadBtn.type = 'button';
      loadBtn.addEventListener('click', () => {
        if (onFetch) onFetch({ file: file.path });
      });
      notice.appendChild(loadBtn);
      body.appendChild(notice);
    } else if (file.status === 'added' && file.hunks.length === 0) {
      body.hidden = isCollapsed;
      const notice = el('p', 'diff-notice', 'Empty file');
      body.appendChild(notice);
    } else if (file.raw || file.notice) {
      body.hidden = isCollapsed;
      const notice = el('p', 'diff-notice', file.notice || 'Combined diff not supported');
      body.appendChild(notice);
    } else if (showLargeCta) {
      body.hidden = false;
      const expandBtn = el('button', 'btn ghost diff-expand-file-btn', `Expand ${lineCount} lines`);
      expandBtn.type = 'button';
      expandBtn.addEventListener('click', () => {
        collapsedPaths.delete(file.path);
        updateCollapsedSet(collapsedPaths);
        while (body.firstChild) body.removeChild(body.firstChild);
        body.appendChild(renderFileTable(file, viewMode, wrap));
        body.hidden = false;
        chevronBtn.setAttribute('aria-expanded', 'true');
        chevronBtn.classList.add('is-expanded');
      });
      body.appendChild(expandBtn);
    } else {
      body.hidden = isCollapsed;
      body.appendChild(renderFileTable(file, viewMode, wrap));
    }

    card.appendChild(body);
    // Sticky gutters only while horizontally scrolled (avoids covering first glyphs at rest).
    body.addEventListener(
      'scroll',
      () => {
        body.classList.toggle('is-hscrolled', body.scrollLeft > 0);
      },
      { passive: true }
    );

    // Chevron toggle
    chevronBtn.classList.toggle('is-expanded', !isCollapsed);
    chevronBtn.addEventListener('click', () => {
      const nowCollapsed = !body.hidden;
      body.hidden = nowCollapsed;
      chevronBtn.setAttribute('aria-expanded', String(!nowCollapsed));
      chevronBtn.classList.toggle('is-expanded', !nowCollapsed);
      if (nowCollapsed) {
        collapsedPaths.add(file.path);
      } else {
        collapsedPaths.delete(file.path);
      }
      updateCollapsedSet(collapsedPaths);
    });

    return card;
  }

  /** Render summary bar */
  function renderSummary(data, viewMode, wrap, handlers, opts = {}) {
    const {
      onToggleView,
      onToggleWrap,
      onCollapseAll,
      onExpandAll,
      onExpand,
    } = handlers;
    const splitOk = opts.splitOk != null ? opts.splitOk : supportsSplit();
    const allCollapsed = opts.allCollapsed || false;

    const bar = el('div', 'diff-summary-bar');
    bar.title = 'n: next file, p: previous file, c: collapse/expand';

    const filesCount = (data.files || []).length;
    const totalAdd = (data.files || []).reduce((sum, f) => sum + (f.additions || 0), 0);
    const totalDel = (data.files || []).reduce((sum, f) => sum + (f.deletions || 0), 0);

    const stats = el('div', 'diff-summary-stats');
    const summary = el('span', 'diff-summary-text');
    summary.appendChild(text(`${filesCount} file${filesCount !== 1 ? 's' : ''} changed `));
    const addSpan = el('span', 'diff-summary-add');
    addSpan.appendChild(text(`+${totalAdd}`));
    summary.appendChild(addSpan);
    summary.appendChild(text(' '));
    const delSpan = el('span', 'diff-summary-del');
    delSpan.appendChild(text(`-${totalDel}`));
    summary.appendChild(delSpan);
    stats.appendChild(summary);

    const total = totalAdd + totalDel;
    if (total > 0) {
      const ratioBar = el('div', 'diff-ratio-bar');
      let addBlocks;
      if (totalAdd === totalDel) {
        addBlocks = 2;
      } else {
        addBlocks = Math.round((totalAdd / total) * 5);
        if (totalAdd > 0 && totalDel > 0) {
          addBlocks = Math.min(4, Math.max(1, addBlocks));
        }
      }
      for (let i = 0; i < 5; i++) {
        const block = el('span', 'diff-ratio-block');
        block.classList.add(i < addBlocks ? 'diff-ratio-add' : 'diff-ratio-del');
        ratioBar.appendChild(block);
      }
      stats.appendChild(ratioBar);
    }
    bar.appendChild(stats);

    const controls = el('div', 'diff-summary-controls');
    const viewControl = el('div', 'diff-view-control segmented');
    viewControl.setAttribute('role', 'group');
    viewControl.setAttribute('aria-label', 'View mode');

    const unifiedBtn = el('button', 'diff-view-btn');
    unifiedBtn.type = 'button';
    unifiedBtn.setAttribute('role', 'radio');
    unifiedBtn.setAttribute('aria-checked', String(viewMode === 'unified'));
    unifiedBtn.appendChild(text('Unified'));
    unifiedBtn.addEventListener('click', () => onToggleView('unified'));
    viewControl.appendChild(unifiedBtn);

    const splitBtn = el('button', 'diff-view-btn');
    splitBtn.type = 'button';
    splitBtn.setAttribute('role', 'radio');
    splitBtn.setAttribute('aria-checked', String(viewMode === 'split'));
    splitBtn.appendChild(text('Split'));
    if (!splitOk) {
      splitBtn.disabled = true;
      splitBtn.title = 'Widen the drawer or expand';
    }
    splitBtn.addEventListener('click', () => onToggleView('split'));
    viewControl.appendChild(splitBtn);
    controls.appendChild(viewControl);

    const wrapBtn = el('button', 'btn icon-btn ghost diff-wrap-btn');
    wrapBtn.type = 'button';
    wrapBtn.title = 'Wrap lines';
    wrapBtn.setAttribute('aria-label', 'Wrap lines');
    wrapBtn.setAttribute('aria-pressed', String(wrap));
    wrapBtn.classList.toggle('is-on', wrap);
    wrapBtn.appendChild(icon('wrap-text'));
    wrapBtn.addEventListener('click', onToggleWrap);
    controls.appendChild(wrapBtn);

    const collapseToggle = el(
      'button',
      `btn icon-btn ghost diff-collapse-toggle ${allCollapsed ? 'diff-expand-all-btn' : 'diff-collapse-all-btn'}`
    );
    collapseToggle.type = 'button';
    const collapseLabel = allCollapsed ? 'Expand all' : 'Collapse all';
    collapseToggle.title = collapseLabel;
    collapseToggle.setAttribute('aria-label', collapseLabel);
    collapseToggle.appendChild(icon('chevrons-up-down'));
    collapseToggle.addEventListener('click', () => {
      if (allCollapsed) onExpandAll();
      else onCollapseAll();
    });
    controls.appendChild(collapseToggle);

    const expandBtn = el('button', 'btn icon-btn ghost diff-expand-overlay-btn');
    expandBtn.type = 'button';
    expandBtn.title = 'Open diff in a larger view';
    expandBtn.setAttribute('aria-label', 'Open diff in a larger view');
    expandBtn.appendChild(icon('maximize-2'));
    expandBtn.addEventListener('click', () => onExpand(expandBtn));
    controls.appendChild(expandBtn);

    bar.appendChild(controls);
    return bar;
  }

  function openOverlay(parsed, data, trigger) {
    const existing = document.getElementById('diff-overlay');
    if (existing) existing.remove();

    const overlay = el('div', 'diff-overlay');
    overlay.id = 'diff-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Diff overlay');

    const panel = el('div', 'diff-overlay-panel');
    const head = el('div', 'diff-overlay-head');
    const title = el('h2', 'type-title', 'Diff');
    head.appendChild(title);
    const closeBtn = el('button', 'btn icon-btn ghost diff-overlay-close');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.appendChild(icon('x'));
    head.appendChild(closeBtn);
    panel.appendChild(head);

    const body = el('div', 'diff-overlay-body');
    panel.appendChild(body);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    const mobile = window.innerWidth < 640;
    overlay.classList.toggle('is-fullscreen', mobile);

    // Measure after layout — drawer root width must not gate overlay Split.
    let canSplit = !mobile && (panel.getBoundingClientRect().width || window.innerWidth) >= 900;
    let mode = canSplit ? getViewMode(true) : 'unified';
    if (!canSplit) mode = 'unified';

    function paintOverlay() {
      canSplit = !mobile && (panel.getBoundingClientRect().width || window.innerWidth) >= 900;
      if (!canSplit && mode === 'split') mode = 'unified';
      while (body.firstChild) body.removeChild(body.firstChild);
      const paths = (parsed.files || []).map((f) => f.path);
      const allCollapsed = paths.length > 0 && paths.every((p) => collapsedPaths.has(p));
      const bar = renderSummary(
        parsed,
        mode,
        currentWrap,
        {
          onToggleView: (next) => {
            if (next === 'split' && !canSplit) return;
            mode = next;
            setViewMode(next);
            paintOverlay();
          },
          onToggleWrap: () => {
            currentWrap = !currentWrap;
            paintOverlay();
          },
          onCollapseAll: () => {
            (parsed.files || []).forEach((f) => collapsedPaths.add(f.path));
            updateCollapsedSet(collapsedPaths);
            paintOverlay();
          },
          onExpandAll: () => {
            collapsedPaths.clear();
            updateCollapsedSet(collapsedPaths);
            paintOverlay();
          },
          onExpand: () => close(),
        },
        { splitOk: canSplit, allCollapsed }
      );
      // Hide overlay-open control inside the overlay itself.
      const openBtn = bar.querySelector('.diff-expand-overlay-btn');
      if (openBtn) openBtn.hidden = true;
      body.appendChild(bar);
      const trunc = renderTruncationBanner(data);
      if (trunc) body.appendChild(trunc);
      const scroller = el('div', 'diff-scroller diff-files');
      (parsed.files || []).forEach((file, index) => {
        scroller.appendChild(renderFileCard(file, index, mode, currentWrap));
      });
      body.appendChild(scroller);
    }
    requestAnimationFrame(() => {
      canSplit = !mobile && (panel.getBoundingClientRect().width || window.innerWidth) >= 900;
      if (canSplit) mode = getViewMode(true);
      paintOverlay();
    });

    const focusables = () =>
      [...panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(
        (n) => !n.disabled && n.offsetParent !== null
      );

    function close() {
      overlay.remove();
      document.removeEventListener('keydown', onKey, true);
      if (trigger && typeof trigger.focus === 'function') trigger.focus();
    }

    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        close();
        return;
      }
      if (e.key !== 'Tab') return;
      const list = focusables();
      if (!list.length) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    closeBtn.addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });
    document.addEventListener('keydown', onKey, true);
    requestAnimationFrame(() => {
      const list = focusables();
      (list[0] || closeBtn).focus();
    });
  }

  /** Render empty state */
  function renderEmpty(message) {
    const empty = el('div', 'diff-empty empty-line');
    const title = el('h3', 'type-title-lg', message || 'No changes yet');
    empty.appendChild(title);
    return empty;
  }

  /** Render error banner */
  function renderError(message) {
    const banner = el('div', 'banner-error');
    banner.appendChild(text(message || 'Diff unavailable'));
    const retryBtn = el('button', 'btn ghost', 'Retry');
    retryBtn.type = 'button';
    retryBtn.addEventListener('click', () => {
      if (onFetch) onFetch({});
    });
    banner.appendChild(retryBtn);
    return banner;
  }

  /** Render skeleton */
  function renderSkeleton() {
    const skeleton = el('div', 'diff-skeleton');
    for (let i = 0; i < 3; i++) {
      const card = el('div', 'skeleton-card');
      skeleton.appendChild(card);
    }
    return skeleton;
  }

  /** Render truncation banner */
  function renderTruncationBanner(data) {
    if (!data.truncated) return null;
    const banner = el('div', 'diff-truncation-banner');
    const total = data.total_bytes || 0;
    const shown = data.shown_bytes || 0;
    const atFullCap = shown >= 2 * 1024 * 1024 - 1024 || (data.full && data.truncated);
    if (atFullCap) {
      banner.appendChild(text('Diff exceeds 2 MB. Open the file in your editor.'));
      return banner;
    }
    banner.appendChild(text(`Showing ${formatBytes(shown)} of ${formatBytes(total)}.`));
    banner.appendChild(text(' '));
    const loadBtn = el('button', 'btn ghost', 'Load full diff');
    loadBtn.type = 'button';
    loadBtn.addEventListener('click', () => {
      if (onFetch) onFetch({ full: 1 });
    });
    banner.appendChild(loadBtn);
    return banner;
  }

  /** Main render function */
  function render(data, opts = {}) {
    const preserve = opts.preserve || false;
    const skeleton = opts.skeleton || false;

    const prevCards = new Map();
    if (preserve) {
      captureState(root);
      const prevScroller = root.querySelector('.diff-scroller');
      if (prevScroller) {
        [...prevScroller.querySelectorAll('.diff-file-card')].forEach((card) => {
          // Detach so the upcoming clear does not destroy reused nodes.
          prevCards.set(card.dataset.path || '', card);
          card.remove();
        });
      }
    }

    currentData = data;

    while (root.firstChild) root.removeChild(root.firstChild);

    if (skeleton) {
      root.appendChild(renderSkeleton());
      return;
    }

    if (!data) {
      root.appendChild(renderEmpty('No worktree for this task yet.'));
      return;
    }

    if (data.error) {
      root.appendChild(renderError(data.error));
      return;
    }

    const files = data.files || [];
    if (files.length === 0) {
      root.appendChild(renderEmpty('No changes yet'));
      return;
    }

    // Parse diff if needed
    let parsed = data;
    if (data.diff && typeof data.diff === 'string') {
      parsed = parseUnifiedDiff(data.diff, data.files || []);
    }

    const canSplit = supportsSplit();
    currentViewMode = getViewMode(canSplit);
    if (!canSplit && currentViewMode === 'split') {
      currentViewMode = 'unified';
    }

    function handleToggleView(mode) {
      if (mode === 'split' && !supportsSplit()) return;
      currentViewMode = mode;
      setViewMode(mode);
      render(currentData, { preserve: true });
    }

    function handleToggleWrap() {
      currentWrap = !currentWrap;
      render(currentData, { preserve: true });
    }

    function handleCollapseAll() {
      files.forEach((f) => collapsedPaths.add(f.path));
      updateCollapsedSet(collapsedPaths);
      render(currentData, { preserve: true });
    }

    function handleExpandAll() {
      collapsedPaths.clear();
      updateCollapsedSet(collapsedPaths);
      render(currentData, { preserve: true });
    }

    function handleExpand(trigger) {
      openOverlay(parsed, data, trigger || root.querySelector('.diff-expand-overlay-btn'));
    }

    const pathList = (parsed.files || []).map((f) => f.path);
    const allCollapsed = pathList.length > 0 && pathList.every((p) => collapsedPaths.has(p));
    const summary = renderSummary(
      parsed,
      currentViewMode,
      currentWrap,
      {
        onToggleView: handleToggleView,
        onToggleWrap: handleToggleWrap,
        onCollapseAll: handleCollapseAll,
        onExpandAll: handleExpandAll,
        onExpand: handleExpand,
      },
      { splitOk: canSplit, allCollapsed }
    );
    root.appendChild(summary);

    const truncBanner = renderTruncationBanner(data);
    if (truncBanner) root.appendChild(truncBanner);

    const scroller = el('div', 'diff-scroller diff-files diff-view');
    const nextHashes = new Map();
    const rawDiff = String(data.diff || '');
    parsed.files.forEach((file, index) => {
      const path = file.path || `idx-${index}`;
      const hash = filePatchHash(file, rawDiff);
      nextHashes.set(path, hash);
      const prev = prevCards.get(path);
      if (preserve && prev && fileHashes.get(path) === hash) {
        const collapsed = collapsedPaths.has(path);
        const body = prev.querySelector('.diff-file-body');
        const chevron = prev.querySelector('.diff-chevron-btn');
        if (body) body.hidden = collapsed;
        if (chevron) {
          chevron.setAttribute('aria-expanded', String(!collapsed));
          chevron.classList.toggle('is-expanded', !collapsed);
        }
        scroller.appendChild(prev);
      } else {
        scroller.appendChild(renderFileCard(file, index, currentViewMode, currentWrap));
      }
    });
    fileHashes = nextHashes;
    root.appendChild(scroller);

    if (preserve) {
      restoreState(root);
    }

    if (!keysBound) {
      keysBound = true;
      root.tabIndex = 0;
      root.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (!root.contains(document.activeElement) && document.activeElement !== root) return;
        const cards = [...root.querySelectorAll('.diff-file-card')];
        const active = root.querySelector('.diff-file-card.is-active');
        let index = active ? cards.indexOf(active) : -1;
        if (e.key === 'n') {
          e.preventDefault();
          index = Math.min(index + 1, cards.length - 1);
          if (cards[index]) {
            cards.forEach((c) => c.classList.remove('is-active'));
            cards[index].classList.add('is-active');
            cards[index].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        } else if (e.key === 'p') {
          e.preventDefault();
          index = Math.max(index - 1, 0);
          if (cards[index]) {
            cards.forEach((c) => c.classList.remove('is-active'));
            cards[index].classList.add('is-active');
            cards[index].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        } else if (e.key === 'c') {
          e.preventDefault();
          if (active) {
            const chevron = active.querySelector('.diff-chevron-btn');
            if (chevron) chevron.click();
          }
        }
      });
    }
  }

  /** Refresh current data */
  function refresh(data, opts = {}) {
    render(data, { ...opts, preserve: true });
  }

  return { render, refresh };
}
