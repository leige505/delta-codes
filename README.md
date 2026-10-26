# delta-codes

「三角洲改枪码速查」工具的自动数据源。

- 每天北京时间 0 点后（GitHub 定时任务，可能有几分钟到几十分钟延迟）自动运行 `fetch_online.py`，抓取博主腾讯文档的改枪码（3 个 sheet）+ 游侠网每日密码（5 图交叉验证）。
- 产物 `codes.json` 自动发布到 GitHub Pages：`https://<用户名>.github.io/delta-codes/codes.json`
- 手动触发：仓库 Actions 页 → daily-update → Run workflow。

## 文件

| 文件 | 用途 |
|---|---|
| fetch_online.py | 主抓取脚本（腾讯文档 protobuf 解析 + 调用密码抓取） |
| fetch_password.py | 每日密码抓取（游侠网，五图交叉验证） |
| conv_codes.py | 清洗/规范逻辑（被 fetch_online 引用） |

全部为 Python 标准库实现，无第三方依赖。
