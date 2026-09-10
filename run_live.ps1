# Wrapper live-watch bid/offer (dijadwalkan via Task Scheduler ~10:00 WIB hari bursa).
# Baca live_today.txt (subset setup MARKUP terbaik hasil scan EOD, top-N by confidence
# — bukan 50 kode watchlist, biar hemat kuota). Jalan 2 jam, ping Telegram saat sinyal.
# Karena unattended: andalkan ping Telegram + file log buat di-review nanti.
Set-Location 'C:\Users\Wahyu.afandi\IDX_Trecker_Wahyu'
$log = "$HOME\Downloads\live_$(Get-Date -Format yyyyMMdd_HHmm).txt"
.\.venv\Scripts\python.exe -u scripts\live_watch.py --codes-file live_today.txt `
    --telegram --duration 120 | Tee-Object -FilePath $log
