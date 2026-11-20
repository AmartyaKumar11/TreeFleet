// diff-parse.js - Pure ES module for parsing unified diffs
// No DOM, no dependencies. Exports parseUnifiedDiff, wordDiff, sanitizeBidi, unescapeCQuoted.

/**
 * Unescape C-quoted Git paths (e.g., "a/path with \"quotes\"\ttab.txt")
 * Handles \t, \n, \r, \\, \", and octal escapes like \040
 */
export function unescapeCQuoted(path) {
  const raw = String(path || '').trim();
  let out;
  if (raw.startsWith('"') && raw.endsWith('"')) {
    const inner = raw.slice(1, -1);
    out = inner.replace(/\\([tnr"\\]|[0-7]{1,3})/g, (match, code) => {
      if (code === 't') return '\t';
      if (code === 'n') return '\n';
      if (code === 'r') return '\r';
      if (code === '"') return '"';
      if (code === '\\') return '\\';
      return String.fromCharCode(parseInt(code, 8));
    });
  } else {
    out = raw;
  }
  // Strip git a/ b/ prefixes after unquoting so C-quoted "b/foo" becomes foo.
  return out.replace(/^[ab]\//, '');
}

/**
 * Replace bidirectional control characters with visible placeholders
 */
export function sanitizeBidi(text) {
  return text.replace(/[\u202A-\u202E\u2066-\u2069]/g, (ch) => {
    const code = ch.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0');
    return `\u2039U+${code}\u203A`; // ‹U+XXXX›
  });
}

/**
 * Parse unified diff text into structured format
 * @param {string} text - Raw unified diff text
 * @param {Array} filesMeta - Optional metadata array with {path, old_path, status, binary, additions, deletions, similarity, too_large}
 * @returns {{files: Array}} - Structured diff with files and hunks
 */
export function parseUnifiedDiff(text, filesMeta = []) {
  const lines = text.split('\n');
  const files = [];
  let currentFile = null;
  let currentHunk = null;
  let i = 0;
  let filesMetaIndex = 0;

  function startNewFile() {
    if (currentFile) {
      if (currentHunk) {
        currentFile.hunks.push(currentHunk);
        currentHunk = null;
      }
      files.push(currentFile);
    }
    currentFile = {
      path: null,
      oldPath: null,
      status: null,
      binary: false,
      similarity: null,
      modeOld: null,
      modeNew: null,
      additions: 0,
      deletions: 0,
      noEolOld: false,
      noEolNew: false,
      partial: false,
      raw: null,
      hunks: []
    };
  }

  function finishHunk() {
    if (currentHunk) {
      delete currentHunk._oldCursor;
      delete currentHunk._newCursor;
      currentFile.hunks.push(currentHunk);
      currentHunk = null;
    }
  }

  while (i < lines.length) {
    const line = lines[i];

    // Combined diff marker (unsupported - store as raw with notice)
    if (line.startsWith('@@@')) {
      if (!currentFile) startNewFile();
      finishHunk();
      const rawStart = i;
      while (i < lines.length && !lines[i].startsWith('diff --git')) {
        i++;
      }
      currentFile.raw = lines.slice(rawStart, i).join('\n');
      currentFile.notice = 'Combined diffs are not supported';
      currentFile.hunks = [];
      files.push(currentFile);
      currentFile = null;
      continue;
    }

    // diff --git header
    if (line.startsWith('diff --git ')) {
      startNewFile();
      // Extract paths: diff --git a/foo b/bar OR diff --git "a/foo" "b/bar"
      const match = line.match(/^diff --git (.+?)\s+(.+)$/);
      if (match) {
        // unescapeCQuoted handles both quoted and unquoted, and strips a/ b/ prefix
        currentFile.oldPath = unescapeCQuoted(match[1]);
        currentFile.path = unescapeCQuoted(match[2]);
      }
      i++;
      continue;
    }

    // Mode changes
    if (line.startsWith('old mode ')) {
      if (!currentFile) startNewFile();
      currentFile.modeOld = line.slice(9).trim();
      i++;
      continue;
    }
    if (line.startsWith('new mode ')) {
      if (!currentFile) startNewFile();
      currentFile.modeNew = line.slice(9).trim();
      i++;
      continue;
    }
    if (line.startsWith('new file mode ')) {
      if (!currentFile) startNewFile();
      currentFile.modeNew = line.slice(14).trim();
      currentFile.status = 'added';
      i++;
      continue;
    }
    if (line.startsWith('deleted file mode ')) {
      if (!currentFile) startNewFile();
      currentFile.modeOld = line.slice(18).trim();
      currentFile.status = 'deleted';
      i++;
      continue;
    }

    // Rename/copy with similarity
    if (line.startsWith('similarity index ')) {
      if (!currentFile) startNewFile();
      const sim = line.match(/similarity index (\d+)%/);
      if (sim) currentFile.similarity = parseInt(sim[1], 10);
      i++;
      continue;
    }
    if (line.startsWith('rename from ')) {
      if (!currentFile) startNewFile();
      currentFile.oldPath = unescapeCQuoted(line.slice(12).trim());
      currentFile.status = 'renamed';
      i++;
      continue;
    }
    if (line.startsWith('rename to ')) {
      if (!currentFile) startNewFile();
      currentFile.path = unescapeCQuoted(line.slice(10).trim());
      i++;
      continue;
    }
    if (line.startsWith('copy from ')) {
      if (!currentFile) startNewFile();
      currentFile.oldPath = unescapeCQuoted(line.slice(10).trim());
      currentFile.status = 'copied';
      i++;
      continue;
    }
    if (line.startsWith('copy to ')) {
      if (!currentFile) startNewFile();
      currentFile.path = unescapeCQuoted(line.slice(8).trim());
      i++;
      continue;
    }

    // Binary files
    if (line.startsWith('Binary files ') || line.startsWith('GIT binary patch')) {
      if (!currentFile) startNewFile();
      currentFile.binary = true;
      // Skip binary content
      if (line.startsWith('GIT binary patch')) {
        i++;
        while (i < lines.length && !lines[i].startsWith('diff --git') && !lines[i].startsWith('---')) {
          i++;
        }
        continue;
      }
      i++;
      continue;
    }

    // --- header
    if (line.startsWith('--- ')) {
      if (!currentFile) startNewFile();
      const path = line.slice(4).trim();
      if (path !== '/dev/null') {
        currentFile.oldPath = unescapeCQuoted(path);
      }
      i++;
      continue;
    }

    // +++ header
    if (line.startsWith('+++ ')) {
      if (!currentFile) startNewFile();
      const path = line.slice(4).trim();
      if (path !== '/dev/null') {
        currentFile.path = unescapeCQuoted(path);
      }
      i++;
      continue;
    }

    // Hunk header
    if (line.startsWith('@@')) {
      finishHunk();
      if (!currentFile) startNewFile();

      // Parse @@ -oldStart[,oldLines] +newStart[,newLines] @@ section
      const hunkMatch = line.match(/^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$/);
      if (hunkMatch) {
        const oldStart = parseInt(hunkMatch[1], 10);
        const oldLines = hunkMatch[2] ? parseInt(hunkMatch[2], 10) : 1;
        const newStart = parseInt(hunkMatch[3], 10);
        const newLines = hunkMatch[4] ? parseInt(hunkMatch[4], 10) : 1;
        const section = hunkMatch[5] ? hunkMatch[5].trim() : '';

        currentHunk = {
          header: line,
          oldStart,
          oldLines,
          newStart,
          newLines,
          section,
          lines: []
        };
      }
      i++;
      continue;
    }

    // No newline marker
    if (line.startsWith('\\ No newline at end of file')) {
      if (currentHunk && currentHunk.lines.length > 0) {
        const lastLine = currentHunk.lines[currentHunk.lines.length - 1];
        lastLine.noEol = true;
        if (lastLine.type === 'del') {
          currentFile.noEolOld = true;
        } else if (lastLine.type === 'add') {
          currentFile.noEolNew = true;
        }
      }
      i++;
      continue;
    }

    // Hunk content lines
    if (currentHunk) {
      const firstChar = line[0];
      if (firstChar === ' ' || firstChar === '+' || firstChar === '-') {
        const text = line.slice(1);
        const hadCR = text.endsWith('\r');
        const cleanText = hadCR ? text.slice(0, -1) : text;
        const type = firstChar === ' ' ? 'ctx' : firstChar === '+' ? 'add' : 'del';
        if (currentHunk._oldCursor == null) {
          currentHunk._oldCursor = currentHunk.oldStart;
          currentHunk._newCursor = currentHunk.newStart;
        }
        const lineObj = {
          type,
          oldNo: type !== 'add' ? currentHunk._oldCursor : null,
          newNo: type !== 'del' ? currentHunk._newCursor : null,
          text: cleanText,
          noEol: false,
          hadCR
        };
        if (type !== 'add') currentHunk._oldCursor += 1;
        if (type !== 'del') currentHunk._newCursor += 1;

        currentHunk.lines.push(lineObj);
        if (firstChar === '+') currentFile.additions++;
        if (firstChar === '-') currentFile.deletions++;
      }
    }

    i++;
  }

  // Finish last file
  if (currentFile) {
    finishHunk();
    files.push(currentFile);
  }

  // Check for partial/truncated input
  if (files.length > 0) {
    const lastFile = files[files.length - 1];
    if (lastFile.hunks.length > 0) {
      const lastHunk = lastFile.hunks[lastFile.hunks.length - 1];
      const expectedOld = lastHunk.oldLines;
      const expectedNew = lastHunk.newLines;
      const actualOld = lastHunk.lines.filter(l => l.type === 'ctx' || l.type === 'del').length;
      const actualNew = lastHunk.lines.filter(l => l.type === 'ctx' || l.type === 'add').length;
      if (actualOld < expectedOld || actualNew < expectedNew) {
        lastFile.partial = true;
      }
    }
  }

  // Merge with filesMeta
  if (filesMeta && filesMeta.length > 0) {
    // First, match parsed files with filesMeta
    for (const file of files) {
      let meta = null;
      
      // Try exact path match first
      if (file.path) {
        meta = filesMeta.find(m => m.path === file.path);
      }
      
      // Fallback: match by index if counts match
      if (!meta && filesMetaIndex < filesMeta.length) {
        const candidate = filesMeta[filesMetaIndex];
        if (files.length === filesMeta.length) {
          meta = candidate;
        }
      }
      
      if (meta) {
        file.status = meta.status || file.status;
        file.binary = meta.binary !== undefined ? meta.binary : file.binary;
        file.additions = meta.additions !== undefined ? meta.additions : file.additions;
        file.deletions = meta.deletions !== undefined ? meta.deletions : file.deletions;
        file.similarity = meta.similarity !== undefined ? meta.similarity : file.similarity;
        if (meta.old_path) file.oldPath = meta.old_path;
        if (meta.too_large) file.tooLarge = meta.too_large;
        filesMetaIndex++;
      }
    }

    // Add missing filesMeta entries as notLoaded files
    for (const meta of filesMeta) {
      const exists = files.some(f => f.path === meta.path || f.oldPath === meta.path);
      if (!exists) {
        const notLoadedFile = {
          path: meta.path,
          oldPath: meta.old_path || null,
          status: meta.status || null,
          binary: meta.binary || false,
          similarity: meta.similarity || null,
          modeOld: null,
          modeNew: null,
          additions: meta.additions || 0,
          deletions: meta.deletions || 0,
          noEolOld: false,
          noEolNew: false,
          partial: false,
          raw: null,
          notLoaded: true,
          hunks: []
        };
        if (meta.too_large) notLoadedFile.tooLarge = meta.too_large;
        files.push(notLoadedFile);
      }
    }
  }

  return { files };
}

/**
 * Word-level diff for inline highlighting
 * @param {string} oldText - Original line text
 * @param {string} newText - Modified line text
 * @returns {Array<{text: string, changed: boolean}>} - Segments with change markers
 */
export function wordDiff(oldText, newText) {
  // Safety limits
  if (oldText.length > 2000 || newText.length > 2000) {
    return [{ text: newText, changed: false }];
  }

  const tokenize = (text) => {
    const tokens = [];
    const regex = /\w+|\s+|[^\w\s]/gu;
    let match;
    while ((match = regex.exec(text)) !== null) {
      tokens.push(match[0]);
    }
    return tokens;
  };

  const oldTokens = tokenize(oldText);
  const newTokens = tokenize(newText);

  if (oldTokens.length > 500 || newTokens.length > 500) {
    return [{ text: newText, changed: false }];
  }

  // LCS-based diff using dynamic programming
  const m = oldTokens.length;
  const n = newTokens.length;
  const dp = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldTokens[i - 1] === newTokens[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack to find diff
  const result = [];
  let i = m;
  let j = n;
  const changes = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldTokens[i - 1] === newTokens[j - 1]) {
      changes.unshift({ type: 'same', token: newTokens[j - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      changes.unshift({ type: 'add', token: newTokens[j - 1] });
      j--;
    } else {
      i--;
    }
  }

  // Merge consecutive tokens of same type
  for (const change of changes) {
    if (result.length > 0 && result[result.length - 1].changed === (change.type === 'add')) {
      result[result.length - 1].text += change.token;
    } else {
      result.push({
        text: change.token,
        changed: change.type === 'add'
      });
    }
  }

  return result.length > 0 ? result : [{ text: newText, changed: false }];
}
