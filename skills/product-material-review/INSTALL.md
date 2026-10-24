# 安装与开始使用

## 从 GitHub 安装

在目标电脑的 Codex 中发送：

> 从 https://github.com/wtt982887807/product-material-skills 的 skills/product-material-review 目录安装这个 Skill，一并安装 requirements.txt 中的依赖，然后运行 scripts/material_agent.py doctor 验证。

由安装助手调用 Skill Installer 下载整个技能目录；使用同一个 Python 3.11+ 解释器安装依赖和运行 doctor。无需传文件包或手写审阅 JSON。安装本身只下载技能文件，Python 依赖检查仍需完成；已有同名技能时先核对版本和用户自定义配置。


需要目标电脑上可用的 Python 3.11 或更新版本。核心处理无需模型 API 密钥；Word/PowerPoint 的原生打开和版面验收需要相应应用或渲染工具。

1. 将整个 `product-material-review` 文件夹复制到目标电脑的 `$CODEX_HOME/skills/`。未设置 `CODEX_HOME` 时，放入默认 `~/.codex/skills/`。保留 `scripts/`、`runtime/`、`references/` 等全部内容，不能只复制 `SKILL.md`。
2. 在复制后的 `product-material-review` 目录打开终端，执行：

```text
python --version
python -m pip install -r requirements.txt
python -X utf8 scripts/material_agent.py doctor
```

`python` 可以替换为目标电脑上已验证的 Python 解释器命令或路径。依赖安装与 `doctor` 必须使用同一个解释器；`doctor` 应返回 `ok: true`。

3. 在能读取该 Skill 的 Codex 会话中提供一份合同和要更新的 DOCX/PPTX 物料，使用例如：

> 使用 $product-material-review，按这份合同更新一页通与 PPT。Word 保留修订和批注，PPT 只输出目标产品要素页；缺少的信息请列出来。

AI 会提取和审阅合同、整理待补清单、维护审阅 JSON 并调用脚本。业务用户无需手写 JSON。程序生成来源可追溯的更新副本，不覆盖原件；缺项及未完成验收会保留状态。

CLI 可以从任意工作目录运行：在其他目录中调用实际安装位置的 `scripts/material_agent.py`，输入与输出路径使用本次实际路径。完整命令见 [CLI 参考](references/cli.md)。

迁移到另一台电脑时，复制完整 Skill 和需要处理的输入资料，安装依赖并重新分析。旧 `task.json` 保存当时的来源路径和版本，不会自动迁移；由 AI 按新任务重新核对审阅决定，不要手工替换旧快照的路径、哈希或 ID。

旧 DOC 需先取得可信的 DOCX 转换版，再直接分析转换版，或使用 CLI 的 `register-conversion` 登记双哈希缓存。扫描 PDF、图片海报以及复杂对象可能需要额外提取或人工处理；`doctor` 通过不等于这些内容已解析或已通过版面验收。
