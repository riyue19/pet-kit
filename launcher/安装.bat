@echo off
rem One-click setup: install dependencies, download models, create the desktop shortcut.
rem Safe to run again; already-downloaded models are skipped.
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0.."

echo ============================================================
echo  桌宠 安装
echo ============================================================
echo.

rem ---------- 1. tools ----------
set "MISSING="
where node >nul 2>&1 || set "MISSING=1"
where uv   >nul 2>&1 || set "MISSING=1"
if defined MISSING (
    echo 缺少以下工具，请先安装，然后关掉这个窗口重新运行本脚本：
    echo.
    where node >nul 2>&1 || echo   - Node.js 20.19+ 或 22.12+：https://nodejs.org/
    where uv   >nul 2>&1 || echo   - uv（Python 包管理器）：https://docs.astral.sh/uv/
    goto :fail
)

rem ---------- 2. frontend dependencies ----------
echo [1/5] 安装前端依赖 ...
pushd frontend
call npm install
if errorlevel 1 ( popd & echo. & echo 前端依赖安装失败。 & goto :fail )
if not exist "node_modules\electron\dist\electron.exe" (
    echo Electron 未下载完整，正在补下载 ...
    call node node_modules\electron\install.js
    if errorlevel 1 ( popd & echo. & echo Electron 下载失败，请检查网络后重新运行本脚本。 & goto :fail )
)
popd

rem ---------- 3. backend dependencies ----------
echo.
echo [2/5] 安装后端依赖（首次会下载 Python 3.12，需要几分钟）...
pushd backend
call uv sync
if errorlevel 1 ( popd & echo. & echo 后端依赖安装失败。 & goto :fail )
popd

rem ---------- 4. models ----------
echo.
echo [3/5] 下载 Live2D 运行库和示例模型 Hiyori ...
pushd frontend
if exist "public\live2d\live2dcubismcore.min.js" (
    echo Live2D 运行库已存在，跳过。
) else (
    call npm run fetch-core
    if errorlevel 1 ( popd & echo. & echo Live2D 运行库下载失败，请检查网络后重新运行本脚本。 & goto :fail )
)
if exist "public\live2d\models\Hiyori\Hiyori.model3.json" (
    echo 示例模型已存在，跳过。
) else (
    call npm run fetch-sample-model
    if errorlevel 1 ( popd & echo. & echo 示例模型下载失败，请检查网络后重新运行本脚本。 & goto :fail )
)
popd

echo.
echo [4/5] 下载语音识别、记忆和摄像头模型（约 185MB，已下载的会跳过）...
pushd backend
call uv run python scripts/fetch_models.py
if errorlevel 1 ( popd & echo. & echo 本地模型下载失败，请检查网络后重新运行本脚本。 & goto :fail )
popd

rem ---------- 5. point the config at the sample model, unless the user already chose one ----------
if not exist "config\frontend.yaml" (
    > "config\frontend.yaml" echo model:
    >>"config\frontend.yaml" echo   path: "live2d/models/Hiyori/Hiyori.model3.json"
    echo 已将示例模型写入 config\frontend.yaml，启动后即可看到。
)

rem ---------- 6. shortcut ----------
echo.
echo [5/5] 创建桌面快捷方式 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-shortcut.ps1"
if errorlevel 1 ( echo. & echo 快捷方式创建失败。 & goto :fail )

echo.
echo ============================================================
echo  安装完成。双击桌面上的「桌宠」即可启动。
echo  启动后在托盘图标上右键打开「控制台」，到「AI 模型」页填写 API Key。
echo  想换自己的模型，把模型文件夹放进 frontend\public\live2d\models\，
echo  再到控制台「形象」页选择。
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo 安装未完成，上面是失败的步骤。解决后重新运行本脚本即可，已完成的步骤会跳过。
echo.
pause
exit /b 1
