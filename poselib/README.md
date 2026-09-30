# 姿勢參考庫

把自己的姿勢參考圖（PNG / JPG / WebP，建議綠幕底、全身）放在這個資料夾，重新整理頁面就會出現在「姿勢修改」的參考清單。

`poselib.json` 可以替每張圖設定顯示名稱與對應的姿勢預設，例如：

```json
{
  "stand.png":  { "label": "站立待機", "pose": "stand" },
  "crouch.png": { "label": "蹲下待機", "pose": "crouch" }
}
```

原作者使用的參考圖含第三方角色，未隨原始碼公開。
