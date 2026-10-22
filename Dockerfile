# Image de production de l'API Flask du chatbot RAG BCM.
# Le frontend Gradio n'est volontairement pas inclus : il reste un outil de
# démo locale (voir roadmap d'industrialisation, Phase 3) et n'est pas conçu
# pour un trafic public multi-utilisateur.
FROM python:3.12-slim

# poppler-utils : rendu des pages PDF (pdftoppm) utilisé par l'analyse des graphiques.
# tesseract-ocr, tesseract-ocr-fra : OCR multiplateforme des pages scannées
#   (scripts/tesseract_ocr.py), qui remplace Apple Vision, absent sous Linux.
# curl : sondes de santé (HEALTHCHECK, scripts d'exploitation).
# zsh : run_api_prod.sh et les autres scripts d'exploitation sont écrits pour zsh.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-fra \
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
COPY widget ./widget
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

# OCR des pages scannées (ex. rapport de l'auditeur externe, p. PDF 119) par
# Tesseract, au même format de sortie qu'Apple Vision. Les pages peu textuelles
# du rapport sont lues dès le build : la première question qui les vise ne paie
# ni le rendu ni l'OCR.
# Les graphiques ne passent pas par Tesseract, qui lit mal leurs petits libellés :
# ils utilisent la lecture visuelle Gemini (CHART_ANALYSIS_BACKEND=gemini_vision),
# qui envoie les pages rendues à Google au moment de la question. Elle reste
# désactivée par défaut tant que la BCM n'a pas validé cet envoi. Pour l'activer :
# CHART_ANALYSIS_ENABLED=true et GEMINI_API_KEY au runtime.
ENV CHART_OCR_PATH=/app/scripts/tesseract_ocr.py \
    DOCUMENT_OCR_ENABLED=true \
    CHART_ANALYSIS_ENABLED=false
RUN chmod +x scripts/tesseract_ocr.py \
    && APP_ENV=production GENERATION_PROVIDER=extractive \
        python scripts/precompute_document_ocr.py

# torch dimensionne son parallélisme sur le nombre de cœurs annoncés par l'hôte,
# pas sur le quota du conteneur. Sur une plateforme comme Railway, ses threads se
# disputent alors la même fraction de CPU. Mesuré sur cette image : une question
# passe de 1,41 s à 0,15 s à 1 vCPU, et de 0,36 s à 0,11 s à 2 vCPU. La
# vectorisation d'une question courte ne tire aucun profit du parallélisme.
ENV OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    APP_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=5000 \
    CHART_ANALYSIS_BACKEND=gemini_vision

RUN useradd --create-home --uid 1000 bcm \
    && chown -R bcm:bcm /app
USER bcm

EXPOSE 5000

# Le port d'écoute est imposé par la plateforme : la sonde doit le suivre.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-${API_PORT:-5000}}/health" || exit 1

CMD ["./run_api_prod.sh"]
