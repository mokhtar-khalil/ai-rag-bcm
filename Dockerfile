# Image de production de l'API Flask du chatbot RAG BCM.
# Le frontend Gradio n'est volontairement pas inclus : il reste un outil de
# démo locale (voir roadmap d'industrialisation, Phase 3) et n'est pas conçu
# pour un trafic public multi-utilisateur.
FROM python:3.12-slim

# poppler-utils : rendu des pages PDF (pdftoppm) utilisé par l'analyse des graphiques.
# curl : sondes de santé (HEALTHCHECK, scripts d'exploitation).
# zsh : run_api_prod.sh et les autres scripts d'exploitation sont écrits pour zsh.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        curl \
        zsh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-api.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    # Torch CPU-only : les variantes GPU/CUDA tirées par défaut par
    # sentence-transformers ajoutent plusieurs Go inutiles sur un serveur sans GPU.
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r requirements-api.txt

COPY api ./api
COPY core ./core
COPY scripts ./scripts
COPY data ./data
COPY tests ./tests
COPY wsgi.py run_api_prod.sh ./

RUN chmod +x run_api_prod.sh && mkdir -p storage logs

# Construit les index lexical et sémantique au moment du build : le conteneur
# démarre alors instantanément et fonctionne sans accès réseau au premier
# appel. GENERATION_PROVIDER=extractive évite d'exiger une clé OpenAI ici ;
# le vrai fournisseur de génération est choisi par .env.production au runtime.
RUN APP_ENV=production GENERATION_PROVIDER=extractive \
        python scripts/index_report.py \
    && APP_ENV=production GENERATION_PROVIDER=extractive \
        python scripts/index_embeddings.py --if-configured \
    && APP_ENV=production GENERATION_PROVIDER=extractive \
        python -c "from api.rag import RAGIndex; from core.config import get_settings; \
s = get_settings(); e = RAGIndex(s.report_path, s.index_path).load(); \
print('corpus indexé :', e.metadata['documents'], 'documents,', e.metadata['chunks'], 'passages'); \
assert e.metadata['chunks'] > 2000, 'index incomplet'"

# L'OCR local des graphiques (api/charts.py, scripts/build_chart_ocr.sh) repose
# sur Swift + Apple Vision, disponibles uniquement sur macOS. Cette image Linux
# ne peut pas l'activer : CHART_ANALYSIS_ENABLED doit rester "false" tant qu'un
# moteur OCR multiplateforme ne l'a pas remplacé (roadmap, au-delà de la Phase 1).
ENV APP_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=5000 \
    CHART_ANALYSIS_ENABLED=false

RUN useradd --create-home --uid 1000 bcm \
    && chown -R bcm:bcm /app
USER bcm

EXPOSE 5000

# Le port d'écoute est imposé par la plateforme : la sonde doit le suivre.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-${API_PORT:-5000}}/health" || exit 1

CMD ["./run_api_prod.sh"]
