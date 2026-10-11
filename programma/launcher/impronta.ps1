# Impronta del codice di BioLoom per l'avviatore di Windows (bioloom.ps1 la carica con dot-source). La definizione è
# quella di docker/impronta.sh, l'implementazione di riferimento, e la CI controlla che le due diano lo stesso valore:
#   - radice: la cartella programma (il contesto della build Docker);
#   - file regolari sotto backend/, frontend/ e docker/, esclusi i percorsi con un segmento che inizia con "."
#     (file e cartelle nascosti), uguale a node_modules o __pycache__ o che finisce in .egg-info, i file *.pyc e
#     i prefissi frontend/dist/, docker/vendor/, docker/qmean/, docker/test/, backend/tests/;
#   - percorsi relativi alla radice con "/", ordinati per byte UTF-8 come LC_ALL=C sort (non con l'ordinale di .NET,
#     che confronta unità UTF-16 e mette i caratteri fuori dal piano base, come le emoji, prima di U+E000-U+FFFF);
#   - per ogni file la riga "<SHA-256 del contenuto senza i byte CR>  <percorso>" (esadecimale minuscolo, due
#     spazi, LF): i CR si tolgono perché nella copia di Windows (git con core.autocrlf) i file di testo sono CRLF;
#   - impronta: SHA-256 (esadecimale minuscolo) di queste righe in UTF-8.
# Funziona con Windows PowerShell 5.1 e con pwsh 7 (anche su Linux, per la CI). I file si leggono a blocchi di byte:
# vanno bene anche quelli grandi o binari.

function Get-BioLoomImpronta([string]$Root) {
    # impronta della cartella $Root (64 cifre esadecimali minuscole), $null se non si può calcolare
    $hex = { param([byte[]]$bytes) ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant() }
    try {
        if (-not $Root) { return $null }
        # un percorso relativo vale rispetto alla cartella corrente di PowerShell (quella di .NET può essere un'altra)
        $base = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).ProviderPath
        if (-not [IO.Directory]::Exists($base)) { return $null }
        $skipDirs = @("frontend/dist", "docker/vendor", "docker/qmean", "docker/test", "backend/tests")
        $files = New-Object 'System.Collections.Generic.List[string]'
        # visita con una pila di coppie (cartella, percorso relativo): come find con -prune, nelle cartelle escluse
        # (node_modules...) non si entra
        $stack = New-Object 'System.Collections.Generic.Stack[object]'
        foreach ($top in @("backend", "frontend", "docker")) {
            $dir = New-Object IO.DirectoryInfo ([IO.Path]::Combine($base, $top))
            if ($dir.Exists) { $stack.Push(@($dir, $top)) }
        }
        while ($stack.Count -gt 0) {
            $item = $stack.Pop()
            foreach ($entry in $item[0].GetFileSystemInfos()) {   # anche i file nascosti e di sistema
                $name = $entry.Name
                if ($name.StartsWith(".", [StringComparison]::Ordinal) -or $name -ceq "node_modules" -or $name -ceq "__pycache__" -or
                    $name.EndsWith(".egg-info", [StringComparison]::Ordinal)) { continue }
                # collegamenti simbolici e giunzioni: find non li segue e non li conta come file regolari (i file di
                # OneDrive hanno anche loro l'attributo ReparsePoint, ma sono file veri)
                if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -and @("SymbolicLink", "Junction") -contains $entry.LinkType) { continue }
                $rel = $item[1] + "/" + $name
                if ($entry -is [IO.DirectoryInfo]) {
                    if ($skipDirs -cnotcontains $rel) { $stack.Push(@($entry, $rel)) }
                } elseif (-not $name.EndsWith(".pyc", [StringComparison]::Ordinal)) {
                    $files.Add($rel)
                }
            }
        }
        # ordine per byte UTF-8: chiave = byte del percorso in esadecimale (due cifre per byte, quindi l'ordinale sulle
        # chiavi è l'ordine dei byte); i percorsi sono tutti diversi, l'ordinamento non stabile di Array.Sort non conta
        $utf8 = New-Object Text.UTF8Encoding $false
        $paths = $files.ToArray()
        $keys = New-Object string[] $paths.Length
        for ($i = 0; $i -lt $paths.Length; $i++) { $keys[$i] = & $hex $utf8.GetBytes($paths[$i]) }
        [Array]::Sort($keys, $paths, [StringComparer]::Ordinal)

        $manifest = New-Object Text.StringBuilder
        $buffer = New-Object byte[] 1048576
        $empty = New-Object byte[] 0
        foreach ($rel in $paths) {
            $sha = [Security.Cryptography.SHA256]::Create()
            # FileShare.ReadWrite: si legge anche un file aperto in scrittura da un editor
            $stream = [IO.File]::Open([IO.Path]::Combine($base, $rel), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
                while (($n = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    # i tratti fra un CR e l'altro vanno nell'hash, i CR no (IndexOf è veloce, un ciclo sui byte no)
                    $start = 0
                    while ($start -lt $n) {
                        $cr = [Array]::IndexOf($buffer, [byte]13, $start, $n - $start)
                        if ($cr -lt 0) { $cr = $n }
                        if ($cr -gt $start) { [void]$sha.TransformBlock($buffer, $start, $cr - $start, $null, 0) }
                        $start = $cr + 1
                    }
                }
                [void]$sha.TransformFinalBlock($empty, 0, 0)
                [void]$manifest.Append((& $hex $sha.Hash)).Append("  ").Append($rel).Append("`n")
            } finally {
                $stream.Dispose()
                $sha.Dispose()
            }
        }
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $result = & $hex $sha.ComputeHash($utf8.GetBytes($manifest.ToString()))
        } finally {
            $sha.Dispose()
        }
        if ($result -cmatch '^[0-9a-f]{64}$') { return $result }
        return $null
    } catch {
        return $null
    }
}
