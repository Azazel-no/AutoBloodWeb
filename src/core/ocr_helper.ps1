# Windows built-in OCR (Windows.Media.Ocr) for the overlay's nickname matching.
# Reads one PNG path per line on stdin and writes one JSON line per image:
# {"path":..., "results":[{"lang":"en-US","text":"...","words":[{text,x,y,w,h}]}, ...]}. Local only: no network.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.Wait() | Out-Null
    return $task.Result
}
$engines = @()
foreach ($tag in @('en-US', 'ru')) {
    $language = New-Object Windows.Globalization.Language $tag
    if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($language)) {
        $engines += [pscustomobject]@{ tag = $tag; engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language) }
    }
}
[Console]::Out.WriteLine((@{ ready = $true; languages = @($engines | ForEach-Object { $_.tag }) } | ConvertTo-Json -Compress))
[Console]::Out.Flush()
while ($true) {
    $path = [Console]::In.ReadLine()
    if ($null -eq $path -or $path -eq '') { break }
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $results = @()
        foreach ($item in $engines) {
            $result = Await ($item.engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $words = @()
            foreach ($line in $result.Lines) {
                foreach ($word in $line.Words) {
                    $r = $word.BoundingRect
                    $words += @{ text = $word.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
                }
            }
            $results += @{ lang = $item.tag; text = $result.Text; words = $words }
        }
        $stream.Dispose()
        [Console]::Out.WriteLine((@{ path = $path; results = $results } | ConvertTo-Json -Compress -Depth 4))
    } catch {
        [Console]::Out.WriteLine((@{ path = $path; error = $_.Exception.Message } | ConvertTo-Json -Compress))
    }
    [Console]::Out.Flush()
}
