param(
    [string]$BaseUrl = "http://127.0.0.1:8877",
    [int]$AcademicYear = 0,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$base = $BaseUrl.TrimEnd("/")
if ($AcademicYear -le 0) {
    $now = Get-Date
    $AcademicYear = if ($now.Month -ge 8) { $now.Year } else { $now.Year - 1 }
}
$accounts = @(Invoke-RestMethod -Uri "$base/api/accounts" -TimeoutSec 30)
$results = @()

foreach ($account in $accounts) {
    if ($account.status -ne "ready") {
        $results += [pscustomobject]@{
            Account = $account.name
            Status = "skipped"
            RunId = ""
            Submittable = 0
            FailedCourses = 0
            Errors = @()
            Error = "account is not authorized"
        }
        continue
    }

    try {
        $collected = Invoke-RestMethod `
            -Method Post `
            -Uri "$base/api/accounts/$($account.id)/collect?academic_year=$AcademicYear" `
            -TimeoutSec 900
        $results += [pscustomobject]@{
            Account = $account.name
            Status = $collected.status
            RunId = $collected.run_id
            Submittable = $collected.submittable
            FailedCourses = $collected.failed_courses
            Errors = $collected.errors
            Error = ""
        }
    }
    catch {
        $results += [pscustomobject]@{
            Account = $account.name
            Status = "failed"
            RunId = ""
            Submittable = 0
            FailedCourses = 0
            Errors = @()
            Error = $_.Exception.Message
        }
    }
}

$results | Format-Table -AutoSize

if ($OutputPath) {
    $payload = [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        academic_year = $AcademicYear
        accounts = $results
    }
    $payload |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $OutputPath -Encoding UTF8
}

if ($results.Status -contains "failed") {
    exit 1
}
if ($results.Status -contains "partial") {
    exit 2
}
