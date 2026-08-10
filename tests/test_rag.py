"""RAG subsystem: chunking attribution, embedder determinism, ranking
sanity, hybrid retrieval, workflow integration, retrieval eval gate, and the
postmortem knowledge loop (docs/17 acceptance #5–#9)."""
import unittest

from aetherops.core.types import WorkflowStatus
from aetherops.demo import build_demo_environment
from aetherops.evals.retrieval import (RETRIEVAL_PRECISION_GATE,
                                       run_retrieval_eval,
                                       run_semantic_retrieval_eval)
from aetherops.rag.chunking import chunk_fixed, chunk_paragraph, get_chunker
from aetherops.rag.corpus import SEED_RUNBOOKS, Document
from aetherops.rag.embeddings import TfidfEmbedder, dot, get_embedder
from aetherops.rag.retriever import RagStore
from aetherops.workflows.incident_remediation import run_incident_remediation

DOC = Document(id="d1", title="T", kind="runbook",
               text=("First paragraph about connection pools and memory "
                     "limits in production services.\n\n"
                     "Second paragraph about rolling back deployments and "
                     "opening revert pull requests afterwards.\n\nTail."))


class TestChunking(unittest.TestCase):
    def test_paragraph_chunks_carry_attribution(self):
        chunks = chunk_paragraph(DOC)
        self.assertGreaterEqual(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.doc_id, "d1")
            self.assertTrue(chunk.ref.startswith("rag://d1#"))
            # offset points at the actual text in the source document
            self.assertTrue(DOC.text[chunk.start:].startswith(
                chunk.text.split("\n\n")[0][:20]))

    def test_fixed_chunks_respect_size_and_overlap(self):
        chunks = chunk_fixed(DOC, size=80, overlap=20)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 80)
        self.assertLess(chunks[1].start, chunks[0].start + 80)  # overlap

    def test_paragraph_bounds_oversized_blocks(self):
        # audit F4: a document with no blank lines must not become one
        # unbounded chunk.
        doc = Document(id="big", title="T", kind="postmortem",
                       text="word " * 400)          # 2000 chars, single block
        chunks = chunk_paragraph(doc, max_chars=300)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 300)

    def test_fixed_offsets_point_at_actual_text(self):
        # audit F5: rag://doc#offset must not drift by stripped whitespace.
        doc = Document(id="d", title="T", kind="runbook",
                       text="First. " + "x" * 90 + " then more words here now.")
        for chunk in chunk_fixed(doc, size=50, overlap=12):
            self.assertTrue(doc.text[chunk.start:].startswith(chunk.text[:12]))

    def test_unknown_chunker_rejected(self):
        with self.assertRaises(ValueError):
            get_chunker("semantic-magic")


class TestEmbeddings(unittest.TestCase):
    def test_tfidf_is_deterministic(self):
        embedder = TfidfEmbedder()
        embedder.fit([c.text for c in chunk_paragraph(DOC)])
        self.assertEqual(embedder.embed("connection pool memory"),
                         embedder.embed("connection pool memory"))

    def test_similar_text_ranks_above_dissimilar(self):
        embedder = TfidfEmbedder()
        texts = ["database connection pool sizing and memory",
                 "tls certificate renewal and expiry monitoring"]
        embedder.fit(texts)
        query = embedder.embed("how big should the connection pool be")
        self.assertGreater(dot(query, embedder.embed(texts[0])),
                           dot(query, embedder.embed(texts[1])))

    def test_embedder_selection_is_config(self):
        self.assertEqual(get_embedder("tfidf").name, "tfidf")
        self.assertEqual(get_embedder("ollama").name, "ollama")
        with self.assertRaises(ValueError):
            get_embedder("word2vec")


class TestRetriever(unittest.TestCase):
    def test_hybrid_search_returns_attributed_relevant_chunks(self):
        store = RagStore(chunker="paragraph", embedder="tfidf")
        results = store.search("pods OOMKilled after raising connection "
                               "pool size", k=3)
        self.assertTrue(results)
        self.assertTrue(all(r.ref.startswith("rag://") for r in results))
        top_docs = {r.chunk.doc_id for r in results}
        self.assertTrue({"runbook-oom", "runbook-conn-pool"} & top_docs)

    def test_runtime_ingestion_makes_new_doc_retrievable(self):
        store = RagStore(chunker="paragraph", embedder="tfidf")
        before = len(store)
        store.add_document(Document(
            id="postmortem-x", title="Postmortem: zeta cache stampede",
            kind="postmortem",
            text="A zeta cache stampede overwhelmed the quorum leader; "
                 "mitigated by enabling request coalescing on zeta."))
        self.assertGreater(len(store), before)
        docs = store.search_docs("zeta cache stampede coalescing", k=3)
        self.assertIn("postmortem-x", docs)


class TestRetrievalEval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_retrieval_eval()

    def test_metrics_reported_per_strategy(self):
        self.assertEqual(set(cls_r := self.report["strategies"]),
                         {"paragraph", "fixed"})
        for metrics in cls_r.values():
            for key in ("precision_at_1", "precision_at_1_ci95",
                        "precision_at_5", "recall_at_5", "mrr", "chunks",
                        "queries"):
                self.assertIn(key, metrics)
            self.assertGreaterEqual(metrics["queries"], 30)
            lo, hi = metrics["precision_at_1_ci95"]
            self.assertLessEqual(lo, metrics["precision_at_1"])   # point in CI
            self.assertLessEqual(metrics["precision_at_1"], hi)

    def test_gate_reads_the_ci_lower_bound(self):
        # The gate must check the CI lower bound, not the point estimate
        # (audit F6): stricter, and regression-sensitive on a small sample.
        gate = self.report["gate"]
        best = self.report["strategies"][self.report["best"]]
        self.assertEqual(gate["ci_lower_bound"],
                         best["precision_at_1_ci95"][0])
        self.assertGreaterEqual(gate["ci_lower_bound"],
                                RETRIEVAL_PRECISION_GATE)
        self.assertTrue(gate["passed"])

    def test_bootstrap_ci_is_deterministic(self):
        again = run_retrieval_eval()
        self.assertEqual(
            self.report["strategies"]["fixed"]["precision_at_1_ci95"],
            again["strategies"]["fixed"]["precision_at_1_ci95"])

    def test_semantic_track_lifts_paraphrases_when_available(self):
        # Phase L: the free local semantic embedder should reach paraphrase
        # queries a lexical retriever cannot. Reported, never gated — so this
        # skips cleanly in CI (no Ollama) rather than failing.
        semantic = run_semantic_retrieval_eval()
        if not semantic["available"]:
            self.skipTest(f"semantic embedder unavailable: "
                          f"{semantic['reason']}")
        base = max(m["paraphrase_precision_at_1"]
                   for m in self.report["strategies"].values())
        best = max(m["paraphrase_precision_at_1"]
                   for m in semantic["strategies"].values())
        self.assertGreaterEqual(best, base)          # never worse, usually more
        # The gate stays on the deterministic TF-IDF track, not the embedder.
        self.assertNotIn("ollama", str(self.report["gate"]))


class TestWorkflowIntegration(unittest.TestCase):
    def test_runbook_guidance_enters_evidence_with_citation(self):
        incident, env = build_demo_environment()
        run, ctx = run_incident_remediation(incident, **env)
        runbook_evidence = [e for e in ctx.evidence if e.kind == "runbook"]
        self.assertTrue(runbook_evidence)
        for evidence in runbook_evidence:
            self.assertEqual(evidence.citation.source, "aetherops-rag")
            self.assertTrue(evidence.citation.ref.startswith("rag://"))

    def test_postmortem_is_ingested_for_future_retrieval(self):
        incident, env = build_demo_environment()
        paused, ctx = run_incident_remediation(incident, **env)
        done, ctx = run_incident_remediation(
            incident, **env, ctx=ctx,
            approvals={paused.pending_gate: True},
            checkpoint=paused.checkpoint)
        self.assertEqual(done.status, WorkflowStatus.SUCCEEDED)
        self.assertGreater(done.checkpoint["postmortem"]["ingested_chunks"], 0)
        docs = env["rag"].search_docs(incident.title, k=5)
        self.assertIn(f"postmortem-{incident.id}", docs)

    def test_seed_corpus_covers_ten_runbooks(self):
        self.assertGreaterEqual(len(SEED_RUNBOOKS), 10)
        self.assertEqual(len({d.id for d in SEED_RUNBOOKS}),
                         len(SEED_RUNBOOKS))


if __name__ == "__main__":
    unittest.main()
