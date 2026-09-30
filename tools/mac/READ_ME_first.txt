Sprite Studio - macOS 使用說明
=================================

【怎麼開】

1. 把整個 SpriteStudio 資料夾解壓縮到你要放的地方
   （建議：使用者資料夾底下，例如 ~/SpriteStudio，不要放在唯讀的磁碟或 iCloud）

2. 對「Start Sprite Studio.command」按右鍵 -> 打開 -> 再按一次「打開」
   ★ 第一次一定要用右鍵，不能直接雙擊。
     macOS 對網路下載來的腳本會擋，右鍵打開才會出現「仍要打開」的按鈕。
     之後每次就可以直接雙擊了。

3. 第一次啟動會自己建立 .venv 並下載 flask / numpy / opencv，
   約 1~3 分鐘（要有網路）。之後啟動只要幾秒。

4. 瀏覽器會自動打開 http://127.0.0.1:8765

【怎麼關】
   回到那個黑色終端機視窗，按 Control + C，或直接把視窗關掉。

【API 金鑰】
   金鑰沒有打包進來。第一次開啟後，點網頁右上角「API 金鑰」填入 fal.ai 的
   key，會存到這個資料夾的 settings.json，之後就不用再填。
   （也可以改用環境變數 FAL_KEY。）

【資料放哪】
   所有專案素材都在這個資料夾底下的 data/ 裡，
   整包搬走 = 資料一起搬走，不會寫到系統其他地方。
   這一包是乾淨的工具，沒有帶 Windows 上既有的專案；
   要搬舊專案的話，把 Windows 上 sprite_studio\data\projects\ 底下的
   資料夾整個複製到 Mac 這邊的 data/projects/ 即可。

【出問題時】

. 雙擊沒反應 / 顯示「無法打開，因為來自未識別的開發者」
    -> 用上面第 2 點的右鍵打開。
       還是不行就開「終端機」，輸入 bash 空格，再把
       Start Sprite Studio.command 拖進終端機視窗，按 Enter。

. 顯示「找不到 Python 3.9 以上」
    -> 終端機執行： xcode-select --install
       或到 https://www.python.org/downloads/macos/ 下載安裝，
       裝完再雙擊一次。

. 顯示「套件安裝失敗」
    -> 通常是沒網路。連上網後再試一次即可（會接續安裝）。

. 想整個重來
    -> 把資料夾裡的 .venv 刪掉（隱藏資料夾，Finder 按 Command+Shift+. 顯示），
       再雙擊一次，會重新建立。

. 說「8765 已經有 Sprite Studio 在跑了」但其實沒有
    -> 終端機執行： lsof -nP -iTCP:8765 -sTCP:LISTEN
       看是誰佔著那個埠，或直接重開機。

【技術細節（給自己看的）】
   - 純 Python，沒有原生執行檔，所以同一包在 Intel / Apple Silicon 都能跑。
   - 相依：flask、numpy、opencv-python-headless（headless 版沒有 GUI 相依，
     這個工具只用 cv2 讀影片/處理影像，不開視窗）。
   - 伺服器只綁 127.0.0.1，外面連不進來。
   - 啟動指令等同於：source .venv/bin/activate && python server.py
