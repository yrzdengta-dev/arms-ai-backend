# ARMS AI 审核系统

AI 审核服务：把证书 PDF 与订单数据比对，产出审核结论（PASS / REJECT / MANUAL_REVIEW）。

## Language

**抽取层（Extraction）**：
把 PDF 文本抽成结构化「字段表」的一步，只负责取值、不做判断。
_Avoid_: 解析、parse（`parse` 专指 PDF→文本，见下）

**判断层（Judgment）**：
拿字段表与订单快照比对、套合规规则、出审核结论的一步。只判断、不取值。
_Avoid_: 审核（太宽，泛指整条流水线）

**字段表（CertificateFields）**：
抽取层产出的结构化字段集合（如证书号、有效期、签发机构），可落库、可作审计证据、可进人工修正历史。
_Avoid_: 提取结果、extracted data

**文本抽取（parse）**：
PDF 字节 → 纯/保版面文本的一步，由 `PdfParser` Protocol 实现（pypdf 或 LLMWhisperer）。是抽取层的上游输入，不等于抽取层。
