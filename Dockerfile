FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# PyTorch só para CPU (evita descarregar vários GB de CUDA que não seriam usados)
RUN pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

# yt-dlp (downloads por link). O YouTube exige um motor de JavaScript: o Deno.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
# + plugin que obtém "PO tokens" do contentor pot-provider (evita o "confirm you're not a bot")
RUN pip install -U "yt-dlp[default]" bgutil-ytdlp-pot-provider

COPY app ./app

# Dados (músicas processadas) e modelos ficam em volumes
ENV DATA_DIR=/data \
    HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    YTDLP_AUTO_UPDATE=1

EXPOSE 8000
# O YouTube muda muitas vezes e versões antigas do yt-dlp deixam de funcionar,
# por isso tenta atualizá-lo sempre que o contentor arranca (se falhar, segue em frente).
CMD ["sh", "-c", "if [ \"$YTDLP_AUTO_UPDATE\" = 1 ]; then timeout 90 pip install -q -U 'yt-dlp[default]' bgutil-ytdlp-pot-provider || echo 'Aviso: não foi possível atualizar o yt-dlp'; fi; exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
