"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None          (TODO KG-1)
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j      (TODO KG-2)
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts               (TODO KG-3)
    GraphRAGAgent.answer(question, top_k)         -> str                           (TODO KG-4)

Everything else in this file is a HINT: one possible ontology (below). Use it as is, change it,
or design your own — your own ontology + report/ONTOLOGY.md earns the bonus (see SUBMISSION.md).

Suggested ontology (Crime is the bridge between the law KB and the news KB):

    (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
    (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text})-[:MENTIONS]->(:Substance {name})
    (:Case {name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
    (:Case)-[:INVOLVES {amount}]->(:Substance)
    (:Case)-[:LOCATED_IN]->(:Location {name})
    (:Person {name, aliases})-[:INVOLVED_IN {role, sentence, charge}]->(:Case)
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    if not name or not name.strip() or not known:
        return None
    norm_name = normalize(name)
    if not norm_name:
        return None

    norm_to_orig: dict[str, str] = {}
    for k in known:
        nk = normalize(k)
        if nk not in norm_to_orig:
            norm_to_orig[nk] = k

    if norm_name in norm_to_orig:
        return norm_to_orig[norm_name]

    matches = difflib.get_close_matches(norm_name, list(norm_to_orig.keys()), n=1, cutoff=0.8)
    if matches:
        return norm_to_orig[matches[0]]

    return None

def find_substances(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in SUBSTANCES if name.lower() in lowered]

# ----------------------------------------------------------------------------------------------
# HINT — suggested ontology: extraction helpers
# ----------------------------------------------------------------------------------------------

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata.get("article", "")
    title_match = re.search(r"^(?:#\s*)?(Điều\s+\d+[^.\n]*)\.\s*([^\n]+)", doc.content, re.MULTILINE)
    title = doc.metadata.get("title", "").split(". ", 1)[-1]
    if title_match:
        article_id = article_id or title_match.group(1).strip()
        title = title or title_match.group(2).strip()

    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0] if text else ""
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        penalty_text = penalty.group(1).rstrip(".") if penalty else ""

        # Extract defined legal terms (e.g. Điều 2 Luật PCMT: "4. Tiền chất là...")
        term_m = re.search(r"^\d+\.\s*([^\n]+?)\s+là\b", first_line)
        terms = [term_m.group(1).strip().lower()] if term_m and len(term_m.group(1).strip()) <= 60 else []

        severity = 0
        if "tử hình" in penalty_text.lower():
            severity = 4
        elif "chung thân" in penalty_text.lower():
            severity = 3
        elif "20 năm" in penalty_text.lower():
            severity = 2
        elif penalty_text:
            severity = 1

        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty_text,
            "severity": severity,
            "text": text,
            "substances": find_substances(text),
            "terms": terms,
        })

    max_sev = max((c["severity"] for c in clauses), default=0)
    for c in clauses:
        c["is_max_penalty"] = (c["severity"] == max_sev and max_sev > 0)

    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

NEWS_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ mua bán 36kg ma túy tại TP.HCM",
  "summary": "1-2 câu tóm tắt",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp", "amount": "khối lượng nếu có"}}],
  "people": [{{"name": "họ tên", "aliases": ["biệt danh"], "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án nếu có, ví dụ: tử hình, 8 năm tù"}}]
}}]}}
Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""

def extract_news_cases(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """LLM extraction for one news article; charges are re-linked to law-KB crimes in code."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        raw = llm_fn(prompt)
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else ""
                raw = raw.rsplit("```", 1)[0].strip()
        cases = json.loads(raw).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
    return cases

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- HINT — suggested ontology: writes

    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name"),
                           ("LegalTerm", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text,
                  cl.doc_id = $doc_id, cl.severity = clause.severity, cl.is_max_penalty = clause.is_max_penalty
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            FOREACH (t IN clause.terms |
                MERGE (term:LegalTerm {name: t})
                  SET term.definition = clause.text, term.doc_id = $doc_id
                MERGE (cl)-[:EXPLAINS]->(term)
            )
            """,
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, []), person.doc_id = $doc_id
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence
                FOREACH (ch IN CASE WHEN p.charge = '' THEN [] ELSE [p.charge] END |
                    MERGE (pc:Crime {name: ch})
                    MERGE (person)-[:CHARGED_WITH]->(pc)
                )
            )
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Graph facts for a question: legal definitions, case facts, multi-hop legal clauses, and seed facts."""
        facts: list[str] = []
        seed_ids, raw_edge_facts = self.seed_facts(question, doc_ids, limit=15)

        # 1. Legal terms: check if question asks about or mentions any legal definition
        term_rows = self.run(
            """
            MATCH (cl:Clause)-[:EXPLAINS]->(t:LegalTerm)
            WHERE toLower($q) CONTAINS toLower(t.name)
            RETURN DISTINCT t.name AS term, cl.id AS clause_id, cl.text AS text
            """,
            q=question,
        )
        for row in term_rows:
            fact = f"[Định nghĩa pháp lý - {row['term']}] {row['clause_id']}: {row['text']}"
            if fact not in facts:
                facts.append(fact)

        # 2. Cases and people relevant to question or seed documents
        case_rows = self.run(
            """
            MATCH (k:Case)
            WHERE elementId(k) IN $ids
               OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
               OR (k.name IS :: STRING AND toLower($q) CONTAINS toLower(k.name))
               OR EXISTS {
                    MATCH (p:Person)-[:INVOLVED_IN]->(k)
                    WHERE (p.name IS :: STRING AND size(p.name) >= 3 AND toLower($q) CONTAINS toLower(p.name))
                       OR any(a IN coalesce(p.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
               }
            OPTIONAL MATCH (k)-[rsub:INVOLVES]->(sub:Substance)
            OPTIONAL MATCH (p:Person)-[r:INVOLVED_IN]->(k)
            RETURN DISTINCT elementId(k) AS id, k.name AS name, k.summary AS summary,
                   collect(DISTINCT {name: sub.name, amount: rsub.amount}) AS substances,
                   collect(DISTINCT {name: p.name, role: r.role, sentence: r.sentence, charge: r.charge}) AS people
            """,
            ids=seed_ids, q=question,
        )
        case_ids = [row["id"] for row in case_rows]
        for row in case_rows:
            if row.get("name") and row.get("summary"):
                sub_info = [f"{s['name']}{(' (' + s['amount'] + ')') if s.get('amount') else ''}" for s in row.get("substances", []) if s.get("name")]
                sub_str = f" [Tang vật: {', '.join(sub_info)}]" if sub_info else ""
                fact = f"Vụ việc '{row['name']}': {row['summary']}{sub_str}"
                if fact not in facts:
                    facts.append(fact)
            for p in row.get("people", []):
                if p.get("name") and (p.get("sentence") or p.get("charge")):
                    details = []
                    if p.get("charge"):
                        details.append(f"tội: {p['charge']}")
                    if p.get("sentence"):
                        details.append(f"mức án: {p['sentence']}")
                    p_fact = f"Người liên quan: {p['name']} ({', '.join(details)}) trong vụ '{row['name']}'"
                    if p_fact not in facts:
                        facts.append(p_fact)

        # 3. For those cases follow: (Case/Person)-[:CHARGED_WITH]->(Crime)<-[:DEFINES]-(Article)-[:HAS_CLAUSE]->(Clause)
        # keep clause 1 + clauses that MENTION a Substance the case INVOLVES + highest clause / max penalty
        q_subs = [s.lower() for s in find_substances(question)]
        clause_rows = self.run(
            """
            MATCH (target)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE elementId(target) IN $case_ids
               OR (target:Person AND (
                    elementId(target) IN $seed_ids
                    OR (target.name IS :: STRING AND size(target.name) >= 3 AND toLower($q) CONTAINS toLower(target.name))
                    OR any(a IN coalesce(target.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
                  ))
            WITH a, cl, target
            WHERE cl.number = 1
               OR cl.is_max_penalty = true
               OR (cl.penalty IS NOT NULL AND (cl.penalty CONTAINS 'chung thân' OR cl.penalty CONTAINS 'tử hình'))
               OR EXISTS { MATCH (target)-[:INVOLVES]->(s:Substance)<-[:MENTIONS]-(cl) }
               OR EXISTS { MATCH (target)-[:INVOLVED_IN]->(:Case)-[:INVOLVES]->(s:Substance)<-[:MENTIONS]-(cl) }
               OR any(qs IN $q_subs WHERE EXISTS { MATCH (cl)-[:MENTIONS]->(sub:Substance) WHERE toLower(sub.name) = toLower(qs) })
               OR NOT EXISTS { MATCH (a)-[:HAS_CLAUSE]->(other:Clause) WHERE other.number > cl.number }
            RETURN DISTINCT a.id AS article_id, a.title AS title, cl.number AS number, cl.text AS text
            ORDER BY a.id, cl.number
            """,
            case_ids=case_ids, seed_ids=seed_ids, q=question, q_subs=q_subs,
        )
        for row in clause_rows:
            fact = f"[{row['article_id']} - {row['title']}] khoản {row['number']}: {row['text']}"
            if fact not in facts:
                facts.append(fact)

        # 4. Articles named directly in the question (e.g. "Điều 251" -> re.findall(r"[Đđ]iều (\d+)", question)):
        art_nums = re.findall(r"[Đđ]iều\s*(\d+)", question)
        if art_nums:
            question_substances = [s.lower() for s in find_substances(question)]
            for num in art_nums:
                pattern = f"Điều {num}"
                direct_clauses = self.run(
                    """
                    MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                    WHERE a.id CONTAINS $pattern
                    RETURN DISTINCT a.id AS article_id, a.title AS title, cl.number AS number, cl.text AS text, cl.is_max_penalty AS is_max_penalty
                    ORDER BY cl.number
                    """,
                    pattern=pattern,
                )
                for row in direct_clauses:
                    is_substance_match = any(sub in row["text"].lower() for sub in question_substances) if question_substances else False
                    if row["number"] == 1 or is_substance_match or row.get("is_max_penalty") or len(direct_clauses) <= 5:
                        fact = f"[{row['article_id']} - {row['title']}] khoản {row['number']}: {row['text']}"
                        if fact not in facts:
                            facts.append(fact)

        # 5. Substances named in the question (e.g. Q6 "liên quan đến ma túy MDMA")
        if q_subs:
            sub_cases = self.run(
                """
                MATCH (k:Case)-[r:INVOLVES]->(s:Substance)
                WHERE any(qs IN $subs WHERE toLower(s.name) = toLower(qs))
                OPTIONAL MATCH (p:Person)-[:INVOLVED_IN]->(k)
                RETURN k.name AS case_name, k.summary AS summary, s.name AS substance, r.amount AS amount, collect(DISTINCT p.name) AS people
                """,
                subs=q_subs,
            )
            for row in sub_cases:
                people_str = f", người liên quan: {', '.join(row['people'])}" if row.get("people") else ""
                amount_str = f" (khối lượng: {row['amount']})" if row.get("amount") else ""
                fact = f"Vụ án liên quan chất {row['substance']}{amount_str}: '{row['case_name']}' - {row['summary']}{people_str}"
                if fact not in facts:
                    facts.append(fact)

            # Also include the Articles & Clauses mentioning this substance
            sub_clauses = self.run(
                """
                MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)-[:MENTIONS]->(s:Substance)
                WHERE any(qs IN $subs WHERE toLower(s.name) = toLower(qs))
                RETURN DISTINCT a.id AS article_id, cl.number AS number
                ORDER BY a.id, cl.number
                """,
                subs=q_subs,
            )
            if sub_clauses:
                art_clause_summary = ", ".join(f"{r['article_id']} khoản {r['number']}" for r in sub_clauses)
                fact = f"Các điều khoản luật có đề cập đến chất {', '.join(q_subs)}: {art_clause_summary}"
                if fact not in facts:
                    facts.append(fact)

        # 6. Append 1-hop seed edges to fill remaining facts up to max_facts
        for ef in raw_edge_facts:
            if ef not in facts:
                facts.append(ef)

        return facts[:max_facts]

# ---------------------------------------------------------------------------------------------- KG-2

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load both KBs into an empty graph. llm_fn(prompt, json_mode=False) -> str (metered OpenAI chat)."""
    graph.suggested_constraints()
    articles = [parse_law_article(d) for d in law_docs]
    for a in articles:
        graph.add_law_article(a)
    crimes = [a["crime"] for a in articles if a["crime"]]
    for d in news_docs:
        for case in extract_news_cases(d, lambda p: llm_fn(p, json_mode=True), crimes):
            graph.add_news_case(case, d)

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)
        doc_ids: list[str] = []
        for chunk in chunks:
            doc_id = chunk.get("metadata", {}).get("doc_id")
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)
        facts = self.graph.context(question, doc_ids)
        chunks_str = "\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1))
        facts_str = "\n".join(f"- {fact}" for fact in facts) if facts else "Không có dữ kiện từ graph."
        prompt = GRAPH_PROMPT.format(facts=facts_str, chunks=chunks_str, question=question)
        return self.llm_fn(prompt)
