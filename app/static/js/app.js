/* 艾宾浩斯复习前端 */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let currentStudyItemId = null;
let notifyTimer = null;
/** 知识点整轮复习会话 */
let quizSession = null;
/** 错题单题复习 */
let wrongSession = null;

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg = data?.detail
      ? typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail)
      : res.statusText;
    throw new Error(msg);
  }
  return data;
}

function showView(name) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  const el = $(`#view-${name}`);
  if (el) el.classList.remove("hidden");
  $$(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  if (name === "home") loadDue();
  if (name === "all") loadAll();
  if (name === "wrong") loadWrongBook();
  if (name === "exam") loadExamView();
  if (name === "interview") loadInterviewView();
  if (name === "settings") loadSettings();
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 把字面量 \\n / \\t 还原为真实换行，便于展示代码。 */
function unescapeNewlines(s) {
  return String(s ?? "")
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t");
}

/** 多要点中文回答：在「要点N」等处补换行，便于阅读。 */
function normalizeProseBreaks(text) {
  let t = unescapeNewlines(text).trim();
  if (!t) return "";
  // 行内「要点1: …；要点2:」→ 换行分段
  t = t.replace(/\s*(要点\s*\d+\s*[：:])/g, "\n$1");
  // 「；1.」「。2、」类编号
  t = t.replace(/([；。！？])\s*(?=\d+[\.、．)）])/g, "$1\n");
  // 行内（1）（2）——避免打断已在行首的编号
  t = t.replace(/([^\n])\s*([（(]\d+[）)])/g, "$1\n$2");
  // 行首已是要点时去掉开头多余换行
  return t.replace(/^\n+/, "").replace(/\n{3,}/g, "\n\n");
}

function looksLikeMultiPointProse(text) {
  const t = unescapeNewlines(text);
  const n = (t.match(/要点\s*\d+/g) || []).length;
  if (n >= 2) return true;
  // 同一段里出现至少 3 个「1. / 2. / （1）」类编号
  const nums = t.match(/(?:^|[\n；。]\s*)(?:\d+[\.、．)]|[（(]\d+[）)])/g) || [];
  return nums.length >= 3;
}

function looksLikeCode(text) {
  const t = unescapeNewlines(text).trim();
  if (!t) return false;
  if (/```/.test(t)) return true;
  // 多要点讲解文不是代码（即使提到 sync_api / playwright）
  if (looksLikeMultiPointProse(t)) return false;

  const cn = (t.match(/[\u4e00-\u9fff]/g) || []).length;
  const lines = t.split("\n").filter((x) => x.trim());
  const codeLineRe =
    /^(def |class |async def |import |from |@|print\(|    |\t|[A-Za-z_]\w*\s*=\s*)|[{};]$/;
  const codeLines = lines.filter((l) => codeLineRe.test(l.trim()) || /[{};]$/.test(l.trim()));

  // 中文讲解占主导 → 不当代码
  if (cn >= 30 && codeLines.length < Math.max(2, Math.ceil(lines.length * 0.45))) {
    return false;
  }

  if (lines.length >= 2) {
    if (
      /^\[.+\]$/.test(lines[0].trim()) || // ini section
      codeLines.length >= Math.ceil(lines.length * 0.5)
    ) {
      return true;
    }
  }
  if (/^(def |class |import |from |async def |@pytest)/m.test(t)) return true;
  if (/\[pytest\]|addopts\s*=|testpaths\s*=/.test(t) && cn < 20) return true;
  return false;
}

function detectCodeLang(text) {
  const t = unescapeNewlines(text);
  if (/```(\w+)/.test(t)) return RegExp.$1.toLowerCase();
  if (/\[pytest\]|addopts\s*=|^\s*\[[^\]]+\]/m.test(t)) return "ini";
  if (/^(def |class |import |from |async def )/m.test(t) || /pytest\.|playwright\./.test(t)) {
    return "python";
  }
  if (/function |const |let |=>|npm |require\(/.test(t)) return "javascript";
  return "";
}

function splitOptionLabel(opt) {
  const m = String(opt ?? "").match(/^([A-Da-d])[\.、．)\s]+([\s\S]*)$/);
  if (m) return { letter: m[1].toUpperCase(), body: m[2] };
  return { letter: "", body: String(opt ?? "") };
}

function renderCodeBlock(code, lang = "") {
  const body = unescapeNewlines(code).replace(/^\n+|\n+$/g, "");
  const langAttr = lang ? ` data-lang="${escapeHtml(lang)}"` : "";
  const langClass = lang ? ` lang-${escapeHtml(lang)}` : "";
  const label = lang
    ? `<div class="code-block-label">${escapeHtml(lang)}</div>`
    : "";
  return `<div class="code-block-wrap${langClass}"${langAttr}>${label}<pre class="code-block"><code>${escapeHtml(
    body
  )}</code></pre></div>`;
}

/** 普通/多要点正文：列表或换行展示 */
function renderProseHtml(text) {
  const t = normalizeProseBreaks(text);
  if (!t) return "";
  const lines = t
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean);

  const pointRe = /^(要点\s*\d+\s*[：:]\s*|\d+[\.、．)\s]+|[（(]\d+[）)]\s*|[-•*]\s*)/;
  const pointLines = lines.filter((l) => pointRe.test(l));
  if (lines.length >= 2 && pointLines.length >= 2 && pointLines.length >= lines.length - 1) {
    return `<ol class="rich-list">${lines
      .map((l) => {
        const body = l.replace(pointRe, "").trim() || l;
        return `<li>${escapeHtml(body)}</li>`;
      })
      .join("")}</ol>`;
  }

  // 单行但含多个要点：再拆一次
  if (lines.length === 1 && looksLikeMultiPointProse(lines[0])) {
    const parts = lines[0]
      .split(/(?=要点\s*\d+\s*[：:])/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (parts.length >= 2) {
      return `<ol class="rich-list">${parts
        .map((l) => {
          const body = l.replace(pointRe, "").trim() || l;
          return `<li>${escapeHtml(body)}</li>`;
        })
        .join("")}</ol>`;
    }
  }

  return `<div class="rich-text">${escapeHtml(t).replace(/\n/g, "<br>")}</div>`;
}

/** 支持 ```python 围栏；否则按代码/普通文本渲染。 */
function renderRichText(text, { forcePython = false } = {}) {
  const raw = unescapeNewlines(text);
  if (!raw.trim()) return "";

  if (/```/.test(raw)) {
    const parts = [];
    const re = /```(\w+)?\s*\n?([\s\S]*?)```/g;
    let last = 0;
    let m;
    while ((m = re.exec(raw))) {
      const before = raw.slice(last, m.index);
      if (before.trim()) {
        parts.push(
          looksLikeCode(before) && !looksLikeMultiPointProse(before)
            ? renderCodeBlock(before, detectCodeLang(before) || (forcePython ? "python" : ""))
            : renderProseHtml(before)
        );
      }
      const lang = (m[1] || detectCodeLang(m[2]) || (forcePython ? "python" : "")).toLowerCase();
      parts.push(renderCodeBlock(m[2], lang || (forcePython ? "python" : "")));
      last = m.index + m[0].length;
    }
    const after = raw.slice(last);
    if (after.trim()) {
      parts.push(
        looksLikeCode(after) && !looksLikeMultiPointProse(after)
          ? renderCodeBlock(after, detectCodeLang(after) || (forcePython ? "python" : ""))
          : renderProseHtml(after)
      );
    }
    return parts.join("");
  }

  // 多要点讲解优先按正文列表，不强行套代码块
  if (looksLikeMultiPointProse(raw)) {
    return renderProseHtml(raw);
  }

  if (forcePython || looksLikeCode(raw)) {
    return renderCodeBlock(
      raw,
      forcePython ? "python" : detectCodeLang(raw) || (forcePython ? "python" : "")
    );
  }
  return renderProseHtml(raw);
}

function renderOptionContent(opt) {
  const { letter, body } = splitOptionLabel(opt);
  const prefix = letter
    ? `<span class="opt-letter">${escapeHtml(letter)}.</span>`
    : "";
  const content = body || unescapeNewlines(opt);
  if (looksLikeCode(content)) {
    const lang = detectCodeLang(content);
    return `<div class="opt-body">${prefix}${renderCodeBlock(content, lang)}</div>`;
  }
  const text = unescapeNewlines(opt);
  return `<span class="opt-text">${escapeHtml(text).replace(/\n/g, "<br>")}</span>`;
}

function formatTime(s) {
  return s ? String(s).replace("T", " ").slice(0, 16) : "-";
}

/** YYYY-MM-DD，用于按天筛选 */
function dayKey(s) {
  if (!s) return "";
  return String(s).replace("T", " ").slice(0, 10);
}

function todayKey() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function filterByDay(items, dateStr, field = "created_at") {
  if (!dateStr) return items || [];
  return (items || []).filter((it) => dayKey(it[field]) === dateStr);
}

/** 统计有数据的日期（新→旧），返回 [{date, count}] */
function collectDayStats(items, field = "created_at") {
  const map = new Map();
  for (const it of items || []) {
    const k = dayKey(it[field]);
    if (!k || k.length < 10) continue;
    map.set(k, (map.get(k) || 0) + 1);
  }
  return [...map.entries()]
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => b.date.localeCompare(a.date));
}

/** 按月聚合有数据的天 */
function buildMonthDayIndex(items, field = "created_at") {
  const byMonth = new Map();
  for (const s of collectDayStats(items, field)) {
    const ym = s.date.slice(0, 7);
    const day = s.date.slice(8, 10);
    if (!byMonth.has(ym)) byMonth.set(ym, []);
    byMonth.get(ym).push({ date: s.date, day, count: s.count });
  }
  for (const arr of byMonth.values()) {
    arr.sort((a, b) => b.day.localeCompare(a.day));
  }
  const months = [...byMonth.keys()].sort((a, b) => b.localeCompare(a));
  return { byMonth, months };
}

function formatMonthLabel(ym, total) {
  if (!ym || ym.length < 7) return ym || "";
  const y = ym.slice(0, 4);
  const m = Number(ym.slice(5, 7));
  return `${y}年${m}月（${total}）`;
}

/**
 * 用「月份 + 日期」两个下拉同步隐藏的 YYYY-MM-DD。
 * hidden.dataset.mode === "all" 表示展示全部。
 */
function syncMonthDaySelects(monthSel, daySel, hiddenSel, items, field = "created_at") {
  const monthEl = $(monthSel);
  const dayEl = $(daySel);
  const hidden = $(hiddenSel);
  if (!monthEl || !dayEl || !hidden) return;

  const { byMonth, months } = buildMonthDayIndex(items, field);
  const modeAll = hidden.dataset.mode === "all";
  const today = todayKey();
  let full = modeAll ? "" : hidden.value || "";

  if (!modeAll && !full) {
    full = today;
    hidden.value = full;
  }

  if (modeAll) {
    monthEl.innerHTML =
      `<option value="">全部月份</option>` +
      months
        .map((m) => {
          const total = byMonth.get(m).reduce((a, x) => a + x.count, 0);
          return `<option value="${escapeHtml(m)}">${escapeHtml(
            formatMonthLabel(m, total)
          )}</option>`;
        })
        .join("");
    monthEl.value = "";
    dayEl.innerHTML = `<option value="">全部日期</option>`;
    dayEl.value = "";
    dayEl.disabled = true;
    hidden.value = "";
    return;
  }

  dayEl.disabled = false;

  let ym = full.length >= 7 ? full.slice(0, 7) : "";
  if (full.length === 8 && full.endsWith("-")) ym = full.slice(0, 7);

  // 默认/指定今天：即使当月无数据也保留该月
  const preferTodayMonth = !ym || full === today || ym === today.slice(0, 7);
  if (!ym || (!byMonth.has(ym) && !preferTodayMonth)) {
    ym = byMonth.has(today.slice(0, 7)) ? today.slice(0, 7) : months[0] || today.slice(0, 7);
  }
  if (!ym) ym = today.slice(0, 7);

  const monthOpts = new Map(months.map((m) => [m, byMonth.get(m)]));
  if (!monthOpts.has(ym)) monthOpts.set(ym, []);
  const monthKeys = [...monthOpts.keys()].sort((a, b) => b.localeCompare(a));

  monthEl.innerHTML = monthKeys
    .map((m) => {
      const arr = monthOpts.get(m) || [];
      const total = arr.reduce((a, x) => a + x.count, 0);
      return `<option value="${escapeHtml(m)}">${escapeHtml(
        formatMonthLabel(m, total)
      )}</option>`;
    })
    .join("");
  monthEl.value = ym;

  const days = [...(byMonth.get(ym) || [])];
  let day = full.length >= 10 ? full.slice(8, 10) : "";
  if (full.length === 8 && full.endsWith("-")) day = "";

  // 切月后：优先今天，否则该月最近有数据的一天
  if (!day || (days.length && !days.some((d) => d.day === day) && full.endsWith("-"))) {
    if (ym === today.slice(0, 7)) day = today.slice(8, 10);
    else day = days[0]?.day || today.slice(8, 10);
  }
  if (!days.some((d) => d.day === day)) {
    days.unshift({ date: `${ym}-${day}`, day, count: 0 });
    days.sort((a, b) => b.day.localeCompare(a.day));
  }

  dayEl.innerHTML = days
    .map(
      (d) =>
        `<option value="${escapeHtml(d.day)}">${escapeHtml(d.day)}日（${d.count}）</option>`
    )
    .join("");
  dayEl.value = day;
  hidden.value = ym && day ? `${ym}-${day}` : "";
  hidden.dataset.mode = "";
}

function bindMonthDayFilter(monthSel, daySel, hiddenSel, clearSel, onChange) {
  const monthEl = $(monthSel);
  const dayEl = $(daySel);
  const hidden = $(hiddenSel);
  const clear = $(clearSel);
  if (hidden && !hidden.value && hidden.dataset.mode !== "all") {
    hidden.value = todayKey();
  }
  if (monthEl && !monthEl.dataset.bound) {
    monthEl.dataset.bound = "1";
    monthEl.addEventListener("change", () => {
      if (!hidden) return;
      if (!monthEl.value) {
        hidden.value = "";
        hidden.dataset.mode = "all";
      } else {
        hidden.dataset.mode = "";
        // 仅定月份，日期由 sync 在当月有数据的天里选
        hidden.value = `${monthEl.value}-`;
      }
      onChange();
    });
  }
  if (dayEl && !dayEl.dataset.bound) {
    dayEl.dataset.bound = "1";
    dayEl.addEventListener("change", () => {
      if (!hidden) return;
      const ym = monthEl?.value || "";
      const day = dayEl.value || "";
      if (!ym || !day) {
        hidden.value = "";
        hidden.dataset.mode = "all";
      } else {
        hidden.dataset.mode = "";
        hidden.value = `${ym}-${day}`;
      }
      onChange();
    });
  }
  if (clear && !clear.dataset.bound) {
    clear.dataset.bound = "1";
    clear.addEventListener("click", () => {
      if (hidden) {
        hidden.value = "";
        hidden.dataset.mode = "all";
      }
      onChange();
    });
  }
}

let wrongCache = [];
let allItemsCache = [];
let examCache = [];
let interviewCache = [];

function renderItemCard(item, { dueMode = false } = {}) {
  const statusMap = {
    pending: "待出题",
    generating: "出题中…",
    ready: "已出题",
    failed: "出题失败",
  };
  const qStatus = statusMap[item.questions_status] || item.questions_status;
  const qCount = Number(item.question_count || 0);
  return `
    <article class="card" data-id="${item.id}">
      <div>
        <h3>${escapeHtml(item.title)}</h3>
        <div class="meta">
          <span class="tag">${escapeHtml(item.stage_label || "")}</span>
          <span>下次：${escapeHtml(formatTime(item.next_review_at))}</span>
          · 录入：${escapeHtml(formatTime(item.created_at))}
          · ${escapeHtml(qStatus)}${qCount ? ` ${qCount} 道` : ""}
          ${item.attachment_count ? ` · 附件 ${item.attachment_count}` : ""}
        </div>
      </div>
      <div class="actions">
        ${
          dueMode
            ? `<button class="btn primary" data-action="quiz" data-id="${item.id}">开始复习</button>`
            : `<button class="btn primary" data-action="quiz" data-id="${item.id}">复习答题</button>`
        }
        <button class="btn" data-action="detail" data-id="${item.id}">详情</button>
        ${
          item.questions_status === "generating"
            ? `<button class="btn" disabled>出题中…</button>`
            : `<button class="btn" data-action="regen" data-id="${item.id}">再出一批题</button>`
        }
        <button class="btn danger" data-action="delete" data-id="${item.id}">删除</button>
      </div>
    </article>
  `;
}

async function loadDue() {
  const data = await api("/api/items/due");
  updateBadge(data.total_count ?? (data.count || 0) + (data.wrong_count || 0));
  renderDueBanner(data);

  const list = $("#dueList");
  const empty = $("#dueEmpty");
  if (!data.items.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
    list.innerHTML = data.items.map((i) => renderItemCard(i, { dueMode: true })).join("");
    bindListActions(list);
  }

  const wlist = $("#dueWrongList");
  const wempty = $("#dueWrongEmpty");
  const wrongs = data.wrong_items || [];
  if (!wrongs.length) {
    wlist.innerHTML = "";
    wempty.classList.remove("hidden");
  } else {
    wempty.classList.add("hidden");
    wlist.innerHTML = wrongs.map((w) => renderWrongCard(w, { dueMode: true })).join("");
    bindWrongActions(wlist);
  }
}

function renderDueBanner(data) {
  const banner = $("#dueBanner");
  if (!banner) return;
  const total = data.total_count ?? (data.count || 0) + (data.wrong_count || 0);
  if (!total) {
    banner.classList.add("hidden");
    banner.innerHTML = "";
    return;
  }
  const titles = (data.titles || []).slice(0, 4).join("、");
  banner.classList.remove("hidden");
  banner.innerHTML = `<strong>有 ${total} 项待复习</strong>${
    titles ? `：${escapeHtml(titles)}${total > 4 ? "…" : ""}` : ""
  } <span class="hint">（系统提醒需 run.py 常驻，并开启桌面/浏览器通知）</span>`;
}

async function loadWrongBook() {
  const data = await api("/api/wrong");
  wrongCache = data.items || [];
  renderWrongList();
}

function renderWrongList() {
  const list = $("#wrongList");
  const empty = $("#wrongEmpty");
  const date = $("#wrongDateFilter")?.value || "";
  syncMonthDaySelects(
    "#wrongMonthSelect",
    "#wrongDaySelect",
    "#wrongDateFilter",
    wrongCache
  );
  const items = filterByDay(wrongCache, date);
  if (!items.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    empty.textContent = date
      ? date === todayKey()
        ? "今天暂无错题。"
        : "该日期暂无错题。"
      : "错题本为空。";
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = items.map((w) => renderWrongCard(w)).join("");
  bindWrongActions(list);
}

function renderWrongCard(w, { dueMode = false } = {}) {
  return `
    <article class="card" data-wrong-id="${w.id}">
      <div>
        <h3>${escapeHtml(w.item_title || "错题")}</h3>
        <div class="meta">
          <span class="tag">错题</span>
          <span class="tag">${escapeHtml(w.stage_label || "")}</span>
          <span>下次：${escapeHtml(formatTime(w.next_review_at))}</span>
          · 入本：${escapeHtml(formatTime(w.created_at))}
        </div>
        <p class="hint" style="margin:0.4rem 0 0">${escapeHtml((w.question?.stem || "").slice(0, 120))}</p>
      </div>
      <div class="actions">
        <button class="btn primary" data-action="wrong-quiz" data-id="${w.id}">
          ${dueMode ? "复习错题" : "答题"}
        </button>
        <button class="btn" data-action="detail" data-id="${w.item_id}">知识点</button>
      </div>
    </article>
  `;
}

function bindWrongActions(root) {
  root.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      const id = Number(btn.dataset.id);
      try {
        if (action === "wrong-quiz") await startWrongQuiz(id);
        if (action === "detail") await showDetail(id);
      } catch (e) {
        alert(e.message);
      }
    });
  });
}

async function loadAll() {
  const data = await api("/api/items");
  allItemsCache = data.items || [];
  renderAllList();
}

function renderAllList() {
  const list = $("#allList");
  const empty = $("#allEmpty");
  const date = $("#allDateFilter")?.value || "";
  syncMonthDaySelects(
    "#allMonthSelect",
    "#allDaySelect",
    "#allDateFilter",
    allItemsCache
  );
  const items = filterByDay(allItemsCache, date);
  if (!items.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    empty.textContent = date
      ? date === todayKey()
        ? "今天暂无知识点。"
        : "该日期暂无知识点。"
      : "暂无知识点。";
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = items.map((i) => renderItemCard(i)).join("");
  bindListActions(list);
}

function bindListActions(root) {
  root.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.dataset.id);
      const action = btn.dataset.action;
      try {
        if (action === "quiz") await startQuiz(id);
        if (action === "detail") await showDetail(id);
        if (action === "regen") {
          btn.disabled = true;
          const oldText = btn.textContent;
          btn.textContent = "出题中…";
          try {
            const data = await api(`/api/items/${id}/generate-questions`, {
              method: "POST",
            });
            const meta = data.generate_meta || {};
            const added = meta.added_count ?? "?";
            const total = meta.total_count ?? data.item?.question_count ?? "?";
            const batch = meta.generation_batch;
            alert(
              `已追加 ${added} 道题` +
                (batch ? `（第 ${batch} 批）` : "") +
                `，当前共 ${total} 道。`
            );
            await loadAll();
            await loadDue();
            await showDetail(id);
          } catch (err) {
            alert(err.message || String(err));
          } finally {
            // 列表可能已重绘，按 data-id 找回按钮
            const alive = document.querySelector(
              `button[data-action="regen"][data-id="${id}"]`
            );
            if (alive) {
              alive.disabled = false;
              alive.textContent = "再出一批题";
            } else {
              btn.disabled = false;
              btn.textContent = oldText || "再出一批题";
            }
          }
        }
        if (action === "delete") {
          if (!confirm("确认删除该知识点及其附件？")) return;
          await api(`/api/items/${id}`, { method: "DELETE" });
          await loadAll();
          await loadDue();
        }
      } catch (e) {
        alert(e.message);
      }
    });
  });
}

function updateBadge(count) {
  const badge = $("#dueBadge");
  if (count > 0) {
    badge.textContent = String(count);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

async function startQuiz(itemId) {
  showView("quiz");
  $("#quizStatus").textContent = "加载题目…";
  $("#quizBox").innerHTML = "";
  wrongSession = null;
  try {
    const data = await api(`/api/items/${itemId}/quiz`);
    $("#quizTitle").textContent = data.item.title;
    if (!data.questions.length) {
      const mode = data.quiz_mode || "";
      let tip = "尚无题目。";
      if (mode === "wrong_only") {
        tip = "本知识点题目已全部做过，且当前没有错题，无需重做。";
      } else if (data.item.questions_status) {
        tip = `尚无题目（状态：${escapeHtml(data.item.questions_status)}）。`;
      }
      $("#quizBox").innerHTML = `
        <div class="quiz-card">
          <p>${tip}
          ${data.item.questions_error ? `<br/>错误：${escapeHtml(data.item.questions_error)}` : ""}</p>
          <button class="btn primary" id="btnRegenQuiz">再出一批题</button>
          <button class="btn" onclick="showView('home')">返回</button>
        </div>`;
      $("#btnRegenQuiz").onclick = async () => {
        try {
          $("#quizStatus").textContent = "正在追加出题…";
          const data = await api(`/api/items/${itemId}/generate-questions`, {
            method: "POST",
          });
          const meta = data.generate_meta || {};
          $("#quizStatus").textContent = `已追加 ${meta.added_count ?? 0} 道，共 ${
            meta.total_count ?? "?"
          } 道`;
          await startQuiz(itemId);
        } catch (e) {
          alert(e.message);
          $("#quizStatus").textContent = "";
        }
      };
      $("#quizStatus").textContent = "";
      return;
    }
    const modeHint =
      data.quiz_mode === "wrong_only"
        ? "已做过一遍，本次仅复习错题。"
        : data.quiz_mode === "first_pass"
          ? "首次复习：先完成尚未作答的题目。"
          : "请完成本轮题目后再结算复习档位。";
    quizSession = {
      itemId,
      title: data.item.title,
      questions: data.questions.slice(),
      index: 0,
      hadWrong: false,
      results: [],
      quizMode: data.quiz_mode || "first_pass",
      modeHint,
    };
    renderCurrentQuizQuestion();
    $("#quizStatus").textContent = "";
  } catch (e) {
    $("#quizStatus").textContent = e.message;
    $("#quizStatus").className = "status err";
  }
}

function renderCurrentQuizQuestion() {
  if (!quizSession) return;
  const { questions, index, title } = quizSession;
  const q = questions[index];
  $("#quizTitle").textContent = `${title}（${index + 1}/${questions.length}）`;
  $("#quizBox").innerHTML =
    `<p class="hint">${escapeHtml(quizSession.modeHint || "")}（${index + 1}/${questions.length}）</p>` +
    renderQuizCard(q);
  bindAnswerModeToggle();
  $("#btnSubmitAnswer").onclick = () =>
    submitAnswer(quizSession.itemId, q.id, q.qtype || "short");
}

async function startWrongQuiz(wrongId) {
  showView("quiz");
  $("#quizStatus").textContent = "加载错题…";
  $("#quizBox").innerHTML = "";
  quizSession = null;
  try {
    const data = await api("/api/wrong");
    const entry = (data.items || []).find((x) => x.id === wrongId);
    if (!entry) throw new Error("错题不存在");
    wrongSession = { wrongId, entry };
    $("#quizTitle").textContent = `错题复习 · ${entry.item_title}`;
    $("#quizBox").innerHTML =
      `<p class="hint">错题独立艾宾浩斯：${escapeHtml(entry.stage_label)}，下次 ${escapeHtml(
        formatTime(entry.next_review_at)
      )}</p>` + renderQuizCard(entry.question);
    bindAnswerModeToggle();
    $("#btnSubmitAnswer").onclick = () =>
      submitWrongAnswer(wrongId, entry.question.qtype || "short");
    $("#quizStatus").textContent = "";
  } catch (e) {
    $("#quizStatus").textContent = e.message;
    $("#quizStatus").className = "status err";
  }
}

function renderQuizCard(q) {
  const typeLabel = q.qtype_label || "题目";
  const qtype = q.qtype || "short";
  let answerArea = "";
  if (qtype === "choice" && Array.isArray(q.options) && q.options.length) {
    answerArea = `<div class="option-list" id="answerInput">
      ${q.options
        .map((opt) => {
          const codeOpt = looksLikeCode(splitOptionLabel(opt).body || opt);
          return `
        <label class="option-row${codeOpt ? " code-opt" : ""}">
          <input type="radio" name="quiz_opt" value="${escapeHtml(opt)}" />
          ${renderOptionContent(opt)}
        </label>`;
        })
        .join("")}
    </div>`;
  } else if (qtype === "judge") {
    const opts = q.options?.length ? q.options : ["正确", "错误"];
    answerArea = `<div class="option-list" id="answerInput">
      ${opts
        .map(
          (opt) => `
        <label class="option-row">
          <input type="radio" name="quiz_opt" value="${escapeHtml(opt)}" />
          <span class="opt-text">${escapeHtml(opt)}</span>
        </label>`
        )
        .join("")}
    </div>`;
  } else {
    const preferCode =
      /代码|code|脚本|pytest\.ini|编写|实现|补全|写出/.test(q.stem || "") ||
      qtype === "scenario";
    const mode = preferCode ? "code" : "text";
    answerArea = `
      <div class="answer-mode-bar">
        <span class="hint">作答方式</span>
        <div class="mode-toggle" id="answerModeToggle" role="group" aria-label="作答方式">
          <button type="button" class="mode-btn${mode === "text" ? " active" : ""}" data-mode="text">文本</button>
          <button type="button" class="mode-btn${mode === "code" ? " active" : ""}" data-mode="code">代码</button>
        </div>
      </div>
      <label class="answer-label">你的回答
        <textarea id="answerInput" class="${mode === "code" ? "code-input" : ""}" rows="14"
          data-answer-mode="${mode}"
          placeholder="${
            mode === "code"
              ? "在此输入 Python / 配置代码…"
              : qtype === "scenario"
                ? "结合场景说明你会怎么做、为什么…"
                : "用自己的话作答…"
          }"></textarea>
      </label>`;
  }
  return `
    <div class="quiz-card">
      <span class="tag">${escapeHtml(typeLabel)}</span>
      <h3>${escapeHtml(q.stem)}</h3>
      ${answerArea}
      <div class="actions" style="margin-top:0.75rem">
        <button class="btn primary" id="btnSubmitAnswer">提交判分</button>
        <button class="btn" onclick="showView('home')">返回</button>
      </div>
      <div id="answerFeedback"></div>
    </div>`;
}

function bindAnswerModeToggle() {
  const bar = $("#answerModeToggle");
  const input = $("#answerInput");
  if (!input || input.tagName !== "TEXTAREA") return;
  if (bar) {
    bar.querySelectorAll(".mode-btn").forEach((btn) => {
      btn.onclick = () => {
        const mode = btn.dataset.mode === "code" ? "code" : "text";
        bar.querySelectorAll(".mode-btn").forEach((b) => {
          b.classList.toggle("active", b.dataset.mode === mode);
        });
        input.dataset.answerMode = mode;
        input.classList.toggle("code-input", mode === "code");
        input.placeholder =
          mode === "code"
            ? "在此输入 Python / 配置代码…"
            : "用自己的话作答…";
      };
    });
  }
  // 代码模式下 Tab 插入缩进
  input.onkeydown = (e) => {
    if (e.key !== "Tab" || input.dataset.answerMode !== "code") return;
    e.preventDefault();
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const v = input.value;
    input.value = v.slice(0, start) + "    " + v.slice(end);
    input.selectionStart = input.selectionEnd = start + 4;
  };
}

function collectAnswer(qtype) {
  if (qtype === "choice" || qtype === "judge") {
    const checked = document.querySelector('input[name="quiz_opt"]:checked');
    return checked ? checked.value.trim() : "";
  }
  const el = $("#answerInput");
  if (!el) return "";
  let text = el.value.trim();
  if (!text) return "";
  // 代码模式下提交时包成 python 代码块，便于判分后按代码块展示
  if (el.dataset.answerMode === "code" && !/```/.test(text)) {
    text = "```python\n" + text + "\n```";
  }
  return text;
}

function lockQuizInputs() {
  const input = $("#answerInput");
  if (input && input.tagName === "TEXTAREA") input.disabled = true;
  document.querySelectorAll('input[name="quiz_opt"]').forEach((el) => {
    el.disabled = true;
  });
  const btn = $("#btnSubmitAnswer");
  if (btn) btn.disabled = true;
}

function renderGradeTeach(result) {
  const refText = String(result.reference_answer || "").trim();
  const explanation = String(result.explanation || "").trim();
  const extension = String(result.extension || "").trim();
  // 反馈里若已内嵌解释/拓展/正确答案，展示时去掉，避免重复
  let feedbackText = String(result.feedback || "");
  feedbackText = feedbackText
    .replace(/\n?正确答案[：:][\s\S]*?(?=\n解释[：:]|\n知识拓展[：:]|（|$)/, "")
    .replace(/\n?解释[：:][\s\S]*?(?=\n知识拓展[：:]|（|$)/, "")
    .replace(/\n?知识拓展[：:][\s\S]*?(?=（|$)/, "")
    .trim();

  const answerBlock = refText
    ? `<div class="correct-answer"><strong>${
        result.correct ? "参考答案：" : "正确答案："
      }</strong>${renderRichText(refText)}</div>`
    : "";
  const explainBlock = explanation
    ? `<div class="teach-block explain-block"><strong>解释：</strong>${renderRichText(
        explanation
      )}</div>`
    : "";
  const extendBlock = extension
    ? `<div class="teach-block extend-block"><strong>知识拓展：</strong>${renderRichText(
        extension
      )}</div>`
    : "";
  return { feedbackText, answerBlock, explainBlock, extendBlock };
}

async function submitAnswer(itemId, questionId, qtype = "short") {
  const answer = collectAnswer(qtype);
  if (!answer) {
    alert(qtype === "choice" || qtype === "judge" ? "请先选择一个选项" : "请先填写答案");
    return;
  }
  if (!quizSession) return;
  const isLast = quizSession.index >= quizSession.questions.length - 1;
  const willHadWrong = quizSession.hadWrong;
  const btn = $("#btnSubmitAnswer");
  btn.disabled = true;
  $("#quizStatus").textContent = "正在判分…";
  try {
    const result = await api(`/api/items/${itemId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_id: questionId,
        user_answer: answer,
        finish_session: isLast,
        session_had_wrong: willHadWrong,
      }),
    });
    if (!result.correct) quizSession.hadWrong = true;
    quizSession.results.push(result);
    lockQuizInputs();
    const fb = $("#answerFeedback");
    const { feedbackText, answerBlock, explainBlock, extendBlock } =
      renderGradeTeach(result);
    const savedNote = `<div class="hint" style="margin-top:0.4rem">答案已保存</div>`;
    const yourAnswer = `<div class="saved-answer"><strong>你的答案：</strong>${renderRichText(
      result.user_answer || answer,
      { forcePython: looksLikeCode(result.user_answer || answer) }
    )}</div>`;
    const wrongNote = result.added_to_wrong_book
      ? `<div class="hint">已加入错题本</div>`
      : "";
    const cls = result.correct ? "right" : "wrong";
    fb.innerHTML = `<div class="feedback ${cls}">${renderRichText(
      feedbackText
    )}${answerBlock}${explainBlock}${extendBlock}${savedNote}${wrongNote}${yourAnswer}</div>`;
    $("#quizStatus").textContent = "";

    if (!result.correct) {
      fb.innerHTML += `<div class="actions" style="margin-top:0.75rem">
        <button class="btn primary" id="btnOpenStudy">查看相关知识点</button>
        <button class="btn" id="btnNextQ">${isLast ? "完成本轮" : "下一题"}</button>
      </div>`;
      $("#btnOpenStudy").onclick = () => openStudy(itemId);
      $("#btnNextQ").onclick = () => advanceQuizSession(result);
    } else {
      fb.innerHTML += `<div class="actions" style="margin-top:0.75rem">
        <button class="btn primary" id="btnNextQ">${isLast ? "完成本轮" : "下一题"}</button>
      </div>`;
      $("#btnNextQ").onclick = () => advanceQuizSession(result);
    }
  } catch (e) {
    $("#quizStatus").textContent = e.message;
    $("#quizStatus").className = "status err";
    btn.disabled = false;
  }
}

function advanceQuizSession(lastResult) {
  if (!quizSession) {
    showView("home");
    loadDue();
    return;
  }
  if (quizSession.index < quizSession.questions.length - 1) {
    quizSession.index += 1;
    renderCurrentQuizQuestion();
    return;
  }
  // 整轮结束
  const total = quizSession.results.length;
  const wrongs = quizSession.results.filter((r) => !r.correct).length;
  const ok = total - wrongs;
  const sched = lastResult?.schedule_updated
    ? `知识点下次复习：${formatTime(lastResult.next_review_at)}（${lastResult.stage_label}）`
    : "知识点日程未变（练习轮或未到期）";
  $("#quizTitle").textContent = quizSession.title;
  $("#quizBox").innerHTML = `
    <div class="quiz-card">
      <h3>本轮完成</h3>
      <p>共 ${total} 题，正确 ${ok}，错误 ${wrongs}。</p>
      <p class="hint">${escapeHtml(sched)}</p>
      ${wrongs ? "<p class=\"hint\">错题已进入错题本单独复习；知识点已按艾宾浩斯进入下一档。</p>" : ""}
      <div class="actions" style="margin-top:0.75rem">
        <button class="btn primary" id="btnBackToday">返回今日复习</button>
        <button class="btn" id="btnGoWrong">查看错题本</button>
      </div>
    </div>`;
  quizSession = null;
  $("#btnBackToday").onclick = () => {
    showView("home");
    loadDue();
  };
  $("#btnGoWrong").onclick = () => {
    showView("wrong");
    loadWrongBook();
  };
}

async function submitWrongAnswer(wrongId, qtype = "short") {
  const answer = collectAnswer(qtype);
  if (!answer) {
    alert(qtype === "choice" || qtype === "judge" ? "请先选择一个选项" : "请先填写答案");
    return;
  }
  const btn = $("#btnSubmitAnswer");
  btn.disabled = true;
  $("#quizStatus").textContent = "正在判分…";
  try {
    const result = await api(`/api/wrong/${wrongId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_answer: answer }),
    });
    lockQuizInputs();
    const fb = $("#answerFeedback");
    const cls = result.correct ? "right" : "wrong";
    const { feedbackText, answerBlock, explainBlock, extendBlock } =
      renderGradeTeach(result);
    fb.innerHTML = `<div class="feedback ${cls}">${renderRichText(feedbackText)}
      ${answerBlock}${explainBlock}${extendBlock}
      <div class="hint">错题下次：${escapeHtml(formatTime(result.next_review_at))}（${escapeHtml(
      result.stage_label
    )}）</div>
      <div class="saved-answer"><strong>你的答案：</strong>${renderRichText(
        result.user_answer || answer,
        { forcePython: looksLikeCode(result.user_answer || answer) }
      )}</div>
    </div>
    <div class="actions" style="margin-top:0.75rem">
      ${
        result.show_material
          ? `<button class="btn primary" id="btnOpenStudy">查看相关知识点</button>`
          : ""
      }
      <button class="btn primary" id="btnBackToday">返回今日复习</button>
    </div>`;
    $("#quizStatus").textContent = "";
    if ($("#btnOpenStudy")) {
      $("#btnOpenStudy").onclick = () => openStudy(result.item_id);
    }
    $("#btnBackToday").onclick = () => {
      showView("home");
      loadDue();
    };
    wrongSession = null;
  } catch (e) {
    $("#quizStatus").textContent = e.message;
    $("#quizStatus").className = "status err";
    btn.disabled = false;
  }
}

async function openStudy(itemId) {
  currentStudyItemId = itemId;
  showView("study");
  $("#studyBox").innerHTML = "加载中…";
  try {
    const data = await api(`/api/items/${itemId}?for_study=true`);
    $("#studyBox").innerHTML = renderMaterial(data.item);
  } catch (e) {
    $("#studyBox").innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

function renderMaterial(item) {
  let html = `<h2>${escapeHtml(item.title)}</h2>`;
  html += `<p class="meta">${escapeHtml(item.stage_label)} · 下次 ${escapeHtml(formatTime(item.next_review_at))}</p>`;
  if (item.content) {
    html += `<h3>正文</h3><div>${escapeHtml(item.content).replace(/\n/g, "<br>")}</div>`;
  }
  if (item.code_snippet) {
    html += `<h3>代码</h3><pre>${escapeHtml(item.code_snippet)}</pre>`;
  }
  if (item.attachments?.length) {
    html += `<h3>附件</h3>`;
    html += renderAttachmentTree(item.attachments);
  }
  if (item.questions?.length) {
    html += `<h3>已生成题目（共 ${item.questions.length} 道）</h3>`;
    const byBatch = {};
    for (const q of item.questions) {
      const b = q.generation_batch || 1;
      if (!byBatch[b]) byBatch[b] = [];
      byBatch[b].push(q);
    }
    const batches = Object.keys(byBatch)
      .map(Number)
      .sort((a, b) => a - b);
    for (const b of batches) {
      html += `<h4 class="batch-title">第 ${b} 批（${byBatch[b].length} 道）</h4>`;
      html += `<ol class="question-answer-list">`;
      for (const q of byBatch[b]) {
        const t = q.qtype_label ? `【${q.qtype_label}】` : "";
        html += `<li class="qa-item">
        <div class="qa-stem">${t}${escapeHtml(q.stem)}</div>`;
        if (q.options?.length) {
          html += `<ul class="qa-options">${q.options
            .map((o) => `<li class="qa-opt-item">${renderOptionContent(o)}</li>`)
            .join("")}</ul>`;
        }
        if (q.reference_answer) {
          html += `<div class="qa-ref"><strong>参考答案：</strong>${renderRichText(
            q.reference_answer
          )}</div>`;
        }
        if (q.latest_answer) {
          const ok = q.latest_answer.is_correct ? "正确" : "错误";
          const cls = q.latest_answer.is_correct ? "ok" : "err";
          html += `<div class="qa-user">
          <strong>你的答案：</strong>${renderRichText(q.latest_answer.user_answer || "")}
          <span class="status ${cls}">（${ok} · ${escapeHtml(
            formatTime(q.latest_answer.reviewed_at)
          )}）</span>
          ${
            q.latest_answer.feedback
              ? `<div class="hint">${renderRichText(q.latest_answer.feedback)}</div>`
              : ""
          }
        </div>`;
        } else {
          html += `<div class="hint">尚未作答</div>`;
        }
        html += `</li>`;
      }
      html += `</ol>`;
    }
  }
  if (item.orphan_answers?.length) {
    html += `<h3>历史作答（题目已更新，未能挂回当前题）</h3>
      <p class="hint">这些答案已保存在数据库中；因重新出题替换了旧题，无法一一对应到上方题目。</p>
      <ol class="question-answer-list">`;
    for (const log of item.orphan_answers) {
      const ok = log.is_correct ? "正确" : "错误";
      const cls = log.is_correct ? "ok" : "err";
      const stem = log.stem || log.question_stem || "（原题干已丢失）";
      html += `<li class="qa-item">
        <div class="qa-stem">${escapeHtml(stem)}</div>
        <div class="qa-user">
          <strong>你的答案：</strong>${renderRichText(log.user_answer || "")}
          <span class="status ${cls}">（${ok} · ${escapeHtml(
            formatTime(log.reviewed_at)
          )}）</span>
          ${
            log.feedback
              ? `<div class="hint">${renderRichText(log.feedback)}</div>`
              : ""
          }
        </div>
      </li>`;
    }
    html += `</ol>`;
  }
  if (item.questions_error) {
    html += `<p class="status err">出题错误：${escapeHtml(item.questions_error)}</p>`;
  }
  return html;
}

function buildAttachmentTree(attachments) {
  const root = { name: "", children: {}, files: [] };
  for (const a of attachments) {
    const rel = (a.relative_path || a.original_name || "").replace(/\\/g, "/");
    const parts = rel.split("/").filter(Boolean);
    if (!parts.length) {
      root.files.push(a);
      continue;
    }
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const name = parts[i];
      if (!node.children[name]) {
        node.children[name] = { name, children: {}, files: [] };
      }
      node = node.children[name];
    }
    node.files.push(a);
  }
  return root;
}

function renderAttachmentTree(attachments) {
  const tree = buildAttachmentTree(attachments);
  return `<div class="file-tree">${renderTreeNode(tree, true)}</div>
    <div id="filePreview" class="file-preview hidden"></div>
    <div id="imgLightbox" class="lightbox hidden" onclick="closeLightbox(event)">
      <img id="lightboxImg" alt="" />
    </div>`;
}

function renderTreeNode(node, isRoot = false) {
  let html = `<ul class="tree-ul${isRoot ? " tree-root" : ""}">`;
  const folderNames = Object.keys(node.children).sort();
  for (const name of folderNames) {
    const child = node.children[name];
    html += `<li class="tree-folder">
      <details open>
        <summary><span class="tree-icon">📁</span>${escapeHtml(name)}</summary>
        ${renderTreeNode(child, false)}
      </details>
    </li>`;
  }
  for (const a of node.files) {
    const label = escapeHtml(a.original_name || a.relative_path || `文件${a.id}`);
    if (a.kind === "image") {
      html += `<li class="tree-file">
        <button type="button" class="tree-link" onclick="openImagePreview(${a.id})">
          <span class="tree-icon">🖼️</span>${label}
        </button>
        <div class="tree-thumb">
          <img src="/api/attachments/${a.id}" alt="${label}" onclick="openImagePreview(${a.id})" />
        </div>
      </li>`;
    } else {
      const fname = a.original_name || a.relative_path || "";
      html += `<li class="tree-file">
        <button type="button" class="tree-link" data-fname="${escapeHtml(fname)}" onclick="toggleFilePreview(${a.id}, this)">
          <span class="tree-icon">📄</span>${label}
        </button>
        <a class="tree-open" href="/api/attachments/${a.id}" target="_blank" rel="noopener">打开</a>
        <div class="file-inline-preview hidden" data-att="${a.id}"></div>
      </li>`;
    }
  }
  html += `</ul>`;
  return html;
}

async function toggleFilePreview(attId, btn, fileName = "") {
  const box = btn.parentElement.querySelector(`.file-inline-preview[data-att="${attId}"]`);
  if (!box) return;
  if (!box.classList.contains("hidden")) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = "加载中…";
  const nameHint = fileName || btn.getAttribute("data-fname") || "";
  try {
    const res = await fetch(`/api/attachments/${attId}`);
    if (!res.ok) throw new Error("无法打开文件");
    const ctype = (res.headers.get("content-type") || "").toLowerCase();
    if (ctype.startsWith("image/")) {
      box.innerHTML = `<img src="/api/attachments/${attId}" alt="" onclick="openImagePreview(${attId})" />`;
      return;
    }
    const buf = await res.arrayBuffer();
    const bytes = new Uint8Array(buf.slice(0, 8000));
    let textLike = true;
    for (let i = 0; i < bytes.length; i++) {
      if (bytes[i] === 0) {
        textLike = false;
        break;
      }
    }
    if (!textLike) {
      box.innerHTML = `<p class="hint">该文件为二进制，请点右侧「打开」下载/查看。</p>`;
      return;
    }
    const text = new TextDecoder("utf-8", { fatal: false }).decode(buf);
    const name = nameHint.toLowerCase();
    const isMd =
      name.endsWith(".md") ||
      name.endsWith(".markdown") ||
      ctype.includes("markdown");
    if (isMd && typeof marked !== "undefined") {
      const html = marked.parse(text.slice(0, 200000), { breaks: true });
      box.innerHTML = `<div class="md-preview">${html}</div>`;
      return;
    }
    box.innerHTML = `<pre class="preview-pre">${escapeHtml(text.slice(0, 20000))}${
      text.length > 20000 ? "\n…（已截断）" : ""
    }</pre>`;
  } catch (e) {
    box.innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

function openImagePreview(attId) {
  const lb = $("#imgLightbox");
  const img = $("#lightboxImg");
  if (!lb || !img) return;
  img.src = `/api/attachments/${attId}`;
  lb.classList.remove("hidden");
}

function closeLightbox(ev) {
  if (ev.target.id === "imgLightbox" || ev.target.id === "lightboxImg") {
    $("#imgLightbox").classList.add("hidden");
    $("#lightboxImg").src = "";
  }
}

async function showDetail(itemId) {
  showView("detail");
  $("#detailBox").innerHTML = "加载中…";
  try {
    const data = await api(`/api/items/${itemId}`);
    $("#detailTitle").textContent = data.item.title;
    $("#detailBox").innerHTML = renderMaterial(data.item);
  } catch (e) {
    $("#detailBox").innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

$("#btnStudyDone").addEventListener("click", () => {
  showView("home");
  loadDue();
});

$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const status = $("#addStatus");
  status.textContent = "保存中…";
  status.className = "status";

  const fd = new FormData();
  fd.append("title", form.title.value.trim());
  fd.append("content", form.content.value);
  fd.append("code_snippet", form.code_snippet.value);

  const fileInput = $("#fileInput");
  const folderInput = $("#folderInput");
  const files = [...(fileInput.files || []), ...(folderInput.files || [])];
  for (const f of files) {
    fd.append("files", f, f.name);
    const rel = f.webkitRelativePath || f.name;
    fd.append("relative_paths", rel);
  }

  try {
    const data = await api("/api/items", { method: "POST", body: fd });
    status.textContent = `已保存「${data.item.title}」，正在后台自动出题…`;
    status.className = "status ok";
    form.reset();
    setTimeout(() => showView("all"), 800);
  } catch (err) {
    status.textContent = err.message;
    status.className = "status err";
  }
});

async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("#settingsForm");
  form.llm_base_url.value = s.llm_base_url || "";
  form.llm_model.value = s.llm_model || "";
  form.desktop_notify.checked = !!s.desktop_notify;
  form.browser_notify.checked = !!s.browser_notify;
  form.web_search_enabled.checked = !!s.web_search_enabled;
  form.web_search_for_grade.checked = !!s.web_search_for_grade;
  form.notify_hour.value = s.notify_hour;
  form.notify_minute.value = s.notify_minute;
    $("#keyStatus").textContent = s.api_key_configured
      ? `API Key：已配置（${s.api_key_type === "cursor" ? "Cursor Agent" : "OpenAI 兼容"}…${s.api_key_suffix || ""}）`
      : "API Key：未配置（请填写或编辑 .env）";
    const eff = s.effective || s;
    $("#settingsStatus").textContent = `当前生效：${eff.llm_model || s.llm_model} @ ${eff.llm_base_url || s.llm_base_url}`;
    $("#settingsStatus").className = "status ok";
}

$("#settingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = {
    llm_base_url: form.llm_base_url.value.trim(),
    llm_model: form.llm_model.value.trim(),
    desktop_notify: form.desktop_notify.checked,
    browser_notify: form.browser_notify.checked,
    web_search_enabled: form.web_search_enabled.checked,
    web_search_for_grade: form.web_search_for_grade.checked,
    notify_hour: Number(form.notify_hour.value),
    notify_minute: Number(form.notify_minute.value),
  };
  if (form.llm_api_key.value.trim()) {
    body.llm_api_key = form.llm_api_key.value.trim();
  }
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    form.llm_api_key.value = "";
    await loadSettings();
    $("#settingsStatus").textContent =
      $("#settingsStatus").textContent || "已保存，新请求将使用上述配置";
    $("#settingsStatus").className = "status ok";
  } catch (err) {
    $("#settingsStatus").textContent = err.message;
    $("#settingsStatus").className = "status err";
  }
});

async function registerSW() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch (e) {
    console.warn("SW register failed", e);
  }
}

async function maybeBrowserNotify() {
  try {
    const s = await api("/api/settings");
    if (!s.browser_notify) return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;

    const now = new Date();
    if (
      now.getHours() < s.notify_hour ||
      (now.getHours() === s.notify_hour && now.getMinutes() < s.notify_minute)
    ) {
      return;
    }

    const due = await api("/api/items/due");
    const total = due.total_count ?? (due.count || 0) + (due.wrong_count || 0);
    if (!total) return;

    const day = now.toISOString().slice(0, 10);
    const fp =
      due.fingerprint ||
      `c:${total}|t:${(due.titles || []).join(",")}`;
    const key = `ebbinghaus-notify-${day}-${fp}`;
    if (localStorage.getItem(key)) return;

    const body = due.titles?.length
      ? `待复习：${due.titles.join("、")}${total > due.titles.length ? "…" : ""}`
      : `有 ${total} 项待复习`;

    const reg = await navigator.serviceWorker.getRegistration();
    if (reg?.showNotification) {
      await reg.showNotification("今日复习提醒", {
        body,
        tag: "ebbinghaus-due",
        renotify: true,
      });
    } else {
      new Notification("今日复习提醒", { body, tag: "ebbinghaus-due" });
    }
    localStorage.setItem(key, "1");
  } catch (e) {
    console.warn(e);
  }
}

$("#btnEnableNotify")?.addEventListener("click", async () => {
  if (!("Notification" in window)) {
    alert("当前浏览器不支持通知");
    return;
  }
  const perm = await Notification.requestPermission();
  alert(perm === "granted" ? "已授权浏览器通知" : `权限状态：${perm}`);
  if (perm === "granted") {
    registerSW();
    maybeBrowserNotify();
  }
});

$("#btnTestDesktopNotify")?.addEventListener("click", async () => {
  const status = $("#settingsStatus");
  try {
    if (status) {
      status.textContent = "正在测试桌面提醒…";
      status.className = "status";
    }
    const data = await api("/api/notify/test", { method: "POST" });
    if (status) {
      status.textContent = data.ok
        ? `已发送测试提醒（待复习 ${data.count || 0} 项）。请看屏幕右下角/通知中心；若系统通知被关，会弹出置顶小窗。`
        : `提醒未弹出：${data.skipped || "未知原因"}。请重启 python run.py 后再试，并检查 Windows「通知」是否开启。`;
      status.className = data.ok ? "status ok" : "status err";
    }
  } catch (e) {
    if (status) {
      status.textContent = e.message;
      status.className = "status err";
    }
  }
});

let practiceDetailKind = "exam";
let practiceShowAnswers = false;
let practiceDetailCache = null;
let practiceQuizSession = null;

async function loadLearnedPicker(pickerId) {
  const box = $(pickerId);
  if (!box) return;
  box.innerHTML = "加载知识点…";
  try {
    const data = await api("/api/learned-items");
    const items = data.items || [];
    if (!items.length) {
      box.innerHTML = `<p class="hint">暂无知识点，请先在「新增」录入。</p>`;
      return;
    }
    box.innerHTML = items
      .map(
        (it) => `
      <label class="pick-row">
        <input type="checkbox" name="item_id" value="${it.id}" />
        <span>
          <strong>${escapeHtml(it.title)}</strong>
          <span class="meta">
            ${it.learned ? "已复习" : "未复习"}
            · ${escapeHtml(it.stage_label || "")}
            · 题 ${it.question_count || 0}
          </span>
        </span>
      </label>`
      )
      .join("");
  } catch (e) {
    box.innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

function selectedItemIds(form) {
  return [...form.querySelectorAll('input[name="item_id"]:checked')].map((el) =>
    Number(el.value)
  );
}

function bindSelectAll(btnId, pickerId) {
  const btn = $(btnId);
  if (!btn) return;
  btn.onclick = () => {
    const box = $(pickerId);
    const boxes = [...(box?.querySelectorAll('input[name="item_id"]') || [])];
    const allOn = boxes.length && boxes.every((b) => b.checked);
    boxes.forEach((b) => {
      b.checked = !allOn;
    });
  };
}

async function loadExamView() {
  await loadLearnedPicker("#examItemPicker");
  bindSelectAll("#btnExamSelectAll", "#examItemPicker");
  await refreshExamList();
}

async function loadInterviewView() {
  await loadLearnedPicker("#interviewItemPicker");
  bindSelectAll("#btnInterviewSelectAll", "#interviewItemPicker");
  await refreshInterviewList();
}

async function refreshExamList() {
  const list = $("#examList");
  try {
    const data = await api("/api/exams");
    examCache = data.items || [];
    renderExamList();
  } catch (e) {
    list.innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

function renderExamList() {
  const list = $("#examList");
  const empty = $("#examEmpty");
  const date = $("#examDateFilter")?.value || "";
  syncMonthDaySelects(
    "#examMonthSelect",
    "#examDaySelect",
    "#examDateFilter",
    examCache
  );
  const items = filterByDay(examCache, date);
  if (!items.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    empty.textContent = date
      ? date === todayKey()
        ? "今天暂无试卷。"
        : "该日期暂无试卷。"
      : "暂无试卷。";
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = items.map((it) => renderPracticeCard(it, "exam")).join("");
  bindPracticeListActions(list, "exam");
}

async function refreshInterviewList() {
  const list = $("#interviewList");
  try {
    const data = await api("/api/interviews");
    interviewCache = data.items || [];
    renderInterviewList();
  } catch (e) {
    list.innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

function renderInterviewList() {
  const list = $("#interviewList");
  const empty = $("#interviewEmpty");
  const date = $("#interviewDateFilter")?.value || "";
  syncMonthDaySelects(
    "#interviewMonthSelect",
    "#interviewDaySelect",
    "#interviewDateFilter",
    interviewCache
  );
  const items = filterByDay(interviewCache, date);
  if (!items.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    empty.textContent = date
      ? date === todayKey()
        ? "今天暂无面试套题。"
        : "该日期暂无面试套题。"
      : "暂无面试套题。";
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = items.map((it) => renderPracticeCard(it, "interview")).join("");
  bindPracticeListActions(list, "interview");
}

function renderPracticeCard(it, kind) {
  const n = (it.questions || []).length || it.question_count || 0;
  const answered = it.answered_count || 0;
  const st =
    {
      ready: "已生成",
      generating: "生成中",
      failed: "失败",
    }[it.status] || it.status;
  const progress =
    n > 0 ? `已答 ${answered}/${n}` + (answered ? ` · 对 ${it.correct_count || 0}` : "") : "";
  return `
    <article class="card">
      <div>
        <h3>${escapeHtml(it.title)}</h3>
        <div class="meta">
          <span class="tag">${kind === "exam" ? "试卷" : "面试"}</span>
          <span>${escapeHtml(st)} · ${n} 题</span>
          ${it.expand ? " · 含拓展" : ""}
          ${progress ? ` · ${escapeHtml(progress)}` : ""}
          · ${escapeHtml(formatTime(it.created_at))}
        </div>
        ${it.error ? `<p class="status err">${escapeHtml(it.error)}</p>` : ""}
      </div>
      <div class="actions">
        <button class="btn primary" data-action="quiz" data-id="${it.id}">${
          answered ? "继续答题" : "开始答题"
        }</button>
        <button class="btn" data-action="open" data-id="${it.id}">详情</button>
        <button class="btn danger" data-action="delete" data-id="${it.id}">删除</button>
      </div>
    </article>`;
}

function bindPracticeListActions(root, kind) {
  root.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.dataset.id);
      const action = btn.dataset.action;
      try {
        if (action === "quiz") await startPracticeQuiz(kind, id);
        if (action === "open") await openPracticeDetail(kind, id);
        if (action === "delete") {
          if (!confirm("确认删除？")) return;
          await api(`/api/${kind === "exam" ? "exams" : "interviews"}/${id}`, {
            method: "DELETE",
          });
          if (kind === "exam") await refreshExamList();
          else await refreshInterviewList();
        }
      } catch (e) {
        alert(e.message);
      }
    });
  });
}

async function startPracticeQuiz(kind, id) {
  showView("quiz");
  quizSession = null;
  wrongSession = null;
  $("#quizStatus").textContent = "加载题目…";
  $("#quizBox").innerHTML = "";
  try {
    const path = kind === "exam" ? `/api/exams/${id}` : `/api/interviews/${id}`;
    const data = await api(path);
    const it = data.item;
    if (!it?.questions?.length) {
      throw new Error("暂无题目可答");
    }
    // 从第一道未作答开始；若都答过则从第 0 题复习
    let startIdx = 0;
    const answers = it.answers || {};
    for (let i = 0; i < it.questions.length; i++) {
      const a = answers[String(i)];
      if (!a || !(a.user_answer || "").trim()) {
        startIdx = i;
        break;
      }
      if (i === it.questions.length - 1) startIdx = 0;
    }
    practiceQuizSession = {
      kind,
      setId: it.id,
      title: it.title,
      questions: it.questions.slice(),
      answers: { ...answers },
      index: startIdx,
      results: [],
    };
    practiceDetailCache = it;
    practiceDetailKind = kind;
    renderPracticeQuizQuestion();
    $("#quizStatus").textContent = "";
  } catch (e) {
    $("#quizStatus").textContent = e.message;
    $("#quizStatus").className = "status err";
  }
}

function renderPracticeQuizQuestion() {
  if (!practiceQuizSession) return;
  const { questions, index, title, kind, answers } = practiceQuizSession;
  const q = { ...questions[index] };
  // 面试题用简答输入框
  if ((q.qtype || "") === "interview") q.qtype = "short";
  $("#quizTitle").textContent = `${title}（${index + 1}/${questions.length}）`;
  const prev = answers[String(index)];
  const hint =
    kind === "exam"
      ? "试卷练习：答完本题后进入下一题（不影响艾宾浩斯日程）。"
      : "面试练习：按要点作答，系统会语义判分并给出考察点。";
  $("#quizBox").innerHTML =
    `<p class="hint">${escapeHtml(hint)}（${index + 1}/${questions.length}）</p>` +
    (prev?.user_answer
      ? `<p class="hint">本题已作答过，可修改后重新提交。</p>`
      : "") +
    renderQuizCard(q);
  bindAnswerModeToggle();
  if (prev?.user_answer && $("#answerInput")?.tagName === "TEXTAREA") {
    // 回填时去掉代码围栏便于编辑
    let raw = prev.user_answer;
    const m = raw.match(/^```(?:python)?\s*\n?([\s\S]*?)```$/);
    if (m) raw = m[1];
    $("#answerInput").value = raw;
  }
  $("#btnSubmitAnswer").onclick = () => submitPracticeAnswer(q.qtype || "short");
}

async function submitPracticeAnswer(qtype = "short") {
  if (!practiceQuizSession) return;
  const answer = collectAnswer(qtype === "interview" ? "short" : qtype);
  if (!answer) {
    alert(qtype === "choice" || qtype === "judge" ? "请先选择一个选项" : "请先填写答案");
    return;
  }
  const { kind, setId, index, questions } = practiceQuizSession;
  const isLast = index >= questions.length - 1;
  const btn = $("#btnSubmitAnswer");
  btn.disabled = true;
  $("#quizStatus").textContent = "正在判分…";
  try {
    const path =
      kind === "exam"
        ? `/api/exams/${setId}/answer`
        : `/api/interviews/${setId}/answer`;
    const result = await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_index: index,
        user_answer: answer,
      }),
    });
    practiceQuizSession.answers[String(index)] = result;
    practiceQuizSession.results.push(result);
    lockQuizInputs();
    const fb = $("#answerFeedback");
    const { feedbackText, answerBlock, explainBlock, extendBlock } =
      renderGradeTeach(result);
    const cls = result.correct ? "right" : "wrong";
    fb.innerHTML = `<div class="feedback ${cls}">${renderRichText(feedbackText)}
      ${answerBlock}${explainBlock}${extendBlock}
      <div class="hint">进度：已答 ${result.answered_count}/${result.total_count}，正确 ${
      result.correct_count
    }</div>
      <div class="saved-answer"><strong>你的答案：</strong>${renderRichText(answer, {
        forcePython: looksLikeCode(answer),
      })}</div>
    </div>
    <div class="actions" style="margin-top:0.75rem">
      <button class="btn primary" id="btnPracticeNext">${
        isLast ? "完成本卷" : "下一题"
      }</button>
      <button class="btn" id="btnPracticeQuit">返回列表</button>
    </div>`;
    $("#quizStatus").textContent = "";
    $("#btnPracticeNext").onclick = () => advancePracticeQuiz();
    $("#btnPracticeQuit").onclick = () => {
      practiceQuizSession = null;
      showView(kind === "exam" ? "exam" : "interview");
    };
  } catch (e) {
    $("#quizStatus").textContent = e.message;
    $("#quizStatus").className = "status err";
    btn.disabled = false;
  }
}

function advancePracticeQuiz() {
  if (!practiceQuizSession) {
    showView("exam");
    return;
  }
  const { questions, index, kind, title, answers, results } = practiceQuizSession;
  if (index < questions.length - 1) {
    practiceQuizSession.index += 1;
    renderPracticeQuizQuestion();
    return;
  }
  const total = questions.length;
  const answered = Object.keys(answers).length;
  const ok = results.filter((r) => r.correct).length;
  // 用持久化统计更准
  let correctAll = 0;
  Object.values(answers).forEach((a) => {
    if (a && a.correct) correctAll += 1;
  });
  $("#quizTitle").textContent = title;
  $("#quizBox").innerHTML = `
    <div class="quiz-card">
      <h3>本卷完成</h3>
      <p>共 ${total} 题，本轮提交 ${results.length} 题，累计正确 ${correctAll}。</p>
      <p class="hint">试卷/面试练习不影响知识点艾宾浩斯日程。</p>
      <div class="actions" style="margin-top:0.75rem">
        <button class="btn primary" id="btnPracticeAgain">再练一遍（清空后重答）</button>
        <button class="btn" id="btnPracticeToList">返回列表</button>
        <button class="btn" id="btnPracticeToDetail">查看详情</button>
      </div>
    </div>`;
  const setId = practiceQuizSession.setId;
  const k = kind;
  practiceQuizSession = null;
  $("#btnPracticeToList").onclick = () => showView(k === "exam" ? "exam" : "interview");
  $("#btnPracticeToDetail").onclick = () => openPracticeDetail(k, setId);
  $("#btnPracticeAgain").onclick = async () => {
    try {
      await api(`/api/${k === "exam" ? "exams" : "interviews"}/${setId}/reset-answers`, {
        method: "POST",
      });
      await startPracticeQuiz(k, setId);
    } catch (e) {
      alert(e.message);
    }
  };
}

async function openPracticeDetail(kind, id) {
  showView("practice-detail");
  practiceDetailKind = kind;
  practiceShowAnswers = false;
  bindPracticeDetailToolbar();
  syncPracticeAnswerToggleBtn();
  $("#practiceDetailTitle").textContent = "加载中…";
  $("#practiceDetailBox").innerHTML = "加载中…";
  $("#practiceDetailMeta").textContent = "";
  try {
    const path = kind === "exam" ? `/api/exams/${id}` : `/api/interviews/${id}`;
    const data = await api(path);
    practiceDetailCache = data.item;
    renderPracticeDetail();
  } catch (e) {
    $("#practiceDetailBox").innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
}

function renderPracticeDetail() {
  const it = practiceDetailCache;
  if (!it) return;
  $("#practiceDetailTitle").textContent = it.title;
  const answered = it.answered_count || 0;
  const total = it.questions?.length || 0;
  $("#practiceDetailMeta").textContent = `${
    it.kind === "exam" ? "试卷" : "面试套题"
  } · ${total} 题 · 已答 ${answered}${
    answered ? ` · 正确 ${it.correct_count || 0}` : ""
  }${it.expand ? " · 含拓展" : ""} · ${formatTime(it.created_at)}`;
  const startBtn = $("#btnStartPracticeQuiz");
  if (startBtn) {
    startBtn.textContent = answered ? "继续答题" : "开始答题";
    startBtn.onclick = () => startPracticeQuiz(practiceDetailKind, it.id);
  }
  const qs = it.questions || [];
  const answers = it.answers || {};
  if (!qs.length) {
    $("#practiceDetailBox").innerHTML = `<p class="hint">暂无题目${
      it.error ? "：" + escapeHtml(it.error) : ""
    }</p>`;
    return;
  }
  let html = `<ol class="question-answer-list practice-q-list">`;
  qs.forEach((q, idx) => {
    const t = q.qtype === "interview" ? "面试题" : q.qtype_label || q.qtype || "题目";
    const diff = q.difficulty ? `<span class="tag">${escapeHtml(q.difficulty)}</span> ` : "";
    const ans = answers[String(idx)];
    html += `<li class="qa-item">
      <div class="qa-stem">${diff}<strong>${idx + 1}. 【${escapeHtml(t)}】</strong>${escapeHtml(
      q.stem || ""
    )}</div>`;
    if (q.options?.length) {
      html += `<div class="qa-options">${q.options
        .map((o) => `<div class="qa-opt-item">${renderOptionContent(o)}</div>`)
        .join("")}</div>`;
    }
    if (q.source_hint) {
      html += `<div class="hint">来源：${escapeHtml(q.source_hint)}</div>`;
    }
    if (ans?.user_answer) {
      const ok = ans.correct ? "正确" : "错误";
      const cls = ans.correct ? "ok" : "err";
      html += `<div class="qa-user"><strong>你的答案：</strong>${renderRichText(
        ans.user_answer
      )} <span class="status ${cls}">（${ok}）</span></div>`;
    } else {
      html += `<div class="hint">尚未作答</div>`;
    }
    // 参考答案/解释/拓展仅由「显示参考答案」开关控制
    if (practiceShowAnswers) {
      const ref = ans?.reference_answer || q.reference_answer;
      if (ref) {
        html += `<div class="correct-answer"><strong>参考答案：</strong>${renderRichText(
          ref
        )}</div>`;
      }
      const explanation = ans?.explanation || q.explanation;
      if (explanation) {
        html += `<div class="teach-block explain-block"><strong>解释/考察点：</strong>${renderRichText(
          explanation
        )}</div>`;
      }
      if (q.followups?.length) {
        html += `<div class="teach-block"><strong>追问：</strong><ul>${q.followups
          .map((f) => `<li>${escapeHtml(f)}</li>`)
          .join("")}</ul></div>`;
      }
      const extension = ans?.extension || q.extension;
      if (extension) {
        html += `<div class="teach-block extend-block"><strong>知识拓展：</strong>${renderRichText(
          extension
        )}</div>`;
      }
      if (!ref && !explanation && !extension && !(q.followups && q.followups.length)) {
        html += `<div class="hint">本题暂无参考答案/解释。</div>`;
      }
    }
    html += `</li>`;
  });
  html += `</ol>`;
  $("#practiceDetailBox").innerHTML = html;
  syncPracticeAnswerToggleBtn();
}

function syncPracticeAnswerToggleBtn() {
  const btn = $("#btnToggleAnswers");
  if (!btn) return;
  btn.textContent = practiceShowAnswers ? "隐藏参考答案" : "显示参考答案";
  btn.classList.toggle("primary", practiceShowAnswers);
}

function bindPracticeDetailToolbar() {
  const toggle = $("#btnToggleAnswers");
  if (toggle) {
    toggle.onclick = () => {
      practiceShowAnswers = !practiceShowAnswers;
      syncPracticeAnswerToggleBtn();
      renderPracticeDetail();
    };
  }
  const resetBtn = $("#btnResetPracticeAnswers");
  if (resetBtn) {
    resetBtn.onclick = async () => {
      const it = practiceDetailCache;
      if (!it) return;
      if (!confirm("确认清空本卷全部作答记录？")) return;
      try {
        const kind = practiceDetailKind;
        const data = await api(
          `/api/${kind === "exam" ? "exams" : "interviews"}/${it.id}/reset-answers`,
          { method: "POST" }
        );
        practiceDetailCache = data.item;
        renderPracticeDetail();
      } catch (e) {
        alert(e.message);
      }
    };
  }
  const back = $("#btnPracticeBack");
  if (back) {
    back.onclick = () => {
      showView(practiceDetailKind === "exam" ? "exam" : "interview");
    };
  }
}

$("#examForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const ids = selectedItemIds(form);
  const status = $("#examStatus");
  const btn = $("#btnExamGenerate");
  if (!ids.length) {
    alert("请至少选择一个知识点");
    return;
  }
  btn.disabled = true;
  status.textContent = "正在生成试卷，请稍候…";
  status.className = "status";
  try {
    const data = await api("/api/exams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_ids: ids,
        expand: !!form.expand.checked,
        question_count: Number(form.question_count.value) || 8,
        title: form.title.value.trim(),
      }),
    });
    status.textContent = `已生成「${data.item.title}」共 ${data.item.questions?.length || 0} 题`;
    status.className = "status ok";
    await refreshExamList();
    await openPracticeDetail("exam", data.item.id);
  } catch (err) {
    status.textContent = err.message;
    status.className = "status err";
  } finally {
    btn.disabled = false;
  }
});

$("#interviewForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const ids = selectedItemIds(form);
  const status = $("#interviewStatus");
  const btn = $("#btnInterviewGenerate");
  if (!ids.length) {
    alert("请至少选择一个知识点");
    return;
  }
  btn.disabled = true;
  status.textContent = "正在生成面试题，请稍候…";
  status.className = "status";
  try {
    const data = await api("/api/interviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_ids: ids,
        expand: !!form.expand.checked,
        question_count: Number(form.question_count.value) || 6,
        title: form.title.value.trim(),
      }),
    });
    status.textContent = `已生成「${data.item.title}」共 ${data.item.questions?.length || 0} 题`;
    status.className = "status ok";
    await refreshInterviewList();
    await openPracticeDetail("interview", data.item.id);
  } catch (err) {
    status.textContent = err.message;
    status.className = "status err";
  } finally {
    btn.disabled = false;
  }
});

function startNotifyPolling() {
  if (notifyTimer) clearInterval(notifyTimer);
  maybeBrowserNotify();
  notifyTimer = setInterval(async () => {
    try {
      const due = await api("/api/items/due");
      updateBadge(due.total_count ?? (due.count || 0) + (due.wrong_count || 0));
      await maybeBrowserNotify();
    } catch {
      /* ignore */
    }
  }, 60 * 1000);
}

$$(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

document.addEventListener("DOMContentLoaded", async () => {
  bindMonthDayFilter(
    "#wrongMonthSelect",
    "#wrongDaySelect",
    "#wrongDateFilter",
    "#wrongDateClear",
    renderWrongList
  );
  bindMonthDayFilter(
    "#allMonthSelect",
    "#allDaySelect",
    "#allDateFilter",
    "#allDateClear",
    renderAllList
  );
  bindMonthDayFilter(
    "#examMonthSelect",
    "#examDaySelect",
    "#examDateFilter",
    "#examDateClear",
    renderExamList
  );
  bindMonthDayFilter(
    "#interviewMonthSelect",
    "#interviewDaySelect",
    "#interviewDateFilter",
    "#interviewDateClear",
    renderInterviewList
  );
  bindPracticeDetailToolbar();
  showView("home");
  registerSW();
  startNotifyPolling();
  try {
    await loadDue();
  } catch (e) {
    $("#dueList").innerHTML = `<p class="status err">${escapeHtml(e.message)}</p>`;
  }
});
