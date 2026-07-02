Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
runtimeHome = fso.GetAbsolutePathName(scriptDir & "\..\.nulla_runtime")

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = scriptDir

Set env = shell.Environment("Process")
env("PYTHONPATH") = scriptDir
env("NULLA_PROJECT_ROOT") = scriptDir
If env("NULLA_HOME") = "" Then
  env("NULLA_HOME") = runtimeHome
End If
If env("OLLAMA_API_KEY") = "" Then
  env("OLLAMA_API_KEY") = "ollama-local"
End If

q = Chr(34)
cmd = "cmd /c " & q & q & scriptDir & "\nulla_background.cmd" & q & q
shell.Run cmd, 0, False
