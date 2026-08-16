"""Interface Gradio du chatbot et client HTTP de l'API Flask."""

from __future__ import annotations

from typing import Any

import gradio as gr
import requests

from core.config import get_settings
from core.language import format_arabic_bidi


SETTINGS = get_settings()
API_URL = SETTINGS.api_url
LANGUAGE_CODES = {"Français": "fr", "العربية": "ar"}

HERO = {
    "fr": """
        <div class="hero">
          <h1>Assistant du rapport BCM</h1>
          <p>Questions-réponses en français avec citations, limitées au <strong>Rapport annuel BCM — exercice 2025</strong>, publié en 2026.</p>
        </div>
    """,
    "ar": """
        <div class="hero">
          <h1>مساعد تقرير البنك المركزي الموريتاني</h1>
          <p>إجابات موثقة باللغة العربية، تستند حصراً إلى <strong>التقرير السنوي للبنك المركزي الموريتاني — سنة <span dir="ltr">2025</span></strong>، المنشور سنة <span dir="ltr">2026</span>.</p>
        </div>
    """,
}

SCOPE = {
    "fr": (
        "Cette application n’utilise qu’un seul document. Si une information n’y figure pas, "
        "elle doit le signaler au lieu de compléter avec des connaissances externes."
    ),
    "ar": (
        "يستخدم هذا المساعد وثيقة واحدة فقط. إذا لم ترد المعلومة في التقرير، "
        "فيجب أن يصرّح بعدم وجودها بدلاً من الاستعانة بمعلومات خارجية."
    ),
}


def _status(language: str = "fr") -> str:
    """Interroge l'API et traduit son état en message lisible dans l'interface."""
    language = LANGUAGE_CODES.get(language, language)
    try:
        response = requests.get(f"{API_URL}/health", timeout=8)
        response.raise_for_status()
        data = response.json()
        if language == "ar":
            return format_arabic_bidi(
                f"🟢 **واجهة API متصلة** · {data['chunks']} مقطعاً · "
                f"{data['pdf_pages']} صفحة PDF · المساعد الوثائقي جاهز"
            )
        return (
            f"🟢 **API connectée** · {data['chunks']} passages · "
            f"{data['pdf_pages']} pages PDF · assistant documentaire prêt"
        )
    except requests.RequestException:
        if language == "ar":
            return "🔴 **واجهة API غير متاحة** · شغّل `./run.sh` من مجلد المشروع."
        return "🔴 **API indisponible** · lancez `./run.sh` depuis le dossier du projet."


def _source_block(
    sources: list[dict[str, Any]], language: str = "fr"
) -> str:
    """Met en forme les pages et extraits justificatifs sous la réponse."""
    if not sources:
        return ""
    title = "المصادر المستعان بها" if language == "ar" else "Sources consultées"
    page_label = "صفحة PDF" if language == "ar" else "Page PDF"
    score_label = "درجة الصلة" if language == "ar" else "pertinence"
    lines = [f"\n\n---\n**{title}**"]
    for source in sources:
        excerpt = source["excerpt"].replace("\n", " ")
        if language == "ar":
            # Le rapport source est en français. Masquer son extrait brut évite
            # de réintroduire du français sous une réponse arabe ; la page et le
            # score restent disponibles pour la vérification.
            lines.append(
                f"- **{page_label} {source['pdf_page']}** · "
                f"{score_label} {source['score']:.3f}"
            )
        else:
            lines.append(
                f"- **{page_label} {source['pdf_page']}** · "
                f"{score_label} {source['score']:.3f}  \n"
                f"  <small>{excerpt}…</small>"
            )
    block = "\n".join(lines)
    return format_arabic_bidi(block) if language == "ar" else block


def chat(
    message: str,
    history: list[dict[str, str]],
    language_choice: str,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    """Envoie un message à l'API puis actualise le chat et les suggestions."""
    message = (message or "").strip()
    if not message:
        return "", history, gr.update()
    # Les huit derniers tours suffisent généralement à résoudre « cette section »
    # ou « répète ce que tu disais sur la liquidité », sans envoyer une session
    # entière à l'API.
    safe_history = history[-16:]
    language = LANGUAGE_CODES.get(language_choice, "fr")
    proposed_questions: list[str] = []
    try:
        response = requests.post(
            f"{API_URL}/api/ask",
            json={
                "question": message,
                "history": safe_history,
                "language": language,
            },
            timeout=190,
        )
        response.raise_for_status()
        data = response.json()
        answer = data["answer"]
        language = data.get("language", language)
        if data.get("chart_analysis"):
            pages = ", ".join(str(page) for page in data.get("chart_pages", []))
            if language == "ar":
                answer += (
                    "\n\n_تم تفعيل التحليل البصري المحلي"
                    + (f" · صفحة PDF {pages}" if pages else "")
                    + " · تظل صورة التقرير على هذا الجهاز._"
                )
            else:
                answer += (
                    "\n\n_Analyse visuelle locale activée"
                    + (f" · page(s) PDF {pages}" if pages else "")
                    + " · l’image du rapport reste sur cette machine._"
                )
        answer += _source_block(data.get("sources", []), language=language)
        if language == "ar":
            answer = format_arabic_bidi(answer)
        proposed_questions = data.get("suggestions", [])
    except requests.HTTPError as exc:
        try:
            detail = exc.response.json().get("error", str(exc))
        except Exception:
            detail = str(exc)
        answer = (
            f"خطأ في واجهة API: {detail}"
            if language == "ar"
            else f"Erreur de l’API : {detail}"
        )
    except requests.RequestException:
        answer = (
            "تعذر الاتصال بواجهة API. تحقق من أن `./run.sh` ما زال قيد التشغيل."
            if language == "ar"
            else "Impossible de joindre l’API Flask. Vérifiez que `./run.sh` est toujours actif."
        )
    updated = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer},
    ]
    return (
        "",
        updated,
        gr.update(
            choices=proposed_questions,
            value=None,
            visible=bool(proposed_questions),
        ),
    )


# Quelques règles suffisent pour garder l'interface lisible sur grand écran.
CSS = """
.gradio-container {max-width: 1120px !important; margin: auto !important;}
.hero {padding: 10px 4px 2px 4px;}
.hero h1 {font-size: 2.2rem !important; margin-bottom: 0.2rem !important;}
.hero p {color: #4b5563; font-size: 1.03rem;}
.scope {border-left: 4px solid #0f766e; padding-left: 14px;}
[dir="rtl"] {text-align: right;}
[dir="rtl"] ul, [dir="rtl"] ol {padding-right: 1.6rem; padding-left: 0;}
[dir="rtl"] code, [dir="rtl"] pre {direction: ltr; text-align: left; unicode-bidi: isolate;}
.scope[dir="rtl"], [dir="rtl"].scope, [dir="rtl"] .scope {border-left: 0 !important; border-right: 4px solid #0f766e; padding-left: 0; padding-right: 14px;}
footer {display: none !important;}
"""


def _language_ui(language_choice: str) -> tuple[Any, ...]:
    """Traduit l'interface et applique le sens de lecture de la conversation."""
    language = LANGUAGE_CODES.get(language_choice, "fr")
    arabic = language == "ar"
    return (
        gr.update(value=HERO[language], rtl=arabic),
        gr.update(value=_status(language), rtl=arabic),
        gr.update(value=SCOPE[language], rtl=arabic),
        gr.update(
            rtl=arabic,
            placeholder=(
                "اكتب سؤالك عن الاقتصاد أو النقد أو البنوك أو المدفوعات أو حسابات البنك المركزي."
                if arabic
                else "Posez une question sur l’économie, la monnaie, les banques, les paiements ou les comptes de la BCM."
            ),
        ),
        gr.update(
            rtl=arabic,
            placeholder=(
                "مثال: ما معدل نمو الناتج المحلي الإجمالي الحقيقي في سنة 2025؟"
                if arabic
                else "Ex. Quel a été le taux de croissance du PIB réel en 2025 ?"
            ),
        ),
        gr.update(value="إرسال" if arabic else "Envoyer"),
        gr.update(
            choices=[],
            value=None,
            visible=False,
            rtl=arabic,
            label=(
                "حدّد سؤالك باختيار إحدى الصياغات"
                if arabic
                else "Précisez votre question en sélectionnant une formulation"
            ),
        ),
        gr.update(value="محادثة جديدة" if arabic else "Nouvelle conversation"),
        gr.update(value="تحديث الحالة" if arabic else "Actualiser l’état"),
        gr.update(visible=not arabic),
        gr.update(visible=arabic),
    )


# Construction déclarative des composants et de leurs événements Gradio.
with gr.Blocks(title="Assistant BCM · Rapport annuel") as demo:
    hero = gr.Markdown(HERO["fr"])
    status = gr.Markdown(_status("fr"))
    scope = gr.Markdown(
        SCOPE["fr"],
        elem_classes=["scope"],
    )
    language_choice = gr.Radio(
        choices=["Français", "العربية"],
        value="Français",
        label="Langue de la conversation / لغة المحادثة",
    )
    chatbot = gr.Chatbot(
        height=560,
        show_label=False,
        placeholder="Posez une question sur l’économie, la monnaie, les banques, les paiements ou les comptes de la BCM.",
    )
    with gr.Row():
        message = gr.Textbox(
            placeholder="Ex. Quel a été le taux de croissance du PIB réel en 2025 ?",
            show_label=False,
            scale=8,
            lines=2,
            max_lines=5,
        )
        send = gr.Button("Envoyer", variant="primary", scale=1)
    suggestions = gr.Radio(
        choices=[],
        label="Précisez votre question en sélectionnant une formulation",
        visible=False,
    )
    with gr.Row():
        clear = gr.Button("Nouvelle conversation")
        refresh = gr.Button("Actualiser l’état")

    with gr.Column(visible=True) as french_examples:
        gr.Examples(
            examples=[
                "Quel a été le taux de croissance du PIB réel en 2025 ?",
                "Comment l’inflation a-t-elle évolué en 2025 ?",
                "Quel était le niveau des réserves officielles brutes fin 2025 ?",
                "Quelles réformes des systèmes de paiement sont présentées ?",
                "Explique le volume des virements par mois en 2025.",
                "Explique l’organigramme de la BCM.",
                "Analyse l’état de la situation financière de la BCM.",
                "Répète ce que tu disais sur la liquidité bancaire.",
            ],
            inputs=message,
            label="Exemples",
        )
    with gr.Column(visible=False) as arabic_examples:
        gr.Examples(
            examples=[
                "ما معدل نمو الناتج المحلي الإجمالي الحقيقي في سنة 2025؟",
                "كيف تطور التضخم خلال سنة 2025؟",
                "ما مستوى الاحتياطيات الرسمية الإجمالية في نهاية سنة 2025؟",
                "ما الإصلاحات المتعلقة بأنظمة الدفع؟",
                "اشرح حجم التحويلات شهرياً خلال سنة 2025.",
                "اشرح الهيكل التنظيمي للبنك المركزي الموريتاني.",
                "حلل حالة الوضعية المالية للبنك المركزي.",
                "كرر ما ذكرته عن السيولة المصرفية.",
            ],
            inputs=message,
            label="أمثلة مقترحة",
        )

    language_choice.change(
        _language_ui,
        inputs=language_choice,
        outputs=[
            hero,
            status,
            scope,
            chatbot,
            message,
            send,
            suggestions,
            clear,
            refresh,
            french_examples,
            arabic_examples,
        ],
    )

    send.click(
        chat,
        [message, chatbot, language_choice],
        [message, chatbot, suggestions],
    )
    message.submit(
        chat,
        [message, chatbot, language_choice],
        [message, chatbot, suggestions],
    )
    suggestions.change(
        lambda value: (value or "", gr.update(visible=False)),
        inputs=suggestions,
        outputs=[message, suggestions],
    )
    clear.click(
        lambda: ("", [], gr.update(choices=[], value=None, visible=False)),
        outputs=[message, chatbot, suggestions],
    )
    refresh.click(_status, inputs=language_choice, outputs=status)


if __name__ == "__main__":
    demo.launch(
        server_name=SETTINGS.gradio_host,
        server_port=SETTINGS.gradio_port,
        show_error=False,
        css=CSS,
        theme=gr.themes.Soft(),
    )
