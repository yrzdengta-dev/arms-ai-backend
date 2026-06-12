# 审核规则引擎 — 决策树 Schema 设计文档

> 本文是**规则包(rules.yaml)的契约**,同时也是**采集端的数据契约**。
> 采集、比对器实现、决策树执行器三方都以本文为唯一真相源。
> 适用业务:CPC合规信息 / GCC合规信息,以及未来"换字段同套路"的合规比对类业务。

## 0. 架构:三层复用

```
① 比对器(kernel)   原子纯函数,全业务共用,产出「事实枚举」
        ↑ 被引用
② 比对内核 / 抽取    模板,共用;从报告抽字段 → 调 kernel → 得事实
        ↑ 被实例化
③ 决策树(rules.yaml) 每业务一份;前置分支 + 事实→结论映射 + 汇总
```

**核心原则:结论永远由代码(决策树执行器)产出,LLM 只负责抽取字段与个别模糊比对,产出的永远是结构化事实。** 保证可审计、可复现、规则与代码分离。

---

## 1. 顶层结构

```yaml
skill_id: cpc_compliance          # 唯一标识
version: 1
cert_type: CPC合规信息             # 证书类型
match:                            # 路由:什么订单用这个 skill
  business_type: 合规信息

inputs:                          # 见 §2
extraction:                      # 见 §3
audit_points:                    # 见 §4
conclusion:                      # 见 §5
```

---

## 2. inputs — 来自 ARMS 采集(order_snapshot)

```yaml
inputs:
  compliance:                                       # 合规信息处的值(比对基准)
    effective_date: snapshot.compliance.effective_date
    lab_name:       snapshot.compliance.lab_name
    lab_address:    snapshot.compliance.lab_address   # 仅 GCC
    lab_phone:      snapshot.compliance.lab_phone      # 仅 GCC
    lab_email:      snapshot.compliance.lab_email      # 仅 GCC
  structural:
    linked_cert_status: snapshot.linked_cert_status    # 枚举,见 §6 采集契约
    multi_file: "auto:report_file_count > 1"           # 系统按关联报告文件数自动算,无需采集
```

---

## 3. extraction — 从 PDF 报告抽取(报告内)

```yaml
extraction:
  - field: all_dates
    method: deterministic_dates       # 正则找全部日期 + 多格式归一
  - field: testing_company_name
    method: llm_semantic              # LLM 语义抽取 + 置信度
    confidence_threshold: 0.8
  - field: testing_company_address    # 仅 GCC
    method: llm_semantic
    confidence_threshold: 0.8
  - field: testing_company_phone      # 仅 GCC
    method: llm_semantic
    confidence_threshold: 0.8
  - field: testing_company_email      # 仅 GCC
    method: llm_semantic
    confidence_threshold: 0.8
```

- `deterministic_dates`:解析器吐的全文里,正则召回所有日期 token → 逐个归一为 `YYYY-MM-DD` → 供 kernel 取最晚。
- `llm_semantic`:Schema 驱动,LLM 返回 `{value, confidence}`;低于 `confidence_threshold` 的字段,其审核点判 `MANUAL`(见 §5)。

---

## 4. audit_points — 每个审核点 = 前置分支 + 比对

```yaml
audit_points:
  - id: compare_effective_date
    name: 对比生效日期
    precondition:                     # 按序匹配,命中即短路出结论
      - if: { linked_cert_status: only_same_material_decl }
        result: PASS
      - if: { linked_cert_status: only_certificate }
        result: PASS                  # CPC=PASS;GCC=MANUAL
      - if: { linked_cert_status: no_linked_report }
        result: MANUAL
      - if: { multi_file: true }
        result: MANUAL
      # 都不命中 → 落到 comparison(即 has_test_report)
    comparison:
      kernel: latest_date_equal       # 取 all_dates 最晚 vs effective_date
      report: all_dates
      against: compliance.effective_date
      fact_map:
        equal:     PASS
        not_equal: REJECT
```

**字段语义:**

| 字段 | 含义 |
|---|---|
| `precondition` | 有序列表;逐条 `if` 匹配,第一条命中即返回 `result`,跳过 comparison。可省略。 |
| `comparison.kernel` | 调用的比对器名(§7) |
| `comparison.report` | 喂给 kernel 的报告内抽取字段 |
| `comparison.against` | 比对基准(compliance.* 字段) |
| `comparison.fact_map` | kernel 产出的**事实枚举** → 结论(PASS/REJECT/MANUAL)。**必须覆盖该 kernel 的全部事实值**,否则执行器报配置错误。 |

**结论取值固定为三个:** `PASS`(通过) / `REJECT`(驳回) / `MANUAL`(转人工)。

---

## 5. conclusion — 汇总

```yaml
conclusion:
  aggregate: reject_gt_manual_gt_pass   # 驳回 > 转人工 > 通过
  low_confidence: MANUAL                # 任一抽取字段置信度不足 → 该订单转人工
```

汇总规则:任一审核点 `REJECT` → 整单 `REJECT`;否则任一 `MANUAL` → `MANUAL`;全 `PASS` → `PASS`。

---

## 6. 🔴 采集契约(已据 HAR 抓包逆向核实,2026-06)

> 依据:`arms-pipeline-extension/docs/arms-har-field-analysis.md`

### 6.1 linked_cert_status —— 需独立接口 + 推断,非现成字段

数据**不在** `get_certificate_audit_detail`,来自独立接口:
```
POST /arms/user_audit/certificate_audit/get_relate_certificate_task_order_list
响应:info.data[].relate_certificate_task_order_list   (array)
```
采集器需**新增此调用**,并按下表**推断**出枚举值写入 `order_snapshot.linked_cert_status`:

| 枚举值 | 推断条件 |
|---|---|
| `no_linked_report` | `relate_certificate_task_order_list` 为空数组 `[]` |
| `has_test_report` | 关联项的 `certificate_type_name` 含 "检测报告"/"测试报告" |
| `only_same_material_decl` | 关联项文件名含 "同材质声明",且非检测报告格式 |
| `only_certificate` | 关联项为证书(`is_non_file_certificate=false`,类型为 CPC/GCC 证书) |

> 推断逻辑需用真实样本逐条验证;边界(同时含报告+证书等)按"有检测报告优先"处理,存疑转人工。

### 6.2 证书类型路由(已实锤)

| 业务 | `certificate_type_id` | `certificate_type_name` |
|---|---|---|
| CPC合规信息 | `1302` | CPC合规信息 |
| GCC合规信息 | `1188` | GCC合规信息 |

### 6.3 比对基准字段来源(全部已定位 ✅)

| 字段 | 来源 | 状态 |
|---|---|---|
| `effective_date` | `aca_task_field_dto.certificate_effective_date` | ✅ |
| `multi_file` | `certificate_file_list.length > 1` | ✅ 系统自动算 |
| `lab_name` | 见 §6.4 → `laboratory_name` | ✅ |
| `lab_address` | 见 §6.4 → `contact_address` | ✅ |
| `lab_phone` | 见 §6.4 → `contact_num` | ✅ |
| `lab_email` | 见 §6.4 → `email` | ✅ |

### 6.4 实验室信息 —— 嵌套 JSON 字符串,需 parse

实验室基准信息埋在一个 **JSON 字符串字段**里,采集时需 `JSON.parse` 后取值:
```
aca_task_field_dto.certificate_related_field_info_list[]
  .certificate_relation_value        # 这是个 JSON 字符串
  → JSON.parse →
  [{ laboratory_name, contact_address, contact_num, email, source, area_code }]
```
- 关联码 `certificate_relation_code = "LEID02"` 标识这是实验室信息条目。
- 样例:`laboratory_name = "Europe Ber (Guangdong) Testing Co., Ltd."`(英文,印证 xlsx 中英文/尾缀分支)、
  `contact_num = "0755-23284856"`(区号+主号,印证主号/分机逻辑)。

映射:`lab_name=laboratory_name`、`lab_address=contact_address`、`lab_phone=contact_num`、`lab_email=email`。

### 6.5 当前可实现范围

**全部 5 个审核点数据齐全,均可落地。** 生效日期、关联状态、实验室信息基准、报告内抽取字段四方就绪。
建议仍先把「对比生效日期」做穿作为引擎首个端到端验证,再批量接入实验室四项(它们共用同一套 kernel+决策表模式)。

### 6.6 ARMS 人工审核结果字段(oracle / 外显)

采集时一并存下 **ARMS 里人工真实判的结果**,作为衡量 AI 准确率的基准,并在列表外显/筛选。

**⚠️ 严格区分三个"结论",命名不可混淆:**

| 概念 | 谁判的 | 字段 |
|---|---|---|
| `ai_decision` | 本系统引擎预审建议 | 已有(audit_result.decision) |
| **`arms_audit_*`** | **ARMS 里人工实判(外部真相)** | 🆕 本节 |
| `human_decision` | 本系统内人工修正 | 已有(correction) |

**字段设计(独立索引列,不入 order_snapshot JSON,便于外显+筛选):**

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `arms_audit_status` | int/str | `aca_task_field_dto.certificate_audit_status`(0未审/1已审) | 粗粒度审核状态 |
| `arms_audit_result` | str\|null | ARMS 审核记录的结论(通过/驳回/…) | 细粒度结论,真正的 oracle;若接口无则留空 |
| `arms_reject_reason` | str\|null | ARMS 驳回原因 | 驳回时的原因文本 |
| `arms_status_synced_at` | datetime | 采集时刻 | **外部状态快照时间**,重采时刷新 |

**用途:** ① 列表外显 + 按审核状态筛选;② 建「AI 建议 vs ARMS 实判」一致率看板,指导规则调优。
**性质:** 外部状态快照,采集后 ARMS 仍可能变化;以 `arms_status_synced_at` 标记时点,不作永久权威。

---

## 7. 比对器(kernel)契约 — 产出事实枚举

每个 kernel 是纯函数:`(report_value(s), against_value) -> fact: str`。容差逻辑全部封装在 kernel 内部,YAML 只做 `fact_map`。

| kernel | 输入 | 产出的事实枚举 | 容差逻辑(封装在内部) |
|---|---|---|---|
| `latest_date_equal` | all_dates[], 目标日期 | `equal` / `not_equal` | 取 all_dates 最晚,多格式归一后比对 |
| `company_name_equal` | 公司名, 实验室名 | `compliance_chinese` / `equal` / `differ_only_suffix` / `differ_core` | 中文判定;剥企业尾缀(Co./Ltd./Inc./Limited/PLC…)后比核心 |
| `address_equal` | 地址, 实验室地址 | `compliance_chinese` / `equal` / `differ_only_floor_room` / `differ_other` | 中文判定;拆楼层/房间号成分,其余比对 |
| `phone_equal` | 电话, 实验室电话 | `not_found` / `equal` / `main_eq_no_ext` / `main_eq_ext_eq` / `main_eq_ext_diff` / `main_diff` | 抠数字拆主号/分机 |
| `email_equal` | 邮箱, 实验室邮箱 | `not_found` / `equal` / `not_equal` | 精确比对 |

新增 kernel = 新增一个纯函数 + 单测,登记到比对器注册表,全业务即可引用。

---

## 8. 规则维护约定

- 规则变更只改 rules.yaml,**不改引擎代码**。
- 决策树就老实每业务写一份,不强求 DRY——可读性优先(规则由开发维护)。
- `fact_map` 必须覆盖 kernel 全部事实值;执行器启动时校验,缺值即报错。
- 引擎封顶在「决策表解释器 + 比对器注册表」,不建通用谓词 DSL / 规则编辑 UI(等真有结构完全不同的业务再议)。

---

## 9. 落地顺序(参考)

1. 采集端补齐 §2/§6 字段 + 抽 5–10 份真实报告样本(进行中,由业务方)
2. PyMuPDF 解析器适配器(实现现有 `PdfParser` Protocol)
3. **比对器(§7,纯函数 + 单测)** ← 不依赖采集/LLM,最先做,/tdd
4. 决策树执行器 + 本 schema 的校验
5. cpc/gcc 两份 rules.yaml
6. LLM 抽取层接 openai_provider
7. 端到端:真实订单跑通 采集 → 抽取 → 判定 → 结论
