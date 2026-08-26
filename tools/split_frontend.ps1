# -*- pwsh -*-
# ⚠️ 历史一次性迁移脚本（已执行完毕）：重跑会以旧头部覆盖 dashboard/js/*.js，
#    冲掉后续评审修复（跨模块导入、S._currentStockName、getSbSection、MA ref 缓存等）。
#    仅作迁移过程留档；日常校验请用 check_modules.mjs / check_crossref.mjs。
# frontend-improvements-y7 #13：把 dashboard/app.js 按域切分为 ES modules。
# 只做行段搬运与导入头注入，不改业务逻辑。执行后需跑 node 链接检查器验证。
$ErrorActionPreference = 'Stop'
$root = "D:\WorkStation\backless\stock-y7\.worktrees\frontend-improvements-y7"
$src = Join-Path $root "dashboard\app.js"
$outDir = Join-Path $root "dashboard\js"
New-Item -ItemType Directory -Force $outDir | Out-Null

$L = Get-Content $src -Encoding UTF8
function Slice([int]$a, [int]$b) { return ($L[($a-1)..($b-1)] -join "`r`n") }

# ---------- 各域行段清单（含端点） ----------
$apiR    = @(@(2,2), @(2115,2129), @(2667,2680))
$sharedR = @(,@(107,121))
$uiR     = @(@(1306,1344), @(1356,1399), @(1455,1549), @(1998,2076), @(2600,2665), @(3150,3157), @(4078,4100), @(4746,4781))
$watchR  = @(@(2357,2599), @(2682,2749), @(2757,2821), @(2825,3023), @(3025,3047), @(3048,3090), @(3103,3149))
$chartR  = @(@(124,1302), @(1346,1354), @(2248,2355), @(2822,2824), @(3259,3521))
$scanR   = @(,@(4386,4745))
$journalR= @(@(3522,3768), @(3769,3968), @(3969,4077), @(4101,4106), @(4107,4385))

function JoinRanges($ranges) {
  ($ranges | ForEach-Object { Slice $_ [0] $_ [1] }) -join "`r`n`r`n"
}
# PowerShell: 调用语法修正
function JoinRanges2($ranges) {
  $parts = foreach ($r in $ranges) { Slice $r[0] $r[1] }
  $parts -join "`r`n`r`n"
}

function AddExport($text, $names) {
  foreach ($n in $names) {
    $text = $text -replace ("(?m)^function {0}\b" -f [regex]::Escape($n)), ("export function {0}" -f $n)
    $text = $text -replace ("(?m)^async function {0}\b" -f [regex]::Escape($n)), ("export async function {0}" -f $n)
    $text = $text -replace ("(?m)^const {0} =" -f [regex]::Escape($n)), ("export const {0} =" -f $n)
    $text = $text -replace ("(?m)^let {0} =" -f [regex]::Escape($n)), ("export let {0} =" -f $n)
  }
  return $text
}

# ---------- shared.js ----------
$sharedTxt = @"
// ==================== 跨模块共享常量与状态（improvements #13） ====================
// C：ECharts 全局主题色（原 app.js 顶部常量，多模块引用）。
export $(Slice 107 121)

// S：跨模块共享的可变状态（原 app.js 顶层 let 声明集中托管；
// 各模块统一以 S.xxx 读写，保证单一事实来源）。
export const S = {
  currentSymbol: '',      // 当前分析标的
  currentView: 'dayk',    // dayk | week | minute
  _mode: 'pro',           // pro | simple（小白模式）
  _fxLevel: 'std',        // FX 动效档位
  _klineData: [],         // K线数据缓存
  _signalLines: [],       // 止损/入场/目标水平线
  _signalPoints: [],      // 买卖点标记
  _lastSignalData: null,  // 上次 signal 数据（模式切换免请求重渲染）
  _lastSignal: {},        // 各标的上次信号 { code: {action,...} }
  _dailyChanlun: null,    // 缠论日线分析缓存
  _dailyFlows: null,      // 日级资金流缓存
  _flowMode: 'realtime',  // realtime | daily
};
"@
Set-Content (Join-Path $outDir "shared.js") $sharedTxt -Encoding UTF8

# ---------- api.js ----------
$apiNames = @('API','DEFAULT_FETCH_TIMEOUT','fetchWithTimeout','isTimeoutError','ERROR_EXPLAIN','GENERIC_ERROR_TEXT','explainError')
$apiTxt = "// ==================== 网络层（improvements #3/#5/#13） ====================`r`n" + (AddExport (JoinRanges2 $apiR) $apiNames)
Set-Content (Join-Path $outDir "api.js") $apiTxt -Encoding UTF8

# ---------- ui.js ----------
$uiHdr = @"
// ==================== 通用 UI 层（improvements #13） ====================
// 转义/事件委托/术语即点即译/风险大白话/toast/搜索推荐/首访引导/更多菜单。
import { C, S } from './shared.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze, setMode, toggleSettings, doLogout } from './main.js';
import { toggleStar, openSbSection, sbToggleCollapse, renameGroupInline, renameGroupInlineById, deleteGroup, moveStock, pinStock, removeFromWatchlist } from './watchlist.js';
import { openScan, renderArchivedRun, exportScanCsv, deleteScanRun, scanPollRetry } from './scan.js';
import { poolNote, poolMove, poolRemove, poolAddCurrent } from './journal.js';

"@
$uiNames = @('DELEGATED_ACTIONS','escHtml','closeMoreMenu','showHotStocksPanel','doSuggest','doSearch','selectStock','RISK_EXPLAIN','explainRisks','riskBannerHtml','_applyTermChips','glossarize','whyTextFor','toggleWhy','countUpScore','fxCardStagger','showToastMsg','showToast','removeToast')
$uiTxt = $uiHdr + (AddExport (JoinRanges2 $uiR) $uiNames)
Set-Content (Join-Path $outDir "ui.js") $uiTxt -Encoding UTF8

# ---------- watchlist.js ----------
$watchHdr = @"
// ==================== 自选/分组/侧边栏工作台（improvements #11/#13） ====================
import { C, S } from './shared.js';
import { escHtml, DELEGATED_ACTIONS, showToast, showToastMsg } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze } from './main.js';
import { loadOverview, loadJournal, loadPool, loadDigest, clearWatchChangeBadge } from './journal.js';
import { renderScanArchiveList } from './scan.js';
import { resizeAllChartsSafe } from './chart.js';

// 图表实例句柄由 chart.js 持有；侧边栏开合需要触发图表 resize，
// 通过注入回调避免反向依赖（由 main.js 启动时调用 registerResizeHook）。
let _resizeHook = null;
export function registerResizeHook(fn) { _resizeHook = fn; }

"@
$watchNames = @('_lsGet','_lsSet','getGroups','saveGroups','getStockMap','saveStockMap','_wlSnapshot','_wlApply','_wlScheduleSync','_wlSyncPush','_wlSyncInit','migrateWatchlist','getWatchlist','saveWatchlist','addToGroup','removeStockEverywhere','getHistory','saveHistory','toggleStar','removeFromWatchlist','updateStarButton','addHistory','clearHistory','renderWatchlist','renderHistory','sigTag','fmtTime','loadSbSection','openSbSection','toggleSbSection','renderSbSection','isMarketOpen','sidebarLoadState','toggleSidebar','applySidebar','sbBadge','renderSidebar','sbToggleCollapse','sbSelectGroup','createGroup','addGroupInline','_finishNewGroup','renameGroupInline','renameGroupInlineById','deleteGroup','moveStock','pinStock','showCtxMenu','hideCtxMenu','sbRefreshQuotes','sbSchedulePolling','switchTab','clearCurrentTab','updateBadges','exportWatchlist','importWatchlist','_syncSbTabsAria')
$watchTxt = $watchHdr + (AddExport (JoinRanges2 $watchR) $watchNames) + "`r`n`r`n// improvements #11：启动后与服务端对齐自选数据（服务端为真，本地为缓存）`r`nsetTimeout(function () { try { _wlSyncInit(); } catch (e) {} }, 400);"
Set-Content (Join-Path $outDir "watchlist.js") $watchTxt -Encoding UTF8

# ---------- chart.js ----------
$chartHdr = @"
// ==================== 图表层：K线/副图指标/分时/资金流/缠论叠加（improvements #13/#14） ====================
import { C, S } from './shared.js';
import { escHtml, glossarize } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze, fxEnabled, chartAnim, resetChartAnim, _initAuth } from './main.js';

// 模块内私有可变状态（仅本域读写，不入共享 S）
let klineChart, volumeChart, flowChart, minuteChart, minuteVolChart, indicatorChart;
let _zoomBound = false;
let _minuteData = null;
let _minuteChanlun = null;
let _minuteYRange = null;
let _currentIndicator = 'none';

"@
$chartNames = @('initCharts','switchView','calcMA','_maState','_maTail','_maSeriesFor','renderKline','bindBoxZoom','findEntryIndex','bindZoomSync','applyRange','bindChartTooltip','updateZoomInfo','syncRangeBtns','renderChanlun','renderChanlunDaily','applyChanlunDailyOverlay','renderMinute','renderFlow','_renderDailyFlow','switchFlowMode','loadRealtimeFlow','renderRealtimeFlow','_lastMA','refreshKlineLastCandle','loadMinute','refreshMinuteLight','resizeAllChartsSafe','switchIndicator','clearBolloverlay','renderIndicator','calcEMA','calcMACD','calcRSI','calcKDJ','calcBOLL','calcWR')
$chartTxt = $chartHdr + (AddExport (JoinRanges2 $chartR) $chartNames)
Set-Content (Join-Path $outDir "chart.js") $chartTxt -Encoding UTF8

# ---------- scan.js ----------
$scanHdr = @"
// ==================== 扫描买入：弹窗/轮询容错/历史归档（improvements #3/#13） ====================
import { escHtml, showToastMsg } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze } from './main.js';
import { openSbSection } from './watchlist.js';

"@
$scanNames = @('_scanTimer','STORAGE_SCAN_ARCHIVE','MAX_SCAN_ARCHIVE','getScanArchive','saveScanArchive','_scanRunSig','archiveScanRun','openScan','closeScan','renderScanIdle','startScan','startScanPolling','scanPollTick','scanPollRetry','showScanConnIssue','hideScanConnIssue','stopScanPolling','renderScanProgress','renderScanResults','_scanTableHtml','_fmtScanTime','renderScanArchiveList','renderArchivedRun','exportScanCsv','deleteScanRun','clearScanArchive','formatScanAction','analyzeFromScan','renderScanError')
$scanTxt = $scanHdr + (AddExport (JoinRanges2 $scanR) $scanNames)
Set-Content (Join-Path $outDir "scan.js") $scanTxt -Encoding UTF8

# ---------- journal.js ----------
$journalHdr = @"
// ==================== 数据面板层：一览/信号档案/核心池/速递/信号统计（improvements #13） ====================
import { C, S } from './shared.js';
import { escHtml, showToast, showToastMsg } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze } from './main.js';
import { getGroups, getStockMap, saveGroups, saveStockMap, getWatchlist, saveHistory, addHistory, getHistory, sigTag, fmtTime, sbBadge } from './watchlist.js';

// 模块内私有状态
const STORAGE_SIGNALS = 'qs_signal_records';
const MAX_SIGNAL_RECORDS = 200;

"@
$journalNames = @('loadOverview','_journalTypeNames','_symNames','_saveSymNames','_knownName','_resolveSymbolNames','_followupMap','loadJournal','_downloadText','_csvCell','_journalExportStem','exportJournalCsv','exportJournalJson','poolPost','loadPool','renderPoolPanel','poolAdd','poolAddCurrent','poolRemove','poolNote','poolMove','_infoToast','togglePoolImport','poolImportSubmit','poolFillIndustry','getSignalRecords','saveSignalRecords','recordSignal','calcSignalAccuracy','renderSignalAccuracy','checkSignalChange','clearWatchChangeBadge','_digestName','_dgCell','_dgCard','loadDigest','refreshDigest','startDigestPolling','stopDigestPolling','_digestSymbols','renderDigest','_digestSections')
$journalTxt = $journalHdr + (AddExport (JoinRanges2 $journalR) $journalNames)
Set-Content (Join-Path $outDir "journal.js") $journalTxt -Encoding UTF8

# ---------- main.js（余量 = 全文件 − 已搬运行段 − 已托管声明） ----------
$moved = @()
foreach ($r in $apiR + $sharedR + $uiR + $watchR + $chartR + $scanR + $journalR) {
  for ($i = $r[0]; $i -le $r[1]; $i++) { $moved += $i }
}
$movedSet = New-Object 'System.Collections.Generic.HashSet[int]'
foreach ($i in $moved) { [void]$movedSet.Add($i) }

$keep = for ($i = 1; $i -le $L.Count; $i++) { if (-not $movedSet.Contains($i)) { $L[$i-1] } }
$mainBody = $keep -join "`r`n"

# 删除已托管的单行声明（改为 import 或 S 提供）
$stripPatterns = @(
  '^let (currentSymbol|currentView|_mode|_fxLevel|_klineData|_signalLines|_signalPoints|_lastSignalData|_lastSignal|_dailyChanlun|_dailyFlows|_flowMode)\b.*$',
  '^const API = .*$',
  '^let klineChart, .*$',
  '^let _zoomBound = .*$',
  '^let _minuteData = .*$',
  '^let _minuteChanlun = .*$',
  '^let _minuteYRange = .*$',
  '^let _currentIndicator = .*$',
  '^const STORAGE_SIGNALS = .*$',
  '^const MAX_SIGNAL_RECORDS = .*$',
  '^const C = [\s\S]*?^\};$',
  '^_wlSyncInit\(\);.*$'
)
foreach ($p in $stripPatterns) { $mainBody = $mainBody -replace "(?m)$p", '' }

# 共享可变状态改写为 S.xxx（全部生成文件统一处理）
$sFields = @('_lastSignalData','_lastSignal','_signalPoints','_signalLines','_klineData','_dailyChanlun','_dailyFlows','_flowMode','currentSymbol','currentView','_fxLevel','_mode')
function ApplySRename($text) {
  foreach ($f in $sFields) { $text = $text -replace ("\b{0}\b" -f [regex]::Escape($f)), ("S.{0}" -f $f) }
  return $text
}
$mainBody  = ApplySRename $mainBody
foreach ($f in @('ui.js','watchlist.js','chart.js','scan.js','journal.js')) {
  $p = Join-Path $outDir $f
  $t = Get-Content $p -Raw -Encoding UTF8
  Set-Content $p (ApplySRename $t) -Encoding UTF8
}

$mainHdr = @"
// ==================== 入口模块：编排/分析流程/信号渲染/FX/设置（improvements #13） ====================
// 原 4781 行单文件按域拆分后的入口；各域见同目录 api/ui/shared/watchlist/chart/journal/scan.js。
import { C, S } from './shared.js';
import { API, fetchWithTimeout, isTimeoutError, explainError } from './api.js';
import { escHtml, glossarize, _applyTermChips, explainRisks, riskBannerHtml, whyTextFor, toggleWhy, showToastMsg, showToast, DELEGATED_ACTIONS } from './ui.js';
import { getGroups, getStockMap, getWatchlist, saveWatchlist, getHistory, saveHistory, addHistory, migrateWatchlist, toggleStar, updateStarButton, updateBadges, openSbSection, toggleSbSection, toggleSidebar, sidebarLoadState, loadSbSection, renderSbSection, applySidebar, renderSidebar, renderWatchlist, exportWatchlist, importWatchlist, sbRefreshQuotes, sbSchedulePolling, removeFromWatchlist, registerResizeHook, clearCurrentTab, addGroupInline } from './watchlist.js';
import { initCharts, switchView, calcMA, renderKline, findEntryIndex, applyRange, bindChartTooltip, updateZoomInfo, renderChanlun, renderChanlunDaily, applyChanlunDailyOverlay, renderMinute, loadMinute, refreshMinuteLight, renderFlow, switchFlowMode, loadRealtimeFlow, refreshKlineLastCandle, resizeAllChartsSafe, switchIndicator, _lastMA } from './chart.js';
import { loadOverview, loadJournal, exportJournalCsv, exportJournalJson, loadPool, poolAdd, poolAddCurrent, poolRemove, poolNote, poolMove, togglePoolImport, poolImportSubmit, poolFillIndustry, recordSignal, renderSignalAccuracy, checkSignalChange, clearWatchChangeBadge, loadDigest, refreshDigest, renderPoolPanel } from './journal.js';
import { openScan, closeScan, renderScanIdle, startScan, stopScanPolling, renderScanArchiveList, clearScanArchive, renderArchivedRun, exportScanCsv, deleteScanRun, analyzeFromScan } from './scan.js';

"@
$exposure = @"

// ==================== 静态 inline handler 的显式全局暴露清单（spec §14/A88 过渡期约定） ====================
Object.assign(window, {
  analyze, setMode, toggleSettings, closeSettings, setFx, doLogout,
  toggleStar, toggleSbSection, toggleSidebar, addGroupInline, clearCurrentTab,
  switchIndicator, switchFlowMode, toggleCard, exportWatchlist, importWatchlist,
  closeScan, openScan, startScan, renderScanIdle, clearScanArchive,
  renderScanArchiveList, renderArchivedRun, exportScanCsv, deleteScanRun, analyzeFromScan,
  toggleWhy, exportJournalCsv, exportJournalJson, poolAdd, poolImportSubmit, poolFillIndustry,
  applyRange, updateQuote, renderPoolPanel, refreshDigest, stopScanPolling,
});

// 侧边栏开合需要图表 resize（chart 实例为 chart.js 私有）
registerResizeHook(resizeAllChartsSafe);
"@
# main 需导出被其他域导入的符号
$mainBody = AddExport $mainBody @('analyze','setMode','toggleSettings','doLogout','fxEnabled','chartAnim','resetChartAnim','_initAuth')

$mainTxt = $mainHdr + $mainBody + $exposure
Set-Content (Join-Path $outDir "main.js") $mainTxt -Encoding UTF8

# ---------- 自校验：锚点必须落在正确模块 ----------
$checks = @(
  @('shared.js', 'export const C'),
  @('shared.js', 'export const S'),
  @('api.js', 'export function fetchWithTimeout'),
  @('ui.js', 'export const DELEGATED_ACTIONS'),
  @('ui.js', 'function escHtml'),
  @('ui.js', 'const ONBOARD_KEY'),
  @('watchlist.js', 'function renderSidebar'),
  @('watchlist.js', '_wlSyncInit'),
  @('chart.js', 'function renderKline'),
  @('chart.js', 'function switchIndicator'),
  @('journal.js', 'async function loadPool'),
  @('journal.js', 'async function loadDigest'),
  @('scan.js', 'function renderScanIdle')
)
$fail = $false
foreach ($ck in $checks) {
  $p = Join-Path $outDir $ck[0]
  if (-not (Select-String -Path $p -Pattern ([regex]::Escape($ck[1])) -Quiet)) {
    Write-Output ("VERIFY FAIL: {0} 缺少 {1}" -f $ck[0], $ck[1]); $fail = $true
  }
}
# main 不应再包含已搬走的锚点
foreach ($bad in @('^const C = \{', 'function renderScanIdle', 'async function loadPool', 'const DELEGATED_ACTIONS')) {
  if (Select-String -Path (Join-Path $outDir 'main.js') -Pattern $bad -Quiet) {
    Write-Output ("VERIFY FAIL: main.js 仍含 {0}" -f $bad); $fail = $true
  }
}

# 去重：原文件存在两个同名 removeFromWatchlist（脚本语义后者生效），ESM 需只保留后者
$wlPath = Join-Path $outDir 'watchlist.js'
$wl = Get-Content $wlPath -Raw -Encoding UTF8
$dupFirst = [regex]::Escape("export function removeFromWatchlist(code) {`r`n  removeStockEverywhere(code);`r`n  updateStarButton(S.currentSymbol);`r`n  renderWatchlist();`r`n  updateBadges();`r`n}`r`n`r`n")
$wl2 = [regex]::Replace($wl, $dupFirst, '', 'Multiline')
if ($wl -eq $wl2) { Write-Output 'WARN: removeFromWatchlist 第一声明未按字面匹配，跳过去重' }
Set-Content $wlPath $wl2 -Encoding UTF8

Write-Output ("split done. main.js lines: " + ((Get-Content (Join-Path $outDir "main.js")).Count))
if ($fail) { exit 1 } else { Write-Output 'VERIFY OK' }

