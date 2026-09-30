/* JSX source edits for the UI studio (app.html). Works on screen source text,
 * never on the live DOM, so every change can be saved and reviewed.
 * Loaded as a browser global (window.UeJsx) and required by ue-jsx.test.js. */
(function (root) {
  'use strict';

  var HOST = ['Page', 'Sidebar', 'Button', 'Field', 'Table', 'Card', 'Badge', 'Hero', 'Image'];
  var STAMP_RE = /<(Button|Field|Table|Card|Badge|Hero|Image|h1|h2|h3|h4|p|label|section|figure|table|button|div|form|header|footer|article|ul|ol|li|img)(?=[\s>/])/g;
  var CONTAINER_TAGS = /^(Card|Hero|div|section|form|header|footer|article|ul|ol|li|figure|main)$/;
  var POSITION_KEYS = ['position', 'left', 'top', 'zIndex', 'margin'];

  // A quote opens a JS string only after an operator or keyword. After a letter
  // it is JSX text ("Don't"), which must not swallow the rest of the file.
  function isStringStart(src, i) {
    var j = i - 1;
    while (j >= 0 && /\s/.test(src[j])) j--;
    if (j < 0) return true;
    if (/[=(,:\[{?!&|+\-*%;<>]/.test(src[j])) return true;
    var word = /([A-Za-z_$]+)$/.exec(src.slice(Math.max(0, j - 10), j + 1));
    return !!(word && /^(return|case|typeof|in|of)$/.test(word[1]));
  }

  function skipQuoted(src, i) {
    var q = src[i];
    for (var j = i + 1; j < src.length; j++) {
      if (src[j] === '\\') { j++; continue; }
      if (src[j] === q) return j;
      if (q !== '`' && src[j] === '\n') return j - 1;
    }
    return src.length - 1;
  }

  function matchBrace(src, open) {
    var pairs = { '{': '}', '(': ')', '[': ']' };
    var openCh = src[open];
    var closeCh = pairs[openCh];
    var depth = 0;
    for (var i = open; i < src.length; i++) {
      var c = src[i];
      if ((c === '"' || c === "'" || c === '`') && isStringStart(src, i)) { i = skipQuoted(src, i); continue; }
      if (c === openCh) depth++;
      else if (c === closeCh) {
        depth--;
        if (depth === 0) return i;
      }
    }
    return -1;
  }

  function tagAt(src, lt) {
    var m = /^<([A-Za-z][\w.]*)/.exec(src.slice(lt, lt + 80));
    return m ? m[1] : '';
  }

  // Index of the '>' that closes the opening tag at lt. Arrow functions inside
  // attribute braces (onClick={() => go()}) do not end the tag.
  function openTagEnd(src, lt) {
    var depth = 0;
    for (var i = lt + 1; i < src.length; i++) {
      var c = src[i];
      if (depth === 0 && (c === '"' || c === "'")) { i = skipQuoted(src, i); continue; }
      if (depth > 0 && (c === '"' || c === "'" || c === '`') && isStringStart(src, i)) { i = skipQuoted(src, i); continue; }
      if (c === '{') depth++;
      else if (c === '}') depth--;
      else if (c === '>' && depth === 0) return i;
    }
    return -1;
  }

  function elementSpan(src, lt) {
    var name = tagAt(src, lt);
    if (!name) return null;
    var oe = openTagEnd(src, lt);
    if (oe < 0) return null;
    if (src[oe - 1] === '/') {
      return { start: lt, end: oe + 1, openEnd: oe + 1, closeStart: -1, name: name, selfClosing: true };
    }
    var depth = 1;
    var i = oe + 1;
    while (i < src.length) {
      var at = src.indexOf('<', i);
      if (at < 0) return null;
      if (src[at + 1] === '/') {
        var cm = /^<\/([A-Za-z][\w.]*)?\s*>/.exec(src.slice(at, at + 80));
        if (!cm) { i = at + 2; continue; }
        if ((cm[1] || '') === name) {
          depth--;
          if (depth === 0) {
            return { start: lt, end: at + cm[0].length, openEnd: oe + 1, closeStart: at, name: name, selfClosing: false };
          }
        }
        i = at + cm[0].length;
        continue;
      }
      var tn = tagAt(src, at);
      if (!tn) { i = at + 1; continue; }
      var e2 = openTagEnd(src, at);
      if (e2 < 0) { i = at + 1; continue; }
      if (tn === name && src[e2 - 1] !== '/') depth++;
      i = e2 + 1;
    }
    return null;
  }

  function findTag(src, id) {
    var idx = src.indexOf('data-ue="' + id + '"');
    if (idx < 0) return -1;
    var lt = src.lastIndexOf('<', idx);
    for (var guard = 0; lt >= 0 && guard < 6; guard++) {
      if (tagAt(src, lt)) {
        var oe = openTagEnd(src, lt);
        if (oe > idx) return lt;
      }
      lt = src.lastIndexOf('<', lt - 1);
    }
    return -1;
  }

  function spanById(src, id) {
    var lt = findTag(src, id);
    return lt < 0 ? null : elementSpan(src, lt);
  }

  function has(src, id) {
    return !!id && src.indexOf('data-ue="' + id + '"') >= 0;
  }

  // ---- functions ---------------------------------------------------------

  function functionSpans(src) {
    var out = [];
    var re = /function\s+([A-Za-z_$][\w$]*)\s*\(/g;
    var m;
    while ((m = re.exec(src))) {
      var paren = src.indexOf('(', m.index);
      var pclose = matchBrace(src, paren);
      if (pclose < 0) continue;
      var brace = src.indexOf('{', pclose);
      if (brace < 0) continue;
      var end = matchBrace(src, brace);
      if (end < 0) continue;
      out.push({ name: m[1], start: m.index, bodyStart: brace + 1, end: end });
    }
    return out;
  }

  function enclosingFunction(spans, pos) {
    var best = null;
    spans.forEach(function (s) {
      if (s.bodyStart <= pos && pos < s.end && (!best || s.bodyStart > best.bodyStart)) best = s;
    });
    return best;
  }

  function sameFunction(src, a, b) {
    var spans = functionSpans(src);
    var fa = enclosingFunction(spans, a);
    var fb = enclosingFunction(spans, b);
    return (fa && fa.start) === (fb && fb.start);
  }

  // ---- stamping ----------------------------------------------------------

  // Give every editable tag a stable data-ue id. Host helper bodies are skipped:
  // an id inside function Button would repeat on every button.
  function stamp(src) {
    src = String(src || '');
    var n = 0;
    (src.match(/data-ue="ue(\d+)"/g) || []).forEach(function (m) {
      var num = Number(m.replace(/\D/g, ''));
      if (num > n) n = num;
    });
    var skip = functionSpans(src).filter(function (s) { return HOST.indexOf(s.name) >= 0; });
    return src.replace(STAMP_RE, function (full, tag, offset) {
      for (var i = 0; i < skip.length; i++) {
        if (offset > skip[i].start && offset < skip[i].end) return full;
      }
      if (/^\s+data-ue=/.test(src.slice(offset + full.length, offset + full.length + 12))) return full;
      n += 1;
      return '<' + tag + ' data-ue="ue' + n + '"';
    });
  }

  function normalize(src) {
    return String(src || '').replace(/\s+data-ue="ue\d+"/g, '');
  }

  function knownIds(src) {
    var out = [];
    String(src || '').replace(/data-ue="(ue\d+)"/g, function (_m, id) { out.push(id); return _m; });
    return out;
  }

  // ---- styles ------------------------------------------------------------

  function splitTop(s) {
    var out = [];
    var depth = 0;
    var start = 0;
    for (var i = 0; i < s.length; i++) {
      var c = s[i];
      if (c === '"' || c === "'" || c === '`') { i = skipQuoted(s, i); continue; }
      if (c === '(' || c === '{' || c === '[') depth++;
      else if (c === ')' || c === '}' || c === ']') depth--;
      else if (c === ',' && depth === 0) { out.push(s.slice(start, i)); start = i + 1; }
    }
    out.push(s.slice(start));
    return out.map(function (p) { return p.trim(); }).filter(Boolean);
  }

  function parseEntries(inner) {
    return splitTop(inner).map(function (part) {
      if (/^\.\.\./.test(part)) return { key: null, raw: part };
      var colon = -1;
      for (var i = 0; i < part.length; i++) {
        var c = part[i];
        if (c === '"' || c === "'") { i = skipQuoted(part, i); continue; }
        if (c === ':') { colon = i; break; }
      }
      if (colon < 0) return { key: null, raw: part };
      var key = part.slice(0, colon).trim().replace(/^["']|["']$/g, '');
      return { key: key, raw: part.slice(colon + 1).trim() };
    });
  }

  function unquote(raw) {
    var r = String(raw || '').trim();
    if (r.length >= 2 && (r[0] === '"' || r[0] === "'") && r[r.length - 1] === r[0]) {
      return r.slice(1, -1).replace(/\\(["'\\])/g, '$1');
    }
    return r;
  }

  function quoteVal(key, v) {
    if (typeof v === 'number' && isFinite(v)) return String(v);
    var s = String(v);
    if (/^-?\d+(\.\d+)?$/.test(s) && key !== 'fontFamily') return s;
    return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
  }

  function keyText(k) {
    return /^[A-Za-z_$][\w$]*$/.test(k) ? k : JSON.stringify(k);
  }

  function styleAttr(src, lt, oe) {
    var re = /\sstyle=\{/g;
    re.lastIndex = lt;
    var m;
    while ((m = re.exec(src)) && m.index < oe) {
      var brace = m.index + m[0].length - 1;
      var close = matchBrace(src, brace);
      if (close < 0 || close > oe) return null;
      var inner = src.slice(brace + 1, close).trim();
      var entries;
      if (inner[0] === '{' && matchBrace(inner, 0) === inner.length - 1) {
        entries = parseEntries(inner.slice(1, -1));
      } else {
        entries = [{ key: null, raw: '...(' + inner + ')' }];
      }
      return { start: m.index + 1, end: close + 1, entries: entries };
    }
    return null;
  }

  function readStyleAt(src, lt) {
    var oe = openTagEnd(src, lt);
    if (oe < 0) return {};
    var sa = styleAttr(src, lt, oe);
    var out = {};
    (sa ? sa.entries : []).forEach(function (e) {
      if (e.key) out[e.key] = unquote(e.raw);
    });
    return out;
  }

  function setStyleAt(src, lt, patch) {
    var oe = openTagEnd(src, lt);
    if (oe < 0) return src;
    var sa = styleAttr(src, lt, oe);
    var entries = sa ? sa.entries.slice() : [];
    Object.keys(patch).forEach(function (k) {
      var v = patch[k];
      var i = -1;
      for (var j = 0; j < entries.length; j++) if (entries[j].key === k) { i = j; break; }
      if (v === '' || v == null) {
        if (i >= 0) entries.splice(i, 1);
      } else if (i >= 0) {
        entries[i].raw = quoteVal(k, v);
      } else {
        entries.push({ key: k, raw: quoteVal(k, v) });
      }
    });
    var text = entries.length
      ? 'style={{' + entries.map(function (e) {
        return e.key == null ? e.raw : keyText(e.key) + ':' + e.raw;
      }).join(', ') + '}}'
      : '';
    if (sa) {
      var a = sa.start;
      if (!text) while (a > lt && /\s/.test(src[a - 1])) a--;
      return src.slice(0, a) + text + src.slice(sa.end);
    }
    if (!text) return src;
    var selfClose = src[oe - 1] === '/';
    var cut = selfClose ? oe - 1 : oe;
    var head = src.slice(0, cut).replace(/\s*$/, '');
    return head + ' ' + text + (selfClose ? ' ' : '') + src.slice(cut);
  }

  function readStyle(src, id) {
    var lt = findTag(src, id);
    return lt < 0 ? {} : readStyleAt(src, lt);
  }

  function setStyle(src, id, patch) {
    var lt = findTag(src, id);
    return lt < 0 ? src : setStyleAt(src, lt, patch);
  }

  function isFree(src, id) {
    return readStyle(src, id).position === 'absolute';
  }

  function clearPosition(src, id) {
    var patch = {};
    POSITION_KEYS.forEach(function (k) { patch[k] = ''; });
    return setStyle(src, id, patch);
  }

  // ---- structure ---------------------------------------------------------

  function removeRange(src, a, b) {
    var ls = src.lastIndexOf('\n', a - 1) + 1;
    if (/^\s*$/.test(src.slice(ls, a))) {
      var le = src.indexOf('\n', b);
      if (le < 0) le = src.length;
      if (/^\s*$/.test(src.slice(b, le))) {
        a = ls;
        b = le < src.length ? le + 1 : le;
      }
    }
    return src.slice(0, a) + src.slice(b);
  }

  // Studio inserts wrap each block in <div className="ue-place">. Once the block
  // moves out, the empty wrapper would leave a gap on the page.
  function dropEmptyPlaces(src) {
    for (var guard = 0; guard < 50; guard++) {
      var re = /<div(?=[\s>])/g;
      var m;
      var changed = false;
      while ((m = re.exec(src))) {
        var oe = openTagEnd(src, m.index);
        if (oe < 0 || !/ue-place/.test(src.slice(m.index, oe))) continue;
        var span = elementSpan(src, m.index);
        if (!span) continue;
        if (span.selfClosing || /^\s*$/.test(src.slice(span.openEnd, span.closeStart))) {
          src = removeRange(src, span.start, span.end);
          changed = true;
          break;
        }
      }
      if (!changed) return src;
    }
    return src;
  }

  function isContainer(src, id) {
    var span = spanById(src, id);
    return !!span && !span.selfClosing && CONTAINER_TAGS.test(span.name);
  }

  function h1Count(src) {
    return (String(src || '').match(/<h1(?=[\s>])/g) || []).length;
  }

  function removeElement(src, id) {
    var span = spanById(src, id);
    if (!span) return null;
    var text = src.slice(span.start, span.end);
    if (/<h1(?=[\s>])/.test(text) && h1Count(src) - h1Count(text) < 1) {
      return { src: src, text: text, refused: 'Keep one heading — the gate requires an h1' };
    }
    return { src: dropEmptyPlaces(removeRange(src, span.start, span.end)), text: text };
  }

  function insertAt(src, at, text, nested) {
    var ls = src.lastIndexOf('\n', at - 1) + 1;
    var lead = src.slice(ls, at);
    if (/^\s*$/.test(lead)) {
      return src.slice(0, at) + (nested ? '  ' : '') + text + '\n' + lead + src.slice(at);
    }
    return src.slice(0, at) + text + src.slice(at);
  }

  // Move element `id` into container `targetId`, before child `beforeId`
  // (or at the end). Returns null when the move would break the JSX.
  function moveElement(src, id, targetId, beforeId) {
    if (!id || !targetId || id === targetId || id === beforeId) return null;
    var span = spanById(src, id);
    var tspan = spanById(src, targetId);
    if (!span || !tspan || tspan.selfClosing) return null;
    if (tspan.start >= span.start && tspan.end <= span.end) return null;
    if (!sameFunction(src, span.start, tspan.start)) return null;
    if (beforeId) {
      var bspan = spanById(src, beforeId);
      if (!bspan || bspan.start < tspan.openEnd || bspan.end > tspan.closeStart) beforeId = '';
      else if (bspan.start >= span.end && src.slice(span.end, bspan.start).trim() === '') {
        return src;
      }
    }
    var text = src.slice(span.start, span.end);
    var out = removeRange(src, span.start, span.end);
    var at;
    var nested = false;
    if (beforeId) {
      at = findTag(out, beforeId);
    } else {
      var t2 = spanById(out, targetId);
      if (!t2) return null;
      at = t2.closeStart;
      nested = true;
    }
    if (at < 0) return null;
    out = insertAt(out, at, text, nested);
    return dropEmptyPlaces(out);
  }

  function duplicateElement(src, id) {
    var span = spanById(src, id);
    if (!span) return null;
    var copy = src.slice(span.start, span.end).replace(/\s+data-ue="ue\d+"/g, '');
    var ls = src.lastIndexOf('\n', span.start - 1) + 1;
    var lead = src.slice(ls, span.start);
    var sep = /^\s*$/.test(lead) ? '\n' + lead : ' ';
    return src.slice(0, span.end) + sep + copy + src.slice(span.end);
  }

  // ---- page-wide ---------------------------------------------------------

  var THEME_ATTR = /data-theme=["']([a-z]+)["']/;

  function themeOf(src) {
    var m = THEME_ATTR.exec(String(src || ''));
    return m ? m[1] : '';
  }

  function setTheme(src, theme) {
    src = String(src || '');
    if (THEME_ATTR.test(src)) return src.replace(/data-theme=["'][a-z]+["']/g, 'data-theme="' + theme + '"');
    if (/<Page(?=[\s>/])/.test(src)) return src.replace(/<Page(?=[\s>/])/, '<Page data-theme="' + theme + '"');
    return null;
  }

  function pageTag(src) {
    var m = /<Page(?=[\s>/])/.exec(String(src || ''));
    return m ? m.index : -1;
  }

  function setPageVars(src, vars) {
    var lt = pageTag(src);
    return lt < 0 ? null : setStyleAt(src, lt, vars);
  }

  function pageVars(src) {
    var lt = pageTag(src);
    return lt < 0 ? {} : readStyleAt(src, lt);
  }

  var api = {
    openTagEnd: openTagEnd,
    elementSpan: elementSpan,
    findTag: findTag,
    has: has,
    stamp: stamp,
    normalize: normalize,
    knownIds: knownIds,
    readStyle: readStyle,
    setStyle: setStyle,
    isFree: isFree,
    clearPosition: clearPosition,
    isContainer: isContainer,
    removeElement: removeElement,
    moveElement: moveElement,
    duplicateElement: duplicateElement,
    dropEmptyPlaces: dropEmptyPlaces,
    themeOf: themeOf,
    setTheme: setTheme,
    setPageVars: setPageVars,
    pageVars: pageVars,
    functionSpans: functionSpans
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.UeJsx = api;
})(typeof window !== 'undefined' ? window : this);
