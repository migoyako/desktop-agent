/* Safe Markdown subset for chat bubbles: the JavaScript twin of
   agent_framework/companion/ui/markdown.py. Keep the grammar identical; the
   shared sample set is tests/fixtures/markdown_samples.json. */
(function (global) {
  "use strict";

  var INLINE_CODE = /`([^`\n]+)`/g;
  var BOLD = /\*\*([^*\n]+)\*\*/g;
  var STRIKE = /~~([^~\n]+)~~/g;
  var LINK = /\[([^\]\n]+)\]\(([^)\s]+)\)/g;
  var BARE_URL = /(^|[\s(])(https?:\/\/[^\s<)]+)/g;
  var TAG = /(<[^>]+>)/;
  var HEADING = /^(#{1,3})\s+(.*)$/;
  var LIST_ITEM = /^(\s*)([-*]|[0-9]+[.)])\s+(.*)$/;
  var QUOTE = /^\s*>\s?(.*)$/;
  var FENCE = /^\s*```\s*([\p{L}\p{N}_+#.-]*)\s*$/u;
  var DIVIDER_CELL = /^:?-+:?$/;
  var PLACEHOLDER = /\x00([0-9]+)\x00/g;
  var URL_TAIL = ",.;:!?，。；：！？、）》」』\"";

  function escapeHtml(text) {
    return String(text === null || text === undefined ? "" : text).replace(
      /[&<>"']/g,
      function (ch) {
        return {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[ch];
      }
    );
  }

  function unescapeHtml(text) {
    return String(text || "").replace(/&(amp|lt|gt|quot|#39);/g, function (m, name) {
      return { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" }[name];
    });
  }

  function safeUrl(value) {
    var raw = String(value === null || value === undefined ? "" : value).trim();
    if (!raw) return "";
    var parsed;
    try {
      parsed = new URL(raw);
    } catch (error) {
      return "";
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    if (!parsed.hostname) return "";
    return raw;
  }

  function autolink(escaped) {
    return escaped
      .split(TAG)
      .map(function (part) {
        if (!part || part.charAt(0) === "<") return part;
        return part.replace(BARE_URL, function (match, prefix, url) {
          var tail = "";
          while (url && URL_TAIL.indexOf(url.charAt(url.length - 1)) >= 0) {
            tail = url.charAt(url.length - 1) + tail;
            url = url.slice(0, -1);
          }
          if (!safeUrl(unescapeHtml(url))) return match;
          return prefix + '<a href="' + url + '">' + url + "</a>" + tail;
        });
      })
      .join("");
  }

  function inline(text) {
    var code = [];
    var out = escapeHtml(text).replace(INLINE_CODE, function (match, span) {
      code.push(span);
      return "\x00" + (code.length - 1) + "\x00";
    });
    out = out.replace(BOLD, "<b>$1</b>");
    out = out.replace(STRIKE, "<s>$1</s>");
    out = out.replace(LINK, function (match, label, url) {
      if (!safeUrl(unescapeHtml(url))) return label + " (" + url + ")";
      return '<a href="' + url + '">' + label + "</a>";
    });
    out = autolink(out);
    return out.replace(PLACEHOLDER, function (match, index) {
      return "<code>" + code[Number(index)] + "</code>";
    });
  }

  function listBlock(items) {
    var parts = [];
    var stack = [];
    function closeLevel() {
      var top = stack.pop();
      if (top[2]) parts.push("</li>");
      parts.push("</" + top[1] + ">");
    }
    items.forEach(function (item) {
      var level = Math.max(0, Math.min(item[0], 3));
      var tag = item[1] ? "ol" : "ul";
      while (stack.length && level < stack[stack.length - 1][0]) closeLevel();
      if (stack.length && level === stack[stack.length - 1][0] && stack[stack.length - 1][1] !== tag) {
        closeLevel();
      }
      if (!stack.length || level > stack[stack.length - 1][0]) {
        parts.push("<" + tag + ">");
        stack.push([level, tag, false]);
      }
      if (stack[stack.length - 1][2]) parts.push("</li>");
      parts.push("<li>" + inline(item[2]));
      stack[stack.length - 1][2] = true;
    });
    while (stack.length) closeLevel();
    return parts.join("");
  }

  function splitRow(line) {
    var value = line.trim();
    if (value.charAt(0) === "|") value = value.slice(1);
    if (value.charAt(value.length - 1) === "|") value = value.slice(0, -1);
    return value.split("|").map(function (cell) {
      return cell.trim();
    });
  }

  function dividerAligns(line) {
    var value = line.trim();
    if (value.indexOf("|") < 0 || value.indexOf("-") < 0) return null;
    if (/[^\-:| ]/.test(value)) return null;
    var aligns = [];
    for (var cell of splitRow(value)) {
      if (!DIVIDER_CELL.test(cell)) return null;
      if (cell.charAt(0) === ":" && cell.charAt(cell.length - 1) === ":") aligns.push("center");
      else if (cell.charAt(cell.length - 1) === ":") aligns.push("right");
      else aligns.push("left");
    }
    return aligns.length ? aligns : null;
  }

  function tableBlock(lines, index) {
    if (index + 1 >= lines.length) return null;
    if (lines[index].indexOf("|") < 0) return null;
    var aligns = dividerAligns(lines[index + 1]);
    if (aligns === null) return null;
    var head = splitRow(lines[index]);
    if (head.length !== aligns.length) return null;
    var rows = [];
    var cursor = index + 2;
    while (cursor < lines.length && lines[cursor].trim() && lines[cursor].indexOf("|") >= 0) {
      var row = splitRow(lines[cursor]);
      while (row.length < aligns.length) row.push("");
      rows.push(row.slice(0, aligns.length));
      cursor += 1;
    }
    var parts = ['<table class="md-table" border="1" cellspacing="0" cellpadding="4">', "<tr>"];
    aligns.forEach(function (align, i) {
      parts.push('<th align="' + align + '">' + inline(head[i]) + "</th>");
    });
    parts.push("</tr>");
    rows.forEach(function (row) {
      parts.push("<tr>");
      aligns.forEach(function (align, i) {
        parts.push('<td align="' + align + '">' + inline(row[i]) + "</td>");
      });
      parts.push("</tr>");
    });
    parts.push("</table>");
    return { html: parts.join(""), next: cursor };
  }

  function codeBlock(language, lines) {
    var label = language ? '<div class="md-lang">' + escapeHtml(language) + "</div>" : "";
    return label + '<pre class="md-code">' + escapeHtml(lines.join("\n")) + "</pre>";
  }

  function startsBlock(line) {
    return HEADING.test(line) || QUOTE.test(line) || LIST_ITEM.test(line) || FENCE.test(line);
  }

  function render(text) {
    var lines = String(text === null || text === undefined ? "" : text)
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n");
    var blocks = [];
    var index = 0;
    var total = lines.length;
    while (index < total) {
      var line = lines[index];
      var fence = FENCE.exec(line);
      if (fence) {
        var language = fence[1];
        index += 1;
        var body = [];
        while (index < total && !FENCE.test(lines[index])) {
          body.push(lines[index]);
          index += 1;
        }
        if (index < total) index += 1;
        blocks.push(codeBlock(language, body));
        continue;
      }
      if (!line.trim()) {
        index += 1;
        continue;
      }
      var heading = HEADING.exec(line);
      if (heading) {
        var level = heading[1].length;
        blocks.push('<div class="md-h md-h' + level + '"><b>' + inline(heading[2]) + "</b></div>");
        index += 1;
        continue;
      }
      if (QUOTE.test(line)) {
        var quoted = [];
        while (index < total) {
          var match = QUOTE.exec(lines[index]);
          if (!match) break;
          quoted.push(inline(match[1]));
          index += 1;
        }
        blocks.push('<blockquote class="md-quote">' + quoted.join("<br/>") + "</blockquote>");
        continue;
      }
      var item = LIST_ITEM.exec(line);
      if (item) {
        var items = [];
        while (index < total) {
          var listMatch = LIST_ITEM.exec(lines[index]);
          if (!listMatch) break;
          var depth = Math.floor(listMatch[1].replace(/\t/g, "    ").length / 2);
          items.push([depth, /^[0-9]/.test(listMatch[2]), listMatch[3]]);
          index += 1;
        }
        blocks.push(listBlock(items));
        continue;
      }
      var table = tableBlock(lines, index);
      if (table) {
        blocks.push(table.html);
        index = table.next;
        continue;
      }
      var paragraph = [];
      while (index < total && lines[index].trim()) {
        if (paragraph.length && (startsBlock(lines[index]) || tableBlock(lines, index))) break;
        paragraph.push(inline(lines[index]));
        index += 1;
      }
      blocks.push("<div>" + paragraph.join("<br/>") + "</div>");
    }
    return blocks.join("") || "<div></div>";
  }

  var api = { escapeHtml: escapeHtml, inline: inline, render: render };
  global.CompanionMarkdown = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
