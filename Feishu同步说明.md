# 飞书多表同步方案

你给出的 2 个源表可以通过飞书多维表格 OpenAPI 同步到 1 个目标表。仓库里已提供脚本：`scripts/feishu_bitable_sync.py`。

## 1. 推荐方法

推荐用“**定时脚本 + Upsert**”方案：

- 从源表 A、B 拉全量记录
- 生成唯一键：`来源记录ID = {source_table_id}:{record_id}`
- 在目标表按该唯一键执行：存在则更新，不存在则新增

这样可以避免重复写入，并支持重复执行（幂等）。

## 2. 前置准备

1. 在飞书开放平台创建企业自建应用，开通多维表格权限（读取源表、读写目标表）。
2. 记录：
   - `app_id`
   - `app_secret`
   - 三个表对应的 `app_token` 与 `table_id`
3. 目标表需先创建字段（字段名可自定义，但要和脚本参数一致）：
   - `来源记录ID`（文本，建议设为唯一）
   - `来源数据表`（文本）
   - 以及你希望同步的业务字段（与源表同名最省事）

## 3. 安装依赖

```bash
pip install requests
```

## 4. 运行示例（按你的 2 源 1 目标场景）

> 注意：下面的 token/id 请替换成你在飞书后台看到的真实值。

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"

python scripts/feishu_bitable_sync.py \
  --source appTokenSrc1:tbl5WSgwJy1AMjXM \
  --source appTokenSrc2:tblbcMcvHhIrPgAE \
  --target appTokenTarget:tblhfQmDaEf96nPS
```

脚本完成后会输出：

- `created=...`：新增记录数
- `updated=...`：更新记录数

## 5. 定时同步

可用 cron 每 10 分钟跑一次：

```bash
*/10 * * * * cd /path/to/repo && /usr/bin/python3 scripts/feishu_bitable_sync.py --source ... --source ... --target ... >> /var/log/feishu_sync.log 2>&1
```

## 6. 你的链接如何映射

- 源表1 URL 包含：`table=tbl5WSgwJy1AMjXM`
- 源表2 URL 包含：`table=tblbcMcvHhIrPgAE`
- 目标表 URL 包含：`table=tblhfQmDaEf96nPS`

`view=...` 是视图 ID，不是脚本必要参数；同步按 table 维度执行。
