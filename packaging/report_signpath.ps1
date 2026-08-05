[CmdletBinding()]
param(
    [string[]] $Artifacts = @(
        "signed\dist\StarCompanion.exe",
        "signed\dist\starcompanion-cli.exe"
    ),
    [string] $Report = "signed\dist\authenticode-report.json",
    [string] $ExpectedSubject = "SignPath Foundation"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Authenticode verification is supported only on Windows."
}

# Resolve the exact signed pair.
$resolved = @($Artifacts | ForEach-Object {
    (Get-Item -LiteralPath $_ -ErrorAction Stop).FullName
})
$names = @($resolved | ForEach-Object { [IO.Path]::GetFileName($_) } | Sort-Object)
$required = @("StarCompanion.exe", "starcompanion-cli.exe") | Sort-Object
if ($resolved.Count -ne 2 -or (Compare-Object $names $required).Count -ne 0) {
    throw "The SignPath result must contain exactly the GUI and CLI executables."
}
$artifactRoot = Split-Path -Parent (Split-Path -Parent $resolved[0])
$presentExecutables = @(
    Get-ChildItem -LiteralPath $artifactRoot -Recurse -File -Filter "*.exe"
)
if ($presentExecutables.Count -ne 2) {
    throw "The signed artifact contains an unexpected executable."
}

$hashes = [ordered]@{}
$certificate = $null
$timestampAuthorities = [ordered]@{}
foreach ($artifact in $resolved) {
    $signature = Get-AuthenticodeSignature -LiteralPath $artifact
    if ($signature.Status -ne "Valid") {
        throw "Authenticode verification failed for ${artifact}: $($signature.Status)"
    }
    if ($signature.SignerCertificate.Subject -notlike "*$ExpectedSubject*") {
        throw "Unexpected SignPath signer for $artifact."
    }
    if ($null -eq $signature.TimeStamperCertificate) {
        throw "The required timestamp is absent from $artifact."
    }
    if ($null -eq $certificate) {
        $certificate = $signature.SignerCertificate
    } elseif ($certificate.Thumbprint -ne $signature.SignerCertificate.Thumbprint) {
        throw "The signed executables use different certificates."
    }
    $timestamp = $signature.TimeStamperCertificate
    $timestampAuthorities[[IO.Path]::GetFileName($artifact)] = [ordered]@{
        subject = $timestamp.Subject
        thumbprint = $timestamp.Thumbprint
        not_after_utc = $timestamp.NotAfter.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    $hashes[[IO.Path]::GetFileName($artifact)] = (
        Get-FileHash -LiteralPath $artifact -Algorithm SHA256
    ).Hash.ToLowerInvariant()
}

# Bind verified signer metadata to the signed bytes.
$reportObject = [ordered]@{
    schema = "starcompanion.authenticode.v2"
    status = "valid"
    provider = "SignPath.io"
    certificate = [ordered]@{
        subject = $certificate.Subject
        thumbprint = $certificate.Thumbprint
        not_after_utc = $certificate.NotAfter.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    timestamp_authorities = $timestampAuthorities
    artifacts = $hashes
}
$reportPath = [IO.Path]::GetFullPath($Report)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($reportPath)) | Out-Null
$reportObject | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding utf8
