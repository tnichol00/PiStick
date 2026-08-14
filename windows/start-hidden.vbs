Option Explicit

Dim fso, shell, scriptDirectory, appDirectory, installRoot
Dim pythonPathFile, pythonExecutable, launcher, command, file

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
appDirectory = fso.GetParentFolderName(scriptDirectory)
installRoot = fso.GetParentFolderName(appDirectory)
pythonPathFile = fso.BuildPath(installRoot, "pythonw-path.txt")
launcher = fso.BuildPath(scriptDirectory, "background_launcher.pyw")

If Not fso.FileExists(pythonPathFile) Then WScript.Quit 2
If Not fso.FileExists(launcher) Then WScript.Quit 3

Set file = fso.OpenTextFile(pythonPathFile, 1, False)
pythonExecutable = Trim(file.ReadAll)
file.Close

If Not fso.FileExists(pythonExecutable) Then WScript.Quit 4
command = Chr(34) & pythonExecutable & Chr(34) & " " & Chr(34) & launcher & Chr(34)
shell.Run command, 0, False
