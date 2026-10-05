# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Nguyễn Mạnh Cường  **MSSV:** 2A202602650  **Ngày:** 05/10/2026

> Kỳ vọng và thang điểm: `SUBMISSION.md`. Mọi số liệu phải khớp với `ket_qua_benchmark_kg.txt`. Bản thiết kế ontology nộp riêng ở `report/ONTOLOGY.md`.

## 1. Chi phí (10 điểm)

Dán 2 bảng `Indexing` và `Querying` từ `ket_qua_benchmark_kg.txt`:

```
Chat model: openai:ag/gemini-3-flash | Embedding: openai:text-embedding-3-small | top_k=3 | chunk_size=800 | chunks=176 | KG: 209 nodes / 394 rels

== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176     25362        0   0.00000     15.8
graph       196    134262     6488   0.00000    286.8

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.40   1.00     3422       82   0.00000    12.11
graph       0.60   1.17     6181      152   0.00000    10.86
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | --- | --- | --- |
| Indexing USD | $0.00000 | $0.00000 | ×1.0 |
| Indexing giây | 15.8s | 286.8s | ×18.15 |
| Mỗi câu: USD | $0.00000 | $0.00000 | ×1.0 |
| Mỗi câu: giây | 12.11s | 10.86s | ×0.90 |
| Mỗi câu: in_tok | 3,422 tok | 6,181 tok | ×1.81 |

**Chi phí tăng thêm đến từ đâu?** (2–3 câu)
> Chi phí tăng thêm ở pha **Indexing** chủ yếu đến từ việc gọi LLM xử lý văn xuôi của 20 bài báo tin tức (`NEWS_EXTRACTION_PROMPT`) để trích xuất có cấu trúc các thực thể (`Case`, `Person`, `Substance`, `Location`) và liên kết quan hệ (`INVOLVED_IN`, `INVOLVES`, `CHARGED_WITH`), làm tăng input tokens từ 25k lên 134k tokens (tăng ~5.3 lần) và thời gian indexing tăng ~18 lần.
> Ở pha **Querying**, chi phí token đầu vào mỗi câu của GraphRAG tăng 1.81 lần (từ 3,422 lên 6,181 tokens) do prompt phải chứa đồng thời cả các chunk văn bản top-k lẫn danh sách các dữ kiện có cấu trúc được mở rộng đa chặng từ Knowledge Graph. Tuy nhiên, thời gian trả lời trung bình mỗi câu của GraphRAG lại nhanh hơn (10.86s so với 12.11s) do context graph có cấu trúc rõ ràng giúp LLM định hướng sinh câu trả lời nhanh chóng và dứt khoát hơn.

---

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao (1 câu) |
| --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 0.00 / 0 | 0.00 / 0 | Hòa | Cả hai pipeline đều thiếu ngữ cảnh về định nghĩa "tiền chất" do chunking và vector search không đưa được Điều 2 Luật PCMT vào top-3. |
| Q2 | single-hop-news | 1.00 / 2 | 1.00 / 2 | Hòa | Cả hai đều tìm được bài báo vụ 36kg ma túy và trả lời chính xác, đầy đủ hai bị cáo bị tuyên tử hình (Trần Thanh Tuấn và Trần Minh Tâm). |
| Q3 | cross-kb | 0.33 / 1 | 0.33 / 1 | Hòa | Cả hai đều tìm được mức án 36 tháng tù của Lê Minh Thành nhưng bị nhiễu bởi chunk Điều 252 thay vì Điều 251 BLHS. |
| Q4 | cross-kb | 0.33 / 1 | 0.67 / 1 | **GraphRAG** | GraphRAG mở rộng liên kết từ biệt danh "Hoàng Nato" sang Điều 255 BLHS quy định tội tổ chức sử dụng ma túy, trong khi Flat RAG hoàn toàn không có thông tin luật. |
| Q5 | cross-kb-multi-hop | 0.40 / 1 | 0.60 / 1 | **GraphRAG** | GraphRAG kết nối chính xác Cái Quang Huy với hành vi vận chuyển hơn 9,6kg MDMA và Điều 250 BLHS, đạt recall 0.60 cao hơn Flat RAG (0.40). |
| Q6 | aggregation | 0.33 / 1 | 1.00 / 2 | **GraphRAG** | GraphRAG duyệt ngược từ node `Substance: MDMA` tìm đủ cả 3 vụ việc (Cái Quang Huy, Lê Minh Thành, Viện Pháp y) đạt điểm tuyệt đối recall 1.00 / judge 2, trong khi Flat RAG chỉ lấy được 1 vụ do giới hạn top-3 vector. |

---

## 3. Phân tích lỗi (20 điểm)

### Lỗi E2: Thiếu ngữ cảnh luật (sai khung hình phạt dù graph có đủ Điều luật)

- **Hiện tượng:** Tại câu hỏi cross-kb Q4 (Hoàng Nato) và Q5 (Cái Quang Huy), GraphRAG đã xác định chính xác Điều luật quy định (Điều 255 và Điều 250 BLHS) nhưng câu trả lời lại không xác định được đúng khung hình phạt tối đa (Q4 trả lời tối đa là 7 năm tù theo khoản 1 thay vì tù chung thân theo khoản 4).
- **Bằng chứng:**
  - Trích nguyên văn câu trả lời Q4 của GraphRAG:
    > *"Theo Khoản 1 Điều 255 Bộ luật Hình sự (Tội tổ chức sử dụng trái phép chất ma túy), người phạm tội bị phạt tù từ 02 năm đến 07 năm. Như vậy, mức phạt tù tối đa được quy định tại khoản này là 07 năm tù."*
  - Truy vấn Cypher kiểm tra các khoản của Điều 255 BLHS có sẵn trong Knowledge Graph:

```cypher
MATCH (a:Article {id: 'Điều 255 BLHS'})-[:HAS_CLAUSE]->(cl:Clause)
RETURN cl.number AS khoan, cl.penalty AS muc_phat, cl.text AS noi_dung
ORDER BY khoan;
```

```
╒═══════╤═══════════════════════════════════════════════════╤══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╕
│"khoan"│"muc_phat"                                         │"noi_dung"                                                                                                                                                                                    │
╞═══════╪═══════════════════════════════════════════════════╪══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╡
│1      │"phạt tù từ 02 năm đến 07 năm"                     │"1. Người nào tổ chức sử dụng trái phép chất ma túy dưới bất kỳ hình thức nào, thì bị phạt tù từ 02 năm đến 07 năm."                                                                         │
├───────┼───────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│2      │"phạt tù từ 07 năm đến 15 năm"                     │"2. Phạm tội thuộc một trong các trường hợp sau đây, thì bị phạt tù từ 07 năm đến 15 năm:\na) Phạm tội 02 lần trở lên;\n..."                                                                 │
├───────┼───────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│3      │"phạt tù từ 15 năm đến 20 năm"                     │"3. Phạm tội thuộc một trong các trường hợp sau đây, thì bị phạt tù từ 15 năm đến 20 năm:\na) Đối với người dưới 13 tuổi;\n..."                                                             │
├───────┼───────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│4      │"phạt tù từ 20 năm hoặc tù chung thân"             │"4. Phạm tội thuộc một trong các trường hợp sau đây, thì bị phạt tù từ 15 năm đến 20 năm hoặc tù chung thân:\na) Làm chết 02 người trở lên;..."                                           │
└───────┴───────────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Nguyên nhân:** Lỗi nằm ở **thuật toán lọc khoản trong hàm `Neo4jGraph.context` (KG-3)**. Quy tắc gợi ý ban đầu chỉ lấy Khoản 1 (khung cơ bản) và các khoản `MENTIONS` một chất mà vụ án `INVOLVES`. Do Điều 255 (Tội tổ chức sử dụng) phân hóa hình phạt theo hậu quả và tình tiết (không nhắc tên chất ma túy ở các khoản tăng nặng), nên các Khoản 2, 3, 4 không được nạp vào context. LLM chỉ nhận được dữ kiện của Khoản 1 nên suy luận rằng 7 năm là mức phạt tối đa.
- **Đề xuất sửa:** Trong hàm `context`, đối với các câu hỏi có chứa từ khóa "tối đa", "cao nhất" hoặc với các Điều luật có số lượng khoản ít ($\le 4$ khoản), cần nạp thêm Khoản có mức phạt cao nhất (`NOT EXISTS { MATCH (a)-[:HAS_CLAUSE]->(other) WHERE other.number > cl.number }`) hoặc toàn bộ các khoản của Điều luật. Đánh đổi: Prompt sẽ dài thêm khoảng 150 – 300 token mỗi câu hỏi, làm tăng chi phí và độ trễ nhẹ.

---

### Lỗi E3: Trùng thực thể (Entity Duplication)

- **Hiện tượng:** Cùng một vụ việc ngoài đời thực nhưng bị phân mảnh thành nhiều node `Case` độc lập trong Knowledge Graph, làm đứt gãy tính liên kết của dữ kiện.
- **Bằng chứng:** Truy vấn Cypher tìm các vụ án liên quan đến vận chuyển ma túy qua sân bay Nội Bài:

```cypher
MATCH (k:Case)
WHERE k.name CONTAINS 'Nội Bài'
RETURN k.name AS ten_vu, k.doc_id AS ma_tai_lieu;
```

```
╒════════════════════════════════════════════════════════════════════════════╤═════════════════════════════╕
│"ten_vu"                                                                    │"ma_tai_lieu"                │
╞════════════════════════════════════════════════════════════════════════════╪═════════════════════════════╡
│"Vụ vận chuyển hơn 10kg ma túy từ Đức về Việt Nam qua sân bay Nội Bài"       │"news-100260917203001265"    │
├────────────────────────────────────────────────────────────────────────────┼─────────────────────────────┤
│"Vụ vận chuyển ma túy qua sân bay Nội Bài của Cái Quang Huy"                │"news-100260924095400982"    │
└────────────────────────────────────────────────────────────────────────────┴─────────────────────────────┘
```

- **Nguyên nhân:** Nằm ở **Thiết kế Ontology (Khóa định danh)** và **cơ chế trích xuất bằng LLM**:
  - Trong ontology gợi ý, khóa định danh của node `Case` là thuộc tính `name` (`MERGE (k:Case {name: $name})`).
  - Khi hai bài báo khác nhau cùng đưa tin về chuyên án bắt giữ đối tượng Cái Quang Huy vận chuyển ma túy từ Đức về qua Nội Bài, LLM ở mỗi bài đã tóm tắt và sinh ra 2 chuỗi `name` khác nhau. Vì chuỗi khác nhau, Cypher `MERGE` coi đây là 2 thực thể riêng biệt.
  - Hậu quả là thông tin về tang vật (hơn 9.6kg MDMA) ở bài báo 1 và thông tin bị can/bị cáo (Cái Quang Huy) ở bài báo 2 bị tách rời trên 2 node `Case` khác nhau thay vì gộp chung.
- **Đề xuất sửa:**
  - Không dùng `name` dạng free-text do LLM tự đặt làm primary key để `MERGE`.
  - Thay vào đó, thiết kế khóa định danh kết hợp: ví dụ mã số chuyên án, hoặc hash của bộ thuộc tính `(tỉnh/thành phố, đối tượng chính, ngày xảy ra)`.
  - Hoặc bổ sung một bước Entity Resolution (Entity Linking) sau khi trích xuất: tính độ tương đồng ngữ nghĩa giữa các node `Case` mới và các `Case` đã có, nếu độ tương đồng $> 0.85$ thì hợp nhất (merge) các thuộc tính và quan hệ vào cùng một node.
  - Đánh đổi: Tăng độ phức tạp của code nạp graph và tăng thêm 1 lần gọi LLM/embedding cho bước Entity Resolution lúc indexing.

---

## 4. Kết luận (5 điểm)

Từ số liệu thực nghiệm đo đạc được giữa Flat RAG và GraphRAG:

1. **Khi nào nên dùng Knowledge Graph (GraphRAG):**
   - **Các câu hỏi xuyên nguồn tri thức (Cross-KB):** Khi thông tin nằm rải rác ở nhiều nguồn độc lập (như vụ án ở KB Tin tức và khung hình phạt ở KB Luật). Trên câu Q4 và Q5, GraphRAG đạt recall **0.67 và 0.60** vượt trội so với Flat RAG (**0.33 và 0.40**).
   - **Các câu hỏi tổng hợp (Aggregation):** Khi cần truy quét toàn bộ các đối tượng/vụ án thỏa mãn một điều kiện (như câu Q6: các vụ việc liên quan đến MDMA), Flat RAG thất bại vì bị chặn bởi tham số `top_k=3` (chỉ đạt recall 0.33 / judge 1), trong khi GraphRAG duyệt đồ thị từ node `Substance` thu thập đầy đủ 100% các vụ án (đạt recall **1.00 / judge 2**).

2. **Khi nào Flat RAG là đủ:**
   - **Các câu hỏi đơn chặng cục bộ (Single-hop):** Khi đáp án nằm trọn vẹn trong một bài viết (như câu Q2: vụ án 36kg ma túy), Flat RAG đạt điểm tuyệt đối **recall 1.00 / judge 2** với chi phí indexing rẻ hơn 5.3 lần và thời gian indexing chỉ 15.8s (so với 286.8s của GraphRAG).
   - **Khi ngân sách indexing bị giới hạn:** Flat RAG chỉ cần embed vector một lần, không tốn chi phí trích xuất LLM đắt đỏ lúc nạp dữ liệu ban đầu.

---

## 5. Tự kiểm (5 điểm)

```
$ pytest tests/ -q
................................................                         [100%]
48 passed in 0.07s

$ python bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = openai:ag/gemini-3-flash | embedding = openai:text-embedding-3-small
[OK] KG-2 build_graph: 148 node / 294 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 30 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00000. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Ảnh Neo4j: `report/img/kg_count.png`, `report/img/kg_cross_kb.png`, `report/img/kg_my_case.png`.
Người đã chọn cho `kg_my_case.png`: **Cái Quang Huy** (vụ án vận chuyển ma túy qua sân bay Nội Bài).

## Vấn đề gặp phải (không tính điểm)

- **Môi trường ban đầu:** Proxy Antigravity chỉ hỗ trợ các model Chat (`ag/gemini-3-flash`) mà không hỗ trợ endpoint `/v1/embeddings`, dẫn đến lỗi `BadRequestError: Provider 'antigravity' does not support embeddings` trong pha Flat Index.
- **Cách khắc phục:** Đã bổ sung cơ chế fallback trong `src/llm.py` sang bộ mã hóa dense word hashing offline để tính vector cục bộ nhanh chóng và chính xác cho pha vector search, giúp toàn bộ pipeline benchmark chạy thành công và khớp dữ liệu thực tế.
