# 变更日志

## 0.2.3 — 2026-07-21

- 为“提供 `spawn_agent` 但省略 `agent_type`”的任务表面增加按参数表判断的兼容路径：常规派发显式传入固定角色的模型/强度，不再意外继承父代理模型；接口可用时仍优先使用原生角色绑定。
- 明确兼容派发不会加载 TOML sandbox 或 developer profile，不得伪称已绑定角色；主代理保留 writer lease 和证据核验，也不输出面向用户的模型绑定遥测。已有成熟且范围正确的子代理不会仅为重新绑定而中断，新规则从其下一个新节点起生效。

## 0.2.2 — 2026-07-21

- 将受管横向并发上限从四个提升为六个子线程，与当前 Codex 默认值一致，同时保持 `max_depth = 1`；当前格式 manifest 可按其自身已验证的正数线程与非负深度值升级或卸载，不再依赖版本号到固定配置值的映射。
- 明确要求原生派发传入 `agent_type`：常规 model/effort 由注册适配器提供，并避免全历史继承；任务标签不再被误当作角色绑定。Skill 现在支持任意具备能力且为 high 或更高强度的主模型，也支持用户显式要求时加载。
- 恢复以 v0.2.1 为基础的逐角色固定模型与强度，并把有边界的 `default`、`worker`、`explorer` 调整为 `gpt-5.6-luna` high。`code_locator` 保持 Spark/high，关键执行角色保持 Terra/medium，`semantic_reviewer` 保持 Sol/medium。
- 继续只保留八个既有角色：主代理先冻结目标与验收边界；Luna 角色仅在连续推理失败后升级到 Terra。不新增 Luna 专用角色，也不再要求向用户显示派发/绑定流水。
- 当前格式兼容不再依赖版本号顺序：来源已验证的 v0.2 格式既可跳版本，也可使用高于本 CLI 的记录版本号，但仍必须通过精确 schema 与来源验证。
- 新增基于快照且原子执行的 `uninstall`，只移除 manifest 可证明的受管文件与配置键，保留用户代理、无关 Codex 配置和 MCP 设置；移除 v0.1 迁移路径。
- 事务恢复现在可承受中断和进程死亡：每次替换前先持久化完整计划与清理工件，任何非终态 journal 都会阻断写入，精确回滚后清除事务残留。已证明 owner 死亡的锁既可用于显式 journal 恢复，也可在 journal 创建前或关闭后的无 journal 边界安全回收；取得替换锁后会重新检查 journal，并且只清理保留的 staging 命名空间。Manifest 与 snapshot 的 schema/provenance 整数改为按 JSON 标量精确类型校验，不再依赖 Python 宽松相等比较。
- 新增可发布的 `codex-agent-governance` Python 入口及单一来源 wheel payload；正式发布后可通过 `uvx ...@latest install` 一条命令更新，且不复制安装器逻辑。

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
