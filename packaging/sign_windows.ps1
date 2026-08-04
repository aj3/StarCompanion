[CmdletBinding()]
param(
    [string[]] $Artifacts = @(
        "dist\StarCompanion.exe",
        "dist\starcompanion-cli.exe"
    ),
    [string] $Report = "dist\authenticode-report.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Authenticode signing is supported only on Windows."
}

function Get-RequiredEnvironmentValue([string] $Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required signing setting $Name is absent."
    }
    return $value
}

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $kitsRoot = [Environment]::GetFolderPath("ProgramFilesX86")
    $candidates = @(Get-ChildItem -Path (
        Join-Path $kitsRoot "Windows Kits\10\bin\*\x64\signtool.exe"
    ) -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)
    if ($candidates.Count -eq 0) {
        throw "signtool.exe was not found; install the Windows SDK signing tools."
    }
    return $candidates[0].FullName
}

function Invoke-SignTool([string] $SignTool, [string[]] $Arguments) {
    & $SignTool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "signtool.exe failed with exit code $LASTEXITCODE."
    }
}

$pfxBase64 = Get-RequiredEnvironmentValue "STARCOMPANION_SIGNING_PFX_BASE64"
$pfxPassword = Get-RequiredEnvironmentValue "STARCOMPANION_SIGNING_PFX_PASSWORD"
$expectedThumbprint = (
    Get-RequiredEnvironmentValue "STARCOMPANION_SIGNING_CERT_THUMBPRINT"
) -replace "\s", ""
$expectedThumbprint = $expectedThumbprint.ToUpperInvariant()
if ($expectedThumbprint -notmatch '^[0-9A-F]{40}$') {
    throw "STARCOMPANION_SIGNING_CERT_THUMBPRINT must be a SHA-1 thumbprint."
}
$timestampUrl = Get-RequiredEnvironmentValue "STARCOMPANION_TIMESTAMP_URL"
$parsedTimestampUrl = $null
if (-not [Uri]::TryCreate($timestampUrl, [UriKind]::Absolute, [ref]$parsedTimestampUrl)) {
    throw "STARCOMPANION_TIMESTAMP_URL must be an absolute HTTP(S) URL."
}
if (
    $parsedTimestampUrl.Scheme -notin @("http", "https") -or
    -not [string]::IsNullOrEmpty($parsedTimestampUrl.UserInfo) -or
    -not [string]::IsNullOrEmpty($parsedTimestampUrl.Query) -or
    -not [string]::IsNullOrEmpty($parsedTimestampUrl.Fragment)
) {
    throw "The timestamp URL cannot contain credentials, a query, or a fragment."
}
if ($pfxBase64.Length -gt 4MB) {
    throw "The encoded signing certificate exceeds the 4 MiB safety limit."
}

$resolvedArtifacts = @($Artifacts | ForEach-Object {
    $item = Get-Item -LiteralPath $_ -ErrorAction Stop
    if ($item.Extension -ine ".exe") {
        throw "Refusing to sign a non-executable artifact: $($item.FullName)"
    }
    $item.FullName
})
if ($resolvedArtifacts.Count -ne 2) {
    throw "Exactly the GUI and CLI executables must be signed together."
}
$artifactNames = @($resolvedArtifacts | ForEach-Object {
    [IO.Path]::GetFileName($_)
} | Sort-Object)
$requiredNames = @("StarCompanion.exe", "starcompanion-cli.exe") | Sort-Object
if ((Compare-Object $artifactNames $requiredNames).Count -ne 0) {
    throw "The signing set must be StarCompanion.exe and starcompanion-cli.exe."
}

$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) (
    "starcompanion-signing-" + [Guid]::NewGuid().ToString("N")
)
[IO.Directory]::CreateDirectory($temporaryDirectory) | Out-Null
$pfxPath = Join-Path $temporaryDirectory "certificate.pfx"
$newCertificateThumbprints = @()
$existingThumbprints = @()
$pfxCertificateThumbprints = @()

try {
    try {
        $pfxBytes = [Convert]::FromBase64String($pfxBase64)
    } catch {
        throw "STARCOMPANION_SIGNING_PFX_BASE64 is not valid base64."
    }
    [IO.File]::WriteAllBytes($pfxPath, $pfxBytes)
    $securePassword = ConvertTo-SecureString $pfxPassword -AsPlainText -Force
    $existingThumbprints = @(Get-ChildItem Cert:\CurrentUser\My | ForEach-Object {
        $_.Thumbprint
    })
    $pfxData = Get-PfxData -FilePath $pfxPath -Password $securePassword
    $pfxCertificateThumbprints = @(
        $pfxData.EndEntityCertificates
        $pfxData.OtherCertificates
    ) | Where-Object { $null -ne $_ } | ForEach-Object { $_.Thumbprint }
    $newCertificateThumbprints = @($pfxCertificateThumbprints | Where-Object {
        $_ -notin $existingThumbprints
    })
    $importParameters = @{
        FilePath = $pfxPath
        CertStoreLocation = "Cert:\CurrentUser\My"
        Password = $securePassword
        Exportable = $false
    }
    $imported = @(Import-PfxCertificate @importParameters)
    $certificate = $imported | Where-Object {
        $_.HasPrivateKey -and $_.Thumbprint -eq $expectedThumbprint
    } | Select-Object -First 1
    if ($null -eq $certificate) {
        throw "The imported certificate does not match the configured thumbprint."
    }
    $codeSigningEku = @($certificate.EnhancedKeyUsageList | Where-Object {
        $_.ObjectId.Value -eq "1.3.6.1.5.5.7.3.3"
    })
    if ($codeSigningEku.Count -eq 0) {
        throw "The selected certificate does not permit code signing."
    }
    $now = Get-Date
    if ($certificate.NotBefore -gt $now -or $certificate.NotAfter -le $now) {
        throw "The selected code-signing certificate is not currently valid."
    }

    $signTool = Find-SignTool
    foreach ($artifact in $resolvedArtifacts) {
        Invoke-SignTool $signTool @(
            "sign", "/sha1", $expectedThumbprint,
            "/fd", "SHA256", "/tr", $timestampUrl, "/td", "SHA256",
            "/v", $artifact
        )
        Invoke-SignTool $signTool @("verify", "/pa", "/all", "/v", $artifact)
        $signature = Get-AuthenticodeSignature -LiteralPath $artifact
        if ($signature.Status -ne "Valid") {
            throw "Authenticode verification failed for ${artifact}: $($signature.Status)"
        }
        if ($signature.SignerCertificate.Thumbprint -ne $expectedThumbprint) {
            throw "Unexpected signer certificate on $artifact."
        }
        if ($null -eq $signature.TimeStamperCertificate) {
            throw "The required RFC 3161 timestamp is absent from $artifact."
        }
    }

    $artifactHashes = [ordered]@{}
    foreach ($artifact in $resolvedArtifacts) {
        $artifactHashes[[IO.Path]::GetFileName($artifact)] = (
            Get-FileHash -LiteralPath $artifact -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }
    $reportObject = [ordered]@{
        schema = "starcompanion.authenticode.v1"
        status = "valid"
        timestamp_url = $timestampUrl
        certificate = [ordered]@{
            subject = $certificate.Subject
            thumbprint = $certificate.Thumbprint
            not_after_utc = $certificate.NotAfter.ToUniversalTime().ToString(
                "yyyy-MM-ddTHH:mm:ssZ"
            )
        }
        artifacts = $artifactHashes
    }
    $reportPath = [IO.Path]::GetFullPath($Report)
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($reportPath)) | Out-Null
    $reportObject | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding utf8
} finally {
    foreach ($thumbprint in $newCertificateThumbprints) {
        Remove-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
    $pfxPassword = $null
    $pfxBase64 = $null
}
