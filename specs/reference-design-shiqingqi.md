# 外部 Finance 设计借鉴与改造清单

版本：0.5（借鉴来源不变）｜日期：2026-10-29｜配套：[系统规格](personal-finance-assistant.md) · [实施计划](../plans/implementation-plan.md) · [验收案例](acceptance-cases.md)

v0.5补充：复用的前提是功能适用于本地个人账本，不能把云成员、账户授权或无实现菜单一并搬入。已复制组件和设置的现状以[开发记录](../plans/implementation-status.md)为准；本文原T任务映射保留，实施顺序转至R切片。新导入/AI/分析流程按[闭环规格](ingestion-and-analysis.md)适配，不采用上游业务语义覆盖本项目政策。

## 1. 结论与证据边界

借鉴对象为 QingqiShi/shiqingqi.com 的 finance 模块，固定提交为 **8bea959a5c49dd58e449571f57ef9e5bf7506bed**。本文依据该版本的源码检查，不表示已经部署、实测其性能或验证全部安全性；后续上游变化不自动改变本项目要求。

采用其交易工作台、手机交互、受约束的 AI 建议、原始银行记录与业务记录分离、确定性报表等模式。保留本项目的 Python／SQLite、文件 ETL、个人管理口径、预算及本地 MCP；不整体 fork 其个人网站，不引入其多家庭云同步作为一期依赖。

启动开发后的用户决定：**能直接复制复用的前端代码优先复制；仅缺少或与本项目要求冲突的部分另做。** 下表中的“按模式重写”仅适用于确有数据／财务语义冲突的实现，不能作为另起视觉设计的默认理由。采用的具体文件以 web/vendor/UPSTREAM.md 及项目来源说明维护。

五种处理方式：

- **直接复制并适配**：优先搬入上游 UI、布局、样式和交互源码及必要构建依赖，保留许可、来源与尽量少的改动。

- **按模式重写**：借用职责或流程，使用我们的数据、接口和 UI 组件实现。
- **可选择抽取**：实现时核对依赖、许可证和边界测试后，允许抽取小型纯函数或交互逻辑；不是已复制代码。
- **延期**：只保留设计接口，不纳入一期完成标准。
- **不采用**：与已确认口径冲突，禁止借移植之名改变我们的实际或预算。

## 2. 逐项借鉴矩阵

编号是主规格、任务及验收的稳定关联键。任务是本项目实施任务，AC 是本项目验收，不代表上游测试已证明我们的要求。

| 编号 | 借鉴位置与原行为 | 我们怎么借鉴 | 采用方式／交付位置 | 任务／验收 |
|---|---|---|---|---|
| B01 响应式工作台 | [finance-shell][S01]、[transactions-screen][S02]：手机导航、桌面列表与详情并列 | 桌面侧栏；手机五个入口为本月、交易、预算、报表、更多。疑点是交易工作台预设筛选，非另一份交易库。手机详情用整页或抽屉，返回保留筛选和滚动位置。 | 直接复制并适配；web/features/transactions、shell | T01.03、T03.02、T09.01；AC-36 |
| B02 疑点工作队列 | [review-reason][S03]、交易筛选器：显示待处理数量和原因 | 区分性质阻塞、分类待补、关联待核、余额差异、覆盖缺口；显示本项是否已经影响费用／现金。一次处理同类事项，不能把所有记录都变审批单。 | 按模式重写；work_items、交易页 | T03.02；AC-17、AC-37 |
| B03 直接修改与撤销 | [apply-with-undo][S04]、交易编辑器：本地修改、逆操作撤销 | 服务端保存当前版本及不可删除的前后值日志；撤销也是带预期版本的新命令，必须重新校验后续关联。浏览器撤销按钮只调用服务，不把浏览器历史当审计。 | 按模式重写；commands、change_log | T02.01；AC-19、AC-24、AC-38 |
| B04 原金额与统计金额说明 | [shown-amount][S05]、[counted-line][S06]：账户金额与计入统计金额分开 | 明细固定提供原始金额及原方向、规范账户变动、计入收入、计入费用、现金流入／流出、预算占用。借贷方向仅在高级明细出现。不同性质使用明确标签，不能只靠颜色和正负号。 | 按模式重写；TransactionDetailDTO | T01.03、T09.01；AC-39 |
| B05 分析可下钻 | [transactions-behind][S07]：把图表条件转成交易筛选 | 每个实际数字返回 drilldown 条件和统计版本；收入、费用、现金、预算执行分别下钻其计量事实。预算计划数字下钻计划，不伪装成已发生交易。 | 可选择抽取筛选思路；查询 DTO、报表页 | T03.03、T05.03；AC-40 |
| B06 纠错形成规则草稿 | [draft-instruction-change][S08]：从人工纠错拟一条规则 | 明确区分“只改本笔”和“以后同类按此处理”；Codex 可提草稿，用户保存启用。规则带适用账户、商户／商品条件、例外、来源、版本；预览影响后才批量改历史。 | 按模式重写；classification_rules、规则页 | T04.03；AC-41 |
| B07 来源事实与业务解释分离 | [schema][S09] 的 bank_transactions、transactions、entries | 扩展成不可变原件／解析版本、证据关联、当前业务版本、现金事实、可重建分录。支付宝订单只补用途证据；不能直接把订单总额再记一遍资金。 | 按模式重写；ETL、evidence、ledger | T01.01、T02.02；AC-01、AC-02、AC-42 |
| B08 金额方向适配 | [to-entry-amount][S10]：每连接 signMultiplier | 每个文件适配器明确方向字段与符号规则，并保留原值。统一账户净变动后，再由业务类型派生收入／费用／现金；不能以符号代替性质判断。 | 按模式重写；adapter contract | T01.01；AC-39 |
| B09 外部余额独立核对 | [balance-check][S11]：银行余额、账本与未支持交易 | 按账户、币种、日期、余额定义比对；余额观察不自动改成期初或补差分录。已知现金变化但性质未定仍可显示，账本完整性另标。 | 按模式重写；reconciliation | T03.01、T03.02；AC-09、AC-15 |
| B10 周期计划关联实际 | [materialise-rules][S12]：生成预计交易、与银行发生匹配 | 采用独立计划表和整年 occurrence，不把计划放进实际交易表。到期只提示；银行证据到达后关联已发生事实，支持部分履约，剩余预测扣减。 | 按模式重写；planning、plan_actual_links | T05.02、T06.02；AC-43 |
| B11 幂等、校验及原子重算 | [apply-mutations][S13]：请求去重、校验、派生重算 | UI、ETL、MCP 调同一命令服务；一个业务及其关联、分录、历史、任务结果一起提交。模型调用不持写锁。支持按 request_id 查提交结果。 | 按模式重写；application/commands | T01.02、T02.01、T04.02；AC-20、AC-21 |
| B12 受约束的 AI 分类 | [row-questions][S14]、[find-past-transactions][S15]、[label-bank-rows][S16]：有限候选、相似案例、疑点理由 | 候选仅含有效科目及已知账户；人工本笔判断优先，明确商品说明优先于商户默认。历史检索支持二字中文商户和商品描述。返回字段级依据，概率不是证据。 | 按模式重写；AI context DTO、MCP tools | T04.01、T04.02；AC-16、AC-19、AC-44、AC-45 |
| B13 确定性报表与快照 | [compute-weekly-report][S17]、[build-report-summary][S18]：代码算数、模板文案 | 三张管理报表、预算、预实从同一查询层计算；生成带版本及覆盖信息的快照。Codex 只引用指标和交易解释，不能用模型输出替换报表数字。 | 按模式重写；reports、analysis | T03.03、T05.03；AC-16、AC-40、AC-46 |
| B14 PWA 和离线状态 | [open-replica-db][S19]、[create-finance-runtime][S20]：IndexedDB 副本、待同步队列 | 本机一期借鉴手机布局与清晰连接状态；可安装 PWA、受保护接入和可选只读快照缓存随 T09.03 独立交付。完整离线编辑、跨设备写入队列、标签页选主延期。手机接入需另有受保护的服务访问路径。 | 直接复用可兼容外壳；写同步延期 | T09.01、T09.03；AC-47 |
| B15 图片导出与 Safari 交互 | [report-share-panel][S21]、[build-report-image-drawing][S22]：生成 PNG、下载／分享／文字摘要 | 图片、PDF、Excel 采用相同报告数据；提前渲染、点击时发起分享，有下载回退。“仅支出”从后端白名单 DTO 生成，浏览器不接收隐藏的收入字段。 | 可选择抽取导出交互；exports | T09.02；AC-48、AC-49 |

StyleX、工作区 UI 包和所需代码生成链可一并复制接入；是否未发布到 npm 不构成重写理由。页面涉及上游 pg schema／replica store 时，通过明确的数据适配接本地服务，保留可复用的展示和交互。金额计算仍以本项目的公共 DTO 和财务合同为接口，原测试作为补充。

## 3. 明确不采用的实现及替代方案

| 编号 | 上游实际实现或缺口 | 不能照搬的原因 | 我们的替代与验证 |
|---|---|---|---|
| N01 分期改写原消费 | [build-instalment-plan][S23] 将原消费变首期，后续每期计费用，贷款不计净资产，费用并入总额 | 会改变消费历史，并弱化剩余负债；与我们的购买额／还款额双口径冲突 | 购买事实保留；本金负债和还款计划独立；首页按月还款展示。预算的付款口径不改变收支表的消费口径。AC-05、AC-50。 |
| N02 AI 失败按正负入收入费用 | [write-labelled-rows][S24] fallback 生成 posted 的未分类收入／费用 | 本人互转、还款、过桥款可能暂时进入收支表 | 性质未知保存未解释收付；消费性质已知才入未分类支出。AI 失败不改变确定事实，不靠重试次数授权记账。AC-17、AC-44。 |
| N03 同额、相近日自动配对 | [plan-bank-matches][S25] 用金额、日期及排序选择匹配 | 多来源可能有同日同额的真实两笔消费 | 来源交易号或支付号可强匹配；当前已验证的银行卡／金额／日期／商户唯一规则按适配器约束使用；其他仅候选。多候选不按先后强选。AC-02、AC-42。 |
| N04 raw 重拉覆盖 | [bank-transaction-repository][S26] 同来源 ID 更新 raw 及交易字段 | 原件、解析变化及人工覆盖无法仅凭最新 raw 复现 | 原件哈希、解析版本、源行定位保留；来源更正形成新观察版本和差异；人工字段不被重导覆盖。AC-01、AC-42。 |
| N05 云端多家庭主架构 | [get-finance-db][S27]、[schema][S09] 使用 PostgreSQL、多家庭、服务端同步 | 一期单用户、本机资料，不需先建设家庭 SaaS | 一个本机 Python 服务为唯一业务写入入口，SQLite 为权威库；MCP 为薄适配器。完整云同步延期。AC-22、AC-47。 |
| N06 分享默认包含净资产 | [build-report-image-drawing][S22] 默认绘制净资产 | 用户已要求仅分享支出，收入、留存及可反推收入的字段均不应出现 | 独立 expense_only 数据合同，禁止收入／留存／储蓄目标差额、隐含百分比和完整摘要／元数据泄漏。AC-48。 |
| N07 英文摘要处理直接移植 | [find-past-transactions][S15] 过滤不足 3 字的词；[row-questions][S14] 倾向把平台收款记平台商户 | “美团、盒马、山姆”等检索失效，支付宝实际商品被平台名称遮蔽 | 中文商户别名、二字词、商品证据及付款卡共同检索；支付通道、收款方、实际商户分别保存。AC-45。 |
| N08 用日汇率重算所有金额 | [create-transaction-amount][S28] 按交易日汇率转换计量金额 | 已有人民币实际结算额不应被公共汇率替换 | 原币、实际人民币结算、估算折算分别保存；有结算先用结算。汇率为精确十进制；估算注明日期来源。AC-01。 |

## 4. 必须由本项目补齐的能力

| 能力 | 上游能提供什么参考 | 本项目需要完成什么 |
|---|---|---|
| 三张个人管理报表 | 资产负债展示、历史收支与周报 | 独立现金收付口径、未知事实及覆盖说明；不把净收支当现金流。 |
| 预算与年度资金安排 | 周期交易规则，通常提前生成一周 | N×12、预算版本、用途分组、历史分期、年度额度、按月可花余额、目标差额及情景比较。 |
| 预实与报销调整 | 收支筛选和分类汇总 | 同窗口累计报销两侧抵减、冻结估计、消费／履约／年度额度各自的比较口径。 |
| 国内账单 ETL | Lunch Flow 接入与同步流程 | 已取得银行／信用支付／支付宝文件的适配、来源去重、订单补充证据；接口覆盖不得假定。 |
| Codex 集成 | 直接模型 API 分类和纠错 | 持久待办、受限本地 MCP、可换会话、无模型时仍可用；不新增模型 API 费用依赖。 |
| 投资明细与收益 | investment 账户和 valuation 快照 | 按产品份额、成本、申赎、分红及已实现／未实现收益，成本不足显示未知。 |
| 修改审计与迁移 | version、软删除、客户端撤销 | 持久前后值、原因、证据、版本，以及从现有脚本结果迁移的 ID 映射与基线对照。 |

上表中的“需要补齐”是本项目要求。对于没有在本次检查中找到的功能，不推断上游作者从未实现，也不把未核实内容写成已有能力。

## 5. 复用与升级规程

1. 实现先读取固定版本相关代码；按 B 编号记录采用的是流程、算法还是具体代码。
2. 抽取代码之前列出它依赖的状态、类型、组件、金额和日期语义。涉及 N01—N08 的逻辑不能通过换名直接引入。
3. 代码抽取须经过本项目对应 AC 和金额算例。保持稳定业务接口，不让我们的账本依赖上游网站整体目录。
4. 上游该提交的 [LICENSE][S29] 为 MIT，版权标注为 Copyright (c) 2026 Qingqi Shi。实际复制代码或实质部分时保留许可及版权声明，在 THIRD_PARTY_NOTICES.md 记录来源、提交、文件、改动；单纯设计参考记录本文即可。第三方依赖和素材仍按各自许可核对。
5. 不自动跟踪 master、不自动合并上游变动；确需升级时逐项复查本项目的口径和验收。

## 6. 固定版本源码索引

[S01]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/shell/finance-shell.tsx
[S02]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/transactions/transactions-screen.tsx
[S03]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/transactions/review-reason.ts
[S04]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/replica/apply-with-undo.ts
[S05]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/transactions/shown-amount.ts
[S06]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/transactions/counted-line.ts
[S07]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/analytics/transactions-behind.ts
[S08]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/ai/draft-instruction-change.ts
[S09]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/db/schema.ts
[S10]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/bank/to-entry-amount.ts
[S11]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/bank/balance-check.ts
[S12]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/rules/materialise-rules.ts
[S13]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/sync/apply-mutations.ts
[S14]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/ai/row-questions.ts
[S15]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/ai/find-past-transactions.ts
[S16]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/ai/label-bank-rows.ts
[S17]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/reports/compute-weekly-report.ts
[S18]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/reports/build-report-summary.ts
[S19]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/replica/open-replica-db.ts
[S20]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/replica/create-finance-runtime.ts
[S21]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/reports/report-share-panel.tsx
[S22]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/reports/build-report-image-drawing.ts
[S23]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/editor/build-instalment-plan.ts
[S24]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/bank/write-labelled-rows.ts
[S25]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/reconciliation/plan-bank-matches.ts
[S26]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/db/repositories/bank-transaction-repository.ts
[S27]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/db/get-finance-db.ts
[S28]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/apps/web/src/finance/domain/transactions/create-transaction-amount.ts
[S29]: https://github.com/QingqiShi/shiqingqi.com/blob/8bea959a5c49dd58e449571f57ef9e5bf7506bed/LICENSE
