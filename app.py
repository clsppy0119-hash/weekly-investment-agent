import json
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import ttk


HOLDINGS = [
    {"name": "台積電", "weight": 35, "change": 2.3},
    {"name": "0050", "weight": 30, "change": 1.1},
    {"name": "比特幣", "weight": 15, "change": -1.8},
    {"name": "現金", "weight": 20, "change": 0.0},
]


def fallback_advice():
    return (
        "本週建議：維持持倉，暫不追價。\n\n"
        "• 台積電與 0050：續抱，避免在短線上漲後一次加碼。\n"
        "• 比特幣：波動較大，維持目前部位，不使用槓桿。\n"
        "• 現金：保留 20%，等待更好的風險報酬機會。\n\n"
        "風險提醒：這是範例資料產生的研究摘要，不構成投資建議。"
    )


def ollama_advice():
    prompt = (
        "你是謹慎的每週投資研究助手。請根據以下範例持倉，用繁體中文給出"
        "簡短、具體、不保證獲利的本週行動建議，並附風險提醒："
        + json.dumps(HOLDINGS, ensure_ascii=False)
    )
    body = json.dumps(
        {"model": "qwen3:8b", "prompt": prompt, "stream": False}
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result.get("response", "").strip() or fallback_advice()


def generate():
    status_var.set("正在產生建議…")
    root.update_idletasks()
    try:
        text = ollama_advice()
        status_var.set("已使用本機 Ollama（qwen3:8b）")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        text = fallback_advice()
        status_var.set("Ollama 未連線，已使用內建規則")
    advice.configure(state="normal")
    advice.delete("1.0", "end")
    advice.insert("1.0", text)
    advice.configure(state="disabled")


root = tk.Tk()
root.title("每週投資建議 Agent")
root.geometry("920x650")
root.minsize(760, 560)
root.configure(bg="#f3f6fa")

style = ttk.Style()
style.theme_use("clam")
style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 22, "bold"),
                background="#f3f6fa", foreground="#172033")
style.configure("Sub.TLabel", font=("Microsoft JhengHei UI", 10),
                background="#f3f6fa", foreground="#667085")
style.configure("Card.TFrame", background="white")
style.configure("CardTitle.TLabel", font=("Microsoft JhengHei UI", 10),
                background="white", foreground="#667085")
style.configure("CardValue.TLabel", font=("Microsoft JhengHei UI", 18, "bold"),
                background="white", foreground="#172033")
style.configure("Action.TButton", font=("Microsoft JhengHei UI", 11, "bold"),
                padding=(18, 10), background="#2563eb", foreground="white")

main = ttk.Frame(root, padding=28, style="TFrame")
main.pack(fill="both", expand=True)
main.configure(style="TFrame")

ttk.Label(main, text="每週投資建議 Agent", style="Title.TLabel").pack(anchor="w")
ttk.Label(main, text="最小可運行版｜目前使用範例資料，不會自動下單", style="Sub.TLabel").pack(anchor="w", pady=(4, 20))

cards = ttk.Frame(main)
cards.pack(fill="x")
cards.columnconfigure((0, 1, 2, 3), weight=1)
for col, (label, value) in enumerate([
    ("投資組合總資產", "NT$ 1,000,000"),
    ("現金比例", "20%"),
    ("持倉檔數", "3"),
    ("本週漲跌", "+1.2%"),
]):
    card = ttk.Frame(cards, padding=18, style="Card.TFrame")
    card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 3 else 6))
    ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor="w")
    ttk.Label(card, text=value, style="CardValue.TLabel").pack(anchor="w", pady=(8, 0))

content = ttk.Frame(main)
content.pack(fill="both", expand=True, pady=(18, 0))
content.columnconfigure(0, weight=2)
content.columnconfigure(1, weight=3)
content.rowconfigure(0, weight=1)

hold_card = ttk.Frame(content, padding=20, style="Card.TFrame")
hold_card.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
ttk.Label(hold_card, text="持倉配置", style="CardValue.TLabel").pack(anchor="w", pady=(0, 12))
for item in HOLDINGS:
    row = ttk.Frame(hold_card, style="Card.TFrame")
    row.pack(fill="x", pady=7)
    ttk.Label(row, text=item["name"], style="CardTitle.TLabel").pack(side="left")
    ttk.Label(row, text=f'{item["weight"]}%', style="CardTitle.TLabel").pack(side="right")
    bar = ttk.Progressbar(hold_card, value=item["weight"], maximum=100)
    bar.pack(fill="x", pady=(0, 4))

advice_card = ttk.Frame(content, padding=20, style="Card.TFrame")
advice_card.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
ttk.Label(advice_card, text="本週投資建議", style="CardValue.TLabel").pack(anchor="w")
status_var = tk.StringVar(value="按下按鈕開始分析")
ttk.Label(advice_card, textvariable=status_var, style="CardTitle.TLabel").pack(anchor="w", pady=(4, 10))
advice = tk.Text(advice_card, wrap="word", borderwidth=0, bg="white",
                 fg="#344054", font=("Microsoft JhengHei UI", 11),
                 padx=2, pady=2, height=13)
advice.pack(fill="both", expand=True)
advice.insert("1.0", "尚未產生建議。")
advice.configure(state="disabled")
ttk.Button(advice_card, text="產生本週建議", command=generate,
           style="Action.TButton").pack(anchor="e", pady=(14, 0))

root.mainloop()
