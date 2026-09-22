# The RQ worker image.
#
# The build context is `backend/`, not `backend/worker/` -- the worker is a
# package inside the backend, and it imports `app.*`:
#
#     docker build -f backend/worker/Dockerfile -t noema-worker backend/
#
# Identical to the API image except for the entrypoint. Kept as a separate
# file rather than a build argument so the two can diverge -- the worker is
# the one that will eventually want more memory and a longer grace period --
# without either of them growing a conditional.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/models

WORKDIR /app

COPY requirements.txt .

# CPU torch first; see the note in ../Dockerfile.
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# The worker's whole job is encoding, so the model is not optional here. Baked
# in for the same reason: a job that begins with a 420 MB download is a job
# that fails differently every time the network does.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-mpnet-base-v2')"

COPY . .

# `/app/data` created and owned for the same reason as in ../Dockerfile: a
# named volume mounted over a path that does not exist in the image gets a
# root-owned mountpoint, which a non-root process cannot write to.
RUN mkdir -p /app/data \
 && useradd --create-home --uid 10001 noema \
 && chown -R noema:noema /app /opt/models
USER noema

CMD ["python", "-m", "worker.run"]
