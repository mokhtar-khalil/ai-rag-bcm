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
      titleFr: globalConfig.titleFr || dataset.titleFr || "Assistant des publications de la BCM",
      titleAr: globalConfig.titleAr || dataset.titleAr || "مساعد منشورات البنك المركزي",
      // URL d'un logo officiel. Vide : la marque intégrée ci-dessous est
      // utilisée. Le fichier doit être servi par le même site que le widget,
      // sinon la politique de sécurité de la page hôte peut le bloquer.
      logoUrl: globalConfig.logoUrl || dataset.logoUrl || "",
      streaming:
        String(globalConfig.streaming || dataset.streaming || "true") !== "false",
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
        "Cette application n’utilise que les documents BCM indexés : le Rapport annuel et les Lettres d’information. Si une information n’y figure pas, elle doit le signaler au lieu de compléter avec des connaissances externes.",
      placeholder: "Posez votre question…",
      send: "Envoyer",
      newConversation: "Nouvelle conversation",
      sourcesTitle: "Sources",
      page: "Page",
      stages: {
        recherche: "Recherche dans les documents…",
        reformulation: "Reformulation de la question…",
        selection: "Sélection des passages…",
        redaction: "Rédaction de la réponse…",
      },
      kindPdf: "Rapport annuel",
      kindLettre: "Lettre d’information",
      kindWeb: "Site bcm.mr",
      statusOk: "Interrogez les publications de la BCM",
      statusDown: "Service indisponible pour le moment",
      statusChecking: "Connexion en cours…",
      errorGeneric: "Une erreur est survenue. Réessayez dans un instant.",
      errorRate: "Trop de questions en peu de temps. Merci de patienter avant de réessayer.",
      launcherLabel: "Ouvrir l’assistant des publications de la BCM",
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
        "يستخدم هذا المساعد وثائق البنك المركزي المفهرسة فقط: التقرير السنوي والرسائل الإخبارية. إذا لم ترد المعلومة فيها، فيجب أن يصرّح بعدم وجودها بدلاً من الاستعانة بمعلومات خارجية.",
      placeholder: "اكتب سؤالك…",
      send: "إرسال",
      newConversation: "محادثة جديدة",
      sourcesTitle: "المصادر",
      page: "صفحة",
      stages: {
        recherche: "البحث في الوثائق…",
        reformulation: "إعادة صياغة السؤال…",
        selection: "اختيار المقاطع…",
        redaction: "تحرير الإجابة…",
      },
      kindPdf: "التقرير السنوي",
      kindLettre: "الرسالة الإخبارية",
      kindWeb: "موقع bcm.mr",
      statusOk: "استفسر عن منشورات البنك المركزي",
      statusDown: "الخدمة غير متاحة حالياً",
      statusChecking: "جارٍ الاتصال…",
      errorGeneric: "حدث خطأ. يرجى إعادة المحاولة بعد قليل.",
      errorRate: "عدد كبير من الأسئلة خلال وقت قصير. يرجى الانتظار قبل إعادة المحاولة.",
      launcherLabel: "افتح مساعد منشورات البنك المركزي",
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
    // Texte reçu au fil de l'eau, remplacé par la réponse validée à la fin.
    streaming: "",
    // Étape en cours du traitement, affichée pendant l'attente initiale.
    stage: "",
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

  // Repères de citation : le rapport annuel garde « p. PDF N », les Lettres
  // d'information portent leur mois. renderInline reçoit du texte déjà échappé,
  // où l'apostrophe vaut &#39; : les motifs doivent l'accepter.
  var CITE_PDF = /\[p\.\s*PDF\s*(\d+)\]/g;
  var CITE_LETTRE = /\[Lettre d(?:&#39;|'|’)information ([^\],]{1,40}), p\.\s*(\d+)\]/g;
  // Forme arabe du même repère. Le rendu bidirectionnel entoure les chiffres
  // d'isolats directionnels (U+2066 à U+2069) : les ignorer ici laisserait la
  // citation en texte brut au milieu de la réponse.
  var ISOLATS = "[\u2066-\u2069]*";
  var CITE_LETTRE_AR = new RegExp(
    "\\[" + ISOLATS + "الرسالة الإخبارية ([^\\]،]{1,40})،\\s*ص\\.\\s*" +
      ISOLATS + "(\\d+)" + ISOLATS + "\\]",
    "g"
  );

  function renderInline(escapedText) {
    return escapedText
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      // L'italique n'est reconnu qu'entouré de limites de mot, afin de ne pas
      // dénaturer un identifiant technique ou une multiplication.
      .replace(/(^|[\s(])\*(?!\s)([^*\n]+?)\*(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_(?!\s)([^_\n]+?)_(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>")
      .replace(CITE_PDF, '<span class="bcm-cite">p.&nbsp;$1</span>')
      .replace(CITE_LETTRE, '<span class="bcm-cite bcm-cite-lettre">$1&nbsp;· p.&nbsp;$2</span>')
      .replace(CITE_LETTRE_AR, '<span class="bcm-cite bcm-cite-lettre">$1&nbsp;· ص.&nbsp;$2</span>');
  }

  // Les modèles alternent les conventions de liste selon les réponses : tirets,
  // astérisques, puces ou numéros. Les ignorer laissait le marqueur brut visible
  // au début de chaque puce.
  var RE_BULLET = /^([-–—•*+])\s+/;
  var RE_ORDERED = /^(\d{1,2})[.)]\s+/;
  var RE_HEADING = /^(#{1,4})\s+/;

  function renderMarkdown(rawText) {
    var lines = escapeHtml(rawText).split(/\n/);
    var html = "";
    var listType = "";

    function closeList() {
      if (listType) {
        html += "</" + listType + ">";
        listType = "";
      }
    }

    function openList(type) {
      if (listType !== type) {
        closeList();
        html += "<" + type + ">";
        listType = type;
      }
    }

    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (trimmed === "") {
        closeList();
        return;
      }

      var heading = trimmed.match(RE_HEADING);
      if (heading) {
        closeList();
        html += '<h4>' + renderInline(trimmed.slice(heading[0].length)) + "</h4>";
        return;
      }

      var bullet = trimmed.match(RE_BULLET);
      if (bullet) {
        openList("ul");
        html += "<li>" + renderInline(trimmed.slice(bullet[0].length)) + "</li>";
        return;
      }

      // Deux chiffres au plus : « 2025. » reste une phrase, pas une énumération.
      var ordered = trimmed.match(RE_ORDERED);
      if (ordered) {
        openList("ol");
        html += "<li>" + renderInline(trimmed.slice(ordered[0].length)) + "</li>";
        return;
      }

      closeList();
      html += "<p>" + renderInline(trimmed) + "</p>";
    });

    closeList();
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
    "*{box-sizing:border-box;font-family:'Open Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;}" +
    ":host{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;}" +
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
    ".bcm-header-icon svg{width:19px;height:19px;fill:#fff;}" +
    ".bcm-logo{width:100%;height:100%;object-fit:contain;border-radius:50%;background:#fff;padding:2px;}" +
    ".bcm-header-text{flex:1;min-width:0;}" +
    ".bcm-header-text h1{font-size:15px;font-weight:700;margin:0;line-height:1.25;}" +
    ".bcm-header-status{font-size:11px;opacity:.92;display:flex;align-items:center;gap:5px;margin-top:2px;}" +
    ".bcm-dot{width:6px;height:6px;border-radius:50%;background:#d1d5db;flex-shrink:0;}" +
    ".bcm-dot.ok{background:#4ade80;}" +
    ".bcm-dot.down{background:#f87171;}" +
    ".bcm-header button{background:rgba(255,255,255,.16);border:none;color:#fff;border-radius:9px;padding:7px 9px;cursor:pointer;font-size:11.5px;font-weight:600;flex-shrink:0;}" +
    ".bcm-header button:hover{background:rgba(255,255,255,.3);}" +
    // Les deux langues restent visibles : l'ancien bouton unique affichait la
    // langue cible, que l'on pouvait lire comme la langue courante.
    ".bcm-lang{display:flex;gap:2px;background:rgba(0,0,0,.16);border-radius:10px;padding:2px;flex-shrink:0;}" +
    ".bcm-lang button{min-width:30px;background:transparent;border-radius:8px;padding:5px 8px;font-size:11.5px;font-weight:600;color:rgba(255,255,255,.55);white-space:nowrap;line-height:1.2;transition:background .15s ease,color .15s ease;}" +
    ".bcm-lang button:hover{background:rgba(255,255,255,.14);color:#fff;}" +
    ".bcm-lang button.actif{background:#fff;font-weight:800;color:" +
    accentDark +
    ";box-shadow:0 1px 3px rgba(0,0,0,.22);}" +
    ".bcm-lang button.actif:hover{background:#fff;}" +
    // Les deux codes gardent la même graisse et la même largeur : l'œil compare
    // l'état actif, pas la forme des libellés.
    ".bcm-lang button{letter-spacing:.02em;}" +
    // Le titre cède la place au sélecteur plutôt que de le comprimer.
    ".bcm-header-text h1{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
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
    ".bcm-msg{max-width:82%;padding:12px 15px;border-radius:16px;font-size:13.5px;line-height:1.62;word-wrap:break-word;overflow-wrap:anywhere;}" +
    ".bcm-msg p{margin:0 0 9px;}" +
    ".bcm-msg>*:last-child{margin-bottom:0;}" +
    ".bcm-msg h4{margin:14px 0 6px;font-size:12px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:" +
    accentDark +
    ";}" +
    ".bcm-msg h4:first-child{margin-top:0;}" +
    ".bcm-msg ul,.bcm-msg ol{margin:2px 0 10px;padding-inline-start:20px;}" +
    ".bcm-msg li{margin-bottom:6px;padding-inline-start:2px;}" +
    ".bcm-msg li:last-child{margin-bottom:0;}" +
    ".bcm-msg ul li::marker{color:" + CONFIG.accentColor + ";}" +
    ".bcm-msg ol li::marker{color:" + accentDark + ";font-weight:700;font-size:12px;}" +
    ".bcm-msg strong{font-weight:700;color:#0b1220;}" +
    ".bcm-msg em{font-style:italic;}" +
    ".bcm-msg.user strong{color:inherit;}" +
    ".bcm-msg.user h4{color:rgba(255,255,255,.92);}" +
    ".bcm-cite{display:inline-block;background:color-mix(in srgb," +
    CONFIG.accentColor +
    " 11%,transparent);color:" +
    accentDark +
    ";border:1px solid color-mix(in srgb," +
    CONFIG.accentColor +
    " 22%,transparent);border-radius:6px;padding:1px 6px;margin:0 1px;font-size:11px;font-weight:600;white-space:nowrap;vertical-align:baseline;}" +
    ".bcm-cite-lettre{border-style:dashed;}" +
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
    ".bcm-source-item{background:#f7f8fa;border:1px solid #eceef2;border-radius:10px;padding:9px 11px;border-inline-start:3px solid " +
    CONFIG.accentColor +
    ";}" +
    ".bcm-source-item.lettre{border-inline-start-color:#b45309;}" +
    ".bcm-source-kind{display:block;font-size:9.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9ca3af;margin-bottom:3px;}" +
    ".bcm-source-page{font-size:11.5px;font-weight:700;color:#374151;margin-bottom:4px;}" +
    ".bcm-source-link{color:" + accentDark + ";text-decoration:none;}" +
    ".bcm-source-link:hover{text-decoration:underline;}" +
    ".bcm-source-link::after{content:' \\2197';font-size:10px;opacity:.65;}" +
    // Un extrait de 420 caractères occupe une dizaine de lignes dans une fiche
    // étroite et noie la liste des sources. Trois lignes suffisent à situer le
    // passage ; le lien mène au document complet.
    ".bcm-source-excerpt{font-size:11.5px;color:#6b7280;line-height:1.45;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;}" +
    ".bcm-source-item:hover .bcm-source-excerpt{-webkit-line-clamp:12;}" +
    ".bcm-source-date{font-size:10.5px;color:#9ca3af;margin-top:5px;}" +
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
    ".bcm-input{flex:1;resize:none;border:1.5px solid #e5e7eb;border-radius:14px;padding:11px 14px;font-size:13.5px;line-height:1.4;max-height:104px;min-height:42px;font-family:inherit;color:#111827;transition:border-color .15s ease,box-shadow .15s ease;}" +
    ".bcm-input::placeholder{color:#9ca3af;}" +
    ".bcm-input:focus{box-shadow:0 0 0 3px color-mix(in srgb," + CONFIG.accentColor + " 14%,transparent);}" +
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
    ".bcm-typing .bcm-stage{width:auto;height:auto;border-radius:0;background:none;animation:bcm-fade 1.4s ease-in-out infinite;font-size:12px;color:#6b7280;white-space:nowrap;}" +
    "@keyframes bcm-fade{0%,100%{opacity:.55;}50%{opacity:1;}}" +
    ".bcm-partiel::after{content:'';display:inline-block;width:2px;height:1em;margin-inline-start:2px;background:" +
    CONFIG.accentColor +
    ";vertical-align:-2px;animation:bcm-curseur 1s steps(2) infinite;}" +
    "@keyframes bcm-curseur{0%,49%{opacity:1;}50%,100%{opacity:0;}}" +
    ".bcm-typing span:nth-child(2){animation-delay:.15s;}" +
    ".bcm-typing span:nth-child(3){animation-delay:.3s;}" +
    "@keyframes bcm-bounce{0%,80%,100%{transform:translateY(0);opacity:.5;}40%{transform:translateY(-4px);opacity:1;}}" +
    ".bcm-messages::-webkit-scrollbar{width:8px;}" +
    ".bcm-messages::-webkit-scrollbar-thumb{background:#d5d8de;border-radius:4px;}" +
    ".bcm-messages::-webkit-scrollbar-thumb:hover{background:#bcc0c8;}" +
    ".bcm-messages::-webkit-scrollbar-track{background:transparent;}" +
    // Sans cette règle, la navigation au clavier ne montre aucun repère visible
    // dans le Shadow DOM, où les styles du site hôte ne s'appliquent pas.
    ":host *:focus-visible{outline:2px solid " +
    CONFIG.accentColor +
    ";outline-offset:2px;border-radius:4px;}" +
    ".bcm-header *:focus-visible{outline-color:#fff;}" +
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;}}" +
    // Plein écran sur mobile, en tenant compte de l'encoche et de la barre
    // système : sans ces marges, la saisie passe sous la barre de l'iPhone.
    "@media (max-width:480px){.bcm-panel{right:0;left:0;bottom:0;width:100%;max-width:100%;height:100%;height:100dvh;border-radius:0;}" +
    ".bcm-header{padding-top:calc(16px + env(safe-area-inset-top));}" +
    ".bcm-inputrow{padding-bottom:calc(13px + env(safe-area-inset-bottom));}" +
    ".bcm-launcher{bottom:calc(18px + env(safe-area-inset-bottom));}}";
  shadow.appendChild(style);

  // Fronton, colonnes et soubassement : la lecture « institution monétaire » est
  // immédiate et n'imite aucun logo existant. Remplaçable par data-logo-url.
  var BOT_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 21.6 8.2V9.6H2.4V8.2L12 3Z' +
    "M5.4 11h1.8v7H5.4zM9.2 11H11v7H9.2zM13 11h1.8v7H13zM16.8 11h1.8v7h-1.8z" +
    'M3.6 19.4h16.8v2H3.6z"/></svg>';

  // Le lanceur garde une bulle de conversation : c'est l'affordance attendue
  // pour ouvrir un dialogue, la marque institutionnelle vit dans l'en-tête.
  var LAUNCHER_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3c5 0 9 3.4 9 7.6 0 4.2-4 7.6-9 7.6-.9 0-1.8-.1-2.6-.3L4 20.5l1.2-3.4C3.6 15.7 3 13.5 3 10.6 3 6.4 7 3 12 3Z"/></svg>';

  // Le logo officiel, quand il est fourni, remplace la marque intégrée partout
  // où l'assistant s'identifie : en-tête et avatar des réponses.
  function brandMarkup() {
    if (!CONFIG.logoUrl) return BOT_ICON;
    return (
      '<img class="bcm-logo" src="' +
      escapeAttribute(CONFIG.logoUrl) +
      '" alt="" aria-hidden="true">'
    );
  }

  function escapeAttribute(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return HTML_ESCAPES[ch];
    });
  }

  var launcher = document.createElement("button");
  launcher.className = "bcm-launcher pulse";
  launcher.setAttribute("aria-label", TEXTS[state.language].launcherLabel);
  launcher.innerHTML = LAUNCHER_ICON;
  shadow.appendChild(launcher);

  var panel = document.createElement("div");
  panel.className = "bcm-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "false");
  panel.setAttribute("aria-labelledby", "bcm-title");
  panel.innerHTML =
    '<div class="bcm-header">' +
    '<div class="bcm-header-icon">' +
    brandMarkup() +
    "</div>" +
    '<div class="bcm-header-text">' +
    '<h1 id="bcm-title"></h1>' +
    '<div class="bcm-header-status"><span class="bcm-dot" id="bcm-dot"></span><span id="bcm-status-text"></span></div>' +
    "</div>" +
    '<div class="bcm-lang" id="bcm-lang" role="group" aria-label="Langue / اللغة">' +
    '<button type="button" data-lang="fr" lang="fr" title="Français" ' +
    'aria-label="Français">FR</button>' +
    '<button type="button" data-lang="ar" lang="ar" title="العربية" ' +
    'aria-label="العربية">AR</button>' +
    "</div>" +
    '<button type="button" id="bcm-close" aria-label="close">✕</button>' +
    "</div>" +
    '<div class="bcm-messages" id="bcm-messages" role="log" aria-live="polite" aria-atomic="false"></div>' +
    '<div class="bcm-suggestions" id="bcm-suggestions"></div>' +
    '<div class="bcm-inputrow">' +
    '<textarea class="bcm-input" id="bcm-input" rows="1"></textarea>' +
    '<button type="button" class="bcm-send" id="bcm-send"></button>' +
    "</div>" +
    '<div class="bcm-footer"><button type="button" id="bcm-new"></button></div>';
  shadow.appendChild(panel);

  var els = {
    title: shadow.getElementById("bcm-title"),
    langButtons: shadow.getElementById("bcm-lang").querySelectorAll("button"),
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
    Array.prototype.forEach.call(els.langButtons, function (bouton) {
      var actif = bouton.getAttribute("data-lang") === state.language;
      bouton.classList.toggle("actif", actif);
      bouton.setAttribute("aria-pressed", actif ? "true" : "false");
    });
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
      if (state.streaming) {
        // Réponse en cours de rédaction : le curseur signale qu'elle continue.
        var partiel = document.createElement("div");
        partiel.className = "bcm-msg assistant bcm-partiel";
        partiel.innerHTML = renderMarkdown(state.streaming);
        row.appendChild(partiel);
      } else {
        var attente = document.createElement("div");
        attente.className = "bcm-typing";
        var libelle = (t().stages || {})[state.stage];
        if (libelle) {
          // Nommer l'étape rend l'attente lisible : plusieurs secondes séparent
          // la question du premier mot, le temps de chercher et de sélectionner.
          var texte = document.createElement("span");
          texte.className = "bcm-stage";
          texte.textContent = libelle;
          attente.appendChild(texte);
        } else {
          attente.innerHTML = "<span></span><span></span><span></span>";
        }
        row.appendChild(attente);
      }
      els.messages.appendChild(row);
    }
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function makeAvatar() {
    var avatar = document.createElement("div");
    avatar.className = "bcm-avatar";
    avatar.innerHTML = brandMarkup();
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
      var kind = source.source_type || "pdf";
      item.className = "bcm-source-item" + (kind === "pdf" ? "" : " " + kind);

      var kindLine = document.createElement("span");
      kindLine.className = "bcm-source-kind";
      kindLine.textContent =
        kind === "lettre" ? t().kindLettre : kind === "web" ? t().kindWeb : t().kindPdf;
      item.appendChild(kindLine);

      var pageLine = document.createElement("div");
      pageLine.className = "bcm-source-page";
      var label =
        source.citation ||
        t().page + " " + (source.source_page || source.pdf_page);
      if (source.source_url) {
        // La lettre est publiée sur bcm.mr : renvoyer le lecteur à la source.
        var link = document.createElement("a");
        link.className = "bcm-source-link";
        link.href = source.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = label;
        pageLine.appendChild(link);
      } else {
        pageLine.textContent = label;
      }
      item.appendChild(pageLine);
      if (state.language === "fr" && source.excerpt) {
        var excerpt = document.createElement("div");
        excerpt.className = "bcm-source-excerpt";
        excerpt.textContent = source.excerpt;
        item.appendChild(excerpt);
      }
      if (source.source_date) {
        var date = document.createElement("div");
        date.className = "bcm-source-date";
        date.textContent = source.source_date;
        item.appendChild(date);
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

  // ---- Envoi : diffusion au fil de l'eau, avec repli en un seul appel ----

  function terminer() {
    state.busy = false;
    state.stage = "";
    state.streaming = "";
    els.send.disabled = false;
    saveHistory();
    renderStatic();
    renderMessages();
    renderSuggestions();
  }

  function appliquerReponse(data) {
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

  function messageErreur(status, data) {
    return status === 429
      ? t().errorRate
      : (data && data.error) || t().errorGeneric;
  }

  function corpsRequete(text, payloadHistory) {
    return JSON.stringify({
      question: text,
      history: payloadHistory,
      language: state.language,
    });
  }

  function envoiSimple(text, payloadHistory, controller, timer) {
    return fetch(CONFIG.apiUrl + "/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: corpsRequete(text, payloadHistory),
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
          state.history.push({
            role: "assistant",
            content: messageErreur(result.status, result.data),
          });
        } else {
          appliquerReponse(result.data);
        }
      })
      .catch(function () {
        clearTimeout(timer);
        state.apiOnline = false;
        state.history.push({ role: "assistant", content: t().errorGeneric });
      })
      .finally(terminer);
  }

  // Le texte diffusé est provisoire : l'API ne valide les citations et n'applique
  // le rendu arabe qu'une fois la réponse complète. L'événement « done » fait donc
  // autorité et remplace ce qui a été affiché.
  function envoiDiffuse(text, payloadHistory, controller, timer) {
    var tampon = "";
    var recuUnEvenement = false;
    var dernierRendu = 0;

    function traiter(nom, charge) {
      recuUnEvenement = true;
      if (nom === "stage") {
        state.stage = charge.stage || "";
        renderMessages();
      } else if (nom === "delta") {
        state.streaming += charge.text || "";
        // Un rendu par fragment saturerait le navigateur : plusieurs centaines
        // arrivent pour une seule réponse.
        var maintenant = Date.now();
        if (maintenant - dernierRendu > 60) {
          dernierRendu = maintenant;
          renderMessages();
        }
      } else if (nom === "done") {
        state.streaming = "";
        appliquerReponse(charge);
      } else if (nom === "error") {
        state.streaming = "";
        state.history.push({
          role: "assistant",
          content: messageErreur(charge.status, charge),
        });
      }
    }

    function decouper(bloc) {
      var nom = "message";
      var donnees = "";
      bloc.split("\n").forEach(function (ligne) {
        if (ligne.indexOf("event: ") === 0) nom = ligne.slice(7).trim();
        else if (ligne.indexOf("data: ") === 0) donnees += ligne.slice(6);
      });
      if (!donnees) return;
      try {
        traiter(nom, JSON.parse(donnees));
      } catch (e) {
        /* trame incomplète ou inattendue : ignorée */
      }
    }

    return fetch(CONFIG.apiUrl + "/api/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: corpsRequete(text, payloadHistory),
    })
      .then(function (response) {
        state.apiOnline = true;
        if (!response.ok || !response.body) {
          throw new Error("flux indisponible");
        }
        var lecteur = response.body.getReader();
        var decodeur = new TextDecoder("utf-8");
        var reste = "";
        function lire() {
          return lecteur.read().then(function (bloc) {
            if (bloc.done) {
              if (reste.trim()) decouper(reste);
              return;
            }
            reste += decodeur.decode(bloc.value, { stream: true });
            var trames = reste.split("\n\n");
            reste = trames.pop();
            trames.forEach(decouper);
            return lire();
          });
        }
        return lire();
      })
      .then(function () {
        clearTimeout(timer);
        if (!recuUnEvenement) {
          throw new Error("flux vide");
        }
      })
      .catch(function (err) {
        clearTimeout(timer);
        if (err && err.name === "AbortError") {
          state.apiOnline = false;
          state.history.push({ role: "assistant", content: t().errorGeneric });
          return;
        }
        // Un proxy peut tamponner ou refuser le flux : on retente en un seul
        // appel plutôt que de laisser l'utilisateur sans réponse.
        state.streaming = "";
        state.stage = "";
        var repli = new AbortController();
        var replTimer = setTimeout(function () {
          repli.abort();
        }, CONFIG.requestTimeoutMs);
        return envoiSimple(text, payloadHistory, repli, replTimer);
      })
      .then(function (deja) {
        if (deja === undefined) terminer();
      });
  }

  function sendMessage(text) {
    text = (text || "").trim();
    if (!text || state.busy) return;
    state.history.push({ role: "user", content: text });
    state.suggestions = [];
    state.busy = true;
    state.streaming = "";
    state.stage = "";
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

    var diffusionPossible =
      CONFIG.streaming &&
      typeof TextDecoder !== "undefined" &&
      typeof ReadableStream !== "undefined";

    if (diffusionPossible) {
      envoiDiffuse(text, payloadHistory, controller, timer);
    } else {
      envoiSimple(text, payloadHistory, controller, timer);
    }
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
    // Rendre le focus au lanceur : sans cela, la navigation au clavier
    // repartirait du début de la page hôte après la fermeture.
    launcher.focus();
  }

  // Échap ferme le panneau : le raccourci attendu de toute boîte de dialogue.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && state.open) {
      closePanel();
    }
  });

  launcher.addEventListener("click", function () {
    if (state.open) {
      closePanel();
    } else {
      openPanel();
    }
  });
  els.close.addEventListener("click", closePanel);
  Array.prototype.forEach.call(els.langButtons, function (bouton) {
    bouton.addEventListener("click", function () {
      var choisie = bouton.getAttribute("data-lang");
      if (choisie === state.language) return;
      state.language = choisie;
      renderStatic();
      renderMessages();
      renderSuggestions();
    });
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
