/**
 * Widget de chat BCM — autonome, sans dépendance, isolé du site hôte via Shadow DOM.
 *
 * Intégration minimale :
 *   <script src="https://.../bcm-chat-widget.js" data-api-url="https://rag.bcm.mr"></script>
 *
 * Options (attributs data-* sur la balise <script>, ou window.BCM_CHAT_CONFIG) :
 *   data-api-url       (obligatoire) URL de base de l'API Flask, sans slash final.
 *   data-language       "fr" (défaut) ou "ar".
 *   data-position        "bottom-right" (défaut) ou "bottom-left".
 *   data-accent-color    couleur d'accent CSS, défaut "#0f766e".
 *   data-title-fr / data-title-ar   titre affiché dans l'en-tête du panneau.
 *
 * L'API doit autoriser l'origine du site hôte via CORS_ALLOWED_ORIGINS (voir .env.production).
 */
(function () {
  "use strict";

  var CURRENT_SCRIPT =
    document.currentScript ||
    (function () {
      var scripts = document.getElementsByTagName("script");
      return scripts[scripts.length - 1];
    })();

  function readConfig() {
    var dataset = (CURRENT_SCRIPT && CURRENT_SCRIPT.dataset) || {};
    var globalConfig = window.BCM_CHAT_CONFIG || {};
    return {
      apiUrl: (globalConfig.apiUrl || dataset.apiUrl || "").replace(/\/+$/, ""),
      language: globalConfig.language || dataset.language || "fr",
      position: globalConfig.position || dataset.position || "bottom-right",
      accentColor: globalConfig.accentColor || dataset.accentColor || "#0f766e",
      titleFr: globalConfig.titleFr || dataset.titleFr || "Assistant BCM",
      titleAr: globalConfig.titleAr || dataset.titleAr || "مساعد البنك المركزي",
      historyLimit: 16,
      requestTimeoutMs: 190000,
    };
  }

  var CONFIG = readConfig();

  if (!CONFIG.apiUrl) {
    console.error(
      "[bcm-chat-widget] data-api-url manquant : le widget ne peut pas contacter l'API."
    );
    return;
  }

  var TEXTS = {
    fr: {
      title: CONFIG.titleFr,
      scope:
        "Cette application n’utilise qu’un seul document. Si une information n’y figure pas, elle doit le signaler au lieu de compléter avec des connaissances externes.",
      placeholder: "Posez une question sur l’économie, la monnaie, les banques, les paiements ou les comptes de la BCM.",
      send: "Envoyer",
      newConversation: "Nouvelle conversation",
      sourcesTitle: "Sources",
      page: "Page",
      statusOk: "Assistant documentaire prêt",
      statusDown: "Service indisponible pour le moment",
      statusChecking: "Connexion en cours…",
      errorGeneric: "Une erreur est survenue. Réessayez dans un instant.",
      errorRate: "Trop de questions en peu de temps. Merci de patienter avant de réessayer.",
      launcherLabel: "Ouvrir l’assistant BCM",
      closeLabel: "Fermer",
      examples: [
        "Quel a été le taux de croissance du PIB réel en 2025 ?",
        "Comment l’inflation a-t-elle évolué en 2025 ?",
        "Quelles réformes des systèmes de paiement sont présentées ?",
      ],
    },
    ar: {
      title: CONFIG.titleAr,
      scope:
        "يستخدم هذا المساعد وثيقة واحدة فقط. إذا لم ترد المعلومة في التقرير، فيجب أن يصرّح بعدم وجودها بدلاً من الاستعانة بمعلومات خارجية.",
      placeholder: "اكتب سؤالك عن الاقتصاد أو النقد أو البنوك أو المدفوعات أو حسابات البنك المركزي.",
      send: "إرسال",
      newConversation: "محادثة جديدة",
      sourcesTitle: "المصادر",
      page: "صفحة",
      statusOk: "المساعد الوثائقي جاهز",
      statusDown: "الخدمة غير متاحة حالياً",
      statusChecking: "جارٍ الاتصال…",
      errorGeneric: "حدث خطأ. يرجى إعادة المحاولة بعد قليل.",
      errorRate: "عدد كبير من الأسئلة خلال وقت قصير. يرجى الانتظار قبل إعادة المحاولة.",
      launcherLabel: "افتح مساعد البنك المركزي",
      closeLabel: "إغلاق",
      examples: [
        "ما معدل نمو الناتج المحلي الإجمالي الحقيقي في سنة 2025؟",
        "كيف تطور التضخم خلال سنة 2025؟",
        "ما الإصلاحات المتعلقة بأنظمة الدفع؟",
      ],
    },
  };

  var STORAGE_KEY = "bcm_chat_widget_history_v1";

  var state = {
    open: false,
    everOpened: false,
    language: TEXTS[CONFIG.language] ? CONFIG.language : "fr",
    history: loadHistory(),
    suggestions: [],
    busy: false,
    apiOnline: null,
  };

  function loadHistory() {
    try {
      var raw = window.sessionStorage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      return [];
    }
  }

  function saveHistory() {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state.history));
    } catch (err) {
      /* stockage indisponible (navigation privée, quota) : la conversation reste en mémoire */
    }
  }

  // ---- Rendu markdown minimal et sûr (gras, listes, citations [p. PDF N]) ----

  var HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (ch) {
      return HTML_ESCAPES[ch];
    });
  }

  function renderInline(escapedText) {
    var withBold = escapedText.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return withBold.replace(/\[p\.\s*PDF\s*(\d+)\]/g, '<span class="bcm-cite">p.&nbsp;$1</span>');
  }

  function renderMarkdown(rawText) {
    var lines = escapeHtml(rawText).split(/\n/);
    var html = "";
    var inList = false;
    lines.forEach(function (line) {
      var trimmed = line.trim();
      var isListItem = /^[-•]\s+/.test(trimmed);
      if (isListItem) {
        if (!inList) {
          html += "<ul>";
          inList = true;
        }
        html += "<li>" + renderInline(trimmed.replace(/^[-•]\s+/, "")) + "</li>";
        return;
      }
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      if (trimmed !== "") {
        html += "<p>" + renderInline(trimmed) + "</p>";
      }
    });
    if (inList) html += "</ul>";
    return html;
  }

  // ---- Construction du DOM, isolée du site hôte via Shadow DOM ----

  var host = document.createElement("div");
  host.id = "bcm-chat-widget-host";
  document.body.appendChild(host);
  var shadow = host.attachShadow({ mode: "open" });

  var accentDark = "color-mix(in srgb, " + CONFIG.accentColor + " 75%, black)";

  var style = document.createElement("style");
  style.textContent =
    ":host{all:initial;}" +
    "*{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;}" +
    ".bcm-launcher{position:fixed;bottom:22px;" +
    (CONFIG.position === "bottom-left" ? "left:22px;" : "right:22px;") +
    "width:58px;height:58px;border-radius:50%;background:linear-gradient(135deg," +
    CONFIG.accentColor +
    "," +
    accentDark +
    ");color:#fff;border:none;box-shadow:0 10px 28px rgba(0,0,0,.28);cursor:pointer;z-index:2147483000;display:flex;align-items:center;justify-content:center;transition:transform .18s ease,box-shadow .18s ease;}" +
    ".bcm-launcher:hover{transform:scale(1.07);box-shadow:0 14px 32px rgba(0,0,0,.32);}" +
    ".bcm-launcher svg{width:26px;height:26px;fill:#fff;}" +
    ".bcm-launcher.pulse::after{content:'';position:absolute;inset:-6px;border-radius:50%;border:2px solid " +
    CONFIG.accentColor +
    ";animation:bcm-pulse 2.2s ease-out infinite;}" +
    "@keyframes bcm-pulse{0%{opacity:.6;transform:scale(.9);}100%{opacity:0;transform:scale(1.35);}}" +
    ".bcm-panel{position:fixed;bottom:92px;" +
    (CONFIG.position === "bottom-left" ? "left:22px;" : "right:22px;") +
    "width:392px;max-width:calc(100vw - 32px);height:min(620px,calc(100vh - 140px));background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(15,23,42,.22);display:flex;flex-direction:column;overflow:hidden;z-index:2147483000;opacity:0;pointer-events:none;transform:translateY(16px) scale(.98);transition:opacity .18s ease,transform .18s ease;}" +
    ".bcm-panel.open{opacity:1;pointer-events:auto;transform:translateY(0) scale(1);}" +
    ".bcm-header{background:linear-gradient(135deg," +
    CONFIG.accentColor +
    "," +
    accentDark +
    ");color:#fff;padding:16px;display:flex;align-items:center;gap:10px;}" +
    ".bcm-header-icon{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.18);display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
    ".bcm-header-icon svg{width:18px;height:18px;fill:#fff;}" +
    ".bcm-header-text{flex:1;min-width:0;}" +
    ".bcm-header-text h1{font-size:15px;font-weight:700;margin:0;line-height:1.25;}" +
    ".bcm-header-status{font-size:11px;opacity:.92;display:flex;align-items:center;gap:5px;margin-top:2px;}" +
    ".bcm-dot{width:6px;height:6px;border-radius:50%;background:#d1d5db;flex-shrink:0;}" +
    ".bcm-dot.ok{background:#4ade80;}" +
    ".bcm-dot.down{background:#f87171;}" +
    ".bcm-header button{background:rgba(255,255,255,.16);border:none;color:#fff;border-radius:9px;padding:7px 9px;cursor:pointer;font-size:11.5px;font-weight:600;flex-shrink:0;}" +
    ".bcm-header button:hover{background:rgba(255,255,255,.3);}" +
    ".bcm-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px;background:#f7f8fa;scroll-behavior:smooth;}" +
    ".bcm-scope{font-size:11.5px;color:#57606f;background:#fff;border-radius:12px;padding:10px 12px;line-height:1.45;border:1px solid #ececf0;}" +
    ".bcm-examples{display:flex;flex-direction:column;gap:7px;}" +
    ".bcm-example{text-align:start;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:10px 12px;font-size:12.5px;cursor:pointer;color:#1f2937;transition:border-color .15s ease,transform .15s ease;}" +
    ".bcm-example:hover{border-color:" +
    CONFIG.accentColor +
    ";transform:translateY(-1px);}" +
    ".bcm-row{display:flex;gap:8px;align-items:flex-end;}" +
    ".bcm-row.user{flex-direction:row-reverse;}" +
    ".bcm-avatar{width:26px;height:26px;border-radius:50%;background:" +
    CONFIG.accentColor +
    ";display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
    ".bcm-avatar svg{width:14px;height:14px;fill:#fff;}" +
    ".bcm-msg{max-width:80%;padding:10px 13px;border-radius:16px;font-size:13.5px;line-height:1.55;word-wrap:break-word;}" +
    ".bcm-msg p{margin:0 0 6px;}" +
    ".bcm-msg p:last-child{margin-bottom:0;}" +
    ".bcm-msg ul{margin:2px 0 8px;padding-inline-start:18px;}" +
    ".bcm-msg li{margin-bottom:3px;}" +
    ".bcm-msg strong{font-weight:700;}" +
    ".bcm-cite{display:inline-block;background:rgba(15,118,110,.1);color:" +
    CONFIG.accentColor +
    ";border-radius:6px;padding:0 5px;font-size:11.5px;font-weight:600;white-space:nowrap;}" +
    ".bcm-msg.user{background:linear-gradient(135deg," +
    CONFIG.accentColor +
    "," +
    accentDark +
    ");color:#fff;border-bottom-right-radius:5px;}" +
    ".bcm-msg.user .bcm-cite{background:rgba(255,255,255,.22);color:#fff;}" +
    ".bcm-msg.assistant{background:#fff;border:1px solid #ececf0;color:#111827;border-bottom-left-radius:5px;box-shadow:0 1px 2px rgba(15,23,42,.04);}" +
    "[dir='rtl'] .bcm-msg.user{border-bottom-right-radius:16px;border-bottom-left-radius:5px;}" +
    "[dir='rtl'] .bcm-msg.assistant{border-bottom-left-radius:16px;border-bottom-right-radius:5px;}" +
    ".bcm-sources{margin-top:9px;padding-top:9px;border-top:1px solid #ececf0;}" +
    ".bcm-sources summary{list-style:none;cursor:pointer;font-size:11.5px;font-weight:600;color:" +
    CONFIG.accentColor +
    ";display:flex;align-items:center;gap:4px;}" +
    ".bcm-sources summary::-webkit-details-marker{display:none;}" +
    ".bcm-sources summary::before{content:'▸';font-size:10px;transition:transform .15s ease;}" +
    ".bcm-sources[open] summary::before{transform:rotate(90deg);}" +
    ".bcm-sources-list{margin-top:8px;display:flex;flex-direction:column;gap:8px;}" +
    ".bcm-source-item{background:#f7f8fa;border-radius:10px;padding:8px 10px;}" +
    ".bcm-source-page{font-size:11px;font-weight:700;color:#374151;margin-bottom:3px;}" +
    ".bcm-source-excerpt{font-size:11.5px;color:#6b7280;line-height:1.45;}" +
    ".bcm-suggestions{display:flex;flex-wrap:wrap;gap:7px;padding:0 16px 12px;}" +
    ".bcm-chip{background:#fff;border:1.5px solid " +
    CONFIG.accentColor +
    ";color:" +
    CONFIG.accentColor +
    ";border-radius:999px;padding:7px 13px;font-size:12px;font-weight:600;cursor:pointer;transition:background .15s ease,color .15s ease;}" +
    ".bcm-chip:hover{background:" +
    CONFIG.accentColor +
    ";color:#fff;}" +
    ".bcm-inputrow{display:flex;gap:9px;padding:13px;border-top:1px solid #ececf0;background:#fff;}" +
    ".bcm-input{flex:1;resize:none;border:1.5px solid #e5e7eb;border-radius:14px;padding:10px 13px;font-size:13.5px;max-height:90px;min-height:40px;font-family:inherit;transition:border-color .15s ease;}" +
    ".bcm-input:focus{outline:none;border-color:" +
    CONFIG.accentColor +
    ";}" +
    ".bcm-send{background:linear-gradient(135deg," +
    CONFIG.accentColor +
    "," +
    accentDark +
    ");color:#fff;border:none;border-radius:14px;padding:0 18px;font-size:13px;cursor:pointer;font-weight:700;flex-shrink:0;transition:opacity .15s ease;}" +
    ".bcm-send:disabled{opacity:.45;cursor:default;}" +
    ".bcm-footer{padding:7px 16px 12px;text-align:center;}" +
    ".bcm-footer button{background:none;border:none;color:#9ca3af;font-size:11.5px;cursor:pointer;font-weight:600;}" +
    ".bcm-footer button:hover{color:" +
    CONFIG.accentColor +
    ";}" +
    ".bcm-typing{align-self:flex-start;display:flex;gap:4px;padding:12px 14px;background:#fff;border:1px solid #ececf0;border-radius:16px;border-bottom-left-radius:5px;}" +
    ".bcm-typing span{width:6px;height:6px;border-radius:50%;background:#9ca3af;animation:bcm-bounce 1.2s infinite ease-in-out;}" +
    ".bcm-typing span:nth-child(2){animation-delay:.15s;}" +
    ".bcm-typing span:nth-child(3){animation-delay:.3s;}" +
    "@keyframes bcm-bounce{0%,80%,100%{transform:translateY(0);opacity:.5;}40%{transform:translateY(-4px);opacity:1;}}" +
    "@media (max-width:480px){.bcm-panel{right:0;left:0;bottom:0;width:100%;max-width:100%;height:100%;border-radius:0;}}";
  shadow.appendChild(style);

  var BOT_ICON =
    '<svg viewBox="0 0 24 24"><path d="M12 2a2 2 0 0 1 2 2c0 .55-.22 1.05-.59 1.41A2 2 0 0 1 14 7v1h2a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3h2V7a2 2 0 0 1 1-1.73A2 2 0 0 1 10 4a2 2 0 0 1 2-2zM9 12a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg>';

  var launcher = document.createElement("button");
  launcher.className = "bcm-launcher pulse";
  launcher.setAttribute("aria-label", TEXTS[state.language].launcherLabel);
  launcher.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z"/></svg>';
  shadow.appendChild(launcher);

  var panel = document.createElement("div");
  panel.className = "bcm-panel";
  panel.innerHTML =
    '<div class="bcm-header">' +
    '<div class="bcm-header-icon">' +
    BOT_ICON +
    "</div>" +
    '<div class="bcm-header-text">' +
    '<h1 id="bcm-title"></h1>' +
    '<div class="bcm-header-status"><span class="bcm-dot" id="bcm-dot"></span><span id="bcm-status-text"></span></div>' +
    "</div>" +
    '<button type="button" id="bcm-lang-toggle"></button>' +
    '<button type="button" id="bcm-close" aria-label="close">✕</button>' +
    "</div>" +
    '<div class="bcm-messages" id="bcm-messages"></div>' +
    '<div class="bcm-suggestions" id="bcm-suggestions"></div>' +
    '<div class="bcm-inputrow">' +
    '<textarea class="bcm-input" id="bcm-input" rows="1"></textarea>' +
    '<button type="button" class="bcm-send" id="bcm-send"></button>' +
    "</div>" +
    '<div class="bcm-footer"><button type="button" id="bcm-new"></button></div>';
  shadow.appendChild(panel);

  var els = {
    title: shadow.getElementById("bcm-title"),
    langToggle: shadow.getElementById("bcm-lang-toggle"),
    close: shadow.getElementById("bcm-close"),
    dot: shadow.getElementById("bcm-dot"),
    statusText: shadow.getElementById("bcm-status-text"),
    messages: shadow.getElementById("bcm-messages"),
    suggestions: shadow.getElementById("bcm-suggestions"),
    input: shadow.getElementById("bcm-input"),
    send: shadow.getElementById("bcm-send"),
    newConversation: shadow.getElementById("bcm-new"),
  };

  function t() {
    return TEXTS[state.language];
  }

  function applyDirection() {
    panel.setAttribute("dir", state.language === "ar" ? "rtl" : "ltr");
  }

  function renderStatic() {
    var texts = t();
    els.title.textContent = texts.title;
    els.langToggle.textContent = state.language === "ar" ? "FR" : "AR";
    els.langToggle.setAttribute(
      "aria-label",
      state.language === "ar" ? "Français" : "العربية"
    );
    els.input.placeholder = texts.placeholder;
    els.send.textContent = texts.send;
    els.newConversation.textContent = texts.newConversation;
    applyDirection();
    renderStatus();
  }

  function renderStatus() {
    var texts = t();
    els.dot.className =
      "bcm-dot" +
      (state.apiOnline === true ? " ok" : state.apiOnline === false ? " down" : "");
    els.statusText.textContent =
      state.apiOnline === true
        ? texts.statusOk
        : state.apiOnline === false
        ? texts.statusDown
        : texts.statusChecking;
  }

  function renderMessages() {
    els.messages.innerHTML = "";
    if (state.history.length === 0) {
      var scope = document.createElement("div");
      scope.className = "bcm-scope";
      scope.textContent = t().scope;
      els.messages.appendChild(scope);

      var examplesWrap = document.createElement("div");
      examplesWrap.className = "bcm-examples";
      t().examples.forEach(function (example) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "bcm-example";
        btn.textContent = example;
        btn.addEventListener("click", function () {
          sendMessage(example);
        });
        examplesWrap.appendChild(btn);
      });
      els.messages.appendChild(examplesWrap);
    }
    state.history.forEach(function (turn) {
      els.messages.appendChild(renderMessageRow(turn));
    });
    if (state.busy) {
      var row = document.createElement("div");
      row.className = "bcm-row";
      row.appendChild(makeAvatar());
      var typing = document.createElement("div");
      typing.className = "bcm-typing";
      typing.innerHTML = "<span></span><span></span><span></span>";
      row.appendChild(typing);
      els.messages.appendChild(row);
    }
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function makeAvatar() {
    var avatar = document.createElement("div");
    avatar.className = "bcm-avatar";
    avatar.innerHTML = BOT_ICON;
    return avatar;
  }

  function renderMessageRow(turn) {
    var isUser = turn.role === "user";
    var row = document.createElement("div");
    row.className = "bcm-row" + (isUser ? " user" : "");
    if (!isUser) row.appendChild(makeAvatar());

    var bubble = document.createElement("div");
    bubble.className = "bcm-msg " + (isUser ? "user" : "assistant");
    if (isUser) {
      bubble.textContent = turn.content;
    } else {
      bubble.innerHTML = renderMarkdown(turn.content);
      if (turn.sources && turn.sources.length) {
        bubble.appendChild(renderSources(turn.sources));
      }
    }
    row.appendChild(bubble);
    return row;
  }

  function renderSources(sources) {
    var details = document.createElement("details");
    details.className = "bcm-sources";
    var summary = document.createElement("summary");
    summary.textContent = t().sourcesTitle + " (" + sources.length + ")";
    details.appendChild(summary);
    var list = document.createElement("div");
    list.className = "bcm-sources-list";
    sources.forEach(function (source) {
      var item = document.createElement("div");
      item.className = "bcm-source-item";
      var pageLine = document.createElement("div");
      pageLine.className = "bcm-source-page";
      pageLine.textContent = t().page + " " + source.pdf_page;
      item.appendChild(pageLine);
      if (state.language === "fr" && source.excerpt) {
        var excerpt = document.createElement("div");
        excerpt.className = "bcm-source-excerpt";
        excerpt.textContent = source.excerpt;
        item.appendChild(excerpt);
      }
      list.appendChild(item);
    });
    details.appendChild(list);
    return details;
  }

  function renderSuggestions() {
    els.suggestions.innerHTML = "";
    state.suggestions.forEach(function (suggestion) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "bcm-chip";
      chip.textContent = suggestion;
      chip.addEventListener("click", function () {
        state.suggestions = [];
        renderSuggestions();
        sendMessage(suggestion);
      });
      els.suggestions.appendChild(chip);
    });
  }

  function checkHealth() {
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, 8000);
    fetch(CONFIG.apiUrl + "/health", { signal: controller.signal })
      .then(function (response) {
        clearTimeout(timer);
        state.apiOnline = response.ok;
        renderStatus();
      })
      .catch(function () {
        clearTimeout(timer);
        state.apiOnline = false;
        renderStatus();
      });
  }

  function sendMessage(text) {
    text = (text || "").trim();
    if (!text || state.busy) return;
    state.history.push({ role: "user", content: text });
    state.suggestions = [];
    state.busy = true;
    els.input.value = "";
    renderMessages();
    renderSuggestions();
    els.send.disabled = true;

    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, CONFIG.requestTimeoutMs);

    var payloadHistory = state.history
      .slice(-CONFIG.historyLimit - 1, -1)
      .map(function (turn) {
        return { role: turn.role, content: turn.content };
      });

    fetch(CONFIG.apiUrl + "/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        question: text,
        history: payloadHistory,
        language: state.language,
      }),
    })
      .then(function (response) {
        clearTimeout(timer);
        return response.json().then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (result) {
        state.apiOnline = true;
        if (!result.ok) {
          var message =
            result.status === 429
              ? t().errorRate
              : (result.data && result.data.error) || t().errorGeneric;
          state.history.push({ role: "assistant", content: message });
        } else {
          var data = result.data;
          if (data.language && TEXTS[data.language]) {
            state.language = data.language;
          }
          state.history.push({
            role: "assistant",
            content: data.answer,
            sources: data.sources || [],
          });
          state.suggestions = data.suggestions || [];
        }
      })
      .catch(function () {
        clearTimeout(timer);
        state.apiOnline = false;
        state.history.push({ role: "assistant", content: t().errorGeneric });
      })
      .finally(function () {
        state.busy = false;
        els.send.disabled = false;
        saveHistory();
        renderStatic();
        renderMessages();
        renderSuggestions();
      });
  }

  function newConversation() {
    state.history = [];
    state.suggestions = [];
    saveHistory();
    renderMessages();
    renderSuggestions();
  }

  function openPanel() {
    state.open = true;
    state.everOpened = true;
    launcher.classList.remove("pulse");
    panel.classList.add("open");
    checkHealth();
    els.input.focus();
  }

  function closePanel() {
    state.open = false;
    panel.classList.remove("open");
  }

  launcher.addEventListener("click", function () {
    if (state.open) {
      closePanel();
    } else {
      openPanel();
    }
  });
  els.close.addEventListener("click", closePanel);
  els.langToggle.addEventListener("click", function () {
    state.language = state.language === "ar" ? "fr" : "ar";
    renderStatic();
    renderMessages();
  });
  els.newConversation.addEventListener("click", newConversation);
  els.send.addEventListener("click", function () {
    sendMessage(els.input.value);
  });
  els.input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(els.input.value);
    }
  });
  els.input.addEventListener("input", function () {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 90) + "px";
  });

  renderStatic();
  renderMessages();
  renderSuggestions();
})();
