---
status: accepted
---

# 用 Docling 做多排版文本抽取，不引入 Unstract 平台

## 决定

最初评估 Unstract，但真实诉求只有一个：**一个兼容多种排版的文本抽取工具**，而非一整套抽取平台。

因此：

1. **文本抽取引擎**选 **Docling**（IBM，本地 Python 库），作为 `PdfParser` Protocol 的新实现顶替 pypdf。理由：本地运行、证书不出域（契合 SSRF / Key 不硬编码的安全基线）、对表格/多栏/盖章件等多排版鲁棒，且**内置 OCR**——后期需要 OCR 时开个开关即可，无需二次更换工具。
2. **不引入 Unstract 平台**，也不接其托管的 LLMWhisperer API（会把证书发到第三方，破坏数据不出域基线）。
3. 维持独立**抽取层（Extraction）**：`PDF →(Docling)文本 → schema 约束抽取 → CertificateFields 字段表 → 判断层`。字段表可落库、可作审计证据、可进人工修正历史。抽取层复用现有 `openai_provider` + 一个 Pydantic schema，不依赖任何外部平台。

## 背景与权衡

需求：应对证书 PDF 的不同排版与不同日期格式，产出精确字段表；后期可能用到 OCR。

抽取工具候选（均为单点工具，非平台）：

- **LLMWhisperer**（Unstract 抽取单品，托管 API）—— 强，但证书需出域。除非明确要托管，否则破坏安全基线。
- **Docling（选定）** —— 本地、多排版鲁棒、内置 OCR、可包成 `PdfParser`，零新基础设施、零数据出域。
- **Marker** —— 本地、强，但偏 PDF→markdown，GPU 依赖更重，不及 Docling 贴合"字段表"路线。
- **PyMuPDF** —— 本地、比 pypdf 强，但**无 OCR**，后期 OCR 需求会迫使二次迁移，淘汰。

关键认知：现栈已具备结构化 JSON 输出能力（`openai_provider.py` 的 `response_format: json_object` + `AuditOutput` 校验），缺的不是"产出 JSON"，而是把抽取从判断拆开 + 一个比 pypdf 更抗排版的文本源。日期格式归一化是 LLM 本身能力，与抽取工具无关。整套 Unstract 自带 Django/PG/Redis/MinIO，与现有后端完全重复，纯负担。

## 后果

- 抽取与判断成为两层，延迟与 token 成本上升；换来字段表可审计、可修正、文本源可替换。
- Docling 引入本地依赖（含可选 OCR 模型权重），需纳入构建/镜像体积考量。
- OCR 暂不启用，作为 Docling 的后期开关；启用时无需更换工具或迁移架构。
- 文本源被 `PdfParser` Protocol 隔离，未来若要改用其他引擎（含托管 API）切换成本低、无平台锁定。
