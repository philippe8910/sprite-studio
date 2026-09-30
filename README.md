# Sprite Studio

**從一張角色立繪，到遊戲可用的 sprite sheet。**
用 AI 影片模型生成角色動作，再自動抽幀、去背、找循環、對齊、打包成遊戲素材；
失敗時依類型分類，只重做壞掉的那一支動作。

*From a single character illustration to a game-ready sprite sheet — AI video generation,
automatic frame extraction, background removal, loop detection, alignment and packing,
with per-animation failure classification so only broken animations get regenerated.*

```
組合提示詞生立繪 ─► 套姿勢參考生候選 ─► 挑一張
      ─► 套動作預設 ─► 影片生成（fal.ai：Kling / Seedance …，或本機 ComfyUI）
      ─► 抽幀・色鍵去背・循環偵測・腳底對齊 ─► 挑幀（即時預覽）
      ─► 打包 sprite strips + anims.json ─► 自動品質評估 ─► 只重做失敗的動作
```

## 重點設計

| 問題 | 做法 | 程式位置 |
|---|---|---|
| 新模型一直上架、參數各不相同 | 執行期向 fal.ai 取模型清單與每個模型的 OpenAPI input schema（清單快取 12 小時、schema 7 天），依 schema 組 payload、鉗制 enum；取不到時退回內建組法。新模型上架不用改程式 | `falcatalog.py` |
| 長時間生成任務 | fal queue 提交 → 輪詢狀態 → 下載結果；金鑰以「查不存在的 request id」零成本驗證（401 / 200） | `falclient.py` |
| 成本看不見 | 每次生成累計花費顯示在右上角；未公布價格的模型顯示「價格未知」而不是 $0 | `server.py`、`static/app.js` |
| 雲端與本機模型並存 | 同一流程可切換 fal.ai 或本機 ComfyUI / A1111 workflow | `localgen.py`、`workflows/` |
| 生成品質不穩 | 打包後自動評估，依失敗類型標出要重做的動作（見下） | `tools/eval_sprites.py` |

## 自動品質評估

```bash
python tools/eval_sprites.py <素材資料夾> --json report.json
```

逐個動作檢查：空白幀、連續重複幀（停格，列為人工確認）、循環接縫跳動或停頓、
原地動作的腳底漂移、左右被畫框切到。任何一項不過即判失敗並附上失敗類型。

**實測**（以此管線產出、已上線的網頁遊戲素材）：

| 動作數 | 素材組 | 通過 | 通過率 | 失敗類型 | 需人工確認（停格） |
|---|---|---|---|---|---|
| 121 | 19 | 109 | 90.1% | 被畫框切到 8、循環接縫跳動 4 | 18 |

規則經過抽查修正：射擊類每發重播，接縫本來就是開火瞬間，不算循環跳動；
跑步類腳本來就會離地，不檢查腳底；倒地角色貼齊下緣是地面基準線，不算裁切。

## 啟動

```bash
pip install -r requirements.txt
cp settings.example.json settings.json     # Windows：copy
python server.py                            # 或雙擊 run.bat
```

開 <http://127.0.0.1:8765>，右上角「API 金鑰」貼上 fal.ai 金鑰（`id:secret`）。
金鑰來源優先序：設定頁 → 環境變數 `FAL_KEY` → 環境變數 `FAL_SECRETS_JSON` 指向的 JSON（`falApiKey` 欄位）。
`settings.json` 已列入 `.gitignore`，不會被提交。

## 結構

| 檔案 | 用途 |
|---|---|
| `server.py` | Flask 後端：專案、候選圖、影片、抽幀、打包 API |
| `pipeline.py` | 抽幀、色鍵去背、循環偵測、對齊、打包 |
| `falclient.py` / `falcatalog.py` | fal.ai 佇列客戶端與線上模型目錄 |
| `localgen.py` | 本機 ComfyUI / A1111 生成 |
| `presets.py` / `poselib/` | 動作提示詞預設與姿勢參考 |
| `gamespec.py` | 從既有遊戲讀取素材慣例（格子大小、腳底錨點） |
| `static/` | 無框架前端（單頁） |
| `tools/eval_sprites.py` | 自動品質評估 |
| `tools/build_mac.py` | 產生 macOS 可攜版 |
| `docs/NOTES_zh.md` | 詳細操作筆記 |

## 開發方式

以 Claude Code 為主要開發夥伴：先定義驗收條件與量測方式，再讓 AI 實作，最後以實際素材與評估工具驗證。
