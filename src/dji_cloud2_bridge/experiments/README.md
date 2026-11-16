# Cloud API 2 SDK 安全实验

这是一套**仅供隔离研究和合成回归**的补丁与工具，不会启用平台真实设备连接，也不是大疆官方模拟器。合成服务只监听容器回环地址，测试值不是用户凭据。

## 来源、版本和许可

- DJI Cloud-API-2 固定提交 `fd679f89301295e3c2085a90dac1d5750cfa563d`，`sdk/VERSION` 为 3.4.0。该 SDK 自有源码为 Apache-2.0；`libdrtc`、`libdrpc` 为大疆专有预编译库，不在 Apache 授权范围。完整声明见 [DJI SDK 源码](https://github.com/dji-sdk/Cloud-API-2/tree/fd679f89301295e3c2085a90dac1d5750cfa563d/sdk) 和本目录 `licenses/`；其他组件各有独立许可。使用、发布组合产物前须核验所有适用条款。
- libwebsockets 固定提交 `8964ce9db75a98e463dfafd2e89f2bc8a95ec6ed`，对应 v2.4.2。它采用 LGPL-2.1 及例外条款，详见 [上游源码和许可证](https://github.com/warmcat/libwebsockets/tree/v2.4.2) 与 `licenses/`。
- 构建容器基础镜像固定为 Dockerfile 中的 digest，顶层工具包锁版本并记录完整包清单。Ubuntu 仓库和间接依赖仍可能变化或撤包，因此本流程用于可追踪、可重复执行的验证，**不保证逐字节可复现构建**；版本不可用时应失败并检查，不能悄悄更换。
- 上游归档文件以 SHA-256 校验后才解压。固定提交的 GitHub 自动压缩包若以后生成的字节变化，校验会拒绝，此时必须重新核验上游内容，不应关闭校验。

本目录不包含 DJI 或第三方二进制、真实凭据、设备数据、证书或私钥。外部构建输出 JAR 会包含上游预编译库，**不得提交公开仓库或用于生产**；即使只是分发实验产物，也须先核验专有库和各第三方许可。

## 构建与回归

需要 Docker、Python 3、curl、git、可访问 GitHub 和约数百 MB 的本地磁盘。当前脚本只支持 Linux aarch64 容器实验；它在 macOS Docker ARM 上的运行结果不等于 DJI 对该平台的官方支持。准备两个仓库之外的空目录，例如 `SOURCE_DIR` 与 `OUTPUT_DIR`，再执行：

```sh
docker build -t cloud2-experiment-build:local -f src/dji_cloud2_bridge/experiments/Dockerfile src/dji_cloud2_bridge/experiments
src/dji_cloud2_bridge/experiments/fetch-sources.sh "$SOURCE_DIR"
src/dji_cloud2_bridge/experiments/build-sdk.sh "$SOURCE_DIR/dji-sdk.tar.gz" "$SOURCE_DIR/libwebsockets.tar.gz" "$OUTPUT_DIR"
src/dji_cloud2_bridge/experiments/run-probes.sh "$OUTPUT_DIR" "$(pwd)/src/dji_cloud2_bridge/DjiCloud2Bridge.java"
```

目录参数必须是绝对路径，输出目录不可位于项目仓库；构建脚本拒绝覆盖已有结果。固定归档先验 SHA-256，再在 `--network none` 容器里应用补丁与编译。可以通过 `CLOUD2_BUILD_JOBS=1` 至 `6` 控制编译并行度；临时工作目录会清理。构建结果包括：

- `sdk-patched.jar`：实验修订版，绑定 Java 类和 JNI；
- `sdk-vulnerable-control.jar`：专门用于阳性对照、故意保留重定向漏洞的实验品，**绝不能连接真实服务或使用真实凭据**；
- `jar-sha256.txt`、`patched-native-sha256.txt`、`build-packages.txt`：构建指纹与包清单。libcurl 静态链接在 JNI 内，JNI 哈希覆盖这部分内容。这些清单与产物位于同一输出目录，只能检查一致性；正式授权必须使用管理员独立保护的签名或固定哈希。

回归脚本先验证 JAR 与 native 指纹，然后使用不继承宿主环境变量的 `--network none` 容器、仅本地生成的一次性测试证书，检测四类目标（跨端口、跨 IP、同 IP/端口不同 Host、同站相对路径）的 301/302/303/307/308。每一项必须同时在漏洞对照中观察到 HTTP 与 WSS 合成凭据泄露，并在修订版中确认目标未收到任何请求、未误报查询成功；接着验证正常 HTTP 查询、WSS 101、原生连接建立和默认/DEBUG 日志中的合成凭据。任一检查失败返回非零。临时证书和私钥仅在测试临时目录使用并删除。

## 仍需满足的开放条件

本实验只检查设备列表和一次 WebSocket 握手。必须完成真实大疆底座、官方模拟器、目标 Linux 运行时、所有相关 SDK 网络路径及日志的独立验证；跨目标出口应由已验证的隔离网络强制限制，清除代理和动态库注入环境。SDK 本身包含写接口：**底座端必须单独签发并验证仅允许设备只读查询的凭据与权限**，不能依赖补丁、Java 桥或文件哈希阻止写操作。还需核验 SDK/二进制和第三方许可、ABI、升级兼容与安全维护。上述条件未满足前，产品真实连接继续保持关闭。
