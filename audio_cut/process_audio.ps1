param(
    [string]$InputDir = (Join-Path $PSScriptRoot "input"),
    [string]$OutputDir = (Join-Path $PSScriptRoot "outputs"),
    [double]$SilenceThresholdDb = -45.0,
    [double]$MinSilenceDuration = 0.30,
    [string]$AudioBitrate = "32k",
    [int]$AudioSampleRate = 24000,
    [switch]$KeepStereo
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pythonScript = Join-Path $PSScriptRoot "process_audio.py"
if (-not (Test-Path -LiteralPath $pythonScript)) {
    throw "Python script not found: $pythonScript"
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python not found in PATH."
}

$arguments = @(
    $pythonScript,
    "--input-dir", $InputDir,
    "--output-dir", $OutputDir,
    "--silence-threshold-db", $SilenceThresholdDb.ToString([System.Globalization.CultureInfo]::InvariantCulture),
    "--min-silence-duration", $MinSilenceDuration.ToString([System.Globalization.CultureInfo]::InvariantCulture),
    "--audio-bitrate", $AudioBitrate,
    "--audio-sample-rate", $AudioSampleRate.ToString()
)

if ($KeepStereo) {
    $arguments += "--keep-stereo"
}

& $pythonCommand.Source @arguments
exit $LASTEXITCODE
