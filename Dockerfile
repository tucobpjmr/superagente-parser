# =====================================================
# SuperAgente Parser — Python 3.11 + Markitdown + OCR
# =====================================================
FROM python:3.11-slim

# Dipendenze di sistema:
# - tesseract-ocr + tesseract-ocr-ita → OCR italiano per PDF scansionati
# - poppler-utils → conversione PDF → immagine (necessaria per OCR)
# - libgl1, libglib2.0-0 → richieste da OpenCV (usato da alcune dipendenze Markitdown)
# - ffmpeg → richiesto da Markitdown per audio (se mai serve)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-ita \
    tesseract-ocr-eng \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia codice
COPY . .

# Railway/Fly.io passano PORT come variabile d'ambiente
ENV PORT=8000
EXPOSE 8000

# Healthcheck (Railway lo usa per il rolling deploy)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:'+__import__('os').environ.get('PORT','8000')+'/health').read()" || exit 1

# Avvio con uvicorn
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 2
