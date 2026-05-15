# ══════════════════════════════════════════════════════════════
# Lyra Decoupling — Backend-Rentus Cleanup Script
# ══════════════════════════════════════════════════════════════
# Run this from PowerShell to remove all legacy Lyra monolithic code
# from backend-rentus. The frontend now calls Lyra directly.
#
# IMPORTANT: The LyraController.php is kept as a thin proxy
# for backward compatibility. Remove it later when transition is complete.
# ══════════════════════════════════════════════════════════════

$base = "C:\xampp\htdocs\backend-rentus"

Write-Host "`n🧹 Cleaning legacy Lyra code from backend-rentus..." -ForegroundColor Cyan

# ── 1. Monolithic Services (the BIG files) ───────────────────
$services = @(
    "app\Services\LyraBridge.php",           # Bridge to microservice (replaced by direct frontend calls)
    "app\Services\LyraBrainService.php",     # 2564-line monolithic NLP engine
    "app\Services\LocalInterpreterEngine.php", # 2500+ line regex intent parser
    "app\Services\LyraVoicePersonality.php", # 3700-line personality templates
    "app\Services\LyraContextService.php",
    "app\Services\LyraStatusService.php",
    "app\Services\LyraStatsService.php",
    "app\Services\LyraSessionService.php",
    "app\Services\LyraHealthService.php",
    "app\Services\LyraConfigService.php",
    "app\Services\LyraVersionService.php",
    "app\Services\FilterEngine.php",
    "app\Services\GeoLocationEngine.php"
)

# ── 2. Admin Controllers ─────────────────────────────────────
$controllers = @(
    "app\Http\Controllers\Admin\AdminLyraController.php",
    "app\Http\Controllers\Admin\AdminLyraHealthController.php",
    "app\Http\Controllers\Admin\AdminLyraAlertController.php",
    "app\Http\Controllers\Admin\AdminLyraVersionController.php",
    "app\Http\Controllers\Admin\AdminLyraConfigController.php",
    "app\Http\Controllers\Admin\AdminLyraSessionController.php"
)

# ── 3. Models ─────────────────────────────────────────────────
$models = @(
    "app\Models\LyraSession.php",
    "app\Models\LyraMessage.php",
    "app\Models\LyraVersion.php",
    "app\Models\LyraConfig.php",
    "app\Models\LyraServiceIncident.php",
    "app\Models\LyraAdminAlert.php"
)

# ── 4. Events ─────────────────────────────────────────────────
$events = @(
    "app\Events\LyraStatusChanged.php",
    "app\Events\LyraSessionUpdated.php",
    "app\Events\LyraSessionDeleted.php",
    "app\Events\LyraNewSession.php",
    "app\Events\LyraMessageNew.php"
)

# ── 5. Observers ──────────────────────────────────────────────
$observers = @(
    "app\Observers\LyraSessionObserver.php"
)

# Combine all
$allFiles = $services + $controllers + $models + $events + $observers

$deleted = 0
$skipped = 0

foreach ($file in $allFiles) {
    $fullPath = Join-Path $base $file
    if (Test-Path $fullPath) {
        Remove-Item $fullPath -Force
        Write-Host "  ✅ Deleted: $file" -ForegroundColor Green
        $deleted++
    } else {
        Write-Host "  ⏭️  Not found: $file" -ForegroundColor DarkGray
        $skipped++
    }
}

Write-Host "`n📊 Summary: $deleted files deleted, $skipped already gone" -ForegroundColor Yellow
Write-Host ""
Write-Host "✅ Backend-rentus Lyra cleanup complete!" -ForegroundColor Green
Write-Host "   Remaining: LyraController.php (thin proxy, remove when ready)" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "📋 Next steps:" -ForegroundColor Cyan
Write-Host "   1. Run: php artisan route:cache  (refresh route cache)" -ForegroundColor White
Write-Host "   2. Verify Rentus still works for property browsing" -ForegroundColor White
Write-Host "   3. Test Lyra at: http://localhost:8000/docs" -ForegroundColor White
