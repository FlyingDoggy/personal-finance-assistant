# 财务图表设计系统

v0.1.0，设计合同，尚不是已发布组件库。报表产品定义见 [reporting-and-visualization.md](../../specs/reporting-and-visualization.md)。

- [registry.json](registry.json)：16张财务图的ID、用途口径、粒度、前置校验、下钻和交付顺序；记录已阅读EIX参考文件的SHA-256，不复制企业界面、字体或业务数据。
- [finance-context.schema.json](finance-context.schema.json)：EIX ChartSpec的`finance`扩展合同，补财务计量、退款、报销、分母、版本、质量与交易引用集合。JSON Schema只约束形状；跨字段、金额守恒、期间比较及权限校验由确定性服务实现。

复用EIX的`resultRef`、ChartBlock、图/表/解释同源、确定性选图和停止条件。展示继续使用现有Finance控件和主题。特别补齐百分比归一化、退款负值、余额未知、两端还款去重、预实basis及引用集合；不能将参考适配器直接接上线就宣称金融语义已验。

当前状态统一为`designed_not_implemented`。V01实施后再按图逐个记录真实接口、反例及桌面/手机验收结果，不批量改成已完成。
