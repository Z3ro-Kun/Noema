# ml/

Home for the NLP/ML pipeline: embedding generation, entity/concept
extraction, relationship discovery, and evaluation. Intentionally empty in
Phase 0 -- these are separate, later phases (see `docs/architecture.md`).

Planned layout, created as each piece is actually implemented rather than
stubbed out in advance:

```text
ml/
├── embeddings/   sentence-transformers wrapper, batching, persistence to the
│                 `embeddings` table
├── nlp/          shared text preprocessing (tokenization, segmentation)
├── concepts/     theme/motif/trope detection
├── entities/     named entity recognition and resolution
├── relations/    relationship discovery (similarity, co-occurrence, ...)
└── evaluation/   scripts/notebooks for measuring extraction quality
```

Code here should run CPU-first and be invoked from background workers
(`backend/worker`), not from the request path.
