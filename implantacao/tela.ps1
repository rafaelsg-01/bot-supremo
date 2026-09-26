# Abre a tela do Chrome do bot no seu navegador (noVNC), por tunel SSH. Nada fica publico.
# Uso (PowerShell, no PC que tem acesso "ssh servidor-caseiro"):
#   powershell -ExecutionPolicy Bypass -File implantacao\tela.ps1
# Feche esta janela para encerrar o tunel.

$ip = (ssh servidor-caseiro "bash ~/bot-supremo/implantacao/ip-warp.sh").Trim()
$senha = (ssh servidor-caseiro "grep ^VNC_SENHA= ~/bot-supremo/.env | cut -d= -f2").Trim()
if (-not $ip) { Write-Host "Nao achei o IP do container warp."; exit 1 }

Write-Host "IP do warp: $ip"
Write-Host "Senha do VNC: $senha"
Write-Host "Abrindo o tunel e o navegador. Feche esta janela para encerrar."

$tunel = Start-Process ssh -ArgumentList "-N", "-L", "6080:${ip}:6080", "servidor-caseiro" -PassThru -NoNewWindow
Start-Sleep -Seconds 3
Start-Process "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"
Wait-Process -Id $tunel.Id
