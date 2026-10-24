# 安装与开始使用

## 从 GitHub 安装

在目标电脑的 Codex 中发送：

> 从 https://github.com/wtt982887807/product-material-skills 的 skills/product-material-review 目录安装这个 Skill，一并安装 requirements.txt 中的依赖，然后运行 scripts/material_agent.py doctor 验证。

由安装助手调用 Skill Installer 下载整个技能目录；使用同一个 Python 3.11+ 解释器安装依赖和运行 doctor。无需传文件包或手写审阅 JSON。安装本身只下载技能文件，Python 依赖检查仍需完成；已有同名技能时先核对版本和用户自定义配置。


本版为 Skill **1.2.1**，随包核心 **2.3.1**。

需要目标电脑上可用的 Python 3.11 或更新版本。核心处理无需模型 API 密钥；Word/PowerPoint 的原生打开和版面验收需要相应应用或渲染工具。

可在 Codex 中直接说：

> 从 `wtt982887807/product-material-skills` 仓库的 `skills/product-material-review` 安装 Skill，同时安装依赖并运行 doctor 验证。

从 GitHub 安装的是本地副本，不会自动同步。后续可要求 Codex 从同一目录更新完整 Skill：先备份并比对本地修改，妥善合并自定义规则，再检查依赖、校验包和运行 `doctor`；失败恢复旧版。更新后重新分析业务输入。本包没有自动更新器。

手动安装步骤：

1. 将整个 `product-material-review` 文件夹复制到目标电脑的 `$CODEX_HOME/skills/`。未设置 `CODEX_HOME` 时，放入默认 `~/.codex/skills/`。保留 `scripts/`、`runtime/`、`references/` 等全部内容，不能只复制 `SKILL.md`。
2. 在复制后的 `product-material-review` 目录打开终端，执行：

```text
python --version
python -m pip install -r requirements.txt
python -X utf8 scripts/material_agent.py doctor
```

`python` 可以替换为目标电脑上已验证的 Python 解释器命令或路径。依赖安装与 `doctor` 必须使用同一个解释器；`doctor` 应返回 `ok: true`。

3. 在能读取该 Skill 的 Codex 会话中提供一份目标产品合同和至少一份 DOCX/PPTX 格式模板，使用例如：

> 使用 $product-material-review，内容按这份合同，格式按其他上传模板制作。Word 保留修订批注，一页通和海报沿用模板文件名；PPT 只输出模板自身要素页，文件名在模板原名后加“产品要素”。缺项列出并清理为待补占位。

AI 会建立合同候选底稿、选择模板主体、把模板要求拆为原子需求并回查合同，再逐项核对后调用脚本。新字段无需先加入公共字典；模板旧值不作为目标事实。用户只需补充回查后仍需要的外部信息，无需手写 JSON。默认采用 `template` 模式；只有审阅已有同产品物料时才显式选择 `review` 模式。

程序不覆盖原件。完整审阅稿保存在输出目录的 `internal/`，用户文件自动生成在 `delivery/`，以 `delivery_manifest.json` 列出的实际文件为交付入口。PPTX 格式的一页通或海报也沿用模板文件名；PPT 选页受阻时不会改交完整稿。最终文件仍须实际打开并检查版面，缺项或未验收不能算全部完成。

CLI 可以从任意工作目录运行：在其他目录中调用实际安装位置的 `scripts/material_agent.py`，输入与输出路径使用本次实际路径。完整命令见 [CLI 参考](references/cli.md)。

迁移到另一台电脑时，复制完整 Skill 和需要处理的输入资料，安装依赖并重新分析。旧 `task.json` 保存当时的来源路径和版本，不会自动迁移；由 AI 按新任务重新核对审阅决定，不要手工替换旧快照的路径、哈希或 ID。

旧 DOC 需先取得可信的 DOCX 转换版，再直接分析转换版，或使用 CLI 的 `register-conversion` 登记双哈希缓存。扫描 PDF、图片海报以及复杂对象可能需要额外提取或人工处理；`doctor` 通过不等于这些内容已解析或已通过版面验收。
