Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
result = shell.Run("""" & folder & "\run.bat""", 0, True)
If result <> 0 Then
    MsgBox "Не удалось запустить Hidden pool overlay. Подробности сохранены в setup.log в папке overlay.", 16, "Hidden pool overlay"
End If
