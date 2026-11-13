# pet-kit

运行在 Windows 桌面的 Live2D AI 桌宠。

## 快速安装

1. 安装 [Node.js](https://nodejs.org/)（20.19+ 或 22.12+，选 LTS 版即可）和 [uv](https://docs.astral.sh/uv/)。
2. 下载本项目，双击 `launcher\安装.bat`。它会自行安装依赖、下载模型（约 200MB）、在桌面创建「桌宠」快捷方式。第一次需要几分钟，之后每次打开都需要六秒启动。
3. 双击桌面上的「桌宠」启动。在托盘图标上右键打开「控制台」，到「AI 模型」页填写你的大模型 API Key（兼容 OpenAI 接口的都可以）。

安装中途失败（多半是网络问题）时，窗口会说明是哪一步。解决后重新双击 `安装.bat` 即可，已完成的步骤会跳过。

Cubism Core 是 Live2D 的专有软件，可在此编辑live2d模型，使用前请阅读 Live2D 官网上的 Live2D Proprietary Software 许可协议。

## 用法

1. 进入形象页，导入你live2d模型包里的model3后缀文件
2. 进入人设页自定义桌宠的姓名与人设
3. 进入AI模型页填写配置模型参数，设置优先级（最好是拥有联网搜索和识别图像功能的LLM模型）

可选：
1. 在形象页配置模型表情和动作
2. 打开状态页面，配置状态栏
3. 进入提示词页面加入知识库或拖动调整提示词优先级
4. 进入表情包模块配置表情包
5. 进入语音页打开麦克风功能，配置语音模块（已适配minimax和阿里云，或使用其他通用语音模型）、识别条件、表情语气（可不做，因为可能会因为语气参数不对报错）
6. 打开记忆页自行配置记忆、总结功能
7. 打开感知与主动，按需打开感知模块。包括屏幕感知、主动对话和摄像头模块
8. 打开快捷键模块配置快捷键功能（初始配置没改动成功原因是撞按键，换个按键组合即可）
9. 打开指令模块配置指令，最好配合语音功能使用，配置指令打开的软件需要输入软件路径连同其exe文件名
10. 打开界面自行配置界面样式，也可以命令AI修改css

## 换模型

把模型文件夹放到 `frontend\public\live2d\models\<名字>\`，或者在控制台「形象」页导入，再在「形象」页选择。


日志在 `%LOCALAPPDATA%\pet-kit\logs\`。

## 手动安装

`安装.bat` 做的事情如下，想自己一步步来时可以照着做。

```
# 前端依赖
cd frontend
npm install
# 如果 node_modules\electron\dist 不存在（Electron 下载失败），补用：
node node_modules/electron/install.js

# Live2D 运行库和示例模型 Hiyori
npm run fetch-core
npm run fetch-sample-model
```

```
# 后端依赖（Python 3.12 由 uv 自动安装）
cd backend
uv sync

# 本地模型，下载到 backend\models\，已下载的会跳过
uv run python scripts/fetch_models.py
```

| 本地模型用途 | 模型 | 大小 |
|------|------|------|
| 语音识别 | SenseVoice-Small int8 + Silero VAD | 约 160MB |
| 长期记忆 | bge-small-zh int8 | 约 24MB |
| 摄像头（可选） | YuNet 人脸检测 | 约 230KB |

然后新建 `config\frontend.yaml`，填上示例模型的路径：

```yaml
model:
  path: "live2d/models/Hiyori/Hiyori.model3.json"
```

最后双击 `launcher\创建快捷方式.bat` 生成桌面快捷方式。项目目录移动后要重新生成一次。

不用快捷方式时，可以开两个终端手动启动：

```
# 终端 1：后端，默认监听 127.0.0.1:8765
cd backend
uv run python -m api
```

```
# 终端 2：前端
cd frontend
npm run dev
```

## 测试

```
# 在 backend\ 下
uv run pytest

# 在 frontend\ 下
npm test
npm run typecheck
```
