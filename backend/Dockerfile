# The API image.
#
# Build from `backend/` as the context:
#
#     docker build -t noema-api backend/
#
# Configuration comes from the environment at run time, never from a file in
# the image -- `.dockerignore` keeps `.env` out for that reason.

FROM python:3.12-slim

# PYTHONUNBUFFERED, or a crash loses the lines that explain it: several
# hosting platforms read stdout from a pipe, and Python buffers 8 KB into a
# pipe by default, so the last thing a dying process said never arrives.
#
# HF_HOME is where sentence-transformers caches the model. Set explicitly so
# the download below lands somewhere the image keeps, rather than in a $HOME
# the runtime user may not have.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/models

WORKDIR /app

COPY requirements.txt .

# The CPU wheel first, and from PyTorch's own index. Installed separately
# because `pip install -r requirements.txt` alone resolves torch from PyPI,
# which is the CUDA build: ~2.5 GB of nvidia-* libraries that nothing in
# Noema uses and that no CPU host can benefit from.
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image.
#
# The API loads it lazily, on the first theme search. Without this, that first
# search -- made by a real person, in production -- pays a ~420 MB download
# before it answers, and the deployment depends on huggingface.co being
# reachable from the runtime network rather than only from the build network.
# Doing it here turns an unpredictable request into a predictable layer.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-mpnet-base-v2')"

COPY . .

# Not root. The serving process needs no write access to anything here.
#
# `/app/data` is created anyway, and owned, because `.dockerignore` excludes
# it and a deployment mounts a volume there for the one-off corpus commands.
# Docker takes a fresh named volume's ownership from the directory it covers;
# if that directory does not exist the mountpoint is created root-owned and
# `seed_corpus` cannot write the texts it downloads.
RUN mkdir -p /app/data \
 && useradd --create-home --uid 10001 noema \
 && chown -R noema:noema /app /opt/models
USER noema

EXPOSE 8000

# Shell form, so `$PORT` is expanded. Render, Railway, Fly and Cloud Run all
# inject the port they expect the process to bind, and a hard-coded 8000 is
# reported as an unhealthy deployment with no log line saying why.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
