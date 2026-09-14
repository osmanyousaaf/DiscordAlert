$Python = "C:\Users\Byteboom\AppData\Local\Programs\Python\Python312\python.exe"
$Src = "c:\Users\Byteboom\Desktop\discord alert\auto.py"

& $Python -m PyInstaller `
    --onefile `
    --noconsole `
    --name "DiscordAlert" `
    --distpath "c:\Users\Byteboom\Desktop\discord alert\dist" `
    --workpath "c:\Users\Byteboom\Desktop\discord alert\build" `
    --specpath "c:\Users\Byteboom\Desktop\discord alert" `
    --hidden-import winsdk `
    --hidden-import winsdk.windows.ui.notifications `
    --hidden-import winsdk.windows.ui.notifications.management `
    --hidden-import winsdk.windows.applicationmodel `
    --hidden-import winsdk.windows.foundation `
    --hidden-import winsdk.windows.foundation.collections `
    --collect-all winsdk `
    $Src
