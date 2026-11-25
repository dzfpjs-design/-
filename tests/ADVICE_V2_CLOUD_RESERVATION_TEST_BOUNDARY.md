# V2 云账本预留：隔离测试边界

本候选只允许专属 PostgreSQL 测试库，不注册路由、不启动正式 worker、不发送模型请求，也不生成 transport intent。预留仅是历史用量登记：`transport_authorized=false`，`send_outcome=unknown`，不能证明已经发送或尚未发送。重复或未知原件保持拒绝；未来每一次实际发送必须以独立的原 claim 与原 intent 为基础，在紧邻发送时重新核对身份、租约、政策、来源、授权和防重复。

`same_goal_advice_cloud_claim_test_v2.install` 必须显式执行，并核对测试库系统标识。它创建测试专用 claim 表，约束同工作区 job、plan 及 CloudCall/Audit 的唯一性；发现 v2 历史或未归属记录即拒绝，不删除、修复或改写历史。该唯一性仅适用于该 claim 协议，不能约束旧应用、外部程序或直接数据库写者。表锁和 schema 核验依赖隔离 PG 的权限与状态，不能直接当生产迁移。

`reserve` 在同一事务中复核当前 Start、租约、预览、资料和 DeepSeek 已发布能力，使用真实 `cloud_budget_policy`，将 CloudCall、原始回执、claim 和按 UTC 数据库时间确定的日账写在一起。日账锁等待或建账前跨日则拒绝，事务回滚；DeepSeek 不设额度，仍记录 calls/tokens，缺少真实 usage 时不得编零。回执中的 `source_recheck_started_at` / `source_recheck_completed_at` 表示数据库时钟记录的复核区间；`accounting_checked_at` 绑定日账日期，`reserved_at` 是登记时间，不是发送时间。

默认 `custody.read_started` 继续拒绝出现云账本的记录；固定的 `read_reserved_test_only` 只读核对原 Start、租约、预览、资料和一份 claim/CloudCall/Audit/日账，返回无发送权摘要。该读法依赖当前有效会话，不能用作撤权后的首答历史保全；遇到未知 intent 等记录继续拒绝。`verify_saved_links` 只是受固定调用方约束的内部组成部分，不能单独作为完整原件证明或发送许可。

定向测试采用真实隔离 PG、合成记录；部分来源和 Goal 是测试替身，不是可研报告或真实业务用户。覆盖 claim 唯一、并发、孤儿和回滚；原生 SQL 及已 flush ORM 变更下的列/数据复核；当前角色变化、旧 worker 停泊；短有效期判定；跨 UTC 日账和合成次日结算。模拟次日结算直接调用现有账务函数并使用合成 receipt，不能证明真实供应商 usage 或完整发送。

**未解决的生产阻碍**：预留事务在测试中以 `SHARE ROW EXCLUSIVE NOWAIT` 锁住两张全表，阻塞其他工作区写入；安全提交前仍读取证据文件。一次合成替身运行测得约 0.24 秒（其中故意包含两次 0.1 秒冲突等待），不是生产基准。来源判定之后时间仍会推进：故意延迟可使 Goal 在最终提交前自然到期，但预留只记录历史，不授权发送。缺少已验证的无当前会话历史保全读法、原 intent、实际传输以及对旧/外部写者的隔离。任何这些未闭环前不得接正式外发。
