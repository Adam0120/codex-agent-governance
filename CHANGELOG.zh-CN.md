# 变更日志

## 0.2.1 — 2026-07-20

- 为降低子代理成本重新平衡八个原生角色：六个角色改用 `gpt-5.6-terra` medium，`code_locator` 保持 `gpt-5.3-codex-spark` high，建议性质的 `semantic_reviewer` 保留 `gpt-5.6-sol` medium。
- 将每个子代理收缩为冻结节点，并把架构与产品决策、风险接受、集成和最终验收收回 Sol/Terra high 或更高推理强度的主代理。
- 默认只使用一个子代理，保留 `max_threads = 4`、`max_depth = 1`、全部角色名和 Sandbox，并移除运行时语言限定文案。
- 增加受管 v0.2.0 到 v0.2.1 的角色替换与逐字节回滚覆盖，不改动无关 Codex 或 MCP 配置。


## 0.2.0 — 2026-07-20

- 用一个精简 Skill 和八个规范、自包含的原生自定义代理 TOML 取代控制器中介 dispatch；移除仅用于 dispatch 的 Luna 变体、运行时 profile、overlay、生成、评估、验证与遥测接口。
- 安装简化为直接复制并移除 `install --link`；保留默认拒绝的锁、冲突检查、原子提升、私有快照、恢复隔离、禁止跟随链接/reparse/hard-link 防护与精确回滚。
- 新增受测的 v0.1.2 受管迁移：替换控制器时代运行时产物，同时保留非代理配置、作为惰性数据的旧 ledger 字节、既有快照、权限、MCP 配置和逐字节精确回滚。
- 修正原生 Codex 兼容性：独立代理 TOML 会被自动发现；全新配置只写入 `agents.max_threads` 与 `agents.max_depth`；来源可证明且已发布的 v0.1.0–v0.1.2 更新会移除旧安装器拥有的 `agents.enabled`，精确回滚则恢复它。

## 0.1.2 — 2026-07-19

- 安全性：在 POSIX 上限制安装器拥有的状态和快照目录，并且不受源文件模式或 umask 影响地限制快照/配置/清单/journal/可选 ledger 文件。
- 新增只读权限诊断，以及在取得锁后通过目录描述符逐级禁止跟随链接的旧版状态加固；敏感硬链接和未知条目默认拒绝，Windows 保留 reparse 防护但不声称等价的 POSIX ACL 保证。

## 0.1.1 — 2026-07-19

- 修复 v0.1.0 之后的 CI YAML、显式 `HOME` 与跨平台别名处理、Windows 受管理链接更新/来源验证、仅 Windows 的链接规范化和词法链接身份。
- 新增 `--version` 诊断，并在兼容的机器可读检查中提供 `release_version`。
- 明确生成适配器的 MCP/Skill 权限边界、无强制依赖的 Spark 定位器边界，并补充版本一致性回归测试。

## 0.1.0

- 初始可移植代理治理控制平面发布。
