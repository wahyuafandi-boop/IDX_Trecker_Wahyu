# Wrapper konfirmasi EOD (dijadwalkan via Task Scheduler ~19:05 WIB hari bursa).
# Baca watchlist_today.txt (hasil screening Stockbit). Konfirmasi akumulasi broker.
Set-Location 'C:\Users\Wahyu.afandi\IDX_Trecker_Wahyu'
$log = "$HOME\Downloads\eod_$(Get-Date -Format yyyyMMdd).txt"
.\.venv\Scripts\python.exe scripts\run_daily.py --codes-file watchlist_today.txt 2>&1 |
    Tee-Object -FilePath $log
