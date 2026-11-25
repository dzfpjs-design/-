# 合成业务建议端到端测试候选

此 runner 仅使用已生成的合成审批资料，禁止真实云网络：外层 macOS sandbox 只允许访问新建 PostgreSQL 回环端口；供应商响应由进程内 MockTransport 生成，假密钥仅为固定测试字符串。它不读取应用数据库、原报告、真实设备或真实密钥，也不具备开启真实 DeepSeek 的模式。测试入口不是生产功能。

调用前准备一份由现有隔离合成流程生成的检查点，使用项目 Python 虚拟环境、PostgreSQL 17 工具和 macOS sandbox：

```sh
python scripts/run_synthetic_advice_mock.py --checkpoint "$SYNTHETIC_CHECKPOINT" --pg-bin "$POSTGRES_BIN"
```

每次调用新建私有测试集群与数据库，恢复前沿用原检查点的合成来源、权限、空云凭据和测试库身份校验。run UUID 在创建初始私有状态时固定，也是启动请求编号；mock 请求前通过独占文件持久记录单次请求，重复申请拒绝。日志、数据库、响应文件和引用均为本次合成数据，不会自动销毁。成功后重启 PostgreSQL 进程，由另一个进程重新验证原许可、保全结果并两次恢复结算，核对用量不重复。失败或中断保留原环境，标记待核，不自动重发或新建尝试。

若已保存一次模拟请求标记和原始许可引用，且该专用测试数据库已停止，可使用仅恢复入口；它不含派发代码，缺失或不完整的首答会拒绝恢复：

```sh
python scripts/recover_synthetic_advice_mock.py --run-root "$SYNTHETIC_RUN_ROOT" --pg-bin "$POSTGRES_BIN"
```

安全和发布边界：现有加密暂存使用系统临时目录且密钥与数据同属测试账户；仅证明应用和 PostgreSQL 进程重启，不能证明操作系统清理、机器重启或持久卷故障后的恢复。恢复仍依赖专用测试库和内部记录引用，尚无生产配置界面、后台恢复任务或容量清理策略。未验证模型在真实账号中的可用性、固定受控出口、真实 DeepSeek 返回的合同质量或用户体验。完整合成链可能耗时十余分钟；正式服务、8766 和真实工作区均未升级。
