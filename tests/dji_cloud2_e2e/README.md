# Cloud API 2 SDK 与隔离沙箱集成测试

该目录仅供**专用、可丢弃的 Linux 测试环境**使用。测试使用修复版 JNI、临时 TLS 证书和虚构鉴权信息，创建 Docker 内部测试网络及 PostgreSQL 临时数据库；它不是大疆官方模拟器、真实设备或外部局域网验收。真实连接入口保持关闭。

测试需要原生 Linux 上由 `dockerd` 直接监听的本地 Unix socket，测试进程必须能验证 daemon 和宿主 PID/网络命名空间。不能使用远程 daemon、host 网络、生产工作区或用户现有数据库。Docker 需要允许测试进程创建和删除自己的隔离内部网络和容器；即使使用内部网络，Docker 也可能修改该 Linux 主机自身的桥接与防火墙，因此必须使用专用测试 VM。

1. 准备独立的 PostgreSQL 测试集群，按项目 `tests/conftest.py` 提供 `GA_TEST_DATABASE_URL`、`GA_TEST_PG_DATA_DIR`、`GA_TEST_PG_SYSTEM_IDENTIFIER`。测试会核验集群身份，并创建、删除随机临时库；禁止填写产品数据库。
2. 按 `src/dji_cloud2_bridge/experiments/README.md` 在仓库外构建实验修复版和**故意带漏洞、仅供阳性对照的** JAR；仅可在隔离测试网络和虚构凭据下使用漏洞对照。
3. 准备可信本地 JDK 17 和 Python 3.11 基础镜像，以 `docker image inspect --format '{{.Id}}'` 得到不可变镜像 ID，分别设置 `GA_DJI2_E2E_BASE_IMAGE` 和 `GA_DJI2_E2E_PYTHON_IMAGE`。测试会为 guard 构建含 iptables 的临时镜像，需要访问发行版软件源。测试镜像及临时证书只用于本次实验，不得部署。
4. 设置 JAR 的绝对路径 `GA_DJI2_E2E_SDK_JAR`、`GA_DJI2_E2E_CONTROL_JAR`，分别设置独立核对的 SHA-256 `GA_DJI2_E2E_SDK_SHA256`、`GA_DJI2_E2E_CONTROL_SHA256`。设置 `GA_DJI2_E2E_ENABLE=1` 后，从项目根目录运行：

   ```sh
   PYTHONPATH=src:. pytest --disable-warnings -q tests/dji_cloud2_e2e/test_sandbox_sdk.py
   ```

   使用相同的隔离环境与 V2 JNI JAR，也可单独运行消息状态链测试：

   ```sh
   PYTHONPATH=src:. pytest --disable-warnings -q tests/dji_cloud2_e2e/test_ws_state_chain.py
   ```

   该测试在合成 TLS 服务拒绝 WebSocket 鉴权后，读取真实 JNI 的 401 回调，经 Java IPC 和生产 Bridge 解码，由带租约的生产状态发布原语写入真实 PostgreSQL 临时工作区并验证 API 展示；撤权后确认迟到结果不能再次发布。它是对生产原语的**手动编排**，不证明 supervisor 自动循环端到端运行，也不代表已连接大疆官方模拟器或真实底座。

测试会选择不与现有 Docker 网络及本机路由重叠的 RFC1918 子网，创建带唯一身份标签的资源，使用临时合成证书；停止时先检查身份再清理。若 Docker 超时、命名空间无法证明、停止或清理无法确认，测试必须失败并留下待核对状态，不得用单次负面查询声称资源不可能随后出现。若测试中断，应只依据临时台账和对应唯一标签人工检查，不得批量删除 Docker 资源。

跨子网试验另运行 `tests/dji_cloud2_e2e/test_lan_egress.py`，除上述条件外还须明确设置 `GA_DJI2_LAN_E2E_ENABLE=1` 和该**专用虚拟机**的 `GA_DJI2_LAN_VM_MACHINE_ID`（与 `/etc/machine-id` 一致），并允许测试进程创建及删除带随机身份标识的 dummy 网卡。测试在虚拟机中建立独立的合成子网与 TLS 服务，SDK 通过单独 Docker bridge 访问该子网；同一个网络命名空间内校验允许的地址和端口、其他实际监听的地址和端口、Docker 网关，以及元数据和 TEST-NET 地址。该模式允许 Docker 在专用 VM 创建非 internal bridge，**不得在宿主机、共享机器或真实业务网络运行**。`lan_test` 同时要求代码侧显式 opt-in，真实业务连接入口仍为硬禁。它只验证 VM 内跨子网，不证明物理局域网隔离或生产部署安全。

内部网络回归中的测试专用子类会允许附着已核验标签和租约的合成服务容器，便于重定向阳性对照和主管进程崩溃恢复测试；这项例外不属于生产沙箱，也不能作为严格附件隔离的证据。跨子网测试不启用该例外。即使在凭据发送前核对附件，也尚未证明持续运行期间 Docker 管理权限持有者并发改变网络的所有时序；异常创建和 daemon 故障仍须按未确认处置。

覆盖的路径：真实 PostgreSQL 工作区授权、SDK 初始化与只读查询、跨地址重定向约束、本地授权撤销和迟到结果拒绝、INIT 前撤销与 INIT 写入时并发撤销、守护进程被杀、降权后恢复不能复活旧授权、主管进程 SIGKILL 后新进程按精确标签检查并清理自己持有的资源。故意带漏洞的对照 JAR 会先在独立测试内网、无 guard 的情况下使用虚构凭据证明目标能收到鉴权，再在相同 JAR 加 guard 后验证目标事件数不增长；不得把该对照用于真实环境。合成服务可分别对错误 HTTP 和 WebSocket 鉴权返回 401；上述独立状态链测试验证 WebSocket 401 被实际 JNI 回调识别并发布到工作区，而不是仅确认服务端拒绝。403、失败历史与真实 `OnOpen` 恢复的 JNI 回调另由 SDK 实验目录的容器回环 TLS 测试验证。

未收到 guard 的断网回执时，台账保持 `cleanup_unverified`，即使资源清理完成也不自动重启。网络创建超时的测试目前为持久状态模拟，不证明 Docker daemon 的实际异步超时恢复。

**开放阻断：**实际底座只读权限、目标 Linux 上生产镜像真实性及配置、物理外部 LAN、后台正式集成、网络异常和 Docker daemon 故障的全面恢复、SDK 全部网络与日志路径、组合二进制许可及大疆官方模拟器均尚未验证。测试夹具不会真实控制飞行器。
