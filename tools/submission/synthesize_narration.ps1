param([int]$Rate = 2)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$workDir = Join-Path $projectRoot 'artifacts/submission-video'
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
Add-Type -AssemblyName System.Speech
$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synthesizer.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
if (-not $voice) { throw 'No Mandarin Windows speech voice is installed.' }
$synthesizer.SelectVoice($voice.VoiceInfo.Name)
$synthesizer.Rate = $Rate
$synthesizer.Volume = 100
$storyboard = Get-Content (Join-Path $projectRoot 'docs/video-storyboard.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$segments = @()
$number = 0
for ($scene = 0; $scene -lt $storyboard.Count; $scene++) {
    # Sentence-level audio keeps burned-in subtitles synchronized without guessing timing.
    $sentences = [regex]::Matches($storyboard[$scene].speech, '[^\u3002\uFF01\uFF1F]+[\u3002\uFF01\uFF1F]?')
    foreach ($sentence in $sentences) {
        $number++
        $wavName = '{0:D3}.wav' -f $number
        $synthesizer.SetOutputToWaveFile((Join-Path $workDir $wavName))
        $synthesizer.Speak($sentence.Value)
        $synthesizer.SetOutputToNull()
        $segments += [pscustomobject]@{ scene = $scene; text = $sentence.Value; wav = $wavName }
    }
}
$synthesizer.Dispose()
$metadata = [pscustomobject]@{ voice = $voice.VoiceInfo.Name; rate = $Rate; source = 'Windows System.Speech synthetic Mandarin'; segments = $segments }
$metadata | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $workDir 'narration.json') -Encoding UTF8
Write-Output "Generated $number Mandarin sentence audio clips."
