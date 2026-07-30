param(
    [string]$BlenderPath = ""
)

$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$blender = $BlenderPath
if (-not $blender -and $env:BLENDER_EXECUTABLE) {
    $blender = $env:BLENDER_EXECUTABLE
}
if (-not $blender) {
    $installRoot = "C:\Program Files\Blender Foundation"
    $blender = Get-ChildItem -LiteralPath $installRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Blender \d+\.\d+$' } |
        Sort-Object {
            [version]($_.Name -replace '^Blender ', '')
        } -Descending |
        ForEach-Object { Join-Path $_.FullName "blender.exe" } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
if (-not (Test-Path -LiteralPath $blender)) {
    throw "Blender executable not found: $blender"
}
$blender = (Resolve-Path -LiteralPath $blender).Path
$testRoot = Join-Path $workspace ".take_system_test"
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$scriptPath = Join-Path $PSScriptRoot "take_system_gui_smoke_test.py"

function Invoke-TakeSystemGuiSmoke {
    param(
        [string]$Name,
        [int]$Width,
        [int]$Height,
        [bool]$UseExistingProperties,
        [bool]$OpenReviewDialog,
        [bool]$OpenSettingsDialog
    )

    $screenshot = Join-Path $testRoot "take_system_gui_$Name.png"
    $stdoutPath = Join-Path $testRoot "take_system_gui_$Name.out.log"
    $stderrPath = Join-Path $testRoot "take_system_gui_$Name.err.log"
    $env:TAKE_SYSTEM_GUI_SCREENSHOT = $screenshot
    if ($UseExistingProperties) {
        $env:TAKE_SYSTEM_GUI_USE_EXISTING = "1"
    }
    else {
        Remove-Item Env:TAKE_SYSTEM_GUI_USE_EXISTING -ErrorAction SilentlyContinue
    }
    if ($OpenReviewDialog) {
        $env:TAKE_SYSTEM_GUI_REVIEW_DIALOG = "1"
    }
    else {
        Remove-Item Env:TAKE_SYSTEM_GUI_REVIEW_DIALOG -ErrorAction SilentlyContinue
    }
    if ($OpenSettingsDialog) {
        $env:TAKE_SYSTEM_GUI_SETTINGS_DIALOG = "1"
    }
    else {
        Remove-Item Env:TAKE_SYSTEM_GUI_SETTINGS_DIALOG -ErrorAction SilentlyContinue
    }

    $process = Start-Process `
        -FilePath $blender `
        -ArgumentList @(
            "--factory-startup",
            "--enable-event-simulate",
            "-p",
            "0",
            "0",
            "$Width",
            "$Height",
            "--python",
            $scriptPath
        ) `
        -WorkingDirectory $workspace `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru
    if (-not $process.WaitForExit(30000)) {
        Stop-Process -Id $process.Id -Force
        throw "GUI smoke test timed out: $Name"
    }

    $stdout = Get-Content -LiteralPath $stdoutPath -Raw
    $stderr = Get-Content -LiteralPath $stderrPath -Raw
    if ($stdout -notmatch "TAKE_SYSTEM_GUI_SMOKE_OK") {
        throw "GUI smoke test failed: $Name`n$stdout`n$stderr"
    }
    if ($stderr -match "Traceback|Error: Python") {
        throw "GUI draw error: $Name`n$stderr"
    }
    if (
        -not (Test-Path -LiteralPath $screenshot) -or
        (Get-Item -LiteralPath $screenshot).Length -le 0
    ) {
        throw "GUI smoke screenshot is missing or empty: $Name"
    }
    Write-Output "TAKE_SYSTEM_GUI_OK $Name $stdout"
}

try {
    Invoke-TakeSystemGuiSmoke `
        -Name "wide" `
        -Width 1400 `
        -Height 900 `
        -UseExistingProperties $false `
        -OpenReviewDialog $false `
        -OpenSettingsDialog $false
    Invoke-TakeSystemGuiSmoke `
        -Name "narrow" `
        -Width 1920 `
        -Height 1000 `
        -UseExistingProperties $true `
        -OpenReviewDialog $false `
        -OpenSettingsDialog $false
    Invoke-TakeSystemGuiSmoke `
        -Name "review" `
        -Width 1400 `
        -Height 900 `
        -UseExistingProperties $false `
        -OpenReviewDialog $true `
        -OpenSettingsDialog $false
    Invoke-TakeSystemGuiSmoke `
        -Name "settings" `
        -Width 1400 `
        -Height 900 `
        -UseExistingProperties $false `
        -OpenReviewDialog $false `
        -OpenSettingsDialog $true
}
finally {
    Remove-Item Env:TAKE_SYSTEM_GUI_SCREENSHOT -ErrorAction SilentlyContinue
    Remove-Item Env:TAKE_SYSTEM_GUI_USE_EXISTING -ErrorAction SilentlyContinue
    Remove-Item Env:TAKE_SYSTEM_GUI_REVIEW_DIALOG -ErrorAction SilentlyContinue
    Remove-Item Env:TAKE_SYSTEM_GUI_SETTINGS_DIALOG -ErrorAction SilentlyContinue
}

Write-Output "TAKE_SYSTEM_ALL_GUI_TESTS_OK"
