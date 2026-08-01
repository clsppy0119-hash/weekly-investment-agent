import json
import os
from datetime import datetime, timedelta, timezone

with open("quotes.json", encoding="utf-8") as source:
    data = json.load(source)
quotes, fundamentals = data.get("quotes", {}), data.get("fundamentals", {})
if os.environ.get("FORCE_REPORT") != "1":
    open("long-research.txt", "w", encoding="utf-8").write("")
    raise SystemExit(0)
code = "2451" if "2451" in quotes else next(iter(fundamentals))
q, f = quotes.get(code, {}), fundamentals.get(code, {})
name = q.get("name", code)
def metric(key, suffix=""):
    value = f.get(key)
    return f"{value:.2f}{suffix}" if isinstance(value, (int, float)) else "資料不足"
today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
report = f'''【長線研究報告｜{code} {name}】
產業別：{f.get("market", "資料不足")}　研究日期：{today}
現價：{q.get("price", "資料不足")}　市值：資料不足　近5年PE區間：資料不足
結論：□ 觀察  ■ 買進候選  □ 不投資
合理價區間：需完成5年估值回溯　預計持有年限：3–5年

1. 一句話論點
受惠於記憶體與工控儲存需求，惟須確認景氣高峰獲利能否持續。

2. 公司在做什麼
產品／地區／客戶營收拆解：待年報與法說資料回補。
最新資料：營收年增 {metric("revenueYoY", "%")}；EPS {metric("eps")}。

3. 護城河
品牌、通路與工控產品認證可能形成門檻；定價權與客戶黏著度待年報驗證。

4. 產業與週期位置
記憶體供需與價格為核心變數；目前需求偏強，但屬循環產業，需持續追蹤報價與庫存。

5. 財務體檢（5年表）
近5年完整表：資料回補中。最新：ROE {metric("roe", "%")}、負債比 {metric("debtRatio", "%")}、PE {metric("pe")}、PB {metric("pb")}。
異常項目：現金流、應收／存貨天數尚未取得，不作通過判定。

6. 經營層與資本配置
董監持股／質押、重大決策：待公開資訊觀測站資料回補。

7. 股利
殖利率 {metric("dividendYield", "%")}；連續配息、配息來源與填息紀錄待除權息資料回補。

8. 估值與買點
目前PE {metric("pe")}；近5年估值區間尚未完成，不設定買進價格。

9. 反向論點 ★
記憶體價格反轉、庫存利益消失、需求降溫；以上任一項可能讓高獲利不可持續。

10. 追蹤指標與頻率
月營收（每月10日）：年增轉弱連續2月即重新評估。
季報：毛利率、營益率、營現金／淨利與庫存天數。
法說會：需求、庫存、報價與資本配置。

11. 賣出條件 ★
論點破壞：營收／毛利趨勢明顯反轉。
估值過高：完成5年估值區間後設定。
更好機會成本：同產業出現品質更高且估值更合理標的。

12. 決策紀錄
買進日／價格／部位比重：由你的投資紀錄帶入。
當時假設與每半年回顧：請在網站決策日誌補充。

資料狀態：本報告未完成的欄位均標示資料不足，不構成買賣保證。'''
open("long-research.txt", "w", encoding="utf-8").write(report)
