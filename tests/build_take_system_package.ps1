param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = Join-Path $workspace "blender_take_system"
if (-not $OutputPath) {
    $OutputPath = Join-Path $workspace "dist\blender_take_system_v0_6_2.zip"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $workspace $OutputPath
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}
$temporaryOutputPath = Join-Path $outputDirectory (
    ".{0}.{1}.tmp" -f
    [System.IO.Path]::GetFileName($OutputPath),
    [guid]::NewGuid().ToString("N")
)

$files = @(
    "__init__.py",
    "model.py",
    "engine.py",
    "recent.py",
    "recording.py",
    "operators.py",
    "ui.py",
    "README.md"
)

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

try {
$archive = [System.IO.Compression.ZipFile]::Open(
    $temporaryOutputPath,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($file in $files) {
        $sourcePath = Join-Path $sourceRoot $file
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            throw "Package source file is missing: $sourcePath"
        }
        $entryName = "blender_take_system/$file"
        $entry = $archive.CreateEntry(
            $entryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        $input = [System.IO.File]::OpenRead($sourcePath)
        $output = $entry.Open()
        try {
            $input.CopyTo($output)
        }
        finally {
            $output.Dispose()
            $input.Dispose()
        }
    }
}
finally {
    $archive.Dispose()
}

$expectedEntries = $files | ForEach-Object { "blender_take_system/$_" }
$verificationArchive = [System.IO.Compression.ZipFile]::OpenRead(
    $temporaryOutputPath
)
try {
    $actualEntries = @($verificationArchive.Entries | ForEach-Object { $_.FullName })
    if (
        $actualEntries.Count -ne $expectedEntries.Count -or
        (Compare-Object $expectedEntries $actualEntries)
    ) {
        throw "Package entry list does not match the distributable source list"
    }

    foreach ($file in $files) {
        $entryName = "blender_take_system/$file"
        $entry = $verificationArchive.GetEntry($entryName)
        $sourcePath = Join-Path $sourceRoot $file
        $entryStream = $entry.Open()
        $sourceStream = [System.IO.File]::OpenRead($sourcePath)
        $entryHasher = [System.Security.Cryptography.SHA256]::Create()
        $sourceHasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $entryHash = [BitConverter]::ToString(
                $entryHasher.ComputeHash($entryStream)
            )
            $sourceHash = [BitConverter]::ToString(
                $sourceHasher.ComputeHash($sourceStream)
            )
        }
        finally {
            $sourceHasher.Dispose()
            $entryHasher.Dispose()
            $sourceStream.Dispose()
            $entryStream.Dispose()
        }
        if ($entryHash -ne $sourceHash) {
            throw "Package entry does not match source: $entryName"
        }
    }
}
finally {
    $verificationArchive.Dispose()
}

if (Test-Path -LiteralPath $OutputPath) {
    [System.IO.File]::Copy($temporaryOutputPath, $OutputPath, $true)
    [System.IO.File]::Delete($temporaryOutputPath)
}
else {
    [System.IO.File]::Move($temporaryOutputPath, $OutputPath)
}
}
catch {
    if (Test-Path -LiteralPath $temporaryOutputPath) {
        Remove-Item -LiteralPath $temporaryOutputPath -Force
    }
    throw
}

Write-Output "TAKE_SYSTEM_PACKAGE_OK $OutputPath"
