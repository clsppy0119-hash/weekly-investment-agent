# 本機投資研究 Agent

這是電腦本機執行的研究 Agent，不會自動下單、移動資金、修改投資規則或傳送未審查的內容。

## 使用方式

```powershell
cd investment_agent
uv sync
uv run python main.py --health
uv run python main.py --research 2330
```

本機健康檢查服務：

```powershell
$env:PORT=8787
uv run python main.py --serve
```

再開啟 `http://127.0.0.1:8787/health`。

## 自我審查範圍

- 檢查報價、基本面資料是否存在。
- 記錄健康檢查結果於未納入版控的 `data/audit-log.jsonl`。
- 資料不足時，Agent 必須標示「資料不足」及「觀察」。
- 改善建議必須由使用者確認後才可改動規則或程式。
