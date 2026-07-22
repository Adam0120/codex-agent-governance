# Govern Agent System

[English](README.md)

这是一个最小化的 Codex 原生多代理配置：一个安装脚本、一个 Skill，以及 8 个子代理配置。

## 安装

在本仓库中执行：

```bash
python3 install.py
```

脚本会把 `SKILL.md` 复制到 `~/.codex/skills/govern-agent-system/`，并将 `.codex/agents/` 下的 8 个 TOML 复制到 `~/.codex/agents/`。如需指定其他 Codex 主目录：

```bash
CODEX_HOME=/path/to/.codex python3 install.py
```

安装后请重启 Codex，再开始新任务，以重新加载子代理注册表。脚本仅替换同名的本 Skill 和 8 个角色文件；它绝不会读取、写入或创建 `config.toml`。

## 包含的角色

| 角色 | 模型 | 用途 |
|---|---|---|
| `default` | Luna | 有边界的只读建议 |
| `worker` | Luna | 已确定的实现切片 |
| `explorer` | Luna | 聚焦的发现或排障 |
| `code_locator` | Spark | 与版本对应的事实定位 |
| `cross_module_architect` | Terra | 跨模块契约证据 |
| `systems_safety` | Terra | 父代理批准的安全补丁 |
| `semantic_reviewer` | Sol | 冻结差异审查 |
| `release_operator` | Terra | 已批准、绑定版本的发布批次 |

## 派发策略

需要委派时使用 `$govern-agent-system`。主代理负责范围、契约、集成、风险决策和最终验收；每次将一个有界、可独立验证的节点交给最合适且成本最低的角色。

线性阶段复用一个子代理。若已有两个或更多真正独立、可以开始的节点，应主动启动最小有益的并行批次，不必等待用户再次要求。写入代理必须使用互不重叠的文件或工作树；存在依赖或写入冲突的工作仍串行。子代理运行期间，主代理继续做安全的协调和集成准备，只在真正的依赖边界等待。

完整工作规范见 [SKILL.md](SKILL.md)，每个角色的执行边界见 `.codex/agents/*.toml`。
