# HKUST(GZ) SIS 选课助手

一个面向个人账号的 Windows + Python 选课辅助工具。用户手动完成登录和二次验证后，程序等待指定时间，必要时自动勾选 `Select All`，然后只尝试点击一次 `Enrol`。

> 请先确认适用的学校和 SIS 使用规则。程序不会绕过 CAPTCHA、MFA、排队、限流或访问控制，也不会点击 `Finish Enrolling`、`Confirm` 或 `Submit`。

## 技术栈

- Python 3.12
- Playwright persistent context
- Microsoft Edge
- Tkinter 图形界面
- PyYAML、python-dotenv、tzdata
- unittest + 模拟 HTML
- PyInstaller Windows one-folder 打包

## 主要功能

- 保留专用 Edge 登录状态，登录/MFA 由用户手动完成。
- 使用 Asia/Shanghai 时区显示目标时间和倒计时。
- 可在到点前 1–3 秒刷新一次页面。
- 等待 Shopping Cart 异步加载完成，避免把临时空页面误判为空购物车。
- 唯一定位并按需点击一次 `Select All`。
- 到点后立即尝试点击一次 `Enrol`，失败不重试。
- 自动记录日志和本地安全截图。
- 锁文件防止多个实例同时运行。

## 直接运行 Windows 程序

1. 解压 Release 中的 `SIS-Cart-Scheduler-windows-x64.zip`。
2. 双击 `SIS-Cart-Scheduler.exe`。
3. 首次启动会在程序目录自动生成 `config.yaml`。
4. 设置未来目标时间，建议先点击“检查页面”。
5. 在专用 Edge 中手动完成登录/MFA并停留在 Shopping Cart。
6. 确认课程识别正确后，点击“开始定时”。

电脑需要已安装 Microsoft Edge。不要移动或删除程序目录中的 `_internal` 文件夹。

## 从源码运行

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
python gui.py
```

运行测试：

```powershell
python -m unittest discover -v
```

复现 Windows 打包：

```powershell
.\build_windows.ps1
```

## 本地文件

- `.private/browser-profile/`：专用 Edge 登录资料
- `var/enroll-click.log`：运行日志
- `var/screenshots/`：检查和点击前后的截图
- `config.yaml`：本机设置

以上目录和文件均已加入 `.gitignore`，不要上传账号、Cookie、日志、截图或浏览器资料。
