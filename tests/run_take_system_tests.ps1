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
$env:PYTHONDONTWRITEBYTECODE = "1"
Write-Output "TAKE_SYSTEM_BLENDER $blender"

& (Join-Path $PSScriptRoot "build_take_system_package.ps1")

$testScripts = @(
    "take_system_core_test.py",
    "take_system_apply_noop_test.py",
    "take_system_propertygroup_stability_test.py",
    "take_system_hierarchy_test.py",
    "take_system_operator_test.py",
    "take_system_ui_test.py",
    "take_system_collection_state_test.py",
    "take_system_recent_action_test.py",
    "take_system_recording_test.py",
    "take_system_recent_perf_test.py",
    "take_system_phase5_test.py",
    "take_system_batch_render_test.py",
    "take_system_persistence_test.py"
)

foreach ($testScript in $testScripts) {
    $testPath = Join-Path $PSScriptRoot $testScript
    & $blender `
        --background `
        --factory-startup `
        --python-exit-code 1 `
        --python $testPath
    if ($LASTEXITCODE -ne 0) {
        throw "Take System test failed: $testScript"
    }
}

$testState = Join-Path $workspace ".take_system_test"
$env:BLENDER_USER_SCRIPTS = Join-Path $testState "blender_user_scripts"
$env:BLENDER_USER_CONFIG = Join-Path $testState "blender_user_config"
$installTest = Join-Path $PSScriptRoot "take_system_install_test.py"
& $blender `
    --background `
    --factory-startup `
    --python-exit-code 1 `
    --python $installTest
if ($LASTEXITCODE -ne 0) {
    throw "Take System packaged-install test failed"
}

Write-Output "TAKE_SYSTEM_ALL_TESTS_OK"
